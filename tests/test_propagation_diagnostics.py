import math

import pytest
import numpy as np
from types import SimpleNamespace

from ran_workbench.model import default_project, validate_project


def test_dense_grid_and_large_receiver_batch_are_independent():
    from ran_workbench.geometry import sample_points
    project = default_project()
    project['solver'].update(batch_size=512, samples_per_src=2_000_000)
    project['sampling'].update(spacing_m=[5, 5, 5], limit=250_000)
    normalized = validate_project(project)
    grid = sample_points(normalized)
    assert grid['requested'] == 65 * 49 * 17
    assert len(grid['positions_m']) > 40_000
    assert normalized['solver']['batch_size'] == 512


def test_batch_budget_allows_more_than_64_without_unbounded_ray_fanout():
    from ran_workbench.compute_policy import effective_batch_size
    settings = default_project()['solver']
    settings.update(batch_size=1024, samples_per_src=1_000_000, max_depth=3)
    assert effective_batch_size(settings) > 64
    settings.update(samples_per_src=10_000_000, max_depth=10, diffuse_reflection=True)
    assert 1 <= effective_batch_size(settings) < 1024


def surface(normal=(0, 0, 1)):
    return {'object_id': 'ground', 'object_name': '地面', 'material': 'concrete',
            'primitive_id': 0, 'normal': list(normal),
            'triangle_m': [[-100, -100, 0], [100, -100, 0], [0, 100, 0]]}


def audit(vertices, kind, hit=None, delay=None):
    from ran_workbench.path_audit import audit_path
    length = sum(math.dist(a, b) for a, b in zip(vertices, vertices[1:]))
    return audit_path(vertices, [kind], length / 299792458 * 1e9 if delay is None else delay,
                      [hit or surface()])


def test_ground_reflection_identifies_real_surface_and_checks_mirror_law():
    result = audit([[-10, 0, 10], [0, 0, 0], [10, 0, 10]], 'specular')
    assert result['geometry_valid']
    assert result['delay_length_error_m'] < 1e-9
    assert result['interactions'][0]['object_name'] == '地面'
    assert result['interactions'][0]['surface_error_m'] < 1e-9


def test_diffuse_is_allowed_away_from_mirror_direction_but_not_through_surface():
    assert audit([[-10, 0, 10], [0, 0, 0], [40, 5, 2]], 'diffuse')['geometry_valid']
    assert not audit([[-10, 0, 10], [0, 0, 0], [40, 5, -2]], 'diffuse')['geometry_valid']


def test_bad_specular_bend_and_airborne_surface_hit_are_reported():
    assert not audit([[-10, 0, 10], [0, 0, 0], [40, 5, 2]], 'specular')['geometry_valid']
    result = audit([[-10, 0, 10], [0, 0, 5], [10, 0, 10]], 'diffuse')
    assert not result['geometry_valid']
    assert result['interactions'][0]['surface_error_m'] == pytest.approx(5)


def test_thin_layer_transmission_is_straight_and_delay_matches_geometry():
    wall = surface(normal=(1, 0, 0))
    wall['triangle_m'] = [[0, -100, -100], [0, 100, -100], [0, 0, 100]]
    assert audit([[-10, 0, 10], [0, 0, 10], [10, 0, 10]], 'transmission', wall)['geometry_valid']
    assert not audit([[-10, 0, 10], [0, 0, 10], [10, 0, 20]], 'transmission', wall)['geometry_valid']
    assert not audit([[-10, 0, 10], [0, 0, 10], [10, 0, 10]], 'transmission', wall, delay=1000)['geometry_valid']


def test_coverage_does_not_materialize_dense_path_vertex_tensors():
    from ran_workbench.engine import _extract_links

    class Paths:
        valid = np.ones((2, 1), dtype=bool)
        _paths_buffer = SimpleNamespace(interaction_types=np.array([[0], [1]]))
        _rx_ind = np.array([0, 1])
        _path_ind = np.array([0, 0])

        def cir(self, **kwargs):
            return np.ones((2, 1, 1), dtype=complex), np.ones((2, 1)) * 1e-7

        @property
        def interactions(self):
            raise AssertionError('Coverage must not allocate dense vertices/objects/interaction tensors')

    links = _extract_links(Paths(), {'id': 'BS', 'power_dbm': 0}, [[0, 0, 1], [1, 0, 1]], 20e6, False, [])
    assert [link['has_los'] for link in links] == [True, False]
    assert [link['path_count'] for link in links] == [1, 1]


def test_empty_coverage_paths_need_no_sionna_sparse_indices():
    from ran_workbench.engine import _extract_links
    paths = SimpleNamespace(valid=np.empty((2, 0), dtype=bool),
        cir=lambda **kwargs: (np.empty((2, 0, 1), dtype=complex), np.empty((2, 0))),
        _paths_buffer=SimpleNamespace(interaction_types=np.empty((0, 1), dtype=int)))
    links = _extract_links(paths, {'id': 'BS', 'power_dbm': 0}, [[0, 0, 1], [1, 0, 1]], 20e6, False, [])
    assert all(link['path_count'] == 0 and not link['has_los'] for link in links)


def test_full_candidate_buffer_is_rejected_before_invalid_paths_can_be_discarded():
    from ran_workbench.engine import _CandidateBudgetGuard
    events = []
    buffer = SimpleNamespace(paths_counter=[10], schedule=lambda: events.append('scheduled'))
    guard = _CandidateBudgetGuard(lambda **kwargs: buffer, SimpleNamespace(eval=lambda: events.append('evaluated')))
    with pytest.raises(RuntimeError, match='Candidate-path'):
        guard(max_num_paths_per_src=10)
    assert events == ['scheduled', 'evaluated']
    buffer.paths_counter = [9]
    assert guard(max_num_paths_per_src=10) is buffer
