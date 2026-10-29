from copy import deepcopy
import pytest


class Runner:
    def start(self,project,mode):self.project=project;self.mode=mode
    def cancel(self):self.cancelled=True


def service():
    from ran_workbench.system.service import SimulationService
    return SimulationService(Runner())


def call(s,action,**data):return s.execute({'action':action,'data':data})


def test_stale_editor_revision_and_invalid_graph_cannot_erase_config():
    s=service();exp=deepcopy(s.experiment);exp['settings']['slots']=10
    assert call(s,'set',experiment=exp,expected_revision=0)['ok']
    assert not call(s,'set',experiment=exp,expected_revision=0)['ok']
    bad=deepcopy(exp);bad['graph']['edges'].pop()
    assert not call(s,'set',experiment=bad,expected_revision=1)['ok']
    assert s.experiment==exp and s.revision==1


def test_layout_preserves_result_logic_invalidates_it():
    from ran_workbench.system.schema import experiment_fingerprint
    s=service();s.result={'experiment_sha256':experiment_fingerprint(s.experiment),'ues':[]}
    exp=deepcopy(s.experiment);exp['graph']['nodes'][0]['position'][0]+=10
    assert call(s,'set',experiment=exp)['ok'] and s.result
    exp['settings']['slots']+=1
    assert call(s,'set',experiment=exp)['ok'] and s.result is None


def test_run_isolated_and_cancel_cannot_publish_completion():
    s=service();assert call(s,'run')['ok'] and s.busy
    assert s.runner.mode=='system'
    assert not call(s,'set',experiment=s.experiment)['ok']
    assert call(s,'cancel')['ok']
    s.on_complete({'experiment_sha256':'wrong'})
    assert s.result is None and s.job['status']=='cancelled'


def test_stale_result_rejected_and_file_overwrite_protected(tmp_path):
    s=service();call(s,'run');s.on_complete({'experiment_sha256':'wrong'})
    assert s.job['status']=='failed' and s.result is None
    path=tmp_path/'试验.ran-sim.json'
    assert call(s,'save',path=str(path))['ok']
    assert not call(s,'save',path=str(path))['ok']
    before=deepcopy(s.experiment)
    exp=deepcopy(before);exp['settings']['slots']=20;call(s,'set',experiment=exp)
    assert not call(s,'open',path=str(path))['ok']
    assert call(s,'open',path=str(path),discard_changes=True)['ok']
    assert s.experiment==before


def test_comparison_requires_shared_scenario():
    s=service()
    s.result={'ues':[dict(ue_id='UE-01',goodput_mbps=1,delivered_bytes=100,delay_mean_ms=2)],'backend':{'backend':'reference_fixture'}}
    assert call(s,'pin')['ok']
    s.result['ues'][0]['goodput_mbps']=2
    result=call(s,'compare')
    assert result['comparison']['ues'][0]['delta_goodput_mbps']==1
    s.experiment['settings']['seed']+=1
    assert not call(s,'compare')['ok']


def test_draft_revision_does_not_advance_with_external_update(tmp_path):
    s=service();draft=deepcopy(s.experiment);draft['settings']['slots']=10
    assert call(s,'draft',experiment=draft,base_revision=0)['ok']
    external=deepcopy(s.experiment);external['settings']['slots']=15
    assert call(s,'set',experiment=external,expected_revision=0)['ok']
    assert s.state()['draft_conflict']
    assert not call(s,'save',path=str(tmp_path/'conflict.json'))['ok']
    assert s.experiment['settings']['slots']==15
    assert call(s,'discard_draft')['ok']
    assert not s.state()['draft_dirty']


def test_save_applies_valid_gui_draft_and_refuses_invalid_draft(tmp_path):
    from ran_workbench.system.schema import load_experiment
    s=service();draft=deepcopy(s.experiment);draft['settings']['slots']=12
    assert call(s,'draft',experiment=draft,base_revision=0)['ok']
    path=tmp_path/'draft.json';assert call(s,'save',path=str(path))['ok']
    assert load_experiment(path)['settings']['slots']==12
    assert not s.state()['draft_dirty']
    draft=deepcopy(s.experiment);draft['graph']['edges'].pop()
    assert call(s,'draft',experiment=draft,base_revision=s.revision)['ok']
    assert not call(s,'save',path=str(tmp_path/'invalid.json'))['ok']
    assert s.state()['draft_dirty']


def test_save_publication_failure_preserves_applied_state_and_unapplied_draft(tmp_path,monkeypatch):
    from ran_workbench.system import service as module
    s=service();draft=deepcopy(s.experiment);draft['settings']['slots']=12
    assert call(s,'draft',experiment=draft,base_revision=0)['ok']
    before=s.state()
    def fail(*a,**k):raise OSError('disk full')
    monkeypatch.setattr(module,'publish_json',fail)
    result=call(s,'save',path=str(tmp_path/'draft.json'))
    assert not result['ok'] and result['error']=='disk full'
    assert s.state()==before
