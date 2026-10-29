from copy import deepcopy
import pytest
from ran_workbench.controller import Controller
from ran_workbench.model import project_fingerprint


def test_snapshot_exposes_the_actual_rt_ground_not_only_receiver_sampling():
    c = Controller()
    assert c.snapshot().get('scene_geometry') == {
        'ground_bounds_m': {'min_m': [-260, -220, 0], 'max_m': [260, 220, 0]}}


def test_small_sampling_box_does_not_clip_buildings_or_devices_from_ground():
    c = Controller()
    project = deepcopy(c.project)
    project['sampling'].update(min_m=[-1, -1, 1.5], max_m=[1, 1, 1.5])
    c.update(project)
    assert c.snapshot()['scene_geometry']['ground_bounds_m'] == {
        'min_m': [-215, -190, 0], 'max_m': [203, 144, 0]}


def test_spherical_measurement_extent_is_included_in_shared_ground():
    c = Controller()
    project = deepcopy(c.project)
    project['surface_sampling'] = dict(enabled=True, center_m=[500, -200, 25], radii_m=[50],
                                       subdivisions=0, replicates=1, ray_chunk_size=1024)
    c.update(project)
    assert c.snapshot()['scene_geometry']['ground_bounds_m'] == {
        'min_m': [-260, -350, 0], 'max_m': [650, 220, 0]}


def test_edit_invalidates_result_and_marks_unsaved():
    c = Controller()
    c.begin('coverage')
    c.complete({'scene_sha256': project_fingerprint(c.project), 'points': []})
    changed = deepcopy(c.project)
    changed['buildings'][0]['height_m'] += 10
    c.update(changed)
    assert c.result is None and c.dirty and c.revision == 1


def test_busy_blocks_edits_and_second_job():
    c = Controller()
    c.begin('coverage')
    with pytest.raises(ValueError, match='计算'):
        c.update(deepcopy(c.project))
    with pytest.raises(ValueError, match='计算'):
        c.begin('ue')


def test_stale_result_rejected_and_job_released():
    c = Controller()
    c.begin('coverage')
    with pytest.raises(ValueError, match='版本'):
        c.complete({'scene_sha256': 'stale'})
    assert not c.busy and c.result is None


def test_invalid_project_does_not_mutate_current_state():
    c = Controller()
    before = deepcopy(c.project)
    changed = deepcopy(before)
    changed['buildings'][0]['height_m'] = -2
    with pytest.raises(ValueError):
        c.update(changed)
    assert c.project == before and not c.dirty


def test_failure_and_load_clear_previous_results():
    c = Controller()
    c.begin('coverage')
    c.fail('已取消')
    assert not c.busy and c.error == '已取消'
    c.replace(c.project, '示例.ran.json')
    assert c.project_path == '示例.ran.json' and not c.dirty and c.error == ''
