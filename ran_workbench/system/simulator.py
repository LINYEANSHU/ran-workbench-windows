"""Synchronous NR slot loop. Policies see only delivered feedback, never H(t).

Inter-cell plans are frozen together. Link-level waveforms/RLC/RRC are not
implemented here. Resource and packet accounting remain actual state, not plots.
"""
from collections import defaultdict
from copy import deepcopy
import hashlib
import math
import time
import numpy as np
from ..model import project_fingerprint
from .schema import validate_experiment, experiment_fingerprint
from .graph import compile_graph, required_channel_links
from .nr import slot_duration_s, transport_block_size, code_block_layout, cqi, mcs
from .link import LinkModel
from .traffic import Traffic
from .mimo import precoder,post_mmse_sinr,select_csi
from . import ENGINE_VERSION


def rng(seed,*key):
    value=hashlib.sha256(('/'.join(map(str,(seed,)+key))).encode()).digest()
    return np.random.default_rng(int.from_bytes(value[:8],'little'))


def simulate(experiment,progress=None,channel_provider=None):
    started=time.monotonic();exp=validate_experiment(experiment);pol=compile_graph(exp)
    scene=exp['scene'];s=exp['settings'];dt=slot_duration_s(s['mu']);model=LinkModel()
    bs=sorted(scene['bs'],key=lambda x:x['id']);ues=sorted(scene['ues'],key=lambda x:x['id'])
    if channel_provider is None:
        from .channel import RTChannel, ReferenceFixture
        channel_provider=RTChannel(exp,progress) if s['channel_backend']=='rt' else ReferenceFixture(exp)
    meta=deepcopy(getattr(channel_provider,'metadata',{'backend':'external-test-provider','calibrated':False}))
    trace=[];timeline=[];omitted=0;feedback_queues={b['id']:[] for b in bs};arrivals=defaultdict(list)
    sequence=0;stats={};traffic={};processes={};csi_store={};last_report={};pf={}
    blookup={b['id']:b for b in bs};tb_id=0;any_extrapolation=False
    power={b['id']:10**((pol['bs'][b['id']]['power']['power_dbm']-30)/10)/s['n_prb'] for b in bs}
    serving={u['id']:pol['ues'][u['id']]['feedback']['serving_bs'] for u in ues}
    required=required_channel_links(scene,pol)
    noise={u['id']:1.380649e-23*scene['network']['temperature_k']*12*15000*(2**s['mu'])*10**(pol['ues'][u['id']]['decode']['noise_figure_db']/10) for u in ues}
    for u in ues:
        uid=u['id'];bp=pol['bs'][serving[uid]]
        traffic[uid]=Traffic(pol['ues'][uid]['traffic'],bp['queue']['limit_bytes'],dt)
        stats[uid]=dict(ue_id=uid,bs_id=serving[uid],allocated_prb_slots=0,air_bits=0,retransmissions=0,
                        transmissions=0,failed_blocks=0,first_transmissions=0,first_failed_blocks=0,
                        feedback_bits=0,feedback_dropped=0,csi_age_sum=0,csi_used=0)
        processes[uid]={};pf[uid]=1.
    def emit(slot,kind,**data):
        nonlocal omitted
        if slot%s['trace_stride']==0:
            if len(trace)<s['trace_limit']:trace.append(dict(slot=slot,type=kind,**data))
            else:omitted+=1
    def enqueue(slot,uid,kind,payload,bits):
        nonlocal sequence
        sequence+=1
        feedback_queues[serving[uid]].append(dict(kind=kind,uid=uid,born=slot,payload=payload,bits=bits,sequence=sequence))
    def finish(uid,tb,drop=False):
        if drop and not tb['decoded']:traffic[uid].drop(tb['spans'])
        processes[uid].pop(tb['id'],None)
    max_prbs={}
    for idx in model.supported_mcs:
        for rank in (1,2):
            valid=[n for n in range(1,s['n_prb']+1) if code_block_layout(transport_block_size(n,s['pdsch_symbols'],s['dmrs_re'],s['overhead_re'],idx,rank),idx)['cb_bits']<=2000]
            max_prbs[idx,rank]=max(valid,default=0)
    for slot in range(s['slots']):
        # 1: only events whose end-to-end delay has elapsed become visible.
        acknowledged_slot=defaultdict(int)
        for event in sorted(arrivals.pop(slot,[]),key=lambda x:x['sequence']):
            uid=event['uid'];msg=event['payload']
            if event['kind']=='csi':
                if slot-msg['measured_slot']<=pol['bs'][serving[uid]]['csi_store']['max_age_slots']:
                    csi_store[uid]=msg;emit(slot,'csi_arrival',ue_id=uid,measured_slot=msg['measured_slot'],cqi=msg['cqi'],mcs=msg['mcs'],ri=msg['ri'],pmi=msg['pmi'])
                else:
                    stats[uid]['feedback_dropped']+=1;emit(slot,'feedback_drop',ue_id=uid,reason='stale_on_arrival')
            else:
                tb=processes[uid].get(msg['tb_id'])
                emit(slot,'ack_arrival',ue_id=uid,tx_slot=msg['tx_slot'],ack=msg['ack'],tb_id=msg['tb_id'])
                if tb is None:continue
                if msg['ack']:
                    acknowledged_slot[uid]+=sum(length for _,_,length in tb['spans'])
                    finish(uid,tb)
                elif msg['attempt']==tb['attempt']:
                    if tb['attempt']>=pol['bs'][serving[uid]]['harq']['max_attempts']:finish(uid,tb,True)
                    else:tb['ready']=True
        arrival_order=ues[slot%len(ues):]+ues[:slot%len(ues)]
        for u in arrival_order:
            uid=u['id'];bid=serving[uid]
            traffic[uid].limit=max(0,pol['bs'][bid]['queue']['limit_bytes']-sum(t.buffer_bytes for peer,t in traffic.items() if peer!=uid and serving[peer]==bid))
            traffic[uid].arrive(slot)
        for u in ues:
            uid=u['id']
            hp=pol['bs'][serving[uid]]['harq']
            for tb in list(processes[uid].values()):
                if not tb['ready'] and slot-tb['last_tx']>=hp['timeout_slots']:
                    if tb['attempt']>=hp['max_attempts']:finish(uid,tb,True)
                    else:tb['ready']=True
                    emit(slot,'harq_timeout',ue_id=uid,tb_id=tb['id'])
        # 2: channel truth stays in the receiver/physical side of the loop.
        h=channel_provider.snapshot(slot)
        for b in bs:
            for u in ues:
                if (b['id'],u['id']) not in required:continue
                if b['id'] not in h or u['id'] not in h[b['id']]:raise ValueError(f"{b['id']} → {u['id']}: 必要信道未解析")
                values=np.asarray(h[b['id']][u['id']])
                expected=(s['n_prb'],pol['ues'][u['id']]['decode']['rx_ports'],pol['bs'][b['id']]['power']['tx_ports'])
                if values.shape!=expected or not np.isfinite(values).all():
                    raise ValueError(f'系统信道需要有限 H{expected}，收到 {values.shape}')
        def covariance(bid,uid,prbs,plans=None):
            values=[];nr=pol['ues'][uid]['decode']['rx_ports']
            for p in prbs:
                r=noise[uid]*np.eye(nr,dtype=complex)
                for other in bs:
                    oid=other['id']
                    if oid==bid or other['channel']!=blookup[bid]['channel']:continue
                    nt=pol['bs'][oid]['power']['tx_ports']
                    if plans is None:w=np.eye(nt,dtype=complex)/np.sqrt(nt)
                    else:
                        row=next((row for row in plans.get(oid,[]) if p in row['prbs']),None)
                        if row is None:continue
                        w=precoder(nt,row['tb']['ri'],row['tb']['pmi'])
                    g=h[oid][uid][p]@w;r+=power[oid]*(g@g.conj().T)
                values.append(r)
            return np.asarray(values)
        def sinrs(bid,uid,prbs,plans=None,rank=1,pmi=0):
            return post_mmse_sinr(h[bid][uid][prbs],precoder(pol['bs'][bid]['power']['tx_ports'],rank,pmi),power[bid],covariance(bid,uid,prbs,plans))
        # 3: simultaneous scheduling, using stored reports and own queues only.
        plans={b['id']:[] for b in bs}
        for b in bs:
            bid=b['id'];bp=pol['bs'][bid];members=[u['id'] for u in ues if serving[u['id']]==bid]
            remaining=list(range(s['n_prb']));assigned={};estimates={}
            for uid in members:
                retry=next((tb for tb in processes[uid].values() if tb['ready']),None)
                if retry and len(retry['prbs'])<=len(remaining):
                    prbs=remaining[:len(retry['prbs'])];del remaining[:len(prbs)]
                    plans[bid].append(dict(uid=uid,prbs=prbs,tb=retry,csi=retry['csi']))
                elif not retry and len(processes[uid])<bp['harq']['processes'] and traffic[uid].has_data:
                    report=csi_store.get(uid)
                    if report and slot-report['measured_slot']>bp['csi_store']['max_age_slots']:report=None
                    idx=bp['adaptation']['fixed_mcs'] if bp['adaptation']['mode']=='fixed' or report is None else report['mcs']
                    idx=max(3,min(bp['adaptation']['max_mcs'],idx-bp['adaptation']['backoff']))
                    use_report=report is not None and bp['adaptation']['precoding']=='reported'
                    rank=report['ri'] if use_report else bp['adaptation']['fixed_rank']
                    pmi=(report.get('pmi') or 0) if use_report else bp['adaptation']['fixed_pmi']
                    estimates[uid]=(idx,report,rank,pmi);assigned[uid]=[]
            alg=bp['scheduler']['algorithm'];rr=slot%max(1,len(members))
            for p in remaining:
                candidates=[uid for uid in assigned if len(assigned[uid])<max_prbs[estimates[uid][0],estimates[uid][2]]]
                if not candidates:break
                def quality(uid):
                    idx,report,rank,pmi=estimates[uid]
                    if report and 'subband_cqi' in report:
                        q=report['subband_cqi'][min(len(report['subband_cqi'])-1,p*len(report['subband_cqi'])//s['n_prb'])]
                        eff=cqi(q)['qm']*cqi(q)['rate_x1024']/1024
                    else:eff=mcs(idx)['qm']*mcs(idx)['rate_x1024']/1024
                    return eff*rank
                if alg=='round_robin':
                    ordered=members[rr:]+members[:rr]
                    uid=next(x for x in ordered if x in candidates);rr=(members.index(uid)+1)%len(members)
                else:
                    uid=max(candidates,key=lambda x:(quality(x)/(max(pf[x],1e-9) if alg=='proportional_fair' else 1.)/(1+len(assigned[x])),x))
                assigned[uid].append(p)
            for uid,prbs in sorted(assigned.items()):
                if not prbs:continue
                idx,report,rank,pmi=estimates[uid]
                bits=transport_block_size(len(prbs),s['pdsch_symbols'],s['dmrs_re'],s['overhead_re'],idx,rank)
                # 3-byte MAC subheader is a declared fixed abstraction per TB.
                spans=traffic[uid].allocate(max(0,bits//8-3))
                if not spans:continue
                tb_id+=1
                free=next(pid for pid in range(bp['harq']['processes']) if pid not in {x['process_id'] for x in processes[uid].values()})
                tb=dict(id=tb_id,process_id=free,spans=spans,mcs=idx,bits=bits,prbs=prbs,attempt=0,decoded=False,ready=True,last_tx=slot,ri=rank,pmi=pmi,csi=report)
                processes[uid][tb_id]=tb;plans[bid].append(dict(uid=uid,prbs=prbs,tb=tb,csi=report))
        delivered_slot=defaultdict(int)
        # 4: all plans frozen. Evaluate real overlap, then receiver decoding.
        for b in bs:
            bid=b['id']
            for plan in plans[bid]:
                uid=plan['uid'];tb=plan['tb'];report=plan['csi'];row=stats[uid]
                tb['attempt']+=1;tb['last_tx']=slot;tb['ready']=False
                values=sinrs(bid,uid,plan['prbs'],plans,tb['ri'],tb['pmi'])
                performance=model.tbler(values,tb['mcs'],tb['bits']);any_extrapolation|=performance['extrapolated']
                success=s['decoder']=='ideal' or rng(s['seed'],'decode',uid,slot).random()>=performance['tbler']
                row['transmissions']+=1;row['air_bits']+=tb['bits'];row['allocated_prb_slots']+=len(plan['prbs'])
                if tb['attempt']==1:
                    row['first_transmissions']+=1;row['first_failed_blocks']+=not success
                else:row['retransmissions']+=1
                row['failed_blocks']+=not success
                if success and not tb['decoded']:
                    delivered_slot[uid]+=traffic[uid].receive(tb['spans'],slot);tb['decoded']=True
                measured=report['measured_slot'] if report else None
                if measured is not None:row['csi_age_sum']+=slot-measured;row['csi_used']+=1
                emit(slot,'tx',bs_id=bid,ue_id=uid,prbs=plan['prbs'],mcs=tb['mcs'],tb_bits=tb['bits'],tb_id=tb['id'],
                     process_id=tb['process_id'],attempt=tb['attempt'],ack=bool(success),sinr_db=performance['sinr_db'],
                     tbler=performance['tbler'],csi_measured_slot=measured,cbs=performance['cb_bits'],ri=tb['ri'],pmi=tb['pmi'] if pol['bs'][bid]['power']['tx_ports']==2 else None,
                     curve_extrapolated=performance['extrapolated'],power_w=power[bid]*len(plan['prbs']),
                     packet_spans=[dict(packet_id=p.id,offset=o,length=n) for p,o,n in tb['spans']])
                enqueue(slot,uid,'ack',dict(tb_id=tb['id'],ack=bool(success),attempt=tb['attempt'],tx_slot=slot),1)
        # 5: reference measurements are visible at UE, not at BS until delivery.
        for u in ues:
            uid=u['id'];up=pol['ues'][uid];mp=up['measure'];rp=up['report'];fp=up['feedback']
            window=pol['bs'][serving[uid]]['scheduler']['pf_window']
            pf[uid]=(1-1/window)*pf[uid]+acknowledged_slot[uid]*8/window
            if slot<mp['offset_slots'] or (slot-mp['offset_slots'])%mp['period_slots']:continue
            bid=serving[uid];observed=h[bid][uid].copy();ap=pol['bs'][bid]['adaptation']
            if up['use_estimator']:
                errors=rng(s['seed'],'estimate',uid,slot)
                observed*=10**(errors.normal(0,up['estimate']['error_std_db'],observed.shape)/20)*np.exp(1j*np.radians(errors.normal(0,up['estimate']['phase_std_deg'],observed.shape)))
            r=covariance(bid,uid,range(s['n_prb']))
            report=select_csi(observed,r,power[bid],min(rp['rank_limit'],ap['max_layers']),rp['codebook_subset'],model,
                symbols=s['pdsch_symbols'],dmrs_re=s['dmrs_re'],target=rp['target_bler'],max_mcs=ap['max_mcs'])
            bits=4+fp['header_bits']
            nt=pol['bs'][bid]['power']['tx_ports']
            if nt==2:
                bits+=(1 if min(up['decode']['rx_ports'],rp['rank_limit'],ap['max_layers'])==2 else 0)+(2 if report['ri']==1 else 1)
            if rp['mode']=='subband':
                effective=post_mmse_sinr(observed,precoder(nt,report['ri'],report['pmi'] or 0),power[bid],r)
                groups=np.array_split(effective,min(rp['subbands'],s['n_prb']))
                qualities=[model.report(g,symbols=s['pdsch_symbols'],dmrs_re=s['dmrs_re'],target=rp['target_bler'],layers=report['ri'])['cqi'] for g in groups]
                delta=[max(-1,min(2,q-report['cqi'])) for q in qualities]
                report['subband_cqi']=[max(0,min(15,report['cqi']+d)) for d in delta]
                report['subband_differential']=delta;bits+=2*len(groups)
            # Refresh using only configured timing and the UE's last generated
            # measurement. No access to the BS's received-report state is used.
            refresh=max(1,pol['bs'][bid]['csi_store']['max_age_slots']-fp['delay_slots']-fp['max_wait_slots']-mp['period_slots']+1)
            if rp['trigger']=='event' and uid in last_report and slot-last_report[uid]['measured_slot']<refresh and report['ri']==last_report[uid]['ri'] and report['pmi']==last_report[uid]['pmi'] and abs(report['cqi']-last_report[uid]['cqi'])<rp['event_delta_cqi']:continue
            report.update(measured_slot=slot);last_report[uid]=deepcopy(report)
            enqueue(slot,uid,'csi',report,bits)
            emit(slot,'csi_generated',ue_id=uid,cqi=report['cqi'],mcs=report['mcs'],bits=bits,ri=report['ri'],pmi=report['pmi'])
        # 6: each BS shares one feedback bearer; ACK priority is explicit.
        for b in bs:
            bid=b['id'];budget=s['feedback_bits_per_slot'];pending=[]
            for msg in sorted(feedback_queues[bid],key=lambda x:(x['kind']!='ack',x['born'],x['uid'],x['sequence'])):
                uid=msg['uid'];fp=pol['ues'][uid]['feedback']
                if msg['kind']=='csi' and slot-msg['born']>fp['max_wait_slots']:
                    stats[uid]['feedback_dropped']+=1;emit(slot,'feedback_drop',ue_id=uid,reason='queue_expired');continue
                # ACKs beyond HARQ timeout have no remaining semantic use.
                if msg['kind']=='ack' and msg['payload']['tb_id'] not in processes[uid]:continue
                if msg['bits']>budget:pending.append(msg);continue
                budget-=msg['bits'];stats[uid]['feedback_bits']+=msg['bits']
                emit(slot,'feedback_sent',bs_id=bid,ue_id=uid,bits=msg['bits'],message_kind=msg['kind'],queued_slots=slot-msg['born'])
                if msg['kind']=='csi' and rng(s['seed'],'csi_loss',uid,msg['born']).random()<fp['loss_rate']:
                    stats[uid]['feedback_dropped']+=1;emit(slot,'feedback_drop',ue_id=uid,reason='transport_loss');continue
                delay=pol['bs'][bid]['harq']['k1_slots'] if msg['kind']=='ack' else fp['delay_slots']
                arrivals[slot+delay].append(msg)
            feedback_queues[bid]=pending
        if slot%s['trace_stride']==0:
            timeline.append(dict(slot=slot,time_ms=(slot+1)*dt*1000,ues=[dict(ue_id=u['id'],delivered_bytes=traffic[u['id']].delivered_bytes,queue_bytes=traffic[u['id']].buffer_bytes) for u in ues]))
        if progress and (slot%10==0 or slot==s['slots']-1):
            progress(dict(stage='NR 时隙仿真',completed=slot+1,total=s['slots']))
    rows=[]
    for u in ues:
        uid=u['id'];row=stats[uid];t=traffic[uid];lat=np.asarray(t.latencies)*dt*1000
        rows.append(dict(**row,**t.summary(),goodput_mbps=t.delivered_bytes*8/(s['slots']*dt)/1e6,
                         first_bler=row['first_failed_blocks']/row['first_transmissions'] if row['first_transmissions'] else None,
                         mean_csi_age_slots=row['csi_age_sum']/row['csi_used'] if row['csi_used'] else None,
                         delay_mean_ms=float(lat.mean()) if len(lat) else None,delay_p95_ms=float(np.percentile(lat,95)) if len(lat) else None))
    return dict(schema_version=1,mode='system',engine_version=ENGINE_VERSION,scene_sha256=project_fingerprint(scene),experiment_sha256=experiment_fingerprint(exp),
                elapsed_s=time.monotonic()-started,standard=exp['standard'],slots=s['slots'],slot_duration_s=dt,
                backend=deepcopy(getattr(channel_provider,'metadata',meta)),ues=rows,trace=trace,timeline=timeline,trace_omitted=omitted,
                bs=[dict(bs_id=b['id'],allocated_prb_slots=sum(r['allocated_prb_slots'] for r in rows if r['bs_id']==b['id']),
                    available_prb_slots=s['slots']*s['n_prb'],pilot_re=s['slots']*s['n_prb']*12*s['pilot_symbols'],
                    feedback_bits=sum(r['feedback_bits'] for r in rows if r['bs_id']==b['id']),
                    harq_buffer_bytes=sum(sum(length for tb in processes[uid].values() for _,_,length in tb['spans']) for uid in processes if serving[uid]==b['id'])) for b in bs],
                assumptions=dict(duplex='logical FDD; uplink feedback budget separate from downlink',
                    receiver='linear MMSE; 1/2-port Type-I single-panel subset; PRB-flat sampling',decoder=s['decoder'],mac_header_bytes_per_tb=3,
                    harq='packet/TB state; no IR or Chase combining gain',cqi_policy='nearest same-modulation MCS reference TBLER',
                    model_calibrated=False,beta_provenance='upstream_historical_unverified',
                    curve_extrapolation_used=bool(any_extrapolation),curve_mcs_range=[3,28],curve_cb_bits_range=[24,2000],
                    event_csi_refresh='UE measurement time; configured max age minus delay, queue allowance and measurement period; at least every measurement if needed',
                    subband_report='research differential CQI; not full bit-exact NR UCI encoding'),
                reference_manifest=model.manifest)
