"""Hand-constructed paths expose corner/visibility failures before RF reduction."""
from types import SimpleNamespace

import numpy as np
import pytest

from ran_workbench.engine import _extract_links, _surface_catalog, _surface_hit
from ran_workbench.geometry import building_mesh
from ran_workbench.path_audit import audit_path, _triangle_distance


def block(**overrides):
    return dict(id='BLOCK', name='Block', material='concrete', base_m=0, height_m=10,
                footprint_m=[[0, 0], [10, 0], [10, 10], [0, 10]], holes_m=[], **overrides)


def catalog_for(building=None):
    building = building or block()
    mesh = building_mesh(building)
    native = SimpleNamespace(
        vertex_positions_buffer=lambda: np.asarray(mesh['vertices'], dtype=np.float32).ravel(),
        faces_buffer=lambda: np.asarray(mesh['faces'], dtype=np.uint32).ravel())
    obj = SimpleNamespace(name='mesh_building_0', object_id=42, mi_mesh=native)
    return _surface_catalog(SimpleNamespace(objects={'block': obj}), {'buildings': [building]})


def wall_primitive(catalog):
    # The x=0 wall has outward normal (-1,0,0); choose its first triangle.
    for index in range(len(catalog[42]['faces'])):
        if np.allclose(_surface_hit(catalog, 42, index)['normal'], [-1, 0, 0]):
            return index
    raise AssertionError('No west wall in the actual extrusion mesh')


class Paths:
    """A complete, tiny Sionna 2.2 boundary fixture (no native runtime needed)."""
    def __init__(self, bends, codes, primitives, coefficients=None, receiver_indices=None):
        count = len(codes)
        depth = max(1, max(map(len, codes)))
        kinds = np.zeros((count, depth), dtype=np.uint32)
        vertices = np.zeros((count, depth, 3), dtype=np.float32)
        shapes = np.full((count, depth), 0xffffffff, dtype=np.uint32)
        faces = shapes.copy()
        for index, row in enumerate(codes):
            if not row:
                continue
            kinds[index, :len(row)] = row
            vertices[index, :len(row)] = bends[index]
            shapes[index, :len(row)] = 42
            faces[index, :len(row)] = primitives[index]
        self._rx_ind = np.zeros(count, dtype=int) if receiver_indices is None else np.asarray(receiver_indices)
        self._path_ind = np.array([sum(self._rx_ind[:i] == rx) for i, rx in enumerate(self._rx_ind)])
        self._paths_buffer = SimpleNamespace(interaction_types=kinds, shapes=shapes,
            primitives=faces, vertices_x=vertices[:, :, 0], vertices_y=vertices[:, :, 1],
            vertices_z=vertices[:, :, 2])
        receivers = max(self._rx_ind, default=0) + 1
        slots = max(self._path_ind, default=-1) + 1
        self.valid = np.zeros((receivers, slots), dtype=bool)
        self.beta = np.zeros((receivers, slots, 1), dtype=complex)
        self.delays = np.zeros((receivers, slots))
        for i, (rx, slot) in enumerate(zip(self._rx_ind, self._path_ind)):
            self.valid[rx, slot] = True
            self.beta[rx, slot, 0] = 1 if coefficients is None else coefficients[i]
            self.delays[rx, slot] = 1e-7

    def cir(self, **kwargs):
        return self.beta, self.delays

    @property
    def interactions(self):
        raise AssertionError('Coverage must validate sparse geometry without dense path tensors')


def extract(start, end, bends, codes, catalog=None, coefficients=None):
    catalog = catalog or catalog_for()
    triangles = catalog[42]['vertices'][catalog[42]['faces']]
    faces = [[int(np.argmin([_triangle_distance(np.asarray(point), triangle) for triangle in triangles]))
              for point in row] for row in bends]
    paths = Paths(bends, codes, faces, coefficients)
    return _extract_links(paths, {'id': 'BS', 'power_dbm': 0, 'position_m': start},
                          [end], 20e6, False, [], catalog)[0]


def test_mirror_law_alone_does_not_make_a_sharp_corner_a_reflector():
    catalog = catalog_for()
    hit = _surface_hit(catalog, 42, wall_primitive(catalog))
    vertices = [[-5, -5, 5], [0, 0, 5], [-5, 5, 5]]
    result = audit_path(vertices, ['specular'], 2 * np.sqrt(50) / 299792458 * 1e9, [hit])
    assert not result['geometry_valid']
    assert any('edge' in warning for warning in result['geometry_warnings'])


@pytest.mark.parametrize('y, expected', [(0, 0), (0.00001, 0), (0.01, 0), (0.049, 0), (0.051, 1), (5, 1)])
def test_corner_reflections_are_rejected_but_wall_interiors_and_diagonals_survive(y, expected):
    result = extract([-5, y - 5, 5], [-5, y + 5, 5], [[[0, y, 5]]], [[1]])
    assert result['path_count'] == expected


@pytest.mark.parametrize('start,end,expected', [
    ([-5, 5, 5], [15, 5, 5], 0),       # unrecorded wall entry and exit
    ([-5, 5, 5], [5, -5, 5], 0),       # touches an unrelated vertical corner
    ([-5, 5, 11], [15, 5, 11], 1),     # above the roof
    ([-5, -0.01, 5], [15, -0.01, 5], 1),  # close but physically clear
    ([-5, 5, 15], [15, 5, 5], 0),      # clips the roof
])
def test_every_open_segment_is_checked_against_the_closed_building(start, end, expected):
    result = extract(start, end, [[]], [[]])
    assert result['path_count'] == expected
    assert result['has_los'] == bool(expected)


def test_face_only_mode_excludes_edge_diffraction_even_in_shadow():
    assert extract([-5, 5, 5], [5, -5, 5], [[[0, 0, 5]]], [[8]])['path_count'] == 0
    assert extract([-5, 5, 5], [15, 15, 5], [[[0, 0, 5]]], [[8]])['path_count'] == 0


def test_a_coplanar_triangulation_diagonal_is_not_a_diffracting_wedge():
    assert extract([-5, 0, 5], [-5, 10, 5], [[[0, 5, 5]]], [[8]])['path_count'] == 0


def test_recorded_entry_exit_transmissions_allow_interior_travel():
    assert extract([-5, 5, 5], [15, 5, 5], [[[0, 5, 5], [10, 5, 5]]], [[4, 4]])['path_count'] == 1
    assert extract([-5, 5, 5], [15, 5, 5], [[[0, 5, 5]]], [[4]])['path_count'] == 0


def test_courtyard_and_elevated_building_empty_space_is_not_occluded():
    building = block()
    building['holes_m'] = [[[3, 3], [3, 7], [7, 7], [7, 3]]]
    assert extract([4, 4, 2], [6, 6, 2], [[]], [[]], catalog_for(building))['path_count'] == 1
    building = block()
    building['base_m'] = 4
    assert extract([-5, 5, 2], [15, 5, 2], [[]], [[]], catalog_for(building))['path_count'] == 1


def test_invalid_strong_path_is_removed_before_power_reduction_for_every_receiver():
    catalog = catalog_for()
    face = wall_primitive(catalog)
    paths = Paths([[[0, 0, 5]], [[0, 5, 5]], [[0, 0, 5]], [[0, 5, 5]]],
                  [[1], [2], [1], [2]], [[face]] * 4, [100, 1, 100, 2], [0, 0, 1, 1])
    links = _extract_links(paths, {'id': 'BS', 'power_dbm': 0, 'position_m': [-5, -5, 5]},
        [[-5, 5, 5], [-5, 10, 5]], 20e6, False, [], catalog)
    assert [link['path_count'] for link in links] == [1, 1]
    assert links[0]['incoherent_dbm'] == pytest.approx(0)
    assert links[1]['incoherent_dbm'] == pytest.approx(20 * np.log10(2))
    assert all(link['geometry_filter']['rejected_path_count'] == 1 for link in links)


def test_large_scene_edge_diagnostics_remain_strict_json():
    import json
    building = block()
    building['footprint_m'] = [[x + 500, y] for x, y in building['footprint_m']]
    catalog = catalog_for(building)
    hit = _surface_hit(catalog, 42, wall_primitive(catalog))
    result = audit_path([[495, -5, 5], [500, 0, 5], [495, 5, 5]], ['specular'],
                        2 * np.sqrt(50) / 299792458 * 1e9, [hit])
    json.dumps(result, allow_nan=False)


def test_roof_edge_reflection_has_no_unique_normal():
    assert extract([5, -5, 15], [5, 5, 15], [[[5, 0, 10]]], [[1]])['path_count'] == 0


def test_continuous_occlusion_catches_a_millimetre_wall_in_a_kilometre_segment():
    building = block()
    building['footprint_m'] = [[0, 0], [.001, 0], [.001, 10], [0, 10]]
    assert extract([-500, 5, 5], [500, 5, 5], [[]], [[]], catalog_for(building))['path_count'] == 0


def test_float32_normal_error_does_not_block_a_valid_grazing_wall_reflection():
    building = block()
    building['footprint_m'] = [[500, 500], [510, 503], [507, 513], [497, 510]]
    point = np.array([506.5151515151515, 501.95454545454544, 5.])
    tangent = np.array([10., 3, 0]) / np.sqrt(109)
    normal = np.array([3., -10, 0]) / np.sqrt(109)
    source, receiver = point - 4 * tangent + .1 * normal, point + 4 * tangent + .1 * normal
    rounded = point.astype(np.float32).astype(float)
    assert extract(source.tolist(), receiver.tolist(), [[rounded.tolist()]], [[1]],
                   catalog_for(building))['path_count'] == 1


def test_native_diffuse_rounding_does_not_make_an_almost_tangent_segment_occluded():
    assert extract([-18, 16, 20], [-2, -148, 1.5], [[[0.0000076, 5, 5]]], [[2]])['path_count'] == 1


def test_a_roof_corner_is_an_edge_even_when_the_reported_triangle_only_touches_it():
    building = block()
    building['footprint_m'] = [[0, 0], [10, 0], [15, 5], [10, 10], [0, 10], [-5, 5]]
    catalog = catalog_for(building)
    vertices = [[-5, 10, 15], [0, 10, 10], [5, 10, 15]]
    hit = _surface_hit(catalog, 42, 3)
    assert not audit_path(vertices, ['specular'], 2 * np.sqrt(50) / 299792458 * 1e9,
                          [hit])['geometry_valid']
    paths = Paths([[[0, 10, 10]]], [[1]], [[3]])
    assert _extract_links(paths, {'id': 'BS', 'power_dbm': 0, 'position_m': vertices[0]},
                          [vertices[-1]], 20e6, False, [], catalog)[0]['path_count'] == 0


def test_a_finite_wedge_endpoint_is_not_first_order_edge_diffraction():
    assert extract([-5, 5, 15], [5, -5, 15], [[[0, 0, 10]]], [[8]])['path_count'] == 0


def test_endpoint_rounding_allowance_cannot_hide_a_concave_protrusion():
    building = block()
    building['footprint_m'] = [[0, 0], [10, 0], [10, 10], [0, 10],
                               [0, 4.9998], [-.01, 4.9998], [-.01, 4.9996], [0, 4.9996]]
    assert extract([-18, 16, 20], [-2, -148, 1.5], [[[0.0000076, 5, 5]]], [[2]],
                   catalog_for(building))['path_count'] == 0


def test_face_only_mode_excludes_roof_edge_diffraction():
    assert extract([5, -5, 8], [5, 5, 13], [[[5, 0, 10]]], [[8]])['path_count'] == 0


@pytest.mark.parametrize('code', [1, 2, 4, 8])
def test_all_interaction_types_are_excluded_near_a_building_corner(code):
    end = [15, .02, 5] if code == 4 else [-5, 5, 5]
    bends = [[[0, .02, 5], [10, .02, 5]]] if code == 4 else [[[0, .02, 5]]]
    result = extract([-5, .02, 5], end, bends, [[code] * len(bends[0])])
    assert result['path_count'] == 0


def test_corner_transmission_is_excluded_before_power_and_cfr_reduction():
    # A strong corner candidate must not contaminate a retained wall path.
    result = extract([-5, 5, 5], [15, 5, 5],
                     [[[0, 0, 5], [10, 10, 5]], [[0, 5, 5], [10, 5, 5]]],
                     [[4, 4], [4, 4]], coefficients=[100, 1])
    assert result['path_count'] == 1
    assert result['incoherent_dbm'] == pytest.approx(0)
    assert result['coherent_dbm'] == pytest.approx(0)
    assert result['geometry_filter']['rejected_by_reason']['interaction_on_edge'] == 1


def test_mixed_path_with_one_corner_event_is_rejected_as_a_whole():
    result = extract([-5, 5, 5], [15, -5, 5],
                     [[[0, 0, 5], [5, 0, 5], [10, 0, 5]]], [[8, 2, 4]])
    assert result['path_count'] == 0
    assert result['geometry_filter']['rejected_by_reason']['diffraction_disabled'] == 1


def test_all_face_mixed_transmission_and_diffuse_path_survives():
    result = extract([-5, 5, 5], [-5, 3.5, 5],
                     [[[0, 5, 5], [10, 5, 5], [0, 4, 5]]], [[4, 2, 4]])
    assert result['path_count'] == 1


def test_roof_exclusion_includes_nearby_edges_without_a_shared_triangle_vertex():
    building = block()
    building['footprint_m'] = [[0, 0], [.08, 0], [.08, 1], [.1, 1],
                               [.1, 3], [.12, 3], [.12, 5], [0, 5]]
    catalog = catalog_for(building)
    # The hit triangle touches x=0 but has no vertex on the nearer x=.1 edge.
    point = [.055, 2.3, 10]
    paths = Paths([[point]], [[1]], [[1]])
    link = _extract_links(paths, {'id': 'BS', 'power_dbm': 0, 'position_m': [.055, 2.3, 15]},
                          [[.055, 2.3, 16]], 20e6, False, [], catalog)[0]
    assert link['path_count'] == 0
    assert link['geometry_filter']['rejected_by_reason']['interaction_on_edge'] == 1
