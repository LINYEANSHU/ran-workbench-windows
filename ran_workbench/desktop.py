"""Native Qt window, local assets/WebChannel and authenticated local automation."""
import argparse
from copy import deepcopy
import json
import mimetypes
import os
from pathlib import Path
import sys
import tempfile
import time
from uuid import uuid4
from urllib.parse import unquote

from PySide6.QtCore import QByteArray, QBuffer, QFile, QIODevice, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor, QWebEngineUrlRequestJob, QWebEngineUrlScheme, QWebEngineUrlSchemeHandler
from PySide6.QtWebEngineWidgets import QWebEngineView

from .controller import Controller
from .jobs import JobRunner
from .model import default_project, load_project, save_project
from .automation_commands import ApplicationCommands, CommandError, failure
from .i18n import get_manager, install_language_menu, t


def resource_root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))


def register_scheme():
    scheme = QWebEngineUrlScheme(b'ran')
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    scheme.setFlags(QWebEngineUrlScheme.Flag.SecureScheme | QWebEngineUrlScheme.Flag.CorsEnabled | QWebEngineUrlScheme.Flag.FetchApiAllowed)
    QWebEngineUrlScheme.registerScheme(scheme)


class LocalOnly(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info):
        if info.requestUrl().scheme() not in ('ran', 'qrc', 'data', 'blob', 'about'):
            info.block(True)


class Assets(QWebEngineUrlSchemeHandler):
    def __init__(self, parent=None, city_root=None):
        super().__init__(parent)
        self.city_root = Path(city_root).resolve() if city_root else None

    def requestStarted(self, job):
        route = unquote(job.requestUrl().path())
        if job.requestUrl().host() != 'app' or bytes(job.requestMethod()) != b'GET':
            job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
            return
        if route == '/qtwebchannel.js':
            source = QFile(':/qtwebchannel/qwebchannel.js')
            if not source.open(QIODevice.OpenModeFlag.ReadOnly):
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return
            data, mime = bytes(source.readAll()), b'application/javascript'
        else:
            root = resource_root()
            if route.startswith('/city-data/'):
                if self.city_root is None:
                    job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
                    return
                from .city_data import resolve_city_route
                try:
                    path = resolve_city_route(self.city_root, route)
                except (ValueError, OSError):
                    job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
                    return
                base, tail = self.city_root, path.relative_to(self.city_root)
            elif route.startswith('/vendor/cesium/'):
                base = root / 'resources/cesium'
                if not base.is_dir():
                    base = root / 'node_modules/cesium/Build/Cesium'
                tail = route[len('/vendor/cesium/'):]
            else:
                base, tail = root / 'ran_workbench/web', route.lstrip('/') or 'index.html'
            path = (base / tail).resolve()
            if not path.is_relative_to(base.resolve()) or not path.is_file():
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return
            mime = {'.js':'application/javascript','.css':'text/css','.json':'application/json','.wasm':'application/wasm'}.get(path.suffix, mimetypes.guess_type(str(path))[0] or 'application/octet-stream').encode()
            # Stream large local meshes through Qt instead of duplicating the
            # whole GLB/texture in a Python QByteArray.
            source = QFile(str(path), job)
            if not source.open(QIODevice.OpenModeFlag.ReadOnly):
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return
            job.reply(mime, source)
            return
        buffer = QBuffer(job)
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(mime, buffer)


class Page(QWebEnginePage):
    def __init__(self, profile, parent):
        super().__init__(profile, parent)
        self.messages = []
        get_manager(profile).register_page(self)

    def javaScriptConsoleMessage(self, level, message, line, source):
        record = f'{level.name} {source}:{line} {message}'
        self.messages = (self.messages + [record])[-80:]

    def acceptNavigationRequest(self, url, kind, main_frame):
        return url.scheme() in ('ran', 'qrc', 'about')


class Bridge(QObject):
    event = Signal(str)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.controller = Controller()
        self.runner = JobRunner(self)
        self.runner.progress.connect(self.on_progress)
        self.runner.completed.connect(self.on_complete)
        self.runner.failed.connect(self.on_failure)
        self.api = ApplicationCommands(self.controller, self.runner, self.emit_state,
                                       getattr(window, 'request_api_close', None))
        if hasattr(window, 'strategy_command'):
            self.api.strategy_handler = window.strategy_command
            self.api.strategy_close_guard = window.check_strategy_close
        self.pending_views = {}

    def emit_state(self):
        self.api.record('state', {'revision': self.controller.revision, 'busy': self.controller.busy,
                                 'job': self.controller.job, 'dirty': self.controller.dirty,
                                 'error': self.controller.error})
        self.event.emit(json.dumps({'type':'state','state':self.controller.snapshot()}, ensure_ascii=False, allow_nan=False))
        self.window.setWindowTitle(('● ' if self.controller.dirty else '') + self.controller.project['name'] + ' — RAN Workbench')

    def on_progress(self, progress):
        if not self.controller.busy or self.controller.job['status'] == 'cancelling':
            return
        self.controller.progress = progress
        self.api.record('progress', progress)
        self.event.emit(json.dumps(dict(progress, type='progress'), ensure_ascii=False, allow_nan=False))

    def on_complete(self, result):
        try:
            self.controller.complete(result)
        except ValueError:
            pass
        self.emit_state()

    def on_failure(self, message):
        self.controller.fail(message)
        self.emit_state()

    def automation_command(self, request, respond):
        try:
            if request.get('action') == 'workspace':
                self.window.shell.automation_command(request, respond)
                return
            action, data = self.api.validate(request)
            if action == 'view':
                self.request_view(data, respond)
            elif action == 'screenshot':
                self.window.capture_view(data, respond)
            else:
                respond(self.api.execute(request))
        except (ValueError, TypeError, KeyError, OSError) as error:
            respond(failure(error))

    def request_view(self, data, respond):
        ticket = uuid4().hex
        self.pending_views[ticket] = respond
        encoded = json.dumps(data, ensure_ascii=True, allow_nan=False)
        script = f'Boolean(window.ranAutomation && window.ranAutomation.execute({json.dumps(ticket)}, {encoded}))'
        def ready(present):
            if not present:
                self.automationReply(ticket, json.dumps({'ok': False, 'error_code': 'VIEW_NOT_READY', 'error': t('三维界面尚未就绪')}))
        self.window.page.runJavaScript(script, ready)
        QTimer.singleShot(10_000, lambda: self.automationReply(ticket, json.dumps(
            {'ok': False, 'error_code': 'VIEW_TIMEOUT', 'error': t('界面操作等待超时')})))

    @Slot(str, str)
    def automationReply(self, ticket, raw):
        respond = self.pending_views.pop(ticket, None)
        if respond:
            try:
                result = json.loads(raw)
                result['console'] = list(self.window.page.messages)
                respond(result)
            except (ValueError, TypeError) as error:
                respond(failure(error))

    def confirm_discard(self):
        self.controller.idle()
        if not self.controller.dirty:
            return True
        buttons = QMessageBox.StandardButton
        answer = QMessageBox.question(self.window, t('保存项目'), t('当前场景有未保存修改，是否先保存？'), buttons.Save | buttons.Discard | buttons.Cancel, buttons.Save)
        if answer == buttons.Cancel:
            return False
        return self.save(False) if answer == buttons.Save else True

    def save(self, save_as):
        self.controller.idle()
        path = self.controller.project_path
        if save_as or not path:
            path, _ = QFileDialog.getSaveFileName(self.window, t('保存 RAN 项目'), path or '城市场景.ran.json', t('RAN 项目 (*.ran.json);;JSON (*.json)'))
        if not path:
            return False
        save_project(self.controller.project, path)
        self.controller.project_path, self.controller.dirty = path, False
        return True

    def import_buildings(self, options):
        if not self.confirm_discard():
            return {'cancelled': True}
        path, _ = QFileDialog.getOpenFileName(self.window, t('导入建筑轮廓（创建新场景并重置部署）'), '', 'GeoJSON (*.geojson *.json)')
        if not path:
            return {'cancelled': True}
        from .project_io import import_project
        project, report = import_project(path, options)
        self.controller.update(project)
        self.controller.project_path = ''
        return {'report': report}

    def export(self):
        result = self.controller.result
        if not result:
            raise ValueError(t('请先完成覆盖或 UE 计算'))
        path, selected_filter = QFileDialog.getSaveFileName(self.window, t('导出传播结果'), '传播结果.json', 'JSON (*.json);;CSV (*.csv)')
        if not path:
            return {'cancelled': True}
        from .project_io import export_result
        target = Path(path)
        if not target.suffix:
            target = target.with_suffix('.csv' if 'csv' in (selected_filter or '').lower() else '.json')
        return export_result(result, target, overwrite=True)

    @Slot(str, result=str)
    def command(self, raw):
        try:
            request = json.loads(raw)
            self.api.validate(request)
            action, data = request.get('action'), request.get('data') or {}
            extra = {}
            if action == 'get_state':
                pass
            elif action == 'strategy':
                return json.dumps(self.api.execute(request), ensure_ascii=False, allow_nan=False)
            elif action == 'set_project':
                self.controller.update(data['project'])
            elif action in ('save', 'save_as'):
                saved = self.save(action == 'save_as')
                if not saved:
                    extra['cancelled'] = True
            elif action == 'new':
                if not self.confirm_discard():
                    extra['cancelled'] = True
                else:
                    self.controller.replace(default_project())
            elif action == 'open':
                if not self.confirm_discard():
                    extra['cancelled'] = True
                else:
                    path, _ = QFileDialog.getOpenFileName(self.window, t('打开 RAN 项目'), '', t('RAN 项目 (*.ran.json *.json)'))
                    if not path:
                        extra['cancelled'] = True
                    else:
                        self.controller.replace(load_project(path), path)
            elif action == 'import_geojson':
                res = self.import_buildings(data)
                if isinstance(res, dict):
                    extra.update(res)
            elif action == 'run':
                response = self.api.execute(request)
                if not response.get('ok'):
                    return json.dumps(response, ensure_ascii=False, allow_nan=False)
            elif action == 'cancel':
                self.controller.request_cancel()
                self.runner.cancel()
            elif action == 'export':
                res = self.export()
                if isinstance(res, dict):
                    extra.update(res)
            else:
                raise ValueError(t('未知操作'))
            self.emit_state()
            response = {'ok': True, 'state': self.controller.snapshot(), **extra}
            return json.dumps(response, ensure_ascii=False, allow_nan=False)
        except Exception as error:
            return json.dumps({'ok': False, 'error': t(str(error))}, ensure_ascii=False)


class Window(QMainWindow):
    def __init__(self, smoke_path=None, automation=True, endpoint_file=None, language=None, settings=None):
        super().__init__()
        self.resize(1440,920)
        self.setMinimumSize(1100,720)
        self.profile = QWebEngineProfile(self)
        self.i18n = get_manager(self.profile, language, settings)
        self.setWindowTitle(t('RAN Workbench — 城市三维无线传播'))
        install_language_menu(self, self.i18n)
        self.assets = Assets(self.profile)
        self.profile.installUrlSchemeHandler(b'ran',self.assets)
        self.interceptor = LocalOnly(self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.view = QWebEngineView(self)
        self.page = Page(self.profile,self.view)
        self.view.setPage(self.page)
        self.setCentralWidget(self.view)
        self.bridge = Bridge(self)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject('bridge',self.bridge)
        self.page.setWebChannel(self.channel)
        self.automation_server = None
        self.api_force_close = False
        if automation:
            from .automation_server import AutomationServer
            self.automation_server = AutomationServer(self.bridge.automation_command, endpoint_file, self)
            QApplication.instance().aboutToQuit.connect(self.automation_server.stop)
        save = self.save_action = QAction(t('保存'),self)
        save.setShortcut('Ctrl+S')
        save.triggered.connect(lambda:self.shell.save_active())
        self.addAction(save)
        self.city_windows = []
        self.strategy_window = None
        from .workspaces import WorkspaceShell
        self.shell = WorkspaceShell(self)
        self.i18n.changed.connect(self.retranslate)
        self.view.setUrl(QUrl('ran://app/index.html'))
        self.smoke_path = Path(smoke_path) if smoke_path else None
        if self.smoke_path:
            self.smoke_started = time.monotonic()
            self.smoke_timer = QTimer(self)
            self.smoke_timer.timeout.connect(self.check_smoke)
            self.smoke_timer.start(400)

    def retranslate(self, _language=None):
        self.save_action.setText(t('保存'))
        self.shell.retranslate()
        self.setWindowTitle(('● ' if self.bridge.controller.dirty else '') +
                            self.bridge.controller.project['name'] + ' — RAN Workbench')

    def setWindowTitle(self, title):
        if hasattr(self, 'shell'):
            self.shell.workbench_title = title
            self.shell.update_title()
        else:
            super().setWindowTitle(title)

    def strategy_command(self, data):
        return self.shell.strategy_command(data)

    def check_strategy_close(self, data):
        if self.strategy_window is None or self.strategy_window.closed:
            return
        service = self.strategy_window.bridge.service
        if service.busy and not data.get('cancel_running', False):
            raise CommandError('BUSY', t('策略实验仍在运行；退出须指定 cancel_running:true'))
        if (service.dirty or service.draft_dirty) and not data.get('discard_changes', False):
            raise CommandError('UNSAVED_CHANGES', t('策略实验有未保存更改；请保存或显式放弃'))

    def open_city_workspace(self):
        self.shell.open_city_dialog()

    def check_smoke(self):
        page = self.shell.active_page
        if page is None:
            self.smoke_result(json.dumps({'ready':True, 'empty':True}))
            return
        page.runJavaScript("JSON.stringify({ready:document.body?.dataset.ready==='true',title:document.title,canvas:!!document.querySelector('canvas'),width:innerWidth,height:innerHeight,text:document.body?.innerText.slice(0,1000)})",self.smoke_result)

    def smoke_result(self, raw):
        try:
            result = json.loads(raw or '{}')
        except ValueError:
            result = {}
        timed_out = time.monotonic()-self.smoke_started > 45
        if not result.get('ready') and not timed_out:
            return
        self.smoke_timer.stop()
        result.update(workspace=self.shell.state(),frozen=bool(getattr(sys,'frozen',False)),executable=sys.executable,
                      resources=str(resource_root()),console=(self.shell.active_page.messages if self.shell.active_page else []))
        self.smoke_path.parent.mkdir(parents=True,exist_ok=True)
        self.smoke_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        QApplication.instance().exit(0 if result.get('ready') else 2)

    def closeEvent(self, event):
        if not self.api_force_close and not self.shell.prepare_close():
            event.ignore()
            return
        self.bridge.runner.shutdown()
        if self.bridge.controller.busy:
            self.bridge.controller.fail('计算已取消')
        if self.strategy_window:
            self.strategy_window.force_close = True
            self.strategy_window.close()
        for city in self.city_windows:
            city.close()
        self.shell.timer.stop()
        if self.automation_server:
            self.automation_server.stop()
        event.accept()

    def request_api_close(self):
        def close():
            self.api_force_close = True
            self.close()
        QTimer.singleShot(100, close)

    def capture_view(self, data, respond):
        path = Path(self.bridge.api._path(data))
        if path.suffix.lower() != '.png':
            raise CommandError('INVALID_ARGUMENT', t('截图路径必须以 .png 结尾'))
        if path.exists() and not data.get('overwrite', False):
            raise CommandError('FILE_EXISTS', t('截图已存在；覆盖须指定 overwrite:true'))
        def capture(ready):
            if not ready:
                respond({'ok': False, 'error_code': 'VIEW_NOT_READY', 'error': t('三维界面尚未渲染完成')})
                return
            def save():
                temporary = None
                try:
                    # Capture the application's own widget, independent of OS input or screen access.
                    pixmap = self.view.grab()
                    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '.', suffix='.tmp', delete=False) as stream:
                        temporary = Path(stream.name)
                    if pixmap.isNull() or not pixmap.save(str(temporary), 'PNG'):
                        raise OSError(t('无法保存应用截图，请检查目标目录'))
                    if data.get('overwrite', False):
                        os.replace(temporary, path)
                    else:
                        os.link(temporary, path)  # Exclusive publication: never replace another caller's image.
                    respond({'ok': True, 'saved_path': str(path), 'width': pixmap.width(), 'height': pixmap.height()})
                except (OSError, RuntimeError) as error:
                    respond(failure(error))
                finally:
                    if temporary and temporary.exists():
                        temporary.unlink()
            QTimer.singleShot(100, save)
        self.page.runJavaScript("document.body?.dataset.ready === 'true'", capture)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', choices=('Modeling', 'Strategy', 'Workbench'), default='Workbench')
    parser.add_argument('--language', choices=('zh-CN', 'en'), help='Interface language (saved for future launches)')
    parser.add_argument('--strategy', action='store_true')
    parser.add_argument('--city', type=Path)
    parser.add_argument('--experiment', type=Path)
    parser.add_argument('--smoke-test',type=str)
    parser.add_argument('--endpoint-file', type=str, help='Write private automation connection record here')
    parser.add_argument('--no-automation', action='store_true', help='Disable the local API for this instance')
    args = parser.parse_args()
    register_scheme()
    app = QApplication(sys.argv[:1])
    app.setApplicationName('RANWorkbench')
    app.setOrganizationName('RANWorkbench')
    window = Window(args.smoke_test, automation=not args.no_automation, endpoint_file=args.endpoint_file, language=args.language)
    target = 'Strategy' if args.strategy or args.experiment else 'Modeling' if args.city else args.workspace
    if not args.city and getattr(sys, 'frozen', False):
        config_path = Path(sys.executable).parent / 'city-default.json'
        try:
            config = json.loads(config_path.read_text('utf-8-sig'))
            if isinstance(config, dict) and isinstance(config.get('manifest'), str):
                window.shell.default_manifest = (config_path.parent / config['manifest']).resolve()
        except (ValueError, OSError):
            pass
    window.shell.select(target)
    if args.city:
        window.shell.open_city(args.city)
    if args.experiment:
        result = window.strategy_window.bridge.service.execute({'action':'open','data':{'path':str(args.experiment)}})
        if not result['ok']:
            raise ValueError(result['error'])
    window.show()
    return app.exec()
