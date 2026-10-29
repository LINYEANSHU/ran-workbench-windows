from copy import deepcopy
import numpy as np
import pytest


class FixedChannel:
    metadata={'backend':'test_fixture','calibrated':False}
    def __init__(self,exp,gain=1e-5):self.exp,self.gain=exp,gain
    def snapshot(self,slot):
        return {b['id']:{u['id']:np.full((self.exp['settings']['n_prb'],1,1),self.gain,complex)
                         for u in self.exp['scene']['ues']} for b in self.exp['scene']['bs']}


def setup_exp(ues=1):
    from ran_workbench.model import default_project
    from ran_workbench.system.schema import default_experiment
    scene=default_project();scene['buildings']=[]
    scene['ues']=[dict(id=f'UE-{i+1:02}',name=str(i),position_m=[10+i,0,1.5]) for i in range(ues)]
    exp=default_experiment(scene);exp['settings'].update(slots=30,decoder='ideal')
    return exp


def node(exp,kind,owner=None):
    return next(n for n in exp['graph']['nodes'] if n['kind']==kind and (owner is None or n['entity_id']==owner))['parameters']


def run(exp,provider=None):
    from ran_workbench.system.simulator import simulate
    return simulate(exp,channel_provider=provider or FixedChannel(exp))


def test_feedback_and_ack_are_causal_and_packets_conserved():
    exp=setup_exp();result=run(exp)
    rows=result['trace']
    first=next(r for r in rows if r['type']=='tx')
    assert first['csi_measured_slot'] is None
    first_csi=next(r for r in rows if r['type']=='csi_arrival')
    assert first_csi['slot']>=first_csi['measured_slot']+2
    assert all(r['csi_measured_slot'] is None or r['csi_measured_slot']<r['slot'] for r in rows if r['type']=='tx')
    assert all(r['slot']>r['tx_slot'] for r in rows if r['type']=='ack_arrival')
    ue=result['ues'][0]
    assert ue['generated_packets']==ue['delivered_packets']+ue['dropped_packets']+ue['pending_packets']
    assert ue['delivered_bytes']==ue['delivered_packets']*200
    assert ue['goodput_mbps']>0


def test_rr_shares_resources_and_never_overallocates():
    exp=setup_exp(3);result=run(exp)
    allocated=[u['allocated_prb_slots'] for u in result['ues']]
    assert max(allocated)-min(allocated)<=12
    for slot in range(30):
        grants=[r['prbs'] for r in result['trace'] if r['type']=='tx' and r['slot']==slot]
        all_prbs=[p for group in grants for p in group]
        assert len(all_prbs)==len(set(all_prbs))<=12


def test_reference_outage_creates_nacks_and_bounded_retries():
    exp=setup_exp();exp['settings']['decoder']='reference'
    node(exp,'harq')['max_attempts']=2
    result=run(exp,FixedChannel(exp,0.))
    assert result['ues'][0]['goodput_mbps']==0
    assert result['ues'][0]['retransmissions']>0
    assert result['ues'][0]['dropped_packets']>0
    assert max(t['attempt'] for t in result['trace'] if t['type']=='tx')<=2


def test_shared_feedback_budget_is_per_bs_and_csi_can_expire():
    exp=setup_exp(3);exp['settings']['feedback_bits_per_slot']=4
    for n in exp['graph']['nodes']:
        if n['kind']=='measure':n['parameters']['period_slots']=1
        if n['kind']=='feedback':n['parameters']['max_wait_slots']=1
    result=run(exp)
    for slot in range(30):
        assert sum(r['bits'] for r in result['trace'] if r['type']=='feedback_sent' and r['slot']==slot)<=4
    assert any(r['type']=='feedback_drop' for r in result['trace'])


def test_reproducible_seed_and_entity_order():
    exp=setup_exp(2)
    a=run(exp);other=deepcopy(exp);other['scene']['ues'].reverse();other['graph']['nodes'].reverse();other['graph']['edges'].reverse()
    b=run(other)
    assert a['ues']==b['ues']
    assert a['trace']==b['trace']


def test_real_channel_provider_failure_is_not_replaced():
    exp=setup_exp()
    class Broken(FixedChannel):
        def snapshot(self,slot):raise RuntimeError('CUDA device lost')
    with pytest.raises(RuntimeError,match='CUDA'):run(exp,Broken(exp))


def test_zero_csi_loss_vs_complete_loss_changes_bs_visible_state():
    exp=setup_exp();a=run(exp)
    node(exp,'feedback')['loss_rate']=1.
    b=run(exp)
    assert any(t['type']=='csi_arrival' for t in a['trace'])
    assert not any(t['type']=='csi_arrival' for t in b['trace'])


def test_oversized_packets_are_fragmented_without_duplicate_delivery():
    exp=setup_exp();node(exp,'traffic')['packet_bytes']=1000;exp['settings']['slots']=100
    result=run(exp);ue=result['ues'][0]
    assert ue['delivered_packets']>0
    assert ue['delivered_bytes']==ue['delivered_packets']*1000
    assert all(t['tb_bits']<2000 for t in result['trace'] if t['type']=='tx')


def test_pf_window_one_handles_zero_delivered_average():
    exp=setup_exp(2);node(exp,'scheduler')['algorithm']='proportional_fair';node(exp,'scheduler')['pf_window']=1
    for n in exp['graph']['nodes']:
        if n['kind']=='traffic':n['parameters']['packet_bytes']=1000
    result=run(exp)
    assert all(u['transmissions']>0 for u in result['ues'])


def test_bs_buffer_limit_is_shared_across_ues():
    exp=setup_exp(3);node(exp,'queue')['limit_bytes']=1000
    result=run(exp)
    assert all(sum(u['queue_bytes'] for u in point['ues'])<=1000 for point in result['timeline'])


def test_pf_cannot_see_decode_success_until_ack_arrives():
    exp=setup_exp(2);exp['settings'].update(slots=8,decoder='reference')
    node(exp,'scheduler').update(algorithm='proportional_fair',pf_window=1)
    node(exp,'adaptation').update(mode='fixed',fixed_mcs=3,max_mcs=3)
    node(exp,'harq').update(k1_slots=10,processes=16)
    for n in exp['graph']['nodes']:
        if n['kind']=='feedback':n['parameters']['delay_slots']=100
        if n['kind']=='traffic':n['parameters']['packet_bytes']=16
    class Variant(FixedChannel):
        def snapshot(self,slot):
            h=super().snapshot(slot)
            h['BS-01']['UE-02']*=0
            return h
    a=run(exp);b=run(exp,Variant(exp))
    def plans(r):return [(t['slot'],t['ue_id'],t['prbs'],t['mcs']) for t in r['trace'] if t['type']=='tx']
    assert plans(a)==plans(b)
    assert not any(t['type']=='ack_arrival' for t in a['trace'])


def test_unoccupied_bs_is_valid_and_still_has_reference_interference():
    from ran_workbench.system.schema import default_experiment
    exp=setup_exp();scene=deepcopy(exp['scene']);base=deepcopy(scene['bs'][0])
    scene['bs'].append(dict(base,id='BS-02',name='empty cell',position_m=[1000,0,10]))
    exp=default_experiment(scene);exp['settings'].update(slots=5,decoder='ideal')
    result=run(exp)
    empty=next(b for b in result['bs'] if b['bs_id']=='BS-02')
    assert empty['allocated_prb_slots']==0
    assert empty['pilot_re']>0
