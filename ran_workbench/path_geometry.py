"""Independent finite-solid visibility and physical-crease checks.

This gate processes Sionna's sparse path buffer before any RF reduction. It
never moves a vertex or changes an interaction type. Float32 uncertainty is
measured normal to the hit surface and bounded by the local finite feature;
the rest of each segment is tested continuously, without sampling along it.
"""
from __future__ import annotations

import numpy as np
import shapely
from shapely.geometry import Polygon


# A deliberate model exclusion band, separate from float32 position tolerance.
FACE_EDGE_EXCLUSION_M = 0.05
FACE_ONLY_WARNING = ('Face-only propagation: interactions within 0.05 m '
                     'of physical edges or vertices are excluded. This is a model restriction, not an accuracy guarantee.')
REJECTION_REASONS = {1: 'reflection_on_edge', 2: 'invalid_diffraction_edge', 4: 'occluded',
                     8: 'interaction_on_edge', 16: 'diffraction_disabled', 32: 'invalid_surface_hit'}


def mesh_topology(vertices, faces):
    """Find physical edges incident to or within the exclusion band of a face.

    A narrow/concave cap can approach a boundary without sharing its vertices.
    Include those finite edges too, excluding coplanar triangle seams.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    triangles = vertices[faces]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    sizes = np.linalg.norm(normals, axis=1)
    normals /= np.where(sizes > 0, sizes, 1)[:, None]
    edges = {}
    for index, triangle in enumerate(triangles):
        for a, b in zip(triangle, np.roll(triangle, -1, axis=0)):
            # Weld equal coordinates as PLY loaders may split normal vertices.
            key = tuple(sorted((tuple(a), tuple(b))))
            edges.setdefault(key, []).append(index)
    incident, physical_edges = {}, []
    for edge, adjacent in edges.items():
        if len(adjacent) == 2 and float(normals[adjacent[0]] @ normals[adjacent[1]]) > 1 - 1e-7:
            continue
        physical_edges.append(edge)
        for vertex in edge:
            incident.setdefault(vertex, set()).add(edge)
    # At a cap vertex the native hit may name a triangle touching a crease only
    # at that vertex. Include vertex incidence so tie-breaking cannot hide it.
    all_edges = np.asarray(physical_edges, dtype=float).reshape(-1, 2, 3)
    edge_low, edge_high = all_edges.min(axis=1), all_edges.max(axis=1)
    margin = max(FACE_EDGE_EXCLUSION_M, position_tolerance(vertices))
    physical = []
    for triangle in triangles:
        local = set().union(*(incident.get(tuple(vertex), set()) for vertex in triangle))
        nearby = np.all(edge_high >= triangle.min(axis=0) - margin, axis=1)
        nearby &= np.all(edge_low <= triangle.max(axis=0) + margin, axis=1)
        candidates = np.flatnonzero(nearby)
        close = _edge_triangle_distances(all_edges[candidates], triangle) <= margin
        local.update(physical_edges[index] for index in candidates[close])
        physical.append(sorted(local))
    return physical


def _edge_triangle_distances(edges, triangle):
    """Continuous finite segment/triangle distance for topology broad-phase hits."""
    if not len(edges):
        return np.empty(0)
    start, end = edges[:, 0], edges[:, 1]
    delta = end - start
    distance = np.minimum(_triangle_distances(start, triangle), _triangle_distances(end, triangle))
    distance = np.minimum(distance, edge_distances(triangle, edges).min(axis=0))
    length2 = np.sum(delta * delta, axis=1)
    for a, b in zip(triangle, np.roll(triangle, -1, axis=0)):
        u, w = b - a, a - start
        aa = float(u @ u)
        bb, dd, ee = delta @ u, w @ u, np.sum(delta * w, axis=1)
        denominator = aa * length2 - bb * bb
        nonparallel = denominator > 16 * np.finfo(float).eps * aa * length2
        s = np.divide(bb * ee - length2 * dd, denominator,
                      out=np.zeros_like(bb), where=nonparallel)
        t = np.divide(aa * ee - bb * dd, denominator,
                      out=np.zeros_like(bb), where=nonparallel)
        interior = nonparallel & (s >= 0) & (s <= 1) & (t >= 0) & (t <= 1)
        distance = np.minimum(distance, np.where(interior,
            np.linalg.norm(w + s[:, None] * u - t[:, None] * delta, axis=1), np.inf))
    normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
    denominator = delta @ normal
    crossing = np.divide((triangle[0] - start) @ normal, denominator,
                         out=np.full(len(edges), -1.), where=denominator != 0)
    inside = (crossing >= 0) & (crossing <= 1)
    distance = np.minimum(distance, np.where(inside,
        _triangle_distances(start + crossing[:, None] * delta, triangle), np.inf))
    return distance


def solid_geometry(building):
    # Match the coordinates of the actual float32 native mesh, rather than
    # compare its hits against a slightly different double-precision footprint.
    rings = [building['footprint_m'], *building.get('holes_m', [])]
    rings = [np.asarray(ring, dtype=np.float32).astype(float) for ring in rings]
    return dict(polygon=Polygon(rings[0], rings[1:]),
                base=float(np.float32(building.get('base_m', 0))),
                top=float(np.float32(building.get('base_m', 0) + building['height_m'])))


def position_tolerance(vertices):
    scale = max(1., float(np.max(np.abs(vertices))))
    return float(max(0.0001, 4 * np.finfo(np.float32).eps * scale))


def edge_distances(points, edges, interior_margin=None):
    """Distances from points to a small list of finite physical edges."""
    points = np.asarray(points, dtype=float)
    if not len(edges):
        return np.full((len(points), 0), np.inf)
    edges = np.asarray(edges, dtype=float)
    a, delta = edges[:, 0], edges[:, 1] - edges[:, 0]
    fraction = np.clip(np.einsum('nej,ej->ne', points[:, None] - a, delta)
                       / np.sum(delta * delta, axis=1), 0, 1)
    distances = np.linalg.norm(points[:, None] - a - fraction[:, :, None] * delta, axis=2)
    if interior_margin is not None:
        length = np.linalg.norm(delta, axis=1)
        endpoint = (fraction * length <= interior_margin) | ((1 - fraction) * length <= interior_margin)
        distances[endpoint] = np.inf
    return distances


def _triangle_distances(points, triangle):
    # Either one fixed triangle or a different triangle for every point.
    a, b, c = triangle[..., 0, :], triangle[..., 1, :], triangle[..., 2, :]
    n = np.cross(b - a, c - a)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    signed = np.sum((points - a) * n, axis=-1)
    projected = points - signed[:, None] * n
    inside = np.ones(len(points), dtype=bool)
    distances = np.full(len(points), np.inf)
    for u, v in ((a, b), (b, c), (c, a)):
        edge = v - u
        inside &= np.sum(np.cross(edge, projected - u) * n, axis=-1) >= -1e-10
        fraction = np.clip(np.sum((points - u) * edge, axis=-1) / np.sum(edge * edge, axis=-1), 0, 1)
        distances = np.minimum(distances, np.linalg.norm(points - u - fraction[:, None] * edge, axis=1))
    return np.where(inside, np.abs(signed), distances)


def _endpoint_trim(points, direction, hit_objects, hit_primitives, object_id, obj, trim):
    """Exclude only rounding at a declared finite hit, including grazing rays.

    A tiny normal error can extend far along a grazing ray. Solve the local
    supporting-plane crossing instead of using a larger global epsilon. Cap
    the allowance by distance to every other nonincident, noncoplanar face so
    it cannot jump over a concave protrusion or an opposite thin wall.
    """
    result = trim.copy()
    tolerance = obj['position_tolerance_m']
    triangles, normals = obj['triangles'], obj['normals']
    own_hit = (hit_objects == object_id) & (hit_primitives >= 0) & (hit_primitives < len(triangles))
    indices = np.flatnonzero(own_hit)
    if not len(indices):
        return result
    # Batch all hit primitives together. Grouping by primitive and looping over
    # every face in each group made complex footprints incur quadratic Python
    # loop overhead even with just one sample per wall.
    p, d = points[indices], direction[indices]
    main = triangles[hit_primitives[indices].astype(int)]
    main_n = normals[hit_primitives[indices].astype(int)]
    main_error = np.sum((p - main[:, 0]) * main_n, axis=1)
    on_surface = _triangle_distances(p, main) <= tolerance
    wanted = trim[indices].copy()
    clearance = np.full(len(indices), np.inf)
    for triangle, n in zip(triangles, normals):
        error = (p - triangle[0]) @ n
        distance = _triangle_distances(p, triangle)
        incident = distance <= tolerance
        denominator = d @ n
        crossing = np.divide(-error, denominator, out=np.zeros_like(error), where=denominator != 0)
        local = incident & (np.abs(error) <= tolerance) & (denominator != 0) & (crossing >= 0)
        wanted = np.maximum(wanted, np.where(local, crossing + trim[indices], 0))
        coplanar = ((np.abs(main_n @ n) > 1 - 1e-7)
                    & (np.abs(np.sum((triangle[0] - main[:, 0]) * main_n, axis=1)) <= tolerance))
        clearance = np.minimum(clearance, np.where(incident | coplanar, np.inf, distance))
    safe = on_surface & (np.abs(main_error) <= tolerance)
    safe &= wanted * np.linalg.norm(d, axis=1) < clearance - tolerance
    result[indices] = np.where(safe, wanted, trim[indices])
    return result


def _planar_test(polygon, a, b, interior):
    lines = shapely.linestrings(np.stack((a[:, :2], b[:, :2]), axis=1))
    result = shapely.covered_by(lines, polygon) if interior else shapely.intersects(lines, polygon)
    vertical = np.all(a[:, :2] == b[:, :2], axis=1)
    if np.any(vertical):
        result[vertical] = shapely.intersects_xy(polygon, a[vertical, 0], a[vertical, 1])
    return result


def _segment_inconsistent(a, b, inside, solid, tolerance, low=None, high=None):
    """Whether an open 3-D segment disagrees with its recorded medium.

    Clip analytically to the building's height interval, then use GEOS polygon
    intersection/coverage. Concavities, holes, roof crossings, vertical rays
    and arbitrarily narrow walls are handled continuously. A tangential touch
    away from a recorded endpoint is blocked too.
    """
    delta = b - a
    lengths = np.linalg.norm(delta, axis=1)
    trim = np.minimum(0.25, tolerance / np.maximum(lengths, tolerance))
    low = trim.copy() if low is None else low
    high = 1 - trim if high is None else high
    base, top, polygon = solid['base'], solid['top'], solid['polygon']
    bad = (lengths <= 2 * tolerance) | (low >= high)
    if np.any(inside):
        indices = np.flatnonzero(inside)
        first = a[indices] + low[indices, None] * delta[indices]
        last = a[indices] + high[indices, None] * delta[indices]
        covered = _planar_test(polygon, first, last, True)
        covered &= (np.minimum(first[:, 2], last[:, 2]) >= base)
        covered &= (np.maximum(first[:, 2], last[:, 2]) <= top)
        bad[indices] |= ~covered
    outside = ~inside
    dz = delta[:, 2]
    sloping = dz != 0
    z0 = np.divide(base - a[:, 2], dz, out=np.zeros_like(dz), where=sloping)
    z1 = np.divide(top - a[:, 2], dz, out=np.ones_like(dz), where=sloping)
    low = np.where(sloping, np.maximum(low, np.minimum(z0, z1)), low)
    high = np.where(sloping, np.minimum(high, np.maximum(z0, z1)), high)
    candidate = outside & (low <= high) & (sloping | ((a[:, 2] >= base) & (a[:, 2] <= top)))
    # Broad phase avoids constructing GEOS lines for distant buildings.
    x0, y0, x1, y1 = polygon.bounds
    candidate &= (np.maximum(a[:, 0], b[:, 0]) >= x0) & (np.minimum(a[:, 0], b[:, 0]) <= x1)
    candidate &= (np.maximum(a[:, 1], b[:, 1]) >= y0) & (np.minimum(a[:, 1], b[:, 1]) <= y1)
    indices = np.flatnonzero(candidate)
    if len(indices):
        first = a[indices] + low[indices, None] * delta[indices]
        last = a[indices] + high[indices, None] * delta[indices]
        bad[indices] |= _planar_test(polygon, first, last, False)
    return bad


def validate_sparse_paths(buffer, source, receivers, receiver_indices, catalog):
    """Return rejection bits for every sparse path, with no padded RX tensors."""
    codes = np.asarray(buffer.interaction_types)
    objects = np.asarray(buffer.shapes)
    primitives = np.asarray(buffer.primitives)
    vertices = np.stack([np.asarray(buffer.vertices_x), np.asarray(buffer.vertices_y),
                         np.asarray(buffer.vertices_z)], axis=-1).astype(float)
    count, depth = codes.shape
    rejected = np.zeros(count, dtype=np.uint8)
    rejected[np.any(codes == 8, axis=1)] |= 16
    known_hits = np.zeros(codes.shape, dtype=bool)
    targets = np.asarray(receivers, dtype=float)[receiver_indices]
    for object_id, obj in catalog.items():
        tolerance = obj['position_tolerance_m']
        hits = (objects == object_id) & (codes != 0)
        known_hits |= hits
        for primitive in np.unique(primitives[hits]):
            rows, columns = np.nonzero(hits & (primitives == primitive))
            if not 0 <= int(primitive) < len(obj['faces']):
                rejected[rows] |= 32
                continue
            edges = obj['physical_edges'][int(primitive)]
            distances = edge_distances(vertices[rows, columns], edges)
            nearest = distances.min(axis=1) if len(edges) else np.full(len(rows), np.inf)
            kinds = codes[rows, columns]
            on_edge = nearest <= max(tolerance, FACE_EDGE_EXCLUSION_M)
            np.bitwise_or.at(rejected, rows[on_edge], 8)
            on_face = _triangle_distances(vertices[rows, columns], obj['triangles'][int(primitive)]) <= tolerance
            np.bitwise_or.at(rejected, rows[~on_face], 32)
            reflection = (kinds == 1) | (kinds == 2)
            np.bitwise_or.at(rejected, rows[reflection & (nearest <= tolerance)], 1)
            edge_interior = edge_distances(vertices[rows, columns], edges, interior_margin=tolerance)
            wedge_distance = edge_interior.min(axis=1) if len(edges) else np.full(len(rows), np.inf)
            np.bitwise_or.at(rejected, rows[(kinds == 8) & (wedge_distance > tolerance)], 2)
        solid = obj.get('solid')
        if solid is None:
            continue
        # Sources/receivers have already been validated as outdoor points.
        # Only explicitly recorded transmission events switch the solid medium.
        inside = np.zeros(count, dtype=bool)
        previous = np.broadcast_to(np.asarray(source, dtype=float), (count, 3)).copy()
        previous_object = np.full(count, -1, dtype=np.int64)
        previous_primitive = np.full(count, -1, dtype=np.int64)

        def check_segments(indices, target, end_objects, end_primitives):
            a, b = previous[indices], target
            delta = b - a
            length = np.linalg.norm(delta, axis=1)
            trim = np.minimum(.25, tolerance / np.maximum(length, tolerance))
            low = _endpoint_trim(a, delta, previous_object[indices], previous_primitive[indices], object_id, obj, trim)
            high = 1 - _endpoint_trim(b, -delta, end_objects, end_primitives, object_id, obj, trim)
            return _segment_inconsistent(a, b, inside[indices], solid, tolerance, low, high)

        for d in range(depth):
            active = np.flatnonzero(codes[:, d] != 0)
            if not len(active):
                continue
            bad = check_segments(active, vertices[active, d], objects[active, d], primitives[active, d])
            rejected[active[bad]] |= 4
            inside ^= (codes[:, d] == 4) & (objects[:, d] == object_id)
            previous[active] = vertices[active, d]
            previous_object[active] = objects[active, d]
            previous_primitive[active] = primitives[active, d]
        no_hit = np.full(count, -1, dtype=np.int64)
        bad = check_segments(np.arange(count), targets, no_hit, no_hit)
        rejected[bad | inside] |= 4
    rejected[np.any((codes != 0) & ~known_hits, axis=1)] |= 32
    return rejected
