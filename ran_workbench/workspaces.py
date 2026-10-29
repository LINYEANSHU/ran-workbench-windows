"""One native frame; lazily owned editors share one local WebEngine profile."""
from copy import deepcopy
from pathlib import Path
import json
import os
import tempfile

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget)
from .i18n import t


class WorkspaceShell(QObject):
    NAMES = ('Modeling', 'Strategy', 'Workbench')

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.workbench_title = window.windowTitle()
        self.default_manifest = None
        self.default_manifest_attempted = False
        frame = QWidget(window)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.tabs = QTabWidget(frame)
        self.tabs.setDocumentMode(True)
        palette = self.tabs.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor('#161c1a'))
        palette.setColor(QPalette.ColorRole.Base, QColor('#161c1a'))
        frame.setPalette(palette)
        frame.setAutoFillBackground(True)
        self.tabs.setPalette(palette)
        self.tabs.setAutoFillBackground(True)
        self.tabs.setStyleSheet("""
            QTabWidget { background: #161c1a; }
            QTabWidget::pane { border: 0; }
            QTabBar { background: #161c1a; }
            QTabBar::tab { background: #1f2725; color: #d3ddd7;
                min-width: 116px; padding: 12px 18px; border: 0; }
            QTabBar::tab:selected { background: #33453f; color: #ffffff; }
            QTabBar::tab:hover { background: #2b3a35; }
            QTabBar::tab:focus { border-bottom: 2px solid #9fc7b8; }
        """)
        self.modeling = QWidget(self.tabs)
        self.modeling_layout = QVBoxLayout(self.modeling)
        self.modeling_layout.setContentsMargins(0, 0, 0, 0)
        self.empty = QWidget(self.modeling)
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setContentsMargins(48, 40, 48, 40)
        empty_layout.addStretch()
        title = self.empty_title = QLabel(t('打开城市模型'), self.empty)
        title.setStyleSheet('font-size:24px;font-weight:600;color:#e3e2da')
        empty_layout.addWidget(title)
        copy = self.empty_copy = QLabel(t('选择已有的 .city.json 清单，在这里检查城市几何。\n模型按需载入；当前精细网格用于查看，尚未参与传播计算。'), self.empty)
        copy.setWordWrap(True)
        copy.setStyleSheet('color:#bdc7c1;font-size:14px;padding:12px 0')
        empty_layout.addWidget(copy)
        open_button = self.empty_open_button = QPushButton(t('打开城市模型…'), self.empty)
        open_button.setMaximumWidth(190)
        open_button.clicked.connect(self.open_city_dialog)
        empty_layout.addWidget(open_button)
        empty_layout.addStretch()
        self.empty.setStyleSheet('QWidget {background:#161c1a;color:#e3e2da;} QPushButton {padding:10px;background:#33453f;color:white;border:1px solid #9fc7b8;}')
        self.modeling_layout.addWidget(self.empty)
        self.strategy = QWidget(self.tabs)
        self.strategy_layout = QVBoxLayout(self.strategy)
        self.strategy_layout.setContentsMargins(0, 0, 0, 0)
        self.strategy_error = None
        self.strategy_message = QLabel(t('在 Workbench 场景中放置至少一个 BS 和 UE 后，进入策略实验。'), self.strategy)
        self.strategy_message.setWordWrap(True)
        self.strategy_message.setStyleSheet('padding:40px;color:#243f3b;font-size:16px;')
        self.strategy_layout.addWidget(self.strategy_message)
        self.open_experiment = QPushButton(t('打开已有策略实验…'), self.strategy)
        self.open_experiment.clicked.connect(self.open_strategy_dialog)
        self.strategy_layout.addWidget(self.open_experiment)
        self.tabs.addTab(self.modeling, 'Modeling')
        self.tabs.addTab(self.strategy, 'Strategy')
        self.tabs.addTab(window.view, 'Workbench')
        self.tabs.setCurrentIndex(2)
        layout.addWidget(self.tabs)
        actions = QWidget(self.tabs)
        actions.setStyleSheet('QWidget {background:#161c1a;color:#d3ddd7;} QPushButton {background:#33453f;color:white;border:1px solid #6b9986;padding:5px 10px;} QPushButton:disabled {color:#8e948d;} QPushButton:focus {border:2px solid #9fc7b8;}')
        row = QHBoxLayout(actions)
        row.setContentsMargins(8, 0, 12, 0)
        self.status = QLabel('', actions)
        self.status.setStyleSheet('color:#bdc7c1')
        row.addWidget(self.status)
        self.open_button = QPushButton(t('打开城市模型…'), actions)
        self.open_button.clicked.connect(self.open_city_dialog)
        row.addWidget(self.open_button)
        self.refresh_button = QPushButton(t('从 Workbench 更新场景'), actions)
        self.refresh_button.setToolTip(t('建立当前 Workbench 场景的新实验；保存、放弃或取消现有编辑'))
        self.refresh_button.clicked.connect(self.refresh_strategy)
        row.addWidget(self.refresh_button)
        self.tabs.setCornerWidget(actions, Qt.Corner.TopRightCorner)
        window.setCentralWidget(frame)
        window.page.loadFinished.connect(lambda _ok: self.update_rendering())
        self.tabs.currentChanged.connect(self._changed)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_status)
        self.timer.start(500)
        self._changed(2)

    def retranslate(self):
        self.empty_title.setText(t('打开城市模型'))
        self.empty_copy.setText(t('选择已有的 .city.json 清单，在这里检查城市几何。\n模型按需载入；当前精细网格用于查看，尚未参与传播计算。'))
        self.empty_open_button.setText(t('打开城市模型…'))
        self.open_button.setText(t('打开城市模型…'))
        self.open_experiment.setText(t('打开已有策略实验…'))
        self.refresh_button.setText(t('从 Workbench 更新场景'))
        self.refresh_button.setToolTip(t('建立当前 Workbench 场景的新实验；保存、放弃或取消现有编辑'))
        if self.strategy_error:
            self.strategy_message.setText(t(self.strategy_error) + t('。请在 Workbench 调整场景，或打开已有策略实验。'))
        else:
            self.strategy_message.setText(t('在 Workbench 场景中放置至少一个 BS 和 UE 后，进入策略实验。'))
        self.update_status()
        self.update_title()

    @property
    def current_name(self):
        return self.NAMES[self.tabs.currentIndex()]

    def select(self, name):
        if name not in self.NAMES:
            raise ValueError(t('未知工作区'))
        self.tabs.setCurrentIndex(self.NAMES.index(name))
        if name == 'Strategy':
            self.ensure_strategy()

    def ensure_strategy(self, use_default=False):
        w = self.window
        if w.strategy_window is None or w.strategy_window.closed:
            from .system.desktop import StrategyWindow
            try:
                from .system.schema import default_experiment
                default_experiment(None if use_default else w.bridge.controller.project)
                editor = StrategyWindow(None if use_default else w.bridge.controller.project, parent=self.strategy,
                                        profile=w.profile, assets=w.assets)
            except ValueError as error:
                self.strategy_error = str(error)
                self.strategy_message.setText(t(self.strategy_error) + t('。请在 Workbench 调整场景，或打开已有策略实验。'))
                return None
            self.strategy_error = None
            self.strategy_message.hide()
            self.open_experiment.hide()
            w.strategy_window = editor
            self.strategy_layout.addWidget(editor)
            editor.page.loadFinished.connect(lambda _ok: self.update_rendering())
            editor.windowTitleChanged.connect(lambda _title: self.update_title())
        return w.strategy_window

    def open_strategy_dialog(self):
        editor = self.ensure_strategy(use_default=True)
        editor.bridge.command('{"action":"open_dialog"}')

    @property
    def active_page(self):
        if self.current_name == 'Workbench':
            return self.window.page
        if self.current_name == 'Strategy':
            return self.window.strategy_window.page if self.window.strategy_window else None
        return self.window.city_windows[0].page if self.window.city_windows else None

    def prepare_close(self):
        w = self.window
        controller = w.bridge.controller
        if controller.busy and QMessageBox.question(w, t('退出'), t('传播计算正在进行，取消并退出？')) != QMessageBox.StandardButton.Yes:
            return False
        if controller.dirty:
            buttons = QMessageBox.StandardButton
            choice = QMessageBox.question(w, t('保存项目'), t('当前场景有未保存修改，是否先保存？'), buttons.Save | buttons.Discard | buttons.Cancel, buttons.Save)
            if choice == buttons.Cancel:
                return False
            if choice == buttons.Save:
                if controller.busy:
                    QMessageBox.information(w, t('计算仍在运行'), t('请先取消传播计算并保存，再退出。'))
                    return False
                if not w.bridge.save(False):
                    return False
        return not w.strategy_window or w.strategy_window.prepare_close()

    def strategy_command(self, data):
        editor = self.ensure_strategy()
        self.select('Strategy')
        if editor is None:
            return {'ok':False, 'error':self.strategy_message.text(), 'state':self.window.bridge.controller.snapshot()}
        response = editor.bridge.service.execute(data if data.get('action') else {'action': 'get'})
        result = {'ok': response['ok'], 'state': self.window.bridge.controller.snapshot(), 'strategy': response}
        if not response['ok']:
            result['error'] = response['error']
        return result

    def refresh_strategy(self):
        from .system.schema import default_experiment
        try:
            default_experiment(self.window.bridge.controller.project)
        except ValueError as error:
            self.status.setText(t(str(error)))
            return False
        editor = self.ensure_strategy()
        if editor is None:
            return False
        service = editor.bridge.service
        if service.busy:
            self.status.setText(t('策略实验正在运行，请先取消或等待完成'))
            return False
        if service.dirty or service.draft_dirty:
            choice = QMessageBox.question(self.window, t('更新实验场景'),
                t('更新将建立一个新实验。先保存当前实验？'),
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save)
            if choice == QMessageBox.StandardButton.Cancel:
                return False
            if choice == QMessageBox.StandardButton.Save:
                saved = json.loads(editor.bridge.command('{"action":"save_dialog"}'))
                if not saved.get('ok') or saved.get('cancelled'):
                    return False
        result = service.execute({'action': 'new', 'data': {
            'scene': deepcopy(self.window.bridge.controller.project), 'discard_changes': True}})
        if not result['ok']:
            self.status.setText(t(result['error']))
            return False
        service.dirty = True
        editor.bridge.emit_state()
        self.select('Strategy')
        self.update_status()
        return True

    def open_city_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self.window, t('打开城市模型'), '', t('城市模型 (*.city.json)'))
        if path:
            self.open_city(path)

    def open_city(self, path, show_error=True):
        from .city_desktop import CityWindow, load_city
        try:
            manifest_path, _document = load_city(path)
            # Validate before replacing the existing view or its resource root.
            editor = CityWindow(manifest_path, automation=False, parent=self.modeling,
                                profile=self.window.profile, assets=self.window.assets)
        except (ValueError, OSError, KeyError, TypeError) as error:
            if show_error:
                QMessageBox.warning(self.window, t('城市模型未能打开'), t(str(error)))
            return False
        for old in self.window.city_windows:
            self.modeling_layout.removeWidget(old)
            old.close()
            old.deleteLater()
        self.window.city_windows = [editor]
        self.empty.hide()
        self.modeling_layout.addWidget(editor)
        editor.page.loadFinished.connect(lambda _ok: self.update_rendering())
        self.select('Modeling')
        self.update_rendering()
        return True

    def save_active(self):
        if self.current_name == 'Strategy':
            editor = self.ensure_strategy()
            if editor is not None:
                editor.page.runJavaScript('window.nrStrategy?.save()')
        elif self.current_name == 'Workbench':
            bridge=self.window.bridge
            response=json.loads(bridge.command('{"action":"save"}'))
            if not response.get('ok'):
                bridge.controller.error=response.get('error','项目未保存')
                bridge.emit_state()
                QMessageBox.warning(self.window,t('项目未保存'),t(bridge.controller.error))
                return False
            if response.get('cancelled'):return False
            bridge.controller.error=''
            bridge.emit_state()
            return True
        else:
            self.status.setText(t('城市模型为只读；可在模型视图中保存截图'))

    def _changed(self, _index):
        if self.current_name == 'Modeling' and self.default_manifest and not self.default_manifest_attempted and not self.window.city_windows:
            self.default_manifest_attempted = True
            self.open_city(self.default_manifest, show_error=False)
        if self.current_name == 'Strategy':
            self.ensure_strategy()
        self.open_button.setVisible(self.current_name == 'Modeling')
        self.refresh_button.setVisible(self.current_name == 'Strategy')
        self.update_status()
        self.update_rendering()
        self.update_title()

    def update_status(self):
        w = self.window
        strategy = w.strategy_window.bridge.service if w.strategy_window else None
        for index, name in enumerate(self.NAMES):
            busy = w.bridge.controller.busy if name == 'Workbench' else bool(strategy and strategy.busy) if name == 'Strategy' else False
            dirty = w.bridge.controller.dirty if name == 'Workbench' else bool(strategy and (strategy.dirty or strategy.draft_dirty)) if name == 'Strategy' else False
            self.tabs.setTabText(index, t(name) + (t(' · 运行中') if busy else ' ●' if dirty else ''))
        self.refresh_button.setEnabled(not (strategy and strategy.busy))
        if self.current_name == 'Strategy':
            self.status.setText(t('运行中') if strategy and strategy.busy else t('场景副本 · 不自动回写'))
        elif self.current_name == 'Modeling':
            self.status.setText(t('模型查看'))
        else:
            self.status.setText(t('本机场景与传播'))

    def update_title(self):
        w = self.window
        title = self.workbench_title
        if self.current_name == 'Strategy' and w.strategy_window:
            title = w.strategy_window.windowTitle()
        elif self.current_name == 'Modeling':
            title = t('Modeling — 城市模型')
        QMainWindow.setWindowTitle(w, title)

    def update_rendering(self):
        w = self.window
        pages = [('Workbench', w.page)]
        if w.strategy_window:
            pages.append(('Strategy', w.strategy_window.page))
        pages.extend(('Modeling', city.page) for city in w.city_windows)
        for name, page in pages:
            page.runJavaScript('window.ranSetWorkspaceActive?.(' + ('true' if name == self.current_name else 'false') + ')')

    def state(self):
        w = self.window
        return {'current': self.current_name, 'tabs': list(self.NAMES),
                'strategy_loaded': w.strategy_window is not None, 'modeling_loaded': bool(w.city_windows),
                'shared_profile': True}

    def automation_command(self, request, respond):
        data = request.get('data', {})
        if not isinstance(data, dict):
            raise ValueError(t('data 必须是对象'))
        if 'overwrite' in data and type(data['overwrite']) is not bool:
            raise ValueError(t('overwrite 需要布尔值'))
        method = data.get('method', 'get')
        if method == 'select':
            self.select(data['name'])
        elif method == 'open_city':
            if not self.open_city(data['path'], show_error=False):
                raise ValueError(t('城市模型未能打开'))
        elif method == 'resize':
            width, height = data.get('width'), data.get('height')
            if type(width) is not int or type(height) is not int or not (1100 <= width <= 5120 and 720 <= height <= 2880):
                raise ValueError(t('窗口尺寸范围为 1100..5120 × 720..2880'))
            self.window.resize(width, height)
        elif method == 'screenshot':
            path = Path(data['path']).resolve()
            if path.suffix.lower() != '.png' or (path.exists() and data.get('overwrite') is not True):
                raise ValueError(t('需要新的 .png 路径或显式 overwrite:true'))
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.png', delete=False) as stream:
                    temporary = Path(stream.name)
                if not self.window.grab().save(str(temporary), 'PNG'):
                    raise OSError(t('无法保存截图'))
                from .atomic_files import publish_file
                publish_file(temporary,path,overwrite=data.get('overwrite') is True)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
        elif method != 'get':
            raise ValueError(t('未知工作区操作'))
        page = self.active_page
        if page is None:
            respond({'ok':True, 'workspace':self.state(), 'view':{'ready':True, 'empty':True}})
        else:
            def observed(raw):
                respond({'ok':True, 'workspace':self.state(), 'view':json.loads(raw or '{}'), 'console':list(page.messages)})
            page.runJavaScript("JSON.stringify({ready:document.body?.dataset.ready==='true',width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight,nodes:document.querySelectorAll('#graph .node').length})", observed)

