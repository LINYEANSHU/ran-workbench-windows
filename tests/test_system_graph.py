from copy import deepcopy
import pytest


def test_graph_compiles_actual_device_policies():
    from ran_workbench.system.schema import default_experiment
    from ran_workbench.system.graph import compile_graph
    exp=default_experiment()
    scheduler=next(n for n in exp['graph']['nodes'] if n['kind']=='scheduler')
    scheduler['parameters']['algorithm']='max_cqi'
    compiled=compile_graph(exp)
    assert compiled['bs']['BS-01']['scheduler']['algorithm']=='max_cqi'


@pytest.mark.parametrize('case',['missing','bad_port','cycle','cross_bs','unknown_parameter'])
def test_invalid_edges_fail_before_simulation(case):
    from ran_workbench.system.schema import default_experiment
    from ran_workbench.system.graph import compile_graph
    exp=default_experiment();edges=exp['graph']['edges']
    if case=='missing': edges.pop(0)
    if case=='bad_port': edges[0]['source_port']='not-a-port'
    if case=='cycle': edges[0]['source']=edges[0]['target']
    if case=='cross_bs':
        next(n for n in exp['graph']['nodes'] if n['kind']=='feedback')['parameters']['serving_bs']='not-a-BS'
    if case=='unknown_parameter': exp['graph']['nodes'][0]['parameters']['silent_typo']=1
    with pytest.raises(ValueError): compile_graph(exp)


def test_bypassing_estimation_changes_compiled_executable_path():
    from ran_workbench.system.schema import default_experiment
    from ran_workbench.system.graph import compile_graph
    exp=default_experiment()
    measure=next(n for n in exp['graph']['nodes'] if n['kind']=='measure')
    estimate=next(n for n in exp['graph']['nodes'] if n['kind']=='estimate')
    edge=next(e for e in exp['graph']['edges'] if e['source']==estimate['id'])
    edge.update(source=measure['id'],source_port='observation')
    assert compile_graph(exp)['ues']['UE-01']['use_estimator'] is False


def test_type_compatible_but_unimplemented_route_is_rejected():
    from ran_workbench.system.schema import default_experiment
    from ran_workbench.system.graph import compile_graph
    exp=default_experiment()
    estimate=next(n for n in exp['graph']['nodes'] if n['kind']=='estimate')
    decode=next(n for n in exp['graph']['nodes'] if n['kind']=='decode')
    edge=next(e for e in exp['graph']['edges'] if e['target']==decode['id'])
    edge.update(source=estimate['id'],source_port='observation')
    with pytest.raises(ValueError,match='信息通路'):compile_graph(exp)


def test_each_ue_must_connect_to_shared_sinks():
    from ran_workbench.system.schema import default_experiment
    from ran_workbench.system.graph import compile_graph,default_graph
    exp=default_experiment();exp['scene']['ues'].append(dict(id='UE-02',name='two',position_m=[0,0,100]))
    exp['graph']=default_graph(exp['scene'])
    exp['graph']['edges']=[e for e in exp['graph']['edges'] if not(e['source']=='UE-02/feedback' and e['target_port']=='report')]
    with pytest.raises(ValueError,match='缺少'):compile_graph(exp)
