"""Self-contained experiment files; existing city scene schema remains unchanged."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from ..model import default_project, validate_project, _copy_json, _unique_object, _invalid_constant
from ..worker import _atomic_json
from .nr import integer, slot_duration_s, STANDARD
from .graph import default_graph, compile_graph, required_channel_links

SETTINGS = dict(slots=200, mu=1, n_prb=12, pdsch_symbols=12, dmrs_re=12,
                overhead_re=0, pilot_symbols=1, seed=42, feedback_bits_per_slot=64,
                trace_stride=1, trace_limit=20000, decoder='reference', channel_backend='rt')


def default_experiment(scene=None):
    scene=validate_project(scene if scene is not None else default_project())
    if not scene['bs'] or not scene['ues']:raise ValueError('实验需要至少一个 BS 和一个 UE')
    return {'schema_version':2,'kind':'nr-experiment','name':scene['name']+' · NR 策略实验',
            'standard':deepcopy(STANDARD),'scene':scene,'settings':deepcopy(SETTINGS),'graph':default_graph(scene)}


def validate_experiment(value):
    exp=_copy_json(value,'experiment')
    if not isinstance(exp,dict) or exp.get('schema_version') not in (1,2) or type(exp.get('schema_version')) is not int or exp.get('kind')!='nr-experiment':raise ValueError('需要 nr-experiment schema_version=2（支持迁移早期 v1）')
    if set(exp)!={'schema_version','kind','name','standard','scene','settings','graph'}:raise ValueError('实验顶层字段缺失或未知')
    if not isinstance(exp['name'],str) or not exp['name'].strip():raise ValueError('实验名称不能为空')
    if exp['standard']!=STANDARD:raise ValueError('此实现锁定 TS 38.214 V18.4.0 / Release 18')
    exp['scene']=validate_project(exp['scene'])
    if not exp['scene']['bs'] or not exp['scene']['ues']:raise ValueError('实验需要 BS 和 UE')
    s=exp['settings']
    if not isinstance(s,dict) or set(s)!=set(SETTINGS):raise ValueError('实验 settings 字段不完整或未知')
    for k,lo,hi in [('slots',1,20000),('mu',0,3),('n_prb',1,100),('pdsch_symbols',1,13),('dmrs_re',0,48),('overhead_re',0,48),('pilot_symbols',1,4),('seed',0,2**32-1),('feedback_bits_per_slot',1,100000),('trace_stride',1,20000),('trace_limit',100,100000)]:integer(s[k],k,lo,hi)
    if s['pdsch_symbols']+s['pilot_symbols']>14 or 12*s['pdsch_symbols']-s['dmrs_re']-s['overhead_re']<=0:raise ValueError('slot RE 配置冲突')
    if s['n_prb']*12*15000*2**s['mu']>exp['scene']['bandwidth_hz']:raise ValueError('PRB 占用带宽超过场景带宽')
    if s['decoder'] not in ('reference','ideal'):raise ValueError('未知解码模型')
    if s['channel_backend'] not in ('rt','reference_fixture'):raise ValueError('未知信道后端')
    if s['slots']*len(exp['scene']['ues'])>500000:raise ValueError('实验工作量超过 500000 UE-slot 预算；减少时长 / UE 数')
    # Additive migration for experiments saved by the first SISO development
    # snapshot. These exact new fields are the only silently migrated keys.
    from .graph import REGISTRY
    additions={'power':('tx_ports','spacing_lambda'),'decode':('rx_ports','spacing_lambda'),
               'estimate':('phase_std_deg',),'report':('rank_limit','codebook_subset'),
               'adaptation':('max_layers','precoding','fixed_rank','fixed_pmi')}
    if exp['schema_version']==1 and isinstance(exp.get('graph'),dict) and isinstance(exp['graph'].get('nodes'),list):
        for n in exp['graph']['nodes']:
            if isinstance(n,dict) and isinstance(n.get('parameters'),dict):
                if n.get('kind')=='scheduler':n['parameters'].pop('weight',None)
                for key in additions.get(n.get('kind'),()):n['parameters'].setdefault(key,deepcopy(REGISTRY[n['kind']]['parameters'][key]['default']))
        nodes=exp['graph']['nodes'];edges=exp['graph'].get('edges')
        channel=next((n for n in nodes if isinstance(n,dict) and n.get('kind')=='channel'),None)
        if channel is not None and isinstance(edges,list):
            for n in nodes:
                if isinstance(n,dict) and n.get('kind')=='mobility' and not any(e.get('source')==n['id'] and e.get('target')==channel['id'] and e.get('target_port')=='motion' for e in edges if isinstance(e,dict)):
                    edges.append(dict(source=n['id'],source_port='motion',target=channel['id'],target_port='motion'))
        exp['schema_version']=2
    policies=compile_graph(exp)
    offered_packets=0
    for policy in policies['ues'].values():
        t=policy['traffic']
        offered_packets+=math.ceil(t['rate_mbps']*1e6/8*slot_duration_s(s['mu'])*s['slots']/t['packet_bytes']) if t['mode']=='constant_rate' else math.ceil(s['slots']*250/t['packet_bytes'])+max(4,math.ceil(2000/t['packet_bytes']))
    if offered_packets>500000:raise ValueError('业务包处理超过 500000 包预算；减少速率 / 时长或增大包')
    moving=any(any(p['mobility'][k]!=0 for k in ('vx','vy','vz')) for p in policies['ues'].values())
    if s['channel_backend']=='rt':
        interval=policies['global']['global']['channel']['snapshot_slots']
        solves=(math.ceil(s['slots']/interval) if moving else 1)*len(required_channel_links(exp['scene'],policies))
        if solves>4096:raise ValueError('RT 链路求解超过 4096 次预算；增加快照间隔或减少时长 / 设备')
    return exp


def experiment_fingerprint(value):
    exp=validate_experiment(value)
    for n in exp['graph']['nodes']:n.pop('position',None)
    exp['graph']['nodes'].sort(key=lambda n:n['id'])
    exp['graph']['edges'].sort(key=lambda e:(e['source'],e['source_port'],e['target'],e['target_port']))
    exp.pop('name',None)
    return hashlib.sha256(json.dumps(exp,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def load_experiment(path):
    with Path(path).open(encoding='utf-8-sig') as file:
        return validate_experiment(json.load(file,object_pairs_hook=_unique_object,parse_constant=_invalid_constant))


def save_experiment(exp,path):
    _atomic_json(Path(path),validate_experiment(exp))
