from copy import deepcopy
import numpy as np
import pytest


def test_cir_response_keeps_all_paths_and_carrier_phase_once():
    from ran_workbench.system.channel import cir_response
    beta=np.ones((1,1,60),complex);beta[0,0,-1]=1j
    tau=np.zeros(60)
    h=cir_response(beta,tau,np.ones(60,bool),np.array([0.,1e6]))
    assert h.shape==(2,1,1)
    assert h[0,0,0]==59+1j
    h=cir_response(np.array([[[1,1]]]),np.array([0,1e-6]),np.array([True,True]),np.array([0,500000]))
    assert h[0,0,0]==pytest.approx(2)
    assert abs(h[1,0,0])<1e-12


def test_empty_or_invalid_cir_is_not_invented_zero_channel():
    from ran_workbench.system.channel import cir_response
    with pytest.raises(ValueError,match='有效路径'):cir_response(np.empty((1,1,0)),np.array([]),np.array([],bool),[0])
    with pytest.raises(ValueError):cir_response(np.ones((1,1,1)),np.array([float('nan')]),[True],[0])


def test_rt_snapshot_cache_and_mobility_positions(monkeypatch):
    from ran_workbench.system.schema import default_experiment
    from ran_workbench.system.channel import RTChannel
    exp=default_experiment();calls=[]
    channel=RTChannel(exp)
    def solve(scene,slot):
        calls.append((slot,deepcopy(scene)))
        return {'BS-01':{'UE-01':np.ones((12,1,1),complex)}}
    monkeypatch.setattr(channel,'_solve',solve)
    a=channel.snapshot(0);b=channel.snapshot(21)
    assert a is b and len(calls)==1
    n=next(n for n in exp['graph']['nodes'] if n['kind']=='mobility');n['parameters']['vx']=1.
    moving=RTChannel(exp);monkeypatch.setattr(moving,'_solve',solve)
    moving.snapshot(0);moving.snapshot(19);moving.snapshot(20)
    assert calls[-1][0]==20
    assert calls[-1][1]['ues'][0]['position_m'][0]==pytest.approx(.01)


def test_system_worker_protocol_produces_bound_experiment_result(tmp_path):
    import json,subprocess,sys,os
    from ran_workbench.system.schema import default_experiment,experiment_fingerprint
    exp=default_experiment();exp['settings'].update(slots=5,channel_backend='reference_fixture')
    request=tmp_path/'input.json';output=tmp_path/'output.json'
    request.write_text(json.dumps({'mode':'system','project':exp}),encoding='utf-8')
    p=subprocess.run([sys.executable,'-m','ran_workbench.worker','--input',str(request),'--output',str(output)],capture_output=True,text=True,encoding='utf-8',env={**os.environ,'PYTHONUTF8':'1'})
    assert p.returncode==0,p.stderr
    result=json.loads(output.read_text(encoding='utf-8'))
    assert result['experiment_sha256']==experiment_fingerprint(exp)
    assert result['backend']['backend']=='reference_fixture'
    assert all(json.loads(line)['type'] in ('progress','done') for line in p.stdout.splitlines())
