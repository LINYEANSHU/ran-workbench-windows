from collections import Counter

import numpy as np
import pytest

from ran_workbench import geometry


def building():
    return {"id": "test", "footprint_m": [[0, 0], [8, 0], [8, 6], [4, 6], [4, 10], [0, 10]],
            "holes_m": [[[1, 1], [1, 3], [3, 3], [3, 1]]], "base_m": 7, "height_m": 12}


def test_concave_holed_mesh_is_closed_outward_and_has_expected_volume():
    mesh = geometry.building_mesh(building())
    vertices = np.asarray(mesh["vertices"])
    faces = np.asarray(mesh["faces"])
    assert vertices.shape == (20, 3)
    assert np.isfinite(vertices).all()
    edges = Counter(tuple(sorted((int(a), int(b)))) for f in faces for a, b in zip(f, np.roll(f, -1)))
    assert set(edges.values()) == {2}
    directed = Counter((int(a), int(b)) for f in faces for a, b in zip(f, np.roll(f, -1)))
    assert all(directed[(b, a)] == count for (a, b), count in directed.items())
    triangles = vertices[faces]
    volume = np.einsum("ij,ij->i", triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])).sum() / 6
    assert volume == pytest.approx(60 * 12)
    top = triangles[np.all(triangles[:, :, 2] == 19, axis=1)]
    assert np.cross(top[:, 1] - top[:, 0], top[:, 2] - top[:, 0])[:, 2].min() > 0
    assert np.cross(top[:, 1] - top[:, 0], top[:, 2] - top[:, 0])[:, 2].sum() / 2 == pytest.approx(60)


@pytest.mark.parametrize("point,want", [([0, 4, 7], True), ([2, 2, 9], False), ([1, 2, 9], True),
                                      ([6, 8, 9], False), ([2, 5, 19], True), ([2, 5, 19.001], False),
                                      ([2, 5, 6.999], False)])
def test_contains_point_respects_holes_concavity_base_and_closed_boundary(point, want):
    assert geometry.contains_point([building()], point) is want


@pytest.mark.parametrize("footprint,holes", [([[0, 0], [5, 5], [0, 5], [5, 0]], []),
    ([[0, 0], [5, 0], [5, 5], [0, 5]], [[[4, 4], [6, 4], [6, 6], [4, 6]]]),
    ([[0, 0], [5, 0], [5, 5], [0, 5]], [[[0, 1], [2, 1], [2, 3], [0, 3]]]),
    ([[0, 0], [1, 0], [2, 0]], []), ([[0, 0], [float("nan"), 0], [1, 1]], [])])
def test_mesh_rejects_invalid_polygon_and_holes(footprint, holes):
    with pytest.raises(ValueError):
        geometry.building_mesh(dict(building(), footprint_m=footprint, holes_m=holes))


def test_circle_footprint_has_requested_radius_and_mesh_volume():
    footprint = geometry.circle_footprint([4, -3], 5, segments=64)
    assert len(footprint) == 64
    assert all(np.linalg.norm(np.array(point) - [4, -3]) == pytest.approx(5) for point in footprint)
    mesh = geometry.building_mesh(dict(building(), footprint_m=footprint, holes_m=[]))
    vertices = np.asarray(mesh["vertices"])
    triangles = vertices[np.asarray(mesh["faces"])]
    volume = np.einsum("ij,ij->i", triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])).sum() / 6
    assert volume == pytest.approx(64 * 25 * np.sin(2 * np.pi / 64) / 2 * 12)


def test_collinear_outline_and_courtyard_vertices_do_not_open_mesh_edges():
    shape = dict(building(), footprint_m=[[0, 0], [4, 0], [8, 0], [8, 8], [0, 8]],
                 holes_m=[[[1, 1], [1, 3], [2, 3], [3, 3], [3, 1]]])
    mesh = geometry.building_mesh(shape)
    edges = Counter(tuple(sorted((int(a), int(b)))) for f in np.asarray(mesh["faces"])
                    for a, b in zip(f, np.roll(f, -1)))
    assert set(edges.values()) == {2}


def test_sampling_does_not_round_nonexact_large_coordinate_endpoint_into_grid():
    result = geometry.sample_points({"buildings": [], "sampling": {
        "min_m": [1e12, 0, 0], "max_m": [1e12 + 1, 0, 0], "spacing_m": [0.6, 1, 1], "limit": 5}})
    assert result["positions_m"][-1] == [1e12 + 0.6, 0, 0]


@pytest.mark.parametrize("radius,segments", [(0, 48), (float("inf"), 48), (1, 2), (1, 3.5)])
def test_circle_rejects_invalid_dimensions(radius, segments):
    with pytest.raises(ValueError):
        geometry.circle_footprint([0, 0], radius, segments)


def test_sampling_includes_exact_endpoints_and_excludes_closed_building_volume():
    project = {"buildings": [{"footprint_m": [[0, 0], [1, 0], [1, 1], [0, 1]],
                               "holes_m": [], "base_m": 1, "height_m": 1}],
               "sampling": {"min_m": [0, 0, 0], "max_m": [2, 2, 3], "spacing_m": [1, 1, 1], "limit": 50}}
    result = geometry.sample_points(project)
    assert result["requested"] == 36
    assert result["excluded"] == 8
    assert len(result["positions_m"]) == 28
    assert [2, 2, 3] in result["positions_m"]
    assert [0, 0, 0] in result["positions_m"]
    assert [1, 1, 2] not in result["positions_m"]


def test_sampling_float_steps_reach_exact_endpoint():
    result = geometry.sample_points({"buildings": [], "sampling": {"min_m": [0, 0, 0], "max_m": [0.3, 0, 0],
                 "spacing_m": [0.1, 1, 1], "limit": 5}})
    assert result["requested"] == 4
    assert result["positions_m"][-1] == [0.3, 0, 0]


@pytest.mark.parametrize("sampling", [
    {"min_m": [0, 0, 0], "max_m": [1e100, 1, 1], "spacing_m": [1e-300, 1, 1], "limit": 50000},
    {"min_m": [0, 0, 0], "max_m": [100, 100, 100], "spacing_m": [1, 1, 1], "limit": 50000},
    {"min_m": [0, 0, -1], "max_m": [1, 1, 1], "spacing_m": [1, 1, 1], "limit": 50},
    {"min_m": [2, 0, 0], "max_m": [1, 1, 1], "spacing_m": [1, 1, 1], "limit": 50},
    {"min_m": [0, 0, 0], "max_m": [1, 1, 1], "spacing_m": [0, 1, 1], "limit": 50}])
def test_bad_or_excessive_sampling_is_rejected_before_allocating(sampling, monkeypatch):
    def allocation_must_not_run(*args, **kwargs):
        raise AssertionError("allocated points before validating cap")

    monkeypatch.setattr(np, "empty", allocation_must_not_run)
    with pytest.raises(ValueError):
        geometry.sample_points({"buildings": [], "sampling": sampling})
