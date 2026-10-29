"""Application-level automation contracts, without a desktop or GPU."""
from copy import deepcopy

from ran_workbench.controller import Controller
from ran_workbench.model import default_project, project_fingerprint


class Runner:
    def __init__(self):
        self.started = []
        self.cancelled = 0

    def start(self, project, mode):
        self.started.append((project, mode))

    def cancel(self):
        self.cancelled += 1


def commands():
    from ran_workbench.automation_commands import ApplicationCommands
    controller, runner = Controller(), Runner()
    return ApplicationCommands(controller, runner), controller, runner


def test_invalid_request_and_unknown_action_are_structured():
    api, c, _ = commands()
    for request in [None, [], {'action': 'get_state', 'data': []}, {'action': 1}]:
        result = api.execute(request)
        assert result['ok'] is False and result['error_code'] == 'INVALID_REQUEST'
    assert api.execute({'action': 'eval'})['error_code'] == 'UNKNOWN_ACTION'
    assert c.revision == 0


def test_project_edits_are_atomic_and_revision_checked():
    api, c, _ = commands()
    original = deepcopy(c.project)
    result = api.execute({'action': 'update_project', 'data': {'patch': {'solver': {'max_depth': 1}}, 'expected_revision': 0}})
    assert result['ok'] and c.project['solver']['max_depth'] == 1
    assert c.project['solver']['seed'] == original['solver']['seed']
    conflict = api.execute({'action': 'update_project', 'data': {'patch': {'name': 'wrong'}, 'expected_revision': 0}})
    assert not conflict['ok'] and conflict['error_code'] == 'REVISION_CONFLICT'
    bad = api.execute({'action': 'update_entity', 'data': {'group': 'buildings', 'id': 'B-01', 'patch': {'height_m': -1}}})
    assert not bad['ok'] and c.revision == 1 and c.project['buildings'] == original['buildings']


def test_removed_solver_option_is_not_a_configurable_api_parameter():
    api, c, _ = commands()
    before = deepcopy(c.project)
    response = api.execute({'action': 'update_project', 'data': {'patch': {'solver': {'diffraction': True}}}})
    assert not response['ok']
    assert c.project == before and c.revision == 0
    assert 'diffraction' not in api.execute({'action': 'get_project'})['project']['solver']


def test_entities_share_geometry_validation_and_invalidate_results():
    api, c, _ = commands()
    c.begin('ue')
    c.complete({'mode': 'ue', 'scene_sha256': project_fingerprint(c.project), 'ues': []})
    item = {'id': 'UE-02', 'name': '新增终端', 'position_m': [150, 100, 3]}
    assert api.execute({'action': 'add_entity', 'data': {'group': 'ues', 'item': item}})['ok']
    assert c.result is None and c.dirty and c.project['ues'][-1] == item
    assert not api.execute({'action': 'add_entity', 'data': {'group': 'ues', 'item': item}})['ok']
    assert api.execute({'action': 'remove_entity', 'data': {'group': 'ues', 'id': 'UE-02'}})['ok']
    assert not api.execute({'action': 'remove_entity', 'data': {'group': 'ues', 'id': 'missing'}})['ok']


def test_nonmodal_dirty_guard_save_open_and_import(tmp_path):
    from synthetic_inputs import write_import_fixture
    api, c, _ = commands()
    api.execute({'action': 'update_project', 'data': {'patch': {'name': '待保存'}}})
    assert api.execute({'action': 'new'})['error_code'] == 'UNSAVED_CHANGES'
    assert api.execute({'action': 'save'})['error_code'] == 'PATH_REQUIRED'
    path = tmp_path / '测试项目.ran.json'
    assert api.execute({'action': 'save', 'data': {'path': str(path)}})['ok']
    assert path.is_file() and not c.dirty
    assert api.execute({'action': 'update_project', 'data': {'patch': {'name': '已改变'}}})['ok']
    assert api.execute({'action': 'open', 'data': {'path': str(path)}})['error_code'] == 'UNSAVED_CHANGES'
    assert api.execute({'action': 'open', 'data': {'path': str(path), 'discard_changes': True}})['ok']
    assert c.project['name'] == '待保存' and not c.dirty
    fixture_path = write_import_fixture(tmp_path / 'import.geojson')
    assert api.execute({'action': 'import_geojson', 'data': {'path': str(fixture_path), 'source_crs': 'EPSG:2326'}})['ok']
    assert len(c.project['buildings']) == 3 and c.dirty and not c.project_path
    assert sum(len(b['holes_m']) for b in c.project['buildings']) == 1


def test_real_job_identity_cancellation_and_stale_wait_protection():
    api, c, runner = commands()
    first = api.execute({'action': 'run', 'data': {'mode': 'ue'}})
    assert first['ok'] and first['state']['job']['status'] == 'running'
    first_id = first['state']['job']['id']
    assert runner.started[0][1] == 'ue'
    assert api.execute({'action': 'new', 'data': {'discard_changes': True}})['error_code'] == 'BUSY'
    assert api.execute({'action': 'cancel'})['ok']
    assert c.job['status'] == 'cancelling' and c.busy and runner.cancelled == 1
    c.fail('计算已取消')
    assert c.job['status'] == 'cancelled' and not c.busy
    assert api.execute({'action': 'run', 'data': {'mode': 'ue'}})['ok']
    assert c.job['id'] != first_id
    c.complete({'mode': 'ue', 'scene_sha256': project_fingerprint(c.project), 'ues': []})
    assert c.job['status'] == 'completed'
    state = api.execute({'action': 'get_state'})['state']
    assert state['result'] is None and state['result_summary']['mode'] == 'ue'
    assert api.execute({'action': 'get_result'})['result']['mode'] == 'ue'


def test_failed_start_releases_job_and_events_have_bounded_cursor():
    api, c, runner = commands()
    def failed(*_):
        raise OSError('cannot start')
    runner.start = failed
    response = api.execute({'action': 'run'})
    assert not response['ok'] and not c.busy and c.job['status'] == 'failed'
    for i in range(600):
        api.record('progress', {'completed': i})
    events = api.execute({'action': 'get_events', 'data': {'after': 0}})
    assert events['truncated'] and len(events['events']) <= 256
    assert events['events'][-1]['data']['completed'] == 599
    assert api.execute({'action': 'get_events', 'data': {'after': events['cursor']}})['events'] == []


def test_close_requires_explicit_dirty_and_busy_policies():
    api, c, _ = commands()
    closed = []
    api.close = lambda: closed.append(True)
    c.update(default_project())
    assert api.execute({'action': 'close'})['error_code'] == 'UNSAVED_CHANGES'
    c.begin('ue')
    assert api.execute({'action': 'close', 'data': {'discard_changes': True}})['error_code'] == 'BUSY'
    assert api.execute({'action': 'close', 'data': {'discard_changes': True, 'cancel_running': True}})['ok']
    assert closed == [True]


def test_accepted_close_immediately_blocks_later_edits_and_jobs():
    api, c, _ = commands()
    api.close = lambda: None  # Real window closure is intentionally deferred until reply flush.
    assert api.execute({'action': 'close'})['ok']
    before = deepcopy(c.project)
    for request in [{'action': 'update_project', 'data': {'patch': {'name': 'lost edit'}}},
                    {'action': 'run'}, {'action': 'view', 'data': {'method': 'form'}}]:
        assert api.execute(request)['error_code'] == 'CLOSING'
    assert c.project == before and not c.busy
    assert api.execute({'action': 'get_state'})['state']['closing']
