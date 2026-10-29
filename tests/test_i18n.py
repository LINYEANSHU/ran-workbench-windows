"""Language preference and live Qt host behavior, isolated from user settings."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_native_translation_keeps_unknown_project_text():
    from ran_workbench.i18n import t, normalize_language
    assert normalize_language('zh_CN') == 'zh-CN'
    assert normalize_language('en-US') == 'en'
    assert normalize_language('unsupported') == 'zh-CN'
    assert t('保存项目', 'en') == 'Save project'
    assert t('保存项目', 'zh-CN') == '保存项目'
    assert t('我的项目 My project', 'en') == '我的项目 My project'


def test_live_language_switch_and_future_pages_keep_editor_state(tmp_path):
    script = tmp_path / 'language_host.py'
    script.write_text('''import sys, json, time
from pathlib import Path
sys.path.insert(0, ROOT)
from PySide6.QtCore import QSettings, QEventLoop, QTimer, QUrl
from PySide6.QtWebEngineCore import QWebEngineScript
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from ran_workbench.desktop import Window, Page, register_scheme
from ran_workbench.i18n import LanguageManager
import ran_workbench.desktop as desktop
# Exercise the real Qt host and document lifecycle with a GPU-independent web
# document; the WebGL editors have their own integration acceptance tests.
fixture_root = Path(SETTINGS).parent / 'assets'
web = fixture_root / 'ran_workbench' / 'web'
web.mkdir(parents=True)
fixture = '<body data-ready="true"><input id="draft"><script>window.firstLanguage=window.RAN_LANGUAGE;window.RANI18n={setLanguage:language=>{document.documentElement.lang=language;window.languageSwitchCount=(window.languageSwitchCount||0)+1;}};</script></body>'
for name in ['index.html', 'strategy.html']:
    (web / name).write_text(fixture, encoding='utf-8')
desktop.resource_root = lambda: fixture_root
register_scheme()
app = QApplication(['language-test'])
app.setQuitOnLastWindowClosed(False)
settings = QSettings(SETTINGS, QSettings.Format.IniFormat)
w = Window(automation=False, language='en', settings=settings)
w.show()
def js(page, source):
    loop = QEventLoop(); result = []
    page.runJavaScript(source, lambda value: (result.append(value), loop.quit()))
    QTimer.singleShot(10000, loop.quit); loop.exec()
    assert result, 'JavaScript callback timed out'
    return result[0]
def ready(page):
    deadline = time.monotonic() + 15
    while not js(page, "document.body?.dataset.ready === 'true'"):
        assert time.monotonic() < deadline, 'editor not ready: ' + repr(page.messages)
        app.processEvents()
try:
    ready(w.page)
    assert w.i18n.language == 'en'
    assert js(w.page, 'window.firstLanguage') == 'en'
    assert [w.shell.tabs.tabText(i) for i in range(3)] == ['Modeling', 'Strategy', 'Workbench']
    assert w.shell.open_button.text() == 'Open city model…'
    w.shell.select('Strategy')
    editor = w.strategy_window
    ready(editor.page)
    draft = json.loads(json.dumps(editor.bridge.service.experiment))
    draft['settings']['slots'] = 17
    assert editor.bridge.service.execute({'action': 'draft', 'data': {'experiment': draft, 'base_revision': 0}})['ok']
    before = editor.bridge.service.state()
    revision = w.bridge.controller.revision
    js(w.page, "window.__languageDraft = {value: '未保存 draft'}")
    initial_url = w.page.url()
    w.language_actions['zh-CN'].trigger()
    app.processEvents()
    assert w.i18n.language == 'zh-CN'
    assert settings.value('ui/language') == 'zh-CN'
    assert w.shell.tabs.tabText(0) == '建模'
    assert w.shell.tabs.tabText(1) == '策略 ●'
    assert w.shell.current_name == 'Strategy'
    assert w.shell.state()['tabs'] == ['Modeling', 'Strategy', 'Workbench']
    assert w.shell.open_button.text() == '打开城市模型…'
    assert editor.bridge.service.state() == before
    assert w.bridge.controller.revision == revision and w.page.url() == initial_url
    assert js(w.page, 'window.__languageDraft.value') == '未保存 draft'
    assert js(w.page, 'window.RAN_LANGUAGE') == 'zh-CN'
    assert js(editor.page, 'window.RAN_LANGUAGE') == 'zh-CN'
    assert js(w.page, 'window.languageSwitchCount') == 1
    assert js(editor.page, 'window.languageSwitchCount') == 1
    # A page created after the switch receives preference before any document script.
    future = Page(w.profile, w.view)
    future.setUrl(QUrl('ran://app/index.html'))
    deadline = time.monotonic() + 10
    while js(future, 'window.firstLanguage') != 'zh-CN':
        assert time.monotonic() < deadline
        app.processEvents()
    future.deleteLater()
    # Dialog captions and errors are presentation only.
    captured = []
    QFileDialog.getSaveFileName = lambda *args: (captured.append(args[1]) or ('', ''))
    w.language_actions['en'].trigger()
    w.bridge.save(False)
    assert captured == ['Save RAN project']
    invalid = json.loads(w.bridge.command('{"action":"export"}'))
    assert invalid['error'] == 'Complete a coverage or UE calculation first'
    restored = LanguageManager(settings=settings)
    assert restored.language == 'en'
finally:
    w.api_force_close = True
    if w.strategy_window:
        w.strategy_window.bridge.service.clear_draft()
    w.close(); app.processEvents()
'''.replace('ROOT', repr(str(ROOT))).replace('SETTINGS', repr(str(tmp_path / 'settings.ini'))), encoding='utf-8')
    env = dict(os.environ, PYTHONUTF8='1', QT_QPA_PLATFORM='offscreen',
               QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --no-sandbox')
    result = subprocess.run([sys.executable, str(script)], cwd=ROOT, env=env,
                            capture_output=True, encoding='utf-8', errors='replace', timeout=65)
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
