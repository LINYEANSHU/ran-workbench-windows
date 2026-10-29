import numpy as np
import pytest


def test_two_port_type_i_known_vectors_and_total_power():
    from ran_workbench.system.mimo import precoder
    assert np.allclose(precoder(2,1,1),np.array([[1],[1j]])/np.sqrt(2))
    assert np.allclose(precoder(2,2,0),np.array([[1,1],[1,-1]])/2)
    assert np.allclose(precoder(2,2,1),np.array([[1,1],[1j,-1j]])/2)
    for ri in (1,2):
        for pmi in range(4 if ri==1 else 2):assert np.linalg.norm(precoder(2,ri,pmi))**2==pytest.approx(1.)
    with pytest.raises(ValueError):precoder(4,1,0)
    with pytest.raises(ValueError):precoder(2,2,3)


def test_mmse_uses_power_once_and_keeps_stream_interference():
    from ran_workbench.system.mimo import precoder,post_mmse_sinr
    h=np.eye(2)[None,...];r=np.eye(2)[None,...]
    assert np.allclose(post_mmse_sinr(h,precoder(2,2,0),2,r),[[1,1]])
    assert np.allclose(post_mmse_sinr(h,precoder(2,1,0),2,r),[[2]])


def test_rank_and_pmi_selection_follow_actual_channel_matrix():
    from ran_workbench.system.mimo import select_csi
    from ran_workbench.system.link import LinkModel
    model=LinkModel();r=np.eye(2)[None,...]
    full=select_csi(100*np.eye(2)[None,...],r,1,2,63,model)
    collapsed=select_csi(100*np.ones((1,2,2)),r,1,2,63,model)
    assert full['ri']==2
    assert collapsed['ri']==1
    pmi=select_csi(np.array([[[100,-100j]]]),np.ones((1,1,1)),1,1,63,model)
    assert pmi['pmi']==1
    restricted=select_csi(np.array([[[100,-100j]]]),np.ones((1,1,1)),1,1,1,model)
    assert restricted['pmi']==0


def test_matrix_channel_csi_reaches_bs_before_precoding_changes():
    from ran_workbench.system.schema import default_experiment
    from ran_workbench.system.simulator import simulate
    exp=default_experiment();exp['settings'].update(slots=12,decoder='ideal')
    for n in exp['graph']['nodes']:
        if n['kind']=='power':n['parameters']['tx_ports']=2
        if n['kind']=='decode':n['parameters']['rx_ports']=2
    class Channel:
        metadata={'backend':'test_matrix'}
        def snapshot(self,slot):return {'BS-01':{'UE-01':np.tile(np.eye(2,dtype=complex)*1e-4,(12,1,1))}}
    result=simulate(exp,channel_provider=Channel())
    tx=[e for e in result['trace'] if e['type']=='tx']
    assert tx[0]['ri']==1 and tx[0]['csi_measured_slot'] is None
    assert any(e['ri']==2 and e['csi_measured_slot'] is not None for e in tx)
    reports=[e for e in result['trace'] if e['type']=='csi_generated']
    assert reports[0]['bits']>4
