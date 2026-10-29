import copy
import math

import numpy as np
import pytest

from ran_workbench.model import default_project, validate_project


def configured(**changes):
    project = default_project()
    project['surface_sampling'] = dict(enabled=True, center_m=[0, 0, 120], center_bs_id=None,
        radii_m=[10, 20], subdivisions=2, replicates=2, ray_chunk_size=65536, **changes)
    return project


def test_surface_spec_follows_bs_and_preserves_legacy_project():
    project = default_project()
    assert 'surface_sampling' not in validate_project(project)
    project['surface_sampling'] = {'enabled': True, 'center_bs_id': project['bs'][0]['id'],
                                  'radii_m': [25, 50], 'subdivisions': 2}
    before = copy.deepcopy(project)
    normalized = validate_project(project)['surface_sampling']
    assert normalized['center_m'] == project['bs'][0]['position_m']
    assert normalized['replicates'] == 2
    assert project == before


@pytest.mark.parametrize('patch', [
    {'radii_m': [0]}, {'radii_m': [10, 10]}, {'radii_m': [20, 10]},
    {'radii_m': [True]}, {'subdivisions': 7}, {'subdivisions': True},
    {'subdivisions': 6, 'radii_m': [10, 20, 30, 40]},
    {'replicates': 0}, {'replicates': 9}, {'ray_chunk_size': 0},
    {'center_bs_id': 'missing'}, {'center_m': [0, 0, -1]}, {'enabled': 1},
])
def test_surface_validation_rejects_invalid_or_excessive_requests(patch):
    project = configured()
    project['surface_sampling'].update(patch)
    with pytest.raises(ValueError):
        validate_project(project)


def test_geodesic_mesh_is_closed_outward_and_has_requested_radius():
    from ran_workbench.spherical import geodesic_sphere
    mesh = geodesic_sphere([3, 4, 5], 20, 2)
    vertices, faces = np.asarray(mesh['vertices_m']), np.asarray(mesh['faces'])
    assert len(faces) == 20 * 4**2
    assert len(vertices) == 10 * 4**2 + 2
    assert np.allclose(np.linalg.norm(vertices - [3, 4, 5], axis=1), 20)
    triangles = vertices[faces]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    assert np.all(np.sum(normals * (triangles.mean(axis=1) - [3, 4, 5]), axis=1) > 0)
    edges, counts = np.unique(np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]],
                                                     faces[:, [2, 0]]]), axis=1), axis=0, return_counts=True)
    assert np.all(counts == 2)
    assert len(vertices) - len(edges) + len(faces) == 2
    area = np.linalg.norm(normals, axis=1).sum() / 2
    assert math.isclose(area, 4 * math.pi * 20**2, rel_tol=.025)


def test_surface_excludes_ground_and_building_intersections():
    from ran_workbench.spherical import sample_surfaces
    project = configured()
    project['surface_sampling'].update(center_m=[-60, 22, 10], radii_m=[25], subdivisions=2)
    sampled = sample_surfaces(validate_project(project))
    assert sampled['requested'] == 320
    assert sampled['excluded'] > 0
    for mesh in sampled['meshes']:
        vertices = np.asarray(mesh['vertices_m'])
        assert np.all(vertices[np.asarray(mesh['faces'])][:, :, 2] >= 0)
    from ran_workbench.geometry import contains_point
    for point in sampled['points']:
        assert point['sample_kind'] == 'surface_cell'
        assert point['area_m2'] > 0
        assert not contains_point(project['buildings'], point['position_m'])
    assert len(sampled['points']) + sampled['excluded'] == sampled['requested']


def test_ray_chunks_preserve_total_and_power_mean_is_linear():
    from ran_workbench.spherical import ray_chunks, weighted_gain_mean
    assert list(ray_chunks(10003, 4096)) == [4096, 4096, 1811]
    assert list(ray_chunks(1, 4096)) == [1]
    result = weighted_gain_mean([np.array([1., 0.]), np.array([3., 2.])], [1, 3])
    assert np.allclose(result, [2.5, 1.5])
    with pytest.raises(ValueError):
        list(ray_chunks(0, 4096))


def test_randomized_launch_rotations_are_reproducible_and_isotropic():
    from ran_workbench.spherical import launch_rotation
    first = launch_rotation(42)
    assert np.array_equal(first, launch_rotation(42))
    assert not np.array_equal(first, launch_rotation(43))
    assert np.allclose(first @ first.T, np.eye(3), atol=1e-12)
    assert math.isclose(np.linalg.det(first), 1., abs_tol=1e-12)
    directions = np.array([launch_rotation(seed) @ [0., 0., 1.] for seed in range(1000)])
    assert np.max(abs(directions.mean(axis=0))) < .06
    assert np.max(abs(directions.T @ directions / len(directions) - np.eye(3) / 3)) < .04


def test_probe_lifecycle_preserves_coverage_but_invalidates_on_edit():
    from ran_workbench.controller import Controller
    from ran_workbench.model import project_fingerprint
    controller = Controller()
    coverage = {'scene_sha256': project_fingerprint(controller.project), 'mode': 'coverage', 'points': []}
    controller.begin('coverage')
    controller.complete(coverage)
    controller.begin('probe')
    assert controller.result == coverage
    probe = dict(coverage, mode='probe', ues=[])
    controller.complete(probe)
    assert controller.snapshot()['probe_result'] == probe
    assert controller.result == coverage
    controller.update(controller.project)
    assert controller.snapshot()['probe_result'] is None


def test_probe_invalid_position_does_not_start_a_job():
    from ran_workbench.automation_commands import ApplicationCommands
    from ran_workbench.controller import Controller
    class Runner:
        def start(self, *args, **kwargs):
            raise AssertionError('Invalid probe must be rejected before a worker is started')
    controller = Controller()
    api = ApplicationCommands(controller, Runner())
    result = api.execute({'action': 'run', 'data': {'mode': 'probe', 'position_m': [0, 0, -1]}})
    assert not result['ok']
    assert not controller.busy


def test_surface_csv_preserves_radius_metric_and_missing_coherent_values(tmp_path):
    import csv
    from ran_workbench.project_io import export_result
    result = {'mode': 'coverage', 'metric_kind': 'surface_average_noncoherent',
              'scene_sha256': 'test', 'points': [dict(position_m=[1, 2, 3], radius_m=25,
              sample_kind='surface_cell', area_m2=2.5, cell_id=7,
              links=[dict(bs_id='BS', incoherent_dbm=-50, coherent_dbm=None,
                          path_count=None, metric_kind='surface_average_noncoherent')])]}
    path = tmp_path / 'surface.csv'
    export_result(result, path)
    row = list(csv.DictReader(path.open(encoding='utf-8-sig')))[0]
    assert row['radius_m'] == '25'
    assert row['metric_kind'] == 'surface_average_noncoherent'
    assert row['sample_kind'] == 'surface_cell'
    assert row['coherent_dbm'] == row['path_count'] == ''


def test_probe_api_starts_with_position_and_exposes_separate_result():
    from ran_workbench.automation_commands import ApplicationCommands
    from ran_workbench.controller import Controller
    from ran_workbench.model import project_fingerprint
    class Runner:
        def start(self, project, mode, probe_position_m=None):
            self.request = (mode, probe_position_m)
    controller, runner = Controller(), Runner()
    api = ApplicationCommands(controller, runner)
    assert api.execute({'action':'run', 'data':{'mode':'probe','position_m':[0,-20,10]}})['ok']
    assert runner.request == ('probe', [0., -20., 10.])
    probe = {'mode':'probe', 'scene_sha256':project_fingerprint(controller.project), 'ues':[]}
    controller.complete(probe)
    assert api.execute({'action':'get_result', 'data':{'target':'probe'}})['result'] == probe
