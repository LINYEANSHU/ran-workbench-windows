"""Local Qt strategy workspace, sharing the host's assets and worker protocol."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from PySide6.QtCore import QObject,Signal,Slot,QTimer,QUrl
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication,QMainWindow,QFileDialog,QMessageBox
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from ..desktop import Assets,Page,LocalOnly,register_scheme
from ..jobs import JobRunner
from ..model import _unique_object,_invalid_constant
from ..i18n import get_manager, install_language_menu, t
from .service import SimulationService


class StrategyBridge(QObject):
    event=Signal(str)
    def __init__(self,window,scene=None):
        super().__init__(window);self.window=window;self.runner=JobRunner(self)
        self.service=SimulationService(self.runner,scene,self.emit_state)
        self.runner.progress.connect(self.service.on_progress);self.runner.completed.connect(self.service.on_complete);self.runner.failed.connect(self.service.on_failure)

    def emit_state(self):
        s=self.service.state()
        self.window.setWindowTitle(('● ' if s['dirty'] else '')+s['experiment']['name']+' — '+t('NR 策略仿真'))
        self.event.emit(json.dumps({'type':'state','state':s},ensure_ascii=False,allow_nan=False))

    @Slot(str,result=str)
    def command(self,raw):
        try:
            req=json.loads(raw,object_pairs_hook=_unique_object,parse_constant=_invalid_constant)
            action=req.get('action');data=req.get('data',{})
            if action in ('open_dialog','save_dialog','export_dialog','new_dialog'):
                self.service.idle()
                if action in ('open_dialog','new_dialog') and (self.service.dirty or self.service.draft_dirty):
                    choice=QMessageBox.question(self.window,t('未保存的实验'),t('保存当前实验后再继续？'),QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Save)
                    if choice==QMessageBox.StandardButton.Cancel:return json.dumps({'ok':True,'cancelled':True})
                    if choice==QMessageBox.StandardButton.Save:
                        saved=json.loads(self.command(json.dumps({'action':'save_dialog'})))
                        if not saved.get('ok') or saved.get('cancelled'):return json.dumps(saved,ensure_ascii=False)
                if action=='new_dialog':req={'action':'new','data':{'discard_changes':True}}
                elif action=='open_dialog':
                    path,_=QFileDialog.getOpenFileName(self.window,t('打开 NR 策略实验'),'',t('NR 实验 (*.ran-sim.json *.json)'))
                    if not path:return json.dumps({'ok':True,'cancelled':True})
                    req={'action':'open','data':{'path':path,'discard_changes':True}}
                else:
                    exporting=action=='export_dialog'
                    path,_=QFileDialog.getSaveFileName(self.window,t('导出实验结果') if exporting else t('保存 NR 策略实验'),
                        'NR结果.json' if exporting else self.service.path or 'NR策略.ran-sim.json','JSON (*.json)')
                    if not path:return json.dumps({'ok':True,'cancelled':True})
                    if not Path(path).suffix:path+='.json' if exporting else '.ran-sim.json'
                    req={'action':'export' if exporting else 'save','data':{'path':path,'overwrite':True}}
            result=self.service.execute(req)
            if not result.get('ok') and isinstance(result.get('error'),str):result=dict(result,error=t(result['error']))
            return json.dumps(result,ensure_ascii=False,allow_nan=False)
        except (ValueError,TypeError,KeyError,OSError) as error:return json.dumps({'ok':False,'error':t(str(error))},ensure_ascii=False)

    def automation_command(self,request,respond):
        action=request.get('action')
        if action=='strategy':respond(self.service.execute(request.get('data',{})))
        elif action=='get_state':
            def observed(raw):
                try:view=json.loads(raw or '{}')
                except ValueError:view={}
                respond({'ok':True,'state':self.service.state(),'view':view,'console':list(self.window.page.messages)})
            self.window.page.runJavaScript("JSON.stringify({ready:document.body?.dataset.ready==='true',title:document.title,nodes:document.querySelectorAll('#graph .node').length,width:innerWidth,height:innerHeight})",observed)
        elif action=='capabilities':
            respond({'ok':True,'state':self.service.state(),'actions':['strategy','get_state','close']})
        elif action=='close':
            data=request.get('data',{})
            if ((self.service.dirty or self.service.draft_dirty) and not data.get('discard_changes')) or (self.service.busy and not data.get('cancel_running')):
                respond({'ok':False,'error':t('退出需保存 / 显式放弃更改，并明确取消运行')});return
            self.window.force_close=True;QTimer.singleShot(50,self.window.close);respond({'ok':True})
        else:respond({'ok':False,'error':t('未知 NR 宿主操作')})


class StrategyWindow(QMainWindow):
    def __init__(self,scene=None,automation=False,endpoint_file=None,parent=None,profile=None,assets=None,language=None,settings=None):
        super().__init__(parent);
        if parent is not None:
            from PySide6.QtCore import Qt
            self.setWindowFlags(Qt.WindowType.Widget)
        self.resize(1500,950);self.setMinimumSize(1100,720);self.force_close=False;self.closed=False
        self.profile=profile or QWebEngineProfile(self)
        self.i18n=get_manager(self.profile,language,settings)
        if parent is None:install_language_menu(self,self.i18n)
        self.assets=assets or Assets(self.profile)
        if profile is None:
            self.profile.installUrlSchemeHandler(b'ran',self.assets)
            self.interceptor=LocalOnly(self.profile);self.profile.setUrlRequestInterceptor(self.interceptor)
        if parent is not None:
            self.setMinimumSize(0, 0)
        self.view=QWebEngineView(self);self.page=Page(self.profile,self.view);self.view.setPage(self.page);self.setCentralWidget(self.view)
        self.bridge=StrategyBridge(self,scene);self.channel=QWebChannel(self.page);self.channel.registerObject('strategy',self.bridge);self.page.setWebChannel(self.channel)
        self.server=None
        if automation:
            from ..automation_server import AutomationServer
            self.server=AutomationServer(self.bridge.automation_command,endpoint_file,self)
            QApplication.instance().aboutToQuit.connect(self.server.stop)
        save=self.save_action=QAction(t('保存实验'),self);save.setShortcut('Ctrl+S');save.triggered.connect(lambda:self.page.runJavaScript('window.nrStrategy?.save()'));self.addAction(save) if parent is None else None
        self.i18n.changed.connect(self.retranslate)
        self.retranslate()
        self.view.setUrl(QUrl('ran://app/strategy.html'))

    def retranslate(self,_language=None):
        self.save_action.setText(t('保存实验'))
        s=self.bridge.service.state()
        self.setWindowTitle(('● ' if s['dirty'] else '')+s['experiment']['name']+' — '+t('NR 策略仿真'))

    def prepare_close(self):
        service=self.bridge.service
        if self.force_close:return True
        if service.busy:
            if QMessageBox.question(self,t('退出策略实验'),t('实验正在运行，取消并退出？'))!=QMessageBox.StandardButton.Yes:return False
        if service.dirty or service.draft_dirty:
            choice=QMessageBox.question(self,t('未保存实验'),t('保存更改后退出？'),QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Save)
            if choice==QMessageBox.StandardButton.Cancel:return False
            if choice==QMessageBox.StandardButton.Save:
                if service.busy:
                    QMessageBox.information(self,t('实验仍在运行'),t('请先取消实验并保存，再退出。'));return False
                response=json.loads(self.bridge.command('{"action":"save_dialog"}'))
                if not response.get('ok'):
                    QMessageBox.warning(self,t('实验未保存'),t(response.get('error','请检查草稿后重试')));return False
                if response.get('cancelled'):return False
        return True

    def closeEvent(self,event):
        if not self.prepare_close():event.ignore();return
        if self.bridge.service.busy:
            self.bridge.service.execute({'action':'cancel'})
        self.bridge.runner.shutdown()
        if self.server:self.server.stop()
        self.closed=True
        event.accept()


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--experiment');parser.add_argument('--endpoint-file');parser.add_argument('--no-automation',action='store_true');parser.add_argument('--language',choices=('zh-CN','en'));args=parser.parse_args(argv)
    register_scheme();app=QApplication(sys.argv[:1]);app.setApplicationName('RAN NR Strategy Workbench')
    window=StrategyWindow(automation=not args.no_automation,endpoint_file=args.endpoint_file,language=args.language)
    if args.experiment:
        response=window.bridge.service.execute({'action':'open','data':{'path':args.experiment}})
        if not response['ok']:raise ValueError(response['error'])
    window.show();return app.exec()
