"""Review regressions against the real Qt host, WebChannel and rendered controls."""
import pytest

from test_unified_workspaces import run_host


STRATEGY_SETUP = '''import time
from copy import deepcopy
s = w.shell
s.select('Strategy')
editor = w.strategy_window
service = editor.bridge.service
def pump(seconds=.03):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(.002)
def until(predicate):
    end = time.monotonic() + 15
    while time.monotonic() < end:
        if predicate(): return
        pump()
    raise AssertionError('Qt/WebChannel condition timed out')
def js(code):
    replies = []
    editor.page.runJavaScript(code, replies.append)
    until(lambda: bool(replies))
    return replies[0]
until(lambda: js("document.body?.dataset.ready==='true'"))
def edit(slots):
    js("document.getElementById('setting-slots').value='%s';document.getElementById('setting-slots').dispatchEvent(new Event('change'));true" % slots)
    until(lambda: service.draft_dirty and service.draft_experiment['settings']['slots'] == slots)
edit(201)
edit(202)
js("document.getElementById('undo').click();true")
until(lambda: service.draft_experiment['settings']['slots'] == 201)
'''


@pytest.mark.parametrize('action,choice', [
    ('new', 'Cancel'), ('open', 'Cancel'),
    ('new', 'Save'), ('open', 'Save'),
    ('open', 'Discard'), ('save', 'Cancel'),
])
def test_strategy_cancel_preserves_draft_and_both_history_stacks(tmp_path, action, choice):
    run_host(STRATEGY_SETUP + f'''
original = deepcopy(service.experiment)
original_id = service.document_id
QMessageBox.question = lambda *a, **k: QMessageBox.StandardButton.{choice}
QFileDialog.getOpenFileName = lambda *a, **k: ('', '')
QFileDialog.getSaveFileName = lambda *a, **k: ('', '')
js("window.confirm=()=>true;document.getElementById('{action}').click();true")
pump(.35)
assert service.experiment == original, 'a cancelled dialog must not apply or replace the experiment'
assert service.document_id == original_id and service.draft_dirty
service.emit()
pump(.05)
assert js("document.getElementById('apply').disabled") is False
assert js("document.getElementById('undo').disabled") is False
assert js("document.getElementById('redo').disabled") is False
js("document.getElementById('redo').click();true")
until(lambda: service.draft_experiment['settings']['slots'] == 202)
js("document.getElementById('undo').click();true")
until(lambda: service.draft_experiment['settings']['slots'] == 201)
js("window.nrStrategy.apply().then(()=>window.applied=true);true")
until(lambda: js('Boolean(window.applied)'))
assert service.experiment['settings']['slots'] == 201 and not service.draft_dirty
''', tmp_path)


def test_successful_strategy_replacement_clears_history_after_commit(tmp_path):
    run_host(STRATEGY_SETUP + '''
original_id = service.document_id
QMessageBox.question = lambda *a, **k: QMessageBox.StandardButton.Discard
js("window.confirm=()=>true;document.getElementById('new').click();true")
until(lambda: service.document_id != original_id)
until(lambda: js("document.getElementById('undo').disabled && document.getElementById('redo').disabled"))
assert not service.draft_dirty
assert js('window.nrStrategy.getDraft().settings.slots') == 200
''', tmp_path)


def test_workspace_screenshot_requires_a_real_boolean_before_file_access(tmp_path):
    run_host('''import time
root = Path(__file__).parent
w.show()
app.processEvents()
def request(data):
    replies = []
    w.bridge.automation_command({'action':'workspace', 'data':data}, replies.append)
    end = time.monotonic() + 10
    while not replies and time.monotonic() < end:
        app.processEvents()
        time.sleep(.002)
    assert replies, 'workspace did not respond'
    return replies[0]
target = root / 'existing.png'
for value in ['false', 'true', 0, 1, None, [], {}]:
    target.write_bytes(b'old image')
    response = request({'method':'screenshot', 'path':str(target), 'overwrite':value})
    assert not response['ok'], (value, response)
    assert response['error_code'] == 'INVALID_ARGUMENT'
    assert target.read_bytes() == b'old image'
    fresh = root / 'must-not-create' / 'fresh.png'
    response = request({'method':'screenshot', 'path':str(fresh), 'overwrite':value})
    assert not response['ok'], (value, response)
    assert not fresh.parent.exists(), 'invalid input must be rejected before directory creation'
for data in [{}, {'overwrite':False}]:
    response = request({'method':'screenshot', 'path':str(target), **data})
    assert not response['ok'] and target.read_bytes() == b'old image'
assert request({'method':'screenshot', 'path':str(target), 'overwrite':True})['ok']
assert target.read_bytes().startswith(b'\\x89PNG\\r\\n\\x1a\\n')
fresh = root / 'new.png'
assert request({'method':'screenshot', 'path':str(fresh)})['ok']
assert fresh.read_bytes().startswith(b'\\x89PNG\\r\\n\\x1a\\n')
''', tmp_path)


def test_native_save_surfaces_errors_busy_and_cancel_without_clearing_dirty(tmp_path):
    run_host('''import ran_workbench.desktop as desktop
c = w.bridge.controller
warnings = []
QMessageBox.warning = lambda *a, **k: warnings.append(a[2])
states = []
w.bridge.event.connect(lambda raw: states.append(json.loads(raw)))
c.dirty = True
c.project_path = str(Path(__file__).parent / 'deleted-parent' / 'scene.ran.json')
assert w.shell.save_active() is False
assert c.error and warnings[-1] == c.error
assert states[-1]['state']['error'] == c.error
assert c.dirty and not Path(c.project_path).exists()
for error in [PermissionError('read-only target'), OSError('disk full')]:
    def fail_write(*a, **k): raise error
    original = desktop.save_project
    desktop.save_project = fail_write
    try:
        assert w.shell.save_active() is False
        assert c.error == str(error) and warnings[-1] == str(error)
        assert c.dirty
    finally:
        desktop.save_project = original
c.busy = True
assert w.shell.save_active() is False
assert c.error and warnings[-1] == c.error
assert c.dirty
c.busy = False
c.project_path = ''
c.error = ''
before = len(warnings)
QFileDialog.getSaveFileName = lambda *a, **k: ('', '')
assert w.shell.save_active() is False
assert c.dirty and not c.error and len(warnings) == before
path = Path(__file__).parent / 'saved.ran.json'
QFileDialog.getSaveFileName = lambda *a, **k: (str(path), '')
assert w.shell.save_active() is True
assert path.is_file() and not c.dirty and not c.error
''', tmp_path)


def test_strategy_global_device_is_distinct_from_environment_in_navigation(tmp_path):
    run_host('''import time
w.bridge.controller.project['bs'][0]['id'] = 'global'
w.bridge.controller.project['bs'][0]['name'] = 'Named base station'
w.shell.select('Strategy')
editor = w.strategy_window
answers=[]
end=time.monotonic()+15
while time.monotonic()<end:
    answers.clear()
    editor.page.runJavaScript("document.body?.dataset.ready==='true' ? document.getElementById('layer-list').textContent : ''",answers.append)
    until=time.monotonic()+.1
    while not answers and time.monotonic()<until:app.processEvents()
    if answers and 'Named base station' in (answers[0] or ''):break
    app.processEvents()
    time.sleep(.01)
assert answers and 'Named base station' in answers[0], answers
assert '无线环境与观察' in answers[0]
''', tmp_path)
