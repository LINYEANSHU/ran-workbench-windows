"""Non-GUI experiment ownership, revisions, job lifecycle and safe file I/O."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4
from .schema import default_experiment,validate_experiment,experiment_fingerprint,load_experiment
from .graph import REGISTRY
from ..model import project_fingerprint
from ..model import _copy_json


def publish_json(path,value,overwrite=False):
    path=Path(path);temporary=None
    try:
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',newline='\n',dir=path.parent,prefix='.'+path.name+'.',suffix='.tmp',delete=False) as stream:
            temporary=Path(stream.name);json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)
            stream.flush();os.fsync(stream.fileno())
        if overwrite:os.replace(temporary,path)
        else:os.link(temporary,path)
    finally:
        if temporary:temporary.unlink(missing_ok=True)


class SimulationService:
    ACTIONS=('get','set','draft','discard_draft','validate','run','cancel','new','open','save','export','pin','compare')
    def __init__(self,runner,scene=None,emit=None):
        self.runner=runner;self.experiment=default_experiment(scene);self.source_scene=deepcopy(self.experiment['scene'])
        self.emit=emit or (lambda:None);self.revision=0;self.result=None;self.pinned=None;self.comparison=None
        self.path='';self.dirty=False;self.busy=False;self.error='';self.progress={};self.job={'status':'idle','id':None}
        self.draft_experiment=None;self.draft_base_revision=None
        self.document_id=uuid4().hex

    @property
    def draft_dirty(self):return self.draft_experiment is not None

    def clear_draft(self):self.draft_experiment=None;self.draft_base_revision=None

    def state(self):
        return dict(experiment=deepcopy(self.experiment),revision=self.revision,document_id=self.document_id,result=self.result,path=self.path,
                    dirty=self.dirty,busy=self.busy,error=self.error,progress=deepcopy(self.progress),job=deepcopy(self.job),
                    pinned=None if not self.pinned else dict(name=self.pinned['experiment']['name'],ues=self.pinned['result']['ues']),
                    comparison=self.comparison,draft_dirty=self.draft_dirty,
                    draft_experiment=deepcopy(self.draft_experiment),draft_base_revision=self.draft_base_revision,
                    draft_conflict=self.draft_dirty and self.draft_base_revision!=self.revision)

    def idle(self):
        if self.busy:raise ValueError('实验正在运行；请先取消或等待完成')

    def on_progress(self,event):
        if self.busy and self.job['status']=='running':self.progress=deepcopy(event);self.emit()

    def on_complete(self,result):
        self.busy=False
        if self.job['status']=='cancelling':self.job['status']='cancelled';self.result=None
        elif result.get('experiment_sha256')!=experiment_fingerprint(self.experiment):
            self.job['status']='failed';self.error='实验结果指纹与当前配置不一致，已丢弃';self.result=None
        else:
            self.result=result;self.job['status']='completed';self.error=''
            self.progress=dict(stage='实验完成',completed=1,total=1)
        self.emit()

    def on_failure(self,message):
        self.busy=False;self.result=None;self.error=str(message)
        self.job['status']='cancelled' if self.job['status']=='cancelling' else 'failed';self.emit()

    @staticmethod
    def file_path(data):
        value=data.get('path')
        if not isinstance(value,str) or not value.strip():raise ValueError('需要明确文件 path')
        return Path(value).expanduser().resolve()

    def replace(self,value,path=''):
        self.experiment=validate_experiment(value);self.revision+=1;self.path=str(path);self.dirty=False
        self.source_scene=deepcopy(self.experiment['scene'])
        self.result=None;self.pinned=None;self.comparison=None;self.error='';self.progress={};self.job={'id':None,'status':'idle'}
        self.clear_draft()
        self.document_id=uuid4().hex

    def update(self,value,from_gui=False):
        exp=validate_experiment(value);changed=experiment_fingerprint(exp)!=experiment_fingerprint(self.experiment)
        self.experiment=exp;self.revision+=1;self.dirty=True;self.error=''
        if changed:self.result=None;self.comparison=None;self.job={'id':None,'status':'idle'}
        if from_gui:self.clear_draft()

    def apply_gui_draft(self):
        if self.draft_dirty:
            if self.draft_base_revision!=self.revision:raise ValueError('草稿的原修订已过期；请保留草稿内容或放弃草稿并读取最新配置')
            self.update(self.draft_experiment,from_gui=True)

    def execute(self,request):
        try:
            if not isinstance(request,dict) or request.get('action') not in self.ACTIONS:raise ValueError('未知策略操作')
            action=request['action'];data=request.get('data',{})
            if not isinstance(data,dict):raise ValueError('data 必须是对象')
            for k in ('overwrite','discard_changes','from_gui'):
                if k in data and type(data[k]) is not bool:raise ValueError(k+' 需要布尔值')
            if 'expected_revision' in data and (type(data['expected_revision']) is not int or data['expected_revision']!=self.revision):raise ValueError('实验修订已变化，请重新读取后再编辑')
            extra={}
            if action=='get':extra['registry']=REGISTRY
            elif action=='validate':
                exp=validate_experiment(data.get('experiment',self.experiment));extra['fingerprint']=experiment_fingerprint(exp)
            elif action=='set':
                self.idle()
                if data.get('from_gui') and self.draft_dirty and self.draft_base_revision!=self.revision:raise ValueError('旧草稿不能覆盖外部更新；请读取最新配置')
                self.update(data['experiment'],data.get('from_gui',False))
            elif action=='draft':
                self.idle();base=data.get('base_revision')
                if type(base) is not int or base<0:raise ValueError('草稿需要原始 base_revision')
                value=_copy_json(data.get('experiment'),'draft')
                if not isinstance(value,dict):raise ValueError('草稿需要实验对象')
                self.draft_experiment=value;self.draft_base_revision=base
            elif action=='discard_draft':
                self.idle();self.clear_draft()
            elif action in ('new','open'):
                self.idle()
                if (self.dirty or self.draft_dirty) and not data.get('discard_changes'):raise ValueError('实验有未保存更改 / 草稿；先保存或显式放弃')
                if action=='open':
                    path=self.file_path(data);self.replace(load_experiment(path),path)
                else:self.replace(default_experiment(data.get('scene',self.source_scene)))
            elif action=='save':
                self.idle();path=self.file_path(data)
                if self.draft_dirty and self.draft_base_revision!=self.revision:raise ValueError('草稿的原修订已过期；请保留草稿内容或放弃草稿并读取最新配置')
                value=validate_experiment(self.draft_experiment if self.draft_dirty else self.experiment)
                publish_json(path,value,data.get('overwrite',False))
                if self.draft_dirty:self.update(value,from_gui=True)
                self.path=str(path);self.dirty=False;extra['saved_path']=str(path)
            elif action=='export':
                self.idle()
                if not self.result:raise ValueError('请先完成实验')
                path=self.file_path(data);publish_json(path,self.result,data.get('overwrite',False));extra['saved_path']=str(path)
            elif action=='run':
                self.idle()
                if self.draft_dirty:raise ValueError('请先应用或放弃未应用草稿，再运行实验')
                self.experiment=validate_experiment(self.experiment)
                self.result=None;self.comparison=None;self.error='';self.busy=True
                self.job={'id':uuid4().hex,'status':'running'};self.progress=dict(stage='准备信道',completed=0,total=1)
                try:self.runner.start(deepcopy(self.experiment),'system')
                except Exception as error:self.on_failure(str(error));raise
            elif action=='cancel':
                if self.busy:self.job['status']='cancelling';self.runner.cancel()
            elif action=='pin':
                self.idle()
                if not self.result:raise ValueError('先完成基准实验 A')
                self.pinned={'experiment':deepcopy(self.experiment),'result':deepcopy(self.result)}
            elif action=='compare':
                self.idle()
                if not self.pinned or not self.result:raise ValueError('需要基准 A 和当前已完成的实验 B')
                old=self.pinned['experiment'];keys=set(self.experiment['settings'])-{'trace_stride','trace_limit'}
                if project_fingerprint(old['scene'])!=project_fingerprint(self.experiment['scene']) or any(old['settings'][k]!=self.experiment['settings'][k] for k in keys):raise ValueError('A/B 需要相同场景、随机种子、时间与物理资源 / 解码配置')
                # Trajectories and snapshot sampling are external inputs, not a
                # strategy change that can be silently compared as equal input.
                for n in old['graph']['nodes']:
                    if n['kind'] in ('mobility','channel'):
                        match=next((x for x in self.experiment['graph']['nodes'] if x['id']==n['id']),None)
                        if not match or match['parameters']!=n['parameters']:raise ValueError('A/B 需要相同轨迹和信道快照配置')
                previous={u['ue_id']:u for u in self.pinned['result']['ues']};rows=[]
                for u in self.result['ues']:
                    a=previous[u['ue_id']];rows.append(dict(ue_id=u['ue_id'],a_goodput_mbps=a['goodput_mbps'],b_goodput_mbps=u['goodput_mbps'],delta_goodput_mbps=u['goodput_mbps']-a['goodput_mbps']))
                self.comparison={'ues':rows,'note':'相同外部输入下比较；不是统计显著性或实测性能结论'};extra['comparison']=self.comparison
            self.emit();return dict(ok=True,state=self.state(),**extra)
        except (ValueError,TypeError,KeyError,OSError,RuntimeError) as error:
            return dict(ok=False,error=str(error),state=self.state())
