"""Typed, scoped strategy graph. The graph is the sole policy source of truth."""
from copy import deepcopy
import math
from .nr import integer


def field(label, default, minimum=None, maximum=None, options=None, unit=''):
    result = dict(label=label, default=default, type='number' if isinstance(default,(int,float)) else 'string', unit=unit)
    if isinstance(default,bool): result['type']='boolean'
    elif isinstance(default,int): result['type']='integer'
    if minimum is not None: result['minimum']=minimum
    if maximum is not None: result['maximum']=maximum
    if options is not None: result['options']=options
    return result


def spec(label, layer, owner, inputs, outputs, params, standard='系统级策略'):
    return dict(label=label,layer=layer,owner=owner,inputs=inputs,outputs=outputs,parameters=params,standard=standard)


REGISTRY = {
 'power':spec('发射功率与天线','RF','bs',{}, {'radio':'Radio'}, {'power_dbm':field('总发射功率上限',30.,-30,70,unit='dBm'),
     'tx_ports':field('发射端口',1,options=[1,2]),'spacing_lambda':field('阵元间距',.5,.1,4.,unit='波长')}),
 'queue':spec('业务缓存','MAC','bs',{'traffic':'Traffic*'},{'queue':'Queue'}, {'limit_bytes':field('缓存上限',1000000,100,100000000,unit='byte')}),
 'csi_store':spec('已到达 CSI','PHY / MAC','bs',{'report':'CsiArrival*'},{'csi':'CsiStore'},{'max_age_slots':field('最大 CSI 年龄',20,1,10000,unit='slot')}),
 'scheduler':spec('资源调度','MAC','bs',{'queue':'Queue','csi':'CsiStore','harq':'HarqState'},{'grant':'Grant'},
     {'algorithm':field('调度算法','round_robin',options=['round_robin','proportional_fair','max_cqi']),
      'pf_window':field('PF 平滑窗口',100,1,10000,unit='slot')}),
 'adaptation':spec('MCS 与发送计划','PHY','bs',{'grant':'Grant','radio':'Radio'},{'tx':'TxPlan'},
     {'mode':field('链路自适应','cqi',options=['cqi','fixed']), 'fixed_mcs':field('固定 / 回退 MCS',3,3,28),
      'max_mcs':field('最大 MCS',28,3,28), 'backoff':field('MCS 保守回退',0,0,25),
      'max_layers':field('最大空间层数',2,1,2), 'precoding':field('预编码策略','reported',options=['reported','fixed']),
      'fixed_rank':field('固定 RI / 无反馈回退',1,1,2),'fixed_pmi':field('固定 PMI / 无反馈回退',0,0,3)},'38.214 §5.1.3；两端口 Type-I table 5.2.2.2.1-1'),
 'harq':spec('HARQ 进程','MAC','bs',{'ack':'HarqArrival*'},{'state':'HarqState'},
     {'processes':field('并行进程数',8,1,16),'k1_slots':field('ACK 传输延迟',2,1,1000,unit='slot'),
      'max_attempts':field('最多传输次数',4,1,16),'timeout_slots':field('ACK 超时',20,2,10000,unit='slot')},'38.321 §5.3.2：系统级 HARQ 状态'),
 'channel':spec('城市场景信道','传播','global',{'tx':'TxPlan*','motion':'Motion*'},{'observation':'Observation'},
     {'snapshot_slots':field('移动快照间隔',20,1,10000,unit='slot')},'Sionna RT / CUDA；非 TR 38.901 随机信道'),
 'traffic':spec('下行业务源','应用','ues',{}, {'traffic':'Traffic'},
     {'mode':field('业务模式','full_buffer',options=['full_buffer','constant_rate']),
      'rate_mbps':field('到达速率',1.,.001,10000.,unit='Mbps'), 'packet_bytes':field('包大小',200,16,65536,unit='byte')}),
 'measure':spec('CSI 参考测量','PHY','ues',{'channel':'Observation'},{'observation':'Observation'},
     {'period_slots':field('测量周期',5,1,320,unit='slot'),'offset_slots':field('时隙偏移',0,0,319,unit='slot')},'38.214 §5.2：参考资源抽象'),
 'estimate':spec('信道估计误差','PHY','ues',{'observation':'Observation'},{'observation':'Observation'},
     {'error_std_db':field('系数幅度误差标准差',0.,0.,20.,unit='dB'),
      'phase_std_deg':field('系数相位误差标准差',0.,0.,180.,unit='度')},'误差模型假设；不模拟逐样本 CSI-RS 接收'),
 'report':spec('CSI 报告策略','CSI','ues',{'observation':'Observation'},{'report':'CsiReport'},
     {'mode':field('报告粒度','wideband',options=['wideband','subband']),
      'subbands':field('子带数量',4,1,16), 'trigger':field('触发方式','periodic',options=['periodic','event']),
      'event_delta_cqi':field('事件触发 CQI 差',1,1,15), 'target_bler':field('参考目标 TBLER',.1,.00001,.5),
      'rank_limit':field('UE 最大报告 RI',2,1,2),'codebook_subset':field('两端口码本子集掩码',63,1,63,unit='bit 0–5')},'38.214 CQI table 1 / 两端口 Type-I；子带为研究扩展'),
 'decode':spec('数据接收与交付','PHY / 应用','ues',{'channel':'Observation'},{'ack':'Ack','delivered':'Delivered'},
     {'noise_figure_db':field('接收噪声系数',7.,0.,40.),'rx_ports':field('接收端口',1,options=[1,2]),
      'spacing_lambda':field('接收阵元间距',.5,.1,4.,unit='波长')},'线性 MMSE 接收；NR LDPC/AWGN 参考曲线抽象'),
 'feedback':spec('反馈承载与延迟','UCI 抽象','ues',{'report':'CsiReport','ack':'Ack'},{'report':'CsiArrival','ack':'HarqArrival'},
     {'serving_bs':field('服务基站','BS-01'), 'delay_slots':field('CSI 传输延迟',2,1,1000,unit='slot'),
      'loss_rate':field('CSI 丢失概率',0.,0.,1.),'max_wait_slots':field('最多排队等待',10,1,10000,unit='slot'),
      'header_bits':field('研究承载头部',0,0,1024,unit='bit')},'FDD 逻辑 UCI 预算；不模拟 PUCCH 波形'),
 'mobility':spec('终端运动','场景','ues',{}, {'motion':'Motion'},
     {'vx':field('东向速度',0.,-100.,100.,unit='m/s'),'vy':field('北向速度',0.,-100.,100.,unit='m/s'),
      'vz':field('上向速度',0.,-20.,20.,unit='m/s')},'轨迹模型'),
 'metrics':spec('指标与事件','观察','global',{'delivered':'Delivered*'},{},{}),
}
BS_KINDS=('power','queue','csi_store','scheduler','adaptation','harq')
UE_KINDS=('traffic','measure','estimate','report','decode','feedback','mobility')
ROUTES={('adaptation','radio'):{'power'},('adaptation','grant'):{'scheduler'},
        ('scheduler','queue'):{'queue'},('scheduler','csi'):{'csi_store'},('scheduler','harq'):{'harq'},
        ('queue','traffic'):{'traffic'},('csi_store','report'):{'feedback'},('harq','ack'):{'feedback'},
        ('channel','tx'):{'adaptation'},('channel','motion'):{'mobility'},
        ('measure','channel'):{'channel'},('decode','channel'):{'channel'},
        ('estimate','observation'):{'measure'},('report','observation'):{'measure','estimate'},
        ('feedback','report'):{'report'},('feedback','ack'):{'decode'},('metrics','delivered'):{'decode'}}


def validate_parameters(kind, parameters, node_id):
    rules = REGISTRY[kind]['parameters']
    if not isinstance(parameters,dict) or set(parameters) != set(rules):
        raise ValueError(f'节点 {node_id}: 参数名称不完整或未知')
    for key, rule in rules.items():
        value=parameters[key];label=f'节点 {node_id}.{key}'
        if rule['type']=='integer': integer(value,label,rule.get('minimum',-10**12),rule.get('maximum',10**12))
        elif rule['type']=='number':
            if type(value) not in (float,int) or not math.isfinite(value): raise ValueError(label+': 需要有限数值')
            if not rule.get('minimum',-math.inf)<=value<=rule.get('maximum',math.inf): raise ValueError(label+': 超出范围')
        elif not isinstance(value,str) or not value.strip(): raise ValueError(label+': 需要非空文字')
        if 'options' in rule and value not in rule['options']: raise ValueError(label+': 不支持的策略')


def default_graph(scene):
    nodes=[];edges=[];lookup={}
    groups=[(b['id'],BS_KINDS) for b in scene['bs']]+[('global',('channel','metrics'))]+[(u['id'],UE_KINDS) for u in scene['ues']]
    for row,(owner,kinds) in enumerate(groups):
        for col,kind in enumerate(kinds):
            params={k:deepcopy(v['default']) for k,v in REGISTRY[kind]['parameters'].items()}
            if kind=='power': params['power_dbm']=next(b['power_dbm'] for b in scene['bs'] if b['id']==owner)
            if kind=='feedback':
                ue=next(u for u in scene['ues'] if u['id']==owner)
                params['serving_bs']=min(scene['bs'],key=lambda b:sum((a-c)**2 for a,c in zip(b['position_m'],ue['position_m'])))['id']
            node={'id':f'{owner}/{kind}','kind':kind,'entity_id':owner,'parameters':params,'position':[40+col*200,50+row*135]}
            nodes.append(node);lookup[(owner,kind)]=node['id']
    def edge(a,port,b,target):
        edges.append({'source':lookup[a],'source_port':port,'target':lookup[b],'target_port':target})
    for b in scene['bs']:
        i=b['id']
        for a,port,z,target in [('power','radio','adaptation','radio'),('queue','queue','scheduler','queue'),
             ('csi_store','csi','scheduler','csi'),('harq','state','scheduler','harq'),('scheduler','grant','adaptation','grant')]:
            edge((i,a),port,(i,z),target)
        edge((i,'adaptation'),'tx',('global','channel'),'tx')
    for u in scene['ues']:
        i=u['id'];b=next(n for n in nodes if n['id']==lookup[(i,'feedback')])['parameters']['serving_bs']
        for a,p,z,q in [('measure','observation','estimate','observation'),('estimate','observation','report','observation'),
                         ('report','report','feedback','report'),('decode','ack','feedback','ack')]: edge((i,a),p,(i,z),q)
        edge(('global','channel'),'observation',(i,'measure'),'channel')
        edge(('global','channel'),'observation',(i,'decode'),'channel')
        edge((i,'traffic'),'traffic',(b,'queue'),'traffic')
        edge((i,'feedback'),'report',(b,'csi_store'),'report')
        edge((i,'feedback'),'ack',(b,'harq'),'ack')
        edge((i,'decode'),'delivered',('global','metrics'),'delivered')
        edge((i,'mobility'),'motion',('global','channel'),'motion')
    return {'nodes':nodes,'edges':edges}


def compile_graph(experiment):
    scene=experiment['scene'];graph=experiment['graph']
    if not isinstance(graph,dict) or not isinstance(graph.get('nodes'),list) or not isinstance(graph.get('edges'),list): raise ValueError('graph 需要 nodes / edges')
    if len(graph['nodes'])>1000 or len(graph['edges'])>3000: raise ValueError('策略图超过节点 / 连线预算')
    owners=[(b['id'],'bs') for b in scene['bs']]+[(u['id'],'ues') for u in scene['ues']]+[('global','global')]
    nodes={};bykind={}
    for n in graph['nodes']:
        if not isinstance(n,dict) or not isinstance(n.get('id'),str) or not n['id'] or n['id'] in nodes: raise ValueError('节点 ID 无效或重复')
        if set(n)!={'id','kind','entity_id','parameters','position'}:raise ValueError('节点字段缺失或未知')
        kind=n.get('kind');owner=n.get('entity_id');ident=n['id']
        if kind not in REGISTRY or (owner,REGISTRY[kind]['owner']) not in owners: raise ValueError(f'节点 {ident}: 类型与实体不兼容')
        if (owner,kind) in bykind: raise ValueError(f'节点 {ident}: 同一实体功能重复')
        position=n.get('position')
        if not isinstance(position,list) or len(position)!=2 or any(type(x) not in (int,float) or not math.isfinite(x) for x in position): raise ValueError(f'节点 {ident}: 位置无效')
        validate_parameters(kind,n.get('parameters'),ident)
        nodes[ident]=n;bykind[owner,kind]=n
    for owner,group in owners:
        for kind in BS_KINDS if group=='bs' else UE_KINDS if group=='ues' else ('channel','metrics'):
            if (owner,kind) not in bykind: raise ValueError(f'实体 {owner}: 缺少 {kind} 节点')
    for u in scene['ues']:
        f=bykind[u['id'],'feedback'];p=f['parameters']
        if p['serving_bs'] not in {b['id'] for b in scene['bs']}: raise ValueError(f"节点 {f['id']}: 服务 BS 不存在")
        if bykind[u['id'],'measure']['parameters']['offset_slots']>=bykind[u['id'],'measure']['parameters']['period_slots']: raise ValueError('测量偏移必须小于周期')
        bid=p['serving_bs'];tp=bykind[bid,'power']['parameters']['tx_ports'];ap=bykind[bid,'adaptation']['parameters'];rp=bykind[u['id'],'report']['parameters']
        rx=bykind[u['id'],'decode']['parameters']['rx_ports']
        if bykind[u['id'],'traffic']['parameters']['mode']=='full_buffer' and bykind[u['id'],'traffic']['parameters']['packet_bytes']>bykind[bid,'queue']['parameters']['limit_bytes']:raise ValueError('满缓存包大小超过服务 BS 缓存上限')
        if ap['fixed_rank']>min(tp,rx,ap['max_layers']):raise ValueError('固定 / 回退 RI 超过服务链路端口或空间层数')
        if ap['fixed_pmi']>(0 if tp==1 else 3 if ap['fixed_rank']==1 else 1):raise ValueError('固定 / 回退 PMI 不属于所选 RI 的码本')
        if tp==2 and not (rp['codebook_subset']&15 or (min(rx,rp['rank_limit'],ap['max_layers'])>=2 and rp['codebook_subset']&48)):raise ValueError('UE 码本子集没有合法的 RI/PMI 候选')
    incoming={};adj={k:[] for k in nodes}
    for e in graph['edges']:
        if not isinstance(e,dict) or set(e)!={'source','source_port','target','target_port'}:raise ValueError('连线需要明确的源 / 目标节点和端口')
        a=nodes.get(e.get('source'));b=nodes.get(e.get('target'))
        if a is None or b is None or a is b: raise ValueError('连线引用无效节点或自身环')
        out=REGISTRY[a['kind']]['outputs'].get(e.get('source_port'))
        inp=REGISTRY[b['kind']]['inputs'].get(e.get('target_port'))
        if not out or not inp or out!=inp.rstrip('*'): raise ValueError(f"连线 {a['id']} → {b['id']}: 端口类型不兼容")
        if a['kind'] not in ROUTES.get((b['kind'],e['target_port']),set()):raise ValueError(f"连线 {a['id']} → {b['id']}: 内核不支持此信息通路")
        ao,bo=a['entity_id'],b['entity_id']
        ag,bg=REGISTRY[a['kind']]['owner'],REGISTRY[b['kind']]['owner']
        if ag!='global' and bg!='global' and (ao,ag)!=(bo,bg):
            if ag!='ues' or bg!='bs' or bykind[ao,'feedback']['parameters']['serving_bs']!=bo: raise ValueError('禁止跨 BS / 非服务 UE 的私有状态连线')
        key=(b['id'],e['target_port']);previous=incoming.setdefault(key,[])
        if previous and not inp.endswith('*'): raise ValueError(f'节点 {b["id"]}: 输入端口重复连接')
        if any(x['source']==e['source'] and x['source_port']==e['source_port'] for x in previous): raise ValueError('重复连线')
        previous.append(e)
        if a['kind']!='feedback': adj[a['id']].append(b['id'])
    for n in nodes.values():
        for port,port_type in REGISTRY[n['kind']]['inputs'].items():
            if not incoming.get((n['id'],port)) and not port_type.endswith('*'): raise ValueError(f"节点 {n['id']}: 缺少输入 {port}")
    # Shared sinks require every relevant device exactly once, not merely one
    # incoming wire of the right type. Otherwise a disconnected UE could still
    # silently run through the kernel's device loop.
    for u in scene['ues']:
        uid=u['id'];bid=bykind[uid,'feedback']['parameters']['serving_bs']
        for source_kind,target_owner,target_kind,port in [('traffic',bid,'queue','traffic'),('feedback',bid,'csi_store','report'),('feedback',bid,'harq','ack'),('decode','global','metrics','delivered'),('mobility','global','channel','motion')]:
            target=bykind[target_owner,target_kind]['id'];source=bykind[uid,source_kind]['id']
            if not any(e['source']==source for e in incoming.get((target,port),[])):raise ValueError(f'节点 {source}: 缺少到 {target}.{port} 的连接')
    channel_id=bykind['global','channel']['id']
    for b in scene['bs']:
        source=bykind[b['id'],'adaptation']['id']
        if not any(e['source']==source for e in incoming.get((channel_id,'tx'),[])):raise ValueError(f'节点 {source}: 缺少发送信道连接')
    seen=set();active=set()
    def visit(i):
        if i in active: raise ValueError('存在无延迟的反馈环')
        if i in seen:return
        active.add(i)
        for j in adj[i]:visit(j)
        active.remove(i);seen.add(i)
    for i in nodes:visit(i)
    compiled={'bs':{},'ues':{},'global':{},'node_ids':{}}
    for owner,group in owners:
        compiled[group][owner]={kind:deepcopy(n['parameters']) for (o,kind),n in bykind.items() if o==owner and REGISTRY[kind]['owner']==group}
    for u in scene['ues']:
        i=u['id'];report=bykind[i,'report']
        source=nodes[incoming[report['id'],'observation'][0]['source']]
        if source['kind'] not in ('measure','estimate'): raise ValueError('CSI report 必须连接本 UE 的测量 / 估计')
        compiled['ues'][i]['use_estimator']=source['kind']=='estimate'
        bid=bykind[i,'feedback']['parameters']['serving_bs']
        reportbits=4+(2*bykind[i,'report']['parameters']['subbands'] if bykind[i,'report']['parameters']['mode']=='subband' else 0)+bykind[i,'feedback']['parameters']['header_bits']+(3 if bykind[bid,'power']['parameters']['tx_ports']==2 else 0)
        if reportbits>experiment['settings']['feedback_bits_per_slot']: raise ValueError(f'UE {i}: 单条报告超过共享反馈预算')
    return compiled


def required_channel_links(scene, policies):
    """Serving plus all cochannel reference/interference links, even idle BSs."""
    stations={b['id']:b for b in scene['bs']}
    return {(b['id'],u['id']) for u in scene['ues'] for b in scene['bs']
            if b['channel']==stations[policies['ues'][u['id']]['feedback']['serving_bs']]['channel']}
