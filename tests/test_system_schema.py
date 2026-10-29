from copy import deepcopy
import pytest


def test_experiment_embeds_scene_and_layout_does_not_invalidate_result():
    from ran_workbench.system.schema import default_experiment, validate_experiment, experiment_fingerprint
    exp = default_experiment()
    assert exp['scene']['schema_version'] == 2
    before = experiment_fingerprint(exp)
    exp['graph']['nodes'][0]['position'] = [700,900]
    assert experiment_fingerprint(exp) == before
    exp['settings']['slots'] += 1
    assert experiment_fingerprint(exp) != before
    assert validate_experiment(exp)['kind'] == 'nr-experiment'


def test_templates_do_not_share_mutable_parameters():
    from ran_workbench.system.schema import default_experiment
    a,b = default_experiment(),default_experiment()
    a['graph']['nodes'][0]['parameters']['power_dbm'] = 99
    assert a != b


@pytest.mark.parametrize('key,value',[('mu',True),('slots',0),('n_prb',999),('seed',-1),('feedback_bits_per_slot',0)])
def test_invalid_settings_are_rejected(key,value):
    from ran_workbench.system.schema import default_experiment, validate_experiment
    exp=default_experiment();exp['settings'][key]=value
    with pytest.raises(ValueError): validate_experiment(exp)


def test_round_trip_is_strict_and_preserves_graph(tmp_path):
    from ran_workbench.system.schema import default_experiment, save_experiment, load_experiment
    exp=default_experiment();p=tmp_path/'试验.ran-sim.json'
    save_experiment(exp,p)
    assert load_experiment(p)==exp
    p.write_text('{"schema_version":NaN}',encoding='utf-8')
    with pytest.raises(ValueError): load_experiment(p)


def test_new_format_does_not_silently_restore_disconnected_motion():
    from ran_workbench.system.schema import default_experiment,validate_experiment
    exp=default_experiment();assert exp['schema_version']==2
    exp['graph']['edges']=[e for e in exp['graph']['edges'] if e['target_port']!='motion']
    with pytest.raises(ValueError,match='缺少'):validate_experiment(exp)


def test_extreme_traffic_is_rejected_before_billions_of_packet_events():
    from ran_workbench.system.schema import default_experiment,validate_experiment
    exp=default_experiment();exp['settings']['slots']=20000
    n=next(n for n in exp['graph']['nodes'] if n['kind']=='traffic')
    n['parameters'].update(mode='constant_rate',packet_bytes=16,rate_mbps=10000.)
    with pytest.raises(ValueError,match='包预算'):validate_experiment(exp)
