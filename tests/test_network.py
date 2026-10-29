"""Analytical RF checks independent of the native ray tracer."""
import copy
import importlib
import json
import math

import pytest

from ran_workbench import channels
from ran_workbench.model import default_project, validate_project


def project_with_bs(count=2):
    p = default_project()
    p['buildings'] = []
    p['bs'] = [dict(copy.deepcopy(p['bs'][0]), id=f'BS-{i+1:02d}',
                    position_m=[i * 20, 0, 25]) for i in range(count)]
    return validate_project(p)


def network(links, p):
    return importlib.import_module('ran_workbench.network').summarize_network(links, p)


def link(bs_id, power=-60):
    return dict(bs_id=bs_id, incoherent_dbm=power, coherent_dbm=power,
                path_count=1, carrier_phasor=[1., 0.])


def test_five_bs_are_accepted_and_six_rejected():
    p = project_with_bs(5)
    assert len(p['bs']) == 5
    p['bs'].append(dict(copy.deepcopy(p['bs'][0]), id='BS-06'))
    with pytest.raises(ValueError, match='5|maximum'):
        validate_project(p)


def test_legacy_project_gets_explicit_rf_defaults():
    p = project_with_bs()
    assert p['network'] == {'temperature_k': 290., 'noise_figure_db': 7.}
    assert p['bs'][0]['channel'] == 0
    assert p['bs'][0]['activity'] == 1
    assert p['bs'][0]['phase_deg'] == 0
    assert p['bs'][0]['time_offset_ns'] == 0


@pytest.mark.parametrize('field,value', [('channel', -1), ('channel', 1.5), ('channel', True),
                                       ('activity', -0.1), ('activity', 1.1),
                                       ('time_offset_ns', 1000001)])
def test_invalid_bs_radio_settings_rejected(field, value):
    p = project_with_bs()
    p['bs'][0][field] = value
    with pytest.raises(ValueError):
        validate_project(p)


@pytest.mark.parametrize('field,value', [('temperature_k', 0), ('temperature_k', 501),
                                       ('noise_figure_db', -1), ('noise_figure_db', 41)])
def test_invalid_receiver_noise_rejected(field, value):
    p = project_with_bs()
    p['network'][field] = value
    with pytest.raises(ValueError):
        validate_project(p)


def test_equal_cochannel_bs_interfere_and_only_conditional_cooperation_adds():
    p = project_with_bs()
    r = network([link('BS-01'), link('BS-02')], p)
    expected_noise = 10 * math.log10(1.380649e-23 * 290 * 20e6) + 30 + 7
    assert r['noise_dbm'] == pytest.approx(expected_noise)
    assert r['serving_bs_id'] == 'BS-01'
    assert r['interference_dbm'] == pytest.approx(-60)
    assert -0.02 < r['sinr_db'] < 0
    assert r['cooperative_dbm'] == pytest.approx(-56.98970004336)
    assert r['cooperation_gain_db'] == pytest.approx(3.01029995664)
    assert r['carrier_joint_dbm'] == pytest.approx(-53.97940008672)
    assert r['carrier_joint_gain_db'] == pytest.approx(6.02059991328)
    assert len(r['per_bs']) == 2
    json.dumps(r, allow_nan=False)


def test_single_station_has_no_interference_and_sinr_equals_snr():
    p = project_with_bs(1)
    r = network([link('BS-01')], p)
    assert r['interference_dbm'] is None
    assert r['sinr_db'] == r['snr_db']
    assert r['cooperation_gain_db'] == 0


def test_orthogonal_channels_and_partial_interferer_activity():
    p = project_with_bs()
    p['bs'][1]['channel'] = 1
    r = network([link('BS-01'), link('BS-02')], p)
    assert r['interference_dbm'] is None
    assert r['sinr_db'] == r['snr_db']
    assert r['cooperation_gain_db'] == 0
    p['bs'][1]['channel'] = 0
    p['bs'][1]['activity'] = .1
    r = network([link('BS-01'), link('BS-02')], p)
    assert r['interference_dbm'] == pytest.approx(-70)
    assert 9.9 < r['sinr_db'] < 10
    # Explicit coordination assumes full simultaneous transmission, not load.
    assert r['cooperation_gain_db'] == pytest.approx(3.01029995664)


def test_weak_interference_is_not_lost_by_subtracting_large_total_power():
    r = network([link('BS-01', 0), link('BS-02', -200)], project_with_bs())
    assert r['interference_dbm'] == pytest.approx(-200)
    assert r['per_bs'][1]['interference_dbm'] == pytest.approx(0)


def test_missing_signal_and_unresolved_interferers_are_explicit():
    p = project_with_bs()
    missing = dict(bs_id='BS-02', incoherent_dbm=None, coherent_dbm=None, path_count=0)
    r = network([link('BS-01'), missing], p)
    assert r['unresolved_bs_ids'] == ['BS-02']
    assert r['per_bs'][1]['sinr_db'] is None
    r = network([dict(link('BS-01'), incoherent_dbm=None), missing], p)
    assert r['serving_bs_id'] is None
    assert r['sinr_db'] is None
    json.dumps(r, allow_nan=False)


def test_opposite_carrier_phasors_cancel_and_phase_control_restores_power():
    p = project_with_bs()
    links = [link('BS-01'), dict(link('BS-02'), carrier_phasor=[-1., 0.])]
    r = network(links, p)
    assert r['carrier_joint_dbm'] is None
    assert r['carrier_joint_zero'] is True
    p['bs'][1]['phase_deg'] = 180
    r = network(links, p)
    assert r['carrier_joint_dbm'] == pytest.approx(-53.97940008672)


def test_surface_power_does_not_invent_carrier_phase():
    r = network([dict(link('BS-01'), coherent_dbm=None, carrier_phasor=None),
                 dict(link('BS-02'), coherent_dbm=None, carrier_phasor=None)], project_with_bs())
    assert r['cooperation_gain_db'] > 3
    assert r['carrier_joint_dbm'] is None
    assert not r.get('carrier_joint_zero')


def test_cross_bs_bandwidth_combining_preserves_absolute_path_delays():
    p = project_with_bs()
    p['bandwidth_hz'] = 63e6
    # Band averaging integrates continuously; the plot endpoints are unrelated.
    links = []
    for bs, delay in zip(p['bs'], [100e-9, 100e-9 + 1 / 64e6]):
        l = link(bs['id'], 0)
        l['complex_cfr'] = channels.complex_response([1], [delay], 0, p['bandwidth_hz'])
        links.append(l)
    r = network(links, p)
    assert r['carrier_joint_dbm'] == pytest.approx(6.02059991328)
    assert r['wideband_joint_dbm'] == pytest.approx(10*math.log10(2+2*math.sin(math.pi*63/64)/(math.pi*63/64)))
    p['bs'][1]['time_offset_ns'] = -1 / 64e6 * 1e9
    r = network(links, p)
    assert r['wideband_joint_dbm'] == pytest.approx(6.02059991328)


@pytest.mark.parametrize('stations',[1,2])
def test_joint_wideband_power_does_not_reintroduce_plot_grid_aliasing(stations):
    p=project_with_bs(stations);p['bandwidth_hz']=20e6
    links=[]
    for index,bs in enumerate(p['bs']):
        beta=[1e-5,1e-5] if stations==1 else [1e-5]
        delays=[0,3150e-9] if stations==1 else [index*3150e-9]
        row=channels.summarize_channels(beta,delays,30,p['bandwidth_hz'])
        row.update(bs_id=bs['id'],complex_cfr=channels.complex_response(beta,delays,30,p['bandwidth_hz']))
        links.append(row)
    r=network(links,p)
    assert r['wideband_joint_dbm']==pytest.approx(-66.98970004336)


def test_attach_network_includes_grid_ue_and_reproducible_assumptions():
    module = importlib.import_module('ran_workbench.network')
    p = project_with_bs(1)
    result = dict(points=[dict(links=[link('BS-01')])], ues=[dict(links=[link('BS-01')])])
    module.attach_network(result, p)
    assert result['points'][0]['network']['serving_bs_id'] == 'BS-01'
    assert result['ues'][0]['network']['sinr_db'] > 0
    assert result['network_model']['power_basis'] == 'incoherent_dbm'
    assert result['network_model']['bs'][0]['channel'] == 0


@pytest.mark.parametrize('resolved_count',[0,1])
def test_unknown_wideband_channels_never_become_known_zero_or_complete_joint_power(resolved_count):
    p=project_with_bs()
    links=[]
    for i,bs in enumerate(p['bs']):
        known=i<resolved_count
        l=link(bs['id']) if known else dict(bs_id=bs['id'],incoherent_dbm=None,coherent_dbm=None,path_count=0)
        l['complex_cfr']=channels.complex_response([1] if known else [],[1e-7] if known else [],0,p['bandwidth_hz'])
        links.append(l)
    r=network(links,p)
    assert r['wideband_joint_dbm'] is None
    assert r.get('wideband_joint_zero',False) is False
    assert all(not item['wideband_joint_zero'] for item in r['per_bs'])


def test_known_intra_channel_zero_is_still_zero_in_joint_wideband_experiment():
    p=project_with_bs(1)
    l=channels.summarize_channels([1,-1],[1e-7,1e-7],0,p['bandwidth_hz'])
    l['bs_id']='BS-01'
    l['complex_cfr']=channels.complex_response([1,-1],[1e-7,1e-7],0,p['bandwidth_hz'])
    r=network([l],p)
    assert r['wideband_joint_dbm'] is None
    assert r['wideband_joint_zero'] is True


def test_all_station_power_is_distinct_from_serving_signal_and_keeps_unknown_count():
    p=project_with_bs(3)
    r=network([link('BS-01'),link('BS-02'),dict(bs_id='BS-03',incoherent_dbm=None)],p)
    assert r['total_received_dbm']==pytest.approx(-56.98970004336)
    assert r['signal_dbm']==-60
    assert r['resolved_bs_count']==2 and r['requested_bs_count']==3
    assert r['sinr_db']==pytest.approx(-10*math.log10(1+10**((r['noise_dbm']+60)/10)))


def test_ue_lists_each_interferer_with_resource_and_activity_contribution():
    module=importlib.import_module('ran_workbench.network')
    p=project_with_bs(3);p['bs'][1]['activity']=.25;p['bs'][2]['channel']=1
    result=dict(points=[dict(links=[link(b['id']) for b in p['bs']])],
                ues=[dict(links=[link(b['id']) for b in p['bs']])])
    module.attach_network(result,p)
    sources=result['ues'][0]['network']['per_bs'][0]['interference_sources']
    assert [s['bs_id'] for s in sources]==['BS-02','BS-03']
    assert sources[0]['contribution_dbm']==pytest.approx(-66.02059991328)
    assert sources[0]['status']=='cochannel'
    assert sources[1]['status']=='orthogonal' and sources[1]['contribution_dbm'] is None
    assert result['ues'][0]['network']['interference_dbm']==pytest.approx(sources[0]['contribution_dbm'])
    assert 'interference_sources' not in result['points'][0]['network']['per_bs'][0]
    assert result['network_model']['calculation_scope']=='all_project_base_stations'
    assert result['network_model']['bs_count']==3
