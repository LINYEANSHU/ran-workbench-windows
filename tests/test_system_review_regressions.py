"""Behavioral regressions for review R02–R05 and R12."""
from copy import deepcopy
from types import SimpleNamespace
from contextlib import nullcontext

import numpy as np
import pytest

from ran_workbench.model import default_project
from ran_workbench.system.schema import default_experiment
from ran_workbench.system.channel import RTChannel
from ran_workbench.system.graph import compile_graph, default_graph
from ran_workbench.system.simulator import simulate
from ran_workbench.system.service import SimulationService


def policy(exp, kind, owner=None):
    return next(n['parameters'] for n in exp['graph']['nodes']
                if n['kind'] == kind and (owner is None or n['entity_id'] == owner))


def experiment():
    scene = default_project()
    scene['buildings'] = []
    scene['bs'][0]['position_m'] = [-10, -10, 5]
    scene['ues'][0]['position_m'] = [-.15, 0, 1.5]
    exp = default_experiment(scene)
    exp['settings'].update(mu=0, slots=80, decoder='ideal')
    return exp


def wall(left=0, right=.2, base=0, height=3):
    return dict(id='wall', name='wall', footprint_m=[[left,-1],[right,-1],[right,1],[left,1]],
                holes_m=[], base_m=base, height_m=height, material='concrete', thickness_m=.2)


class FixedChannel:
    metadata = {'backend': 'test_fixture', 'calibrated': False}

    def __init__(self, exp):
        self.exp = exp

    def snapshot(self, slot):
        return {b['id']: {u['id']: np.full((12,1,1), 1e-5, complex)
                          for u in self.exp['scene']['ues']} for b in self.exp['scene']['bs']}


@pytest.mark.parametrize('case', ['cached_inside', 'thin_wall', 'resnapshot_thin_wall', 'last_cached_slot', 'underground'])
def test_motion_rejects_invalid_position_and_continuous_segment_before_cache(monkeypatch, case):
    exp = experiment()
    exp['settings']['slots'] = 4
    policy(exp, 'mobility')['vx'] = 100.
    exp['scene']['buildings'] = [wall()]
    if case in ('thin_wall', 'resnapshot_thin_wall'):
        exp['scene']['buildings'] = [wall(-.12, -.10)]
    if case == 'resnapshot_thin_wall':
        policy(exp, 'channel')['snapshot_slots'] = 1
    if case == 'last_cached_slot':
        exp['scene']['buildings'] = [wall(.10, .2)]
    if case == 'underground':
        exp['scene']['buildings'] = []
        exp['scene']['ues'][0]['position_m'][2] = .01
        policy(exp, 'mobility')['vz'] = -20.
    channel = RTChannel(exp)
    solved = []
    def solve(project, slot):
        solved.append(slot)
        return FixedChannel(exp).snapshot(slot)
    monkeypatch.setattr(channel, '_solve', solve)
    with pytest.raises(ValueError, match='building|ground|建筑|地下'):
        simulate(exp, channel_provider=channel)
    assert solved == [0]


@pytest.mark.parametrize('case', ['outside', 'above_roof', 'below_elevated', 'courtyard'])
def test_motion_keeps_legal_outdoor_segments_and_channel_hold(monkeypatch, case):
    exp = experiment()
    exp['scene']['buildings'] = [wall()]
    policy(exp, 'mobility')['vx'] = 100.
    if case == 'outside':
        exp['scene']['ues'][0]['position_m'][1] = 2
    if case == 'above_roof':
        exp['scene']['ues'][0]['position_m'][2] = 4
    if case == 'below_elevated':
        exp['scene']['buildings'] = [wall(base=2)]
    if case == 'courtyard':
        building = wall(-2, 2)
        building['footprint_m'] = [[-2,-2],[2,-2],[2,2],[-2,2]]
        building['holes_m'] = [[[-1,-1],[-1,1],[1,1],[1,-1]]]
        exp['scene']['buildings'] = [building]
    channel = RTChannel(exp)
    monkeypatch.setattr(channel, '_solve', lambda project, slot: FixedChannel(exp).snapshot(slot))
    first = channel.snapshot(0)
    assert channel.snapshot(4) is first
    assert len(channel.metadata['snapshots']) == 1


def test_stable_event_csi_refreshes_without_bs_private_state():
    exp = experiment()
    policy(exp, 'report')['trigger'] = 'event'
    result = simulate(exp, channel_provider=FixedChannel(exp))
    generated = [r['slot'] for r in result['trace'] if r['type'] == 'csi_generated']
    assert len(generated) >= 4 and generated[-1] >= 60
    fresh_tx = [r for r in result['trace'] if r['type'] == 'tx' and r['slot'] >= 21 and r['attempt'] == 1]
    assert fresh_tx and all(r['csi_measured_slot'] is not None for r in fresh_tx)
    assert all(r['mcs'] == 28 for r in fresh_tx)


def test_event_csi_recovers_after_initial_transport_loss_and_respects_delay():
    exp = experiment()
    policy(exp, 'report')['trigger'] = 'event'
    policy(exp, 'feedback').update(loss_rate=.5, delay_slots=7)
    # Select a fixed, hand-checked seed: the slot-0 report is lost.
    exp['settings']['seed'] = 1
    result = simulate(exp, channel_provider=FixedChannel(exp))
    drops = [r for r in result['trace'] if r['type'] == 'feedback_drop' and r['reason'] == 'transport_loss']
    arrivals = [r for r in result['trace'] if r['type'] == 'csi_arrival']
    assert drops and drops[0]['slot'] == 0
    assert arrivals and arrivals[0]['measured_slot'] > 0
    assert all(r['slot'] >= r['measured_slot'] + 7 for r in arrivals)
    assert any(r['type'] == 'tx' and r['csi_measured_slot'] is not None for r in result['trace'])


def two_cells(same_resource=False):
    exp = experiment()
    exp['scene']['bs'][0].update(position_m=[-10,0,5], channel=0)
    exp['scene']['bs'].append(dict(deepcopy(exp['scene']['bs'][0]), id='BS-02', position_m=[10,0,5], channel=0 if same_resource else 1))
    exp['scene']['ues'] = [dict(id='UE-01', name='left', position_m=[-9,0,1.5]),
                          dict(id='UE-02', name='right', position_m=[9,0,1.5])]
    exp['graph'] = default_graph(exp['scene'])
    exp['settings']['slots'] = 3
    return exp


class ServingOnlyChannel(FixedChannel):
    def snapshot(self, slot):
        complete = super().snapshot(slot)
        return {'BS-01': {'UE-01': complete['BS-01']['UE-01']},
                'BS-02': {'UE-02': complete['BS-02']['UE-02']}}


def test_orthogonal_unresolved_cross_links_are_not_required_by_simulator():
    exp = two_cells()
    result = simulate(exp, channel_provider=ServingOnlyChannel(exp))
    assert all(u['transmissions'] > 0 for u in result['ues'])


def test_unresolved_necessary_same_resource_interference_is_explicitly_rejected():
    exp = two_cells(same_resource=True)
    with pytest.raises(ValueError, match='BS-01.*UE-02|BS-02.*UE-01'):
        simulate(exp, channel_provider=ServingOnlyChannel(exp))


def test_rt_skips_orthogonal_cross_link_solver_calls(monkeypatch):
    """Replace only the external RT runtime; exercise the real pair-selection loop."""
    from ran_workbench import engine, path_geometry
    exp = two_cells()
    class Scene:
        def __init__(self): self.objects = {}
        def add(self, item): self.objects[item.name] = item
        def remove(self, name): self.objects.pop(name)
    class Solver:
        _candidate_generator = None
        def __call__(self, scene, **kwargs):
            source = scene.objects['source'].position
            target = scene.objects['receiver'].position
            # Cross links have no propagation paths in this fixture.
            n = int(source[0] * target[0] > 0)
            return SimpleNamespace(valid=np.ones(n, bool), _paths_buffer=None,
                _rx_ind=np.zeros(n,int), _path_ind=np.arange(n),
                cir=lambda **kw: (np.full((1,1,n,1),1e-5,complex),np.zeros(n)))
    rt = SimpleNamespace(PathSolver=Solver, PlanarArray=lambda **kw: None,
                         Transmitter=lambda **kw: SimpleNamespace(**kw), Receiver=lambda **kw: SimpleNamespace(**kw))
    monkeypatch.setattr(engine, '_runtime', lambda: (rt,SimpleNamespace(variant=lambda:'test-runtime'),SimpleNamespace(sync_thread=lambda:None),None))
    monkeypatch.setattr(engine, '_staged_scene', lambda root: nullcontext(None))
    monkeypatch.setattr(engine, '_create_scene', lambda *args: Scene())
    monkeypatch.setattr(engine, '_surface_catalog', lambda *args: {})
    monkeypatch.setattr(engine, '_CandidateBudgetGuard', lambda generator, dr: generator)
    monkeypatch.setattr(path_geometry, 'validate_sparse_paths', lambda buffer,tx,rx,indices,catalog: np.zeros(len(indices),np.uint8))
    channel = RTChannel(exp)
    snapshot = channel.snapshot(0)
    assert set(snapshot['BS-01']) == {'UE-01'}
    assert set(snapshot['BS-02']) == {'UE-02'}
    assert len(channel.metadata['latest_links']) == 2


def test_orthogonal_experiment_work_budget_counts_only_required_links():
    from ran_workbench.system.schema import validate_experiment
    exp=two_cells()
    exp['settings']['slots']=2000
    policy(exp,'mobility')['vy']=1.
    policy(exp,'channel')['snapshot_slots']=1
    # 2000 snapshots * 2 required links = 4000, within the 4096 budget.
    assert validate_experiment(exp)['settings']['slots']==2000
    exp['scene']['bs'][1]['channel']=0
    with pytest.raises(ValueError,match='4096'):
        validate_experiment(exp)


@pytest.mark.parametrize('key,value', [('feedback_bits_per_slot',4), ('seed',43), ('slots',100),
    ('mu',0), ('n_prb',11), ('decoder','ideal'), ('channel_backend','reference_fixture'),
    ('pdsch_symbols',11), ('dmrs_re',6), ('overhead_re',6), ('pilot_symbols',2)])
def test_compare_rejects_each_external_settings_difference(key, value):
    service = SimulationService(SimpleNamespace())
    service.result = {'ues': []}
    assert service.execute({'action':'pin'})['ok']
    service.experiment['settings'][key] = value
    outcome = service.execute({'action':'compare'})
    assert not outcome['ok'], key


def test_compare_allows_trace_only_settings_and_scheduler_changes():
    service = SimulationService(SimpleNamespace())
    service.result = {'ues': []}
    assert service.execute({'action':'pin'})['ok']
    service.experiment['settings'].update(trace_stride=2, trace_limit=1000)
    policy(service.experiment, 'scheduler')['algorithm'] = 'max_cqi'
    assert service.execute({'action':'compare'})['ok']


@pytest.mark.parametrize('group', ['bs','ues'])
def test_global_device_id_compiles_and_runs_in_distinct_scope(group):
    exp = experiment()
    exp['scene'][group][0]['id'] = 'global'
    exp['graph'] = default_graph(exp['scene'])
    exp['settings']['slots'] = 3
    compiled = compile_graph(exp)
    assert 'global' in compiled[group]
    assert set(compiled['global']['global']) == {'channel','metrics'}
    result = simulate(exp, channel_provider=FixedChannel(exp))
    assert result['ues'][0]['transmissions'] > 0


def test_global_device_id_does_not_bypass_cross_device_private_state_rule():
    exp = two_cells()
    exp['scene']['bs'][0]['id'] = 'global'
    exp['graph'] = default_graph(exp['scene'])
    edge = next(e for e in exp['graph']['edges'] if e['target'] == 'BS-02/adaptation' and e['target_port'] == 'radio')
    edge['source'] = 'global/power'
    with pytest.raises(ValueError, match='私有状态'):
        compile_graph(exp)
