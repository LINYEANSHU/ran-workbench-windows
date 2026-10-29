"""Native, read-only workspace for verified official city meshes."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import time
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView

from .automation_commands import failure, CommandError
from .automation_server import AutomationServer
from .city_data import resolve_city_route, atomic_json
from .desktop import Assets, LocalOnly, Page, register_scheme


def load_city(path):
    """Validate every renderer-required manifest field before changing any Qt state."""
    path = Path(path).resolve()
    document = json.loads(path.read_text('utf-8-sig'))
    if not isinstance(document, dict) or document.get('schema') != 'ran-city-1' or document.get('rf_supported') is not False:
        raise ValueError('需要有效的城市模型清单；此工作区只读取已有模型数据。')

    def vector(value, size, label):
        if not isinstance(value, list) or len(value) != size or any(type(x) not in (int, float) or not math.isfinite(x) for x in value):
            raise ValueError('城市清单字段无效：' + label)
        return value

    def bounds(value, label):
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError('城市清单范围无效：' + label)
        low, high = (vector(v, 3, label) for v in value)
        if any(a > b for a, b in zip(low, high)):
            raise ValueError('城市清单范围次序无效：' + label)

    def count(value, label):
        if type(value) is not int or value < 0:
            raise ValueError('城市清单数量无效：' + label)

    coordinate, source, statistics = (document.get(key) for key in ('coordinate_system', 'source', 'statistics'))
    if not all(isinstance(value, dict) for value in (coordinate, source, statistics)) or type(document.get('complete')) is not bool:
        raise ValueError('城市清单缺少坐标、来源、统计或完整性信息')
    vector(coordinate.get('anchor_native_m'), 3, 'anchor_native_m')
    longitude, latitude = vector(coordinate.get('render_anchor_deg'), 2, 'render_anchor_deg')
    if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
        raise ValueError('城市渲染经纬度超出有效范围')
    bounds(document.get('bounds_native_m'), 'bounds_native_m')
    for key in ('provider', 'attribution'):
        if not isinstance(source.get(key), str):
            raise ValueError('城市清单缺少来源字段：' + key)
    for key in ('building_models', 'triangles'):
        count(statistics.get(key), key)
    tiles, assets = document.get('tiles'), document.get('assets')
    if not isinstance(tiles, list) or not tiles or not isinstance(assets, list) or not assets:
        raise ValueError('城市模型清单没有可显示的几何')
    tile_ids = set()
    for group, records in [('tiles', tiles), ('assets', assets)]:
        identifiers = set()
        for item in records:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'] or item['id'] in identifiers:
                raise ValueError('城市模型标识无效或重复：' + group)
            identifiers.add(item['id'])
            if not isinstance(item.get('url'), str) or not isinstance(item.get('bank'), str):
                raise ValueError('城市模型资源路径或分区无效')
            resolve_city_route(path.parent, item['url'])
            bounds(item.get('bounds_native_m'), group)
            for key in ('vertices', 'triangles'):
                count(item.get(key), key)
            if group == 'tiles':
                vector(item.get('origin_native_m'), 3, 'origin_native_m')
                count(item.get('models'), 'models')
                count(item.get('buildings'), 'buildings')
                tile_ids.add(item['id'])
            elif item.get('tile') not in tile_ids or not isinstance(item.get('level_code'), str):
                raise ValueError('城市单体模型的图幅或等级无效')
    return path, document


class CityBridge(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.pending = {}

    @Slot(str, result=str)
    def command(self, raw):
        try:
            request = json.loads(raw)
            if request.get('action') == 'get_config':
                return json.dumps({'ok': True, 'manifest_url': '/city-data/' + self.window.manifest_path.name}, ensure_ascii=False)
            if request.get('action') == 'save_screenshot':
                path, _ = QFileDialog.getSaveFileName(self.window, '保存城市模型截图', '维港两岸.png', 'PNG (*.png)')
                if path and not self.window.view.grab().save(path, 'PNG'):
                    raise OSError('截图无法保存，请检查所选目录。')
                return json.dumps({'ok': True, 'cancelled': not bool(path)}, ensure_ascii=False)
            raise ValueError('未知城市工作区操作')
        except (ValueError, OSError, KeyError) as error:
            return json.dumps(failure(error), ensure_ascii=False)

    @Slot(str, str)
    def automationReply(self, ticket, raw):
        respond = self.pending.pop(ticket, None)
        if respond:
            try:
                result = json.loads(raw)
                result['console'] = list(self.window.page.messages)
                respond(result)
            except (ValueError, TypeError) as error:
                respond(failure(error))

    def view_command(self, data, respond):
        ticket = uuid4().hex
        self.pending[ticket] = respond
        script = 'Boolean(window.cityAutomation && window.cityAutomation.execute(' + json.dumps(ticket) + ',' + json.dumps(data, ensure_ascii=True, allow_nan=False) + '))'

        def present(value):
            if not value:
                self.automationReply(ticket, json.dumps(failure(CommandError('VIEW_NOT_READY', '城市界面尚未就绪'))))

        self.window.page.runJavaScript(script, present)
        QTimer.singleShot(90_000, lambda: self.automationReply(ticket,
            json.dumps(failure(CommandError('VIEW_TIMEOUT', '城市界面操作超时')))))


class CityWindow(QMainWindow):
    def __init__(self, manifest_path, endpoint_file=None, automation=True, smoke_path=None, parent=None, profile=None, assets=None):
        super().__init__(parent)
        if parent is not None:
            from PySide6.QtCore import Qt
            self.setWindowFlags(Qt.WindowType.Widget)
        self.manifest_path, self.document = load_city(manifest_path)
        self.setWindowTitle('维港两岸 · 官方精细模型 — RAN Workbench')
        self.resize(1440, 920)
        self.setMinimumSize(1050, 700)
        self.profile = profile or QWebEngineProfile(self)
        self.assets = assets or Assets(self.profile, city_root=self.manifest_path.parent)
        self.assets.city_root = self.manifest_path.parent
        if profile is None:
            self.profile.installUrlSchemeHandler(b'ran', self.assets)
            self.interceptor = LocalOnly(self.profile)
            self.profile.setUrlRequestInterceptor(self.interceptor)
        if parent is not None:
            self.setMinimumSize(0, 0)
        self.view = QWebEngineView(self)
        self.page = Page(self.profile, self.view)
        self.view.setPage(self.page)
        self.setCentralWidget(self.view)
        self.bridge = CityBridge(self)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject('bridge', self.bridge)
        self.page.setWebChannel(self.channel)
        self.automation_server = AutomationServer(self.automation_command, endpoint_file, self,
                                                   timeout_ms=100_000) if automation else None
        if self.automation_server:
            QApplication.instance().aboutToQuit.connect(self.automation_server.stop)
        self.view.setUrl(QUrl('ran://app/city.html'))
        self.smoke_path = Path(smoke_path) if smoke_path else None
        if self.smoke_path:
            self.smoke_started = time.monotonic()
            self.smoke_timer = QTimer(self)
            self.smoke_timer.timeout.connect(self.check_smoke)
            self.smoke_timer.start(500)

    def automation_command(self, request, respond):
        try:
            action = request.get('action')
            data = request.get('data', {})
            if not isinstance(data, dict):
                raise ValueError('data 必须为对象')
            if action == 'capabilities':
                respond({'ok': True, 'workspace': 'official-city',
                    'actions': ['get_state', 'view', 'screenshot', 'close'], 'rf_supported': False})
            elif action == 'get_state':
                self.bridge.view_command({'method': 'get'}, respond)
            elif action == 'view':
                self.bridge.view_command(data, respond)
            elif action == 'screenshot':
                path = Path(data['path']).resolve()
                if path.suffix.lower() != '.png':
                    raise ValueError('截图路径必须以.png结尾')
                if path.exists() and not data.get('overwrite'):
                    raise FileExistsError(path)
                path.parent.mkdir(parents=True, exist_ok=True)

                def save():
                    temporary = path.with_name(path.name + '.capturing')
                    try:
                        if not self.view.grab().save(str(temporary), 'PNG'):
                            raise OSError('无法保存城市截图')
                        if data.get('overwrite'):
                            __import__('os').replace(temporary, path)
                        else:
                            __import__('os').link(temporary, path)
                        respond({'ok': True, 'saved_path': str(path)})
                    except OSError as error:
                        respond(failure(error))
                    finally:
                        temporary.unlink(missing_ok=True)

                QTimer.singleShot(150, save)
            elif action == 'close':
                respond({'ok': True})
                QTimer.singleShot(150, self.close)
            elif action == 'run':
                raise CommandError('CITY_RF_UNSUPPORTED', '当前官方三角网格工作区用于模型检查；尚未接入无线传播求解。')
            else:
                raise CommandError('UNKNOWN_ACTION', '未知城市工作区操作')
        except (ValueError, OSError, KeyError, TypeError) as error:
            respond(failure(error))

    def check_smoke(self):
        def received(result):
            if result.get('ok') and result.get('view', {}).get('ready') or time.monotonic() - self.smoke_started > 100:
                self.smoke_timer.stop()
                atomic_json(self.smoke_path, result)
                QApplication.instance().exit(0 if result.get('view', {}).get('ready') else 2)
        self.automation_command({'action': 'view', 'data': {'method': 'get'}}, received)

    def closeEvent(self, event):
        if self.automation_server:
            self.automation_server.stop()
        event.accept()


def main():
    parser = argparse.ArgumentParser(description='查看港府原始精细城市模型')
    parser.add_argument('--city', type=Path)
    parser.add_argument('--endpoint-file', type=Path)
    parser.add_argument('--no-automation', action='store_true')
    parser.add_argument('--smoke-test', type=Path)
    args = parser.parse_args()
    register_scheme()
    app = QApplication(sys.argv[:1])
    app.setApplicationName('RANCityWorkbench')
    app.setOrganizationName('RANWorkbench')
    path = args.city
    if not path:
        pointer = Path(sys.executable).parent / 'city-default.json'
        if pointer.is_file():
            value = json.loads(pointer.read_text('utf-8'))
            path = (pointer.parent / value['manifest']).resolve()
    if not path:
        selected, _ = QFileDialog.getOpenFileName(None, '选择官方城市模型', '', '城市模型 (*.city.json)')
        if not selected:
            return 0
        path = Path(selected)
    try:
        window = CityWindow(path, args.endpoint_file, not args.no_automation, args.smoke_test)
    except (ValueError, OSError) as error:
        QMessageBox.critical(None, '城市模型未能打开', str(error))
        return 2
    window.show()
    return app.exec()
