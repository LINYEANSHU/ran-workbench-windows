"""Behavioral Qt regressions in isolated child processes."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run_host(body, tmp_path):
    script = tmp_path / 'host.py'
    script.write_text('''import sys, json
from pathlib import Path
sys.path.insert(0, ROOT)
from PySide6.QtWidgets import QApplication, QMessageBox, QFileDialog
from PySide6.QtCore import QSettings
from ran_workbench.desktop import Window, register_scheme
register_scheme()
app = QApplication(['unified-test'])
app.setQuitOnLastWindowClosed(False)
settings = QSettings(str(Path(__file__).with_suffix('.ini')), QSettings.Format.IniFormat)
w = Window(automation=False, language='zh-CN', settings=settings)
try:
BODY
finally:
    w.api_force_close = True
    w.close()
    app.processEvents()
'''.replace('ROOT', repr(str(ROOT))).replace('BODY', '\n'.join('    '+s for s in body.splitlines())), encoding='utf-8')
    env = dict(os.environ, PYTHONUTF8='1', QT_QPA_PLATFORM='offscreen', QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --no-sandbox')
    result = subprocess.run([sys.executable, str(script)], cwd=ROOT, env=env, capture_output=True, encoding='utf-8', errors='replace', timeout=45)
    assert result.returncode == 0, result.stdout + '\n' + result.stderr


def test_ordered_tabs_lazy_strategy_and_single_window(tmp_path):
    run_host('''assert hasattr(w, 'shell'), 'unified workspace shell is missing'
s = w.shell
from ran_workbench.i18n import tr
assert [s.tabs.tabText(i) for i in range(3)] == [tr(n) for n in s.NAMES]
assert s.current_name == 'Workbench'
assert w.strategy_window is None and not w.city_windows
s.select('Strategy')
first = w.strategy_window
assert first is not None and not first.isWindow()
assert first.profile is w.profile
first.bridge.service.experiment['settings']['slots'] = 17
s.select('Workbench')
s.select('Strategy')
assert w.strategy_window is first
assert first.bridge.service.experiment['settings']['slots'] == 17
s.select('Modeling')
assert not w.city_windows
assert not any(x is not w and x.isWindow() and x.isVisible() for x in app.topLevelWidgets())
''', tmp_path)


def test_scene_refresh_cancel_busy_and_explicit_discard(tmp_path):
    run_host('''assert hasattr(w, 'shell'), 'unified workspace shell is missing'
s = w.shell
s.select('Strategy')
service = w.strategy_window.bridge.service
original = service.state()
w.bridge.controller.project['name'] = 'new scene'
service.dirty = True
QMessageBox.question = lambda *a, **k: QMessageBox.StandardButton.Cancel
assert s.refresh_strategy() is False
assert service.experiment == original['experiment'] and service.dirty
service.busy = True
assert s.refresh_strategy() is False
assert service.experiment == original['experiment']
service.busy = False
QMessageBox.question = lambda *a, **k: QMessageBox.StandardButton.Discard
assert s.refresh_strategy() is True
assert service.experiment['scene']['name'] == 'new scene'
assert service.source_scene['name'] == 'new scene'
assert service.result is None and service.pinned is None
assert service.dirty
''', tmp_path)


def test_invalid_city_does_not_create_or_replace_workspace(tmp_path):
    run_host('''assert hasattr(w, 'shell'), 'unified workspace shell is missing'
s = w.shell
assert not s.open_city(str(Path('no-such-file.city.json').resolve()), show_error=False)
assert not w.city_windows
assert s.current_name == 'Workbench'
''', tmp_path)


def test_new_scene_becomes_source_for_following_new_experiment():
    from copy import deepcopy
    from ran_workbench.system.service import SimulationService
    service = SimulationService(object())
    new_scene = deepcopy(service.source_scene)
    new_scene['name'] = 'updated source'
    service.pinned = {'experiment': deepcopy(service.experiment), 'result': {'ues': []}}
    assert service.execute({'action': 'new', 'data': {'scene': new_scene}})['ok']
    assert service.source_scene == new_scene
    assert service.pinned is None
    assert service.execute({'action': 'new'})['ok']
    assert service.experiment['scene']['name'] == 'updated source'



def test_missing_devices_can_recover_without_crashing_strategy_tab(tmp_path):
    run_host('''from copy import deepcopy
scene = deepcopy(w.bridge.controller.project)
w.bridge.controller.project['bs'] = []
w.shell.select('Strategy')
assert w.strategy_window is None
assert not w.shell.refresh_strategy()
w.bridge.controller.project = scene
w.shell.select('Workbench')
w.shell.select('Strategy')
assert w.strategy_window is not None
''', tmp_path)


def test_cancelled_exit_does_not_cancel_running_strategy(tmp_path):
    run_host('''w.shell.select('Strategy')
service = w.strategy_window.bridge.service
service.busy = True
service.dirty = True
QMessageBox.question = lambda *a, **k: QMessageBox.StandardButton.Yes if a[1] == '退出策略实验' else QMessageBox.StandardButton.Cancel
assert not w.strategy_window.prepare_close()
assert service.busy, 'cancelled exit must leave the task running'
service.busy = False
''', tmp_path)


def test_cancelled_host_exit_preserves_strategy_task(tmp_path):
    run_host('''w.shell.select('Strategy')
service = w.strategy_window.bridge.service
service.busy = True
w.bridge.controller.dirty = True
QMessageBox.question = lambda *a, **k: QMessageBox.StandardButton.Cancel if a[1] == '保存项目' else QMessageBox.StandardButton.Yes
w.close()
assert service.busy, 'host cancellation must not cancel another workspace task'
service.busy = False
''', tmp_path)


def test_embedded_workspace_does_not_force_a_larger_window(tmp_path):
    run_host('''w.show()
w.shell.select('Strategy')
app.processEvents()
w.resize(1100, 720)
app.processEvents()
assert w.height() == 720, w.height()
assert w.strategy_window.minimumHeight() == 0
''', tmp_path)


def test_failed_strategy_preflight_retains_no_webengine_pages(tmp_path):
    run_host('''from PySide6.QtWebEngineCore import QWebEnginePage
w.bridge.controller.project['bs'] = []
before = len(w.findChildren(QWebEnginePage))
for _ in range(4):
    assert w.shell.ensure_strategy() is None
assert len(w.findChildren(QWebEnginePage)) == before
''', tmp_path)


def test_structurally_invalid_city_preserves_current_view_and_resource_root(tmp_path):
    run_host('''sys.path.insert(0, str(Path(sys.path[0]) / 'tests'))
from synthetic_inputs import city_fixture
path = city_fixture(Path(__file__).parent / 'city')
assert w.shell.open_city(path, show_error=False)
current = w.city_windows[0]
root = w.assets.city_root
valid = json.loads(path.read_text('utf-8'))
for invalid in [[], {key:value for key,value in valid.items() if key != 'coordinate_system'}]:
    bad = path.with_name('invalid.city.json')
    bad.write_text(json.dumps(invalid), encoding='utf-8')
    assert not w.shell.open_city(bad, show_error=False)
    assert w.city_windows[0] is current and w.assets.city_root == root
''', tmp_path)
