"""Bounded geodesic measurement surfaces; no RF calculation on the CPU."""
from functools import lru_cache
import math

import numpy as np
from shapely.geometry import LineString, Point, Polygon

from .geometry import MAX_SAMPLE_POINTS, _integer, _number, _solid, _vector


def normalize_surface_sampling(value, base_stations):
    if not isinstance(value, dict):
        raise ValueError('surface_sampling must be an object')
    enabled = value.get('enabled', False)
    if type(enabled) is not bool:
        raise ValueError('surface_sampling enabled must be boolean')
    center_id = value.get('center_bs_id')
    if center_id is not None:
        source = next((bs for bs in base_stations if bs['id'] == center_id), None)
        if source is None:
            raise ValueError('surface_sampling center_bs_id does not exist')
        center = list(source['position_m'])
    else:
        center = _vector(value.get('center_m', [0, 0, 25]), 3, 'surface center_m')
    if center[2] < 0:
        raise ValueError('surface center must be above ground')
    radii = value.get('radii_m', [25, 50, 75, 100])
    if not isinstance(radii, list) or not 1 <= len(radii) <= 16:
        raise ValueError('surface radii_m requires 1..16 radii')
    radii = [_number(radius, 'surface radius') for radius in radii]
    if any(radius <= 0 for radius in radii) or any(a >= b for a, b in zip(radii, radii[1:])):
        raise ValueError('surface radii must be positive, unique and ascending')
    if max(abs(c) for c in center) + max(radii) > 10_000_000:
        raise ValueError('surface exceeds the bounded local ENU coordinate range')
    level = _integer(value.get('subdivisions', 3), 'surface subdivisions', 0, 6)
    if 20 * 4**level * len(radii) > MAX_SAMPLE_POINTS:
        raise ValueError(f'surface requested cell count exceeds limit {MAX_SAMPLE_POINTS}')
    return dict(enabled=enabled, center_m=center, center_bs_id=center_id, radii_m=radii,
                subdivisions=level,
                replicates=_integer(value.get('replicates', 2), 'surface replicates', 1, 8),
                ray_chunk_size=_integer(value.get('ray_chunk_size', 1_000_000),
                                        'surface ray_chunk_size', 1024, 2_000_000))


@lru_cache(maxsize=7)
def _unit_sphere(level):
    phi = (1 + math.sqrt(5)) / 2
    vertices = [np.array(v, dtype=float) for v in [
        (-1, phi, 0), (1, phi, 0), (-1, -phi, 0), (1, -phi, 0),
        (0, -1, phi), (0, 1, phi), (0, -1, -phi), (0, 1, -phi),
        (phi, 0, -1), (phi, 0, 1), (-phi, 0, -1), (-phi, 0, 1)]]
    vertices = [v / np.linalg.norm(v) for v in vertices]
    faces = [(0,11,5),(0,5,1),(0,1,7),(0,7,10),(0,10,11),(1,5,9),(5,11,4),
             (11,10,2),(10,7,6),(7,1,8),(3,9,4),(3,4,2),(3,2,6),(3,6,8),
             (3,8,9),(4,9,5),(2,4,11),(6,2,10),(8,6,7),(9,8,1)]
    for _ in range(level):
        midpoints, refined = {}, []
        def midpoint(a, b):
            key = tuple(sorted((a, b)))
            if key not in midpoints:
                point = vertices[a] + vertices[b]
                midpoints[key] = len(vertices)
                vertices.append(point / np.linalg.norm(point))
            return midpoints[key]
        for a, b, c in faces:
            ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
            refined.extend(((a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)))
        faces = refined
    vertices, faces = np.asarray(vertices), np.asarray(faces, dtype=np.int32)
    vertices.flags.writeable = faces.flags.writeable = False
    return vertices, faces


def geodesic_sphere(center_m, radius_m, subdivisions):
    center = _vector(center_m, 3, 'sphere center')
    radius = _number(radius_m, 'sphere radius')
    if radius <= 0:
        raise ValueError('sphere radius must be positive')
    level = _integer(subdivisions, 'sphere subdivisions', 0, 6)
    vertices, faces = _unit_sphere(level)
    return {'vertices_m': (vertices * radius + center).tolist(), 'faces': faces.tolist()}


def _clip_height(vertices, height, above):
    """Clip a convex 3-D polygon against one horizontal plane."""
    if not len(vertices):
        return []
    result = []
    previous = vertices[-1]
    previous_inside = previous[2] >= height if above else previous[2] <= height
    for current in vertices:
        inside = current[2] >= height if above else current[2] <= height
        if inside != previous_inside:
            fraction = (height - previous[2]) / (current[2] - previous[2])
            result.append(previous + fraction * (current - previous))
        if inside:
            result.append(current)
        previous, previous_inside = current, inside
    return result


def _outdoor_faces(vertices, faces, buildings):
    triangles = vertices[faces]
    lower, upper = triangles.min(axis=1), triangles.max(axis=1)
    excluded = lower[:, 2] < 0
    ground_count = int(excluded.sum())
    for _, footprint, base, top in [_solid(building) for building in buildings]:
        xmin, ymin, xmax, ymax = footprint.bounds
        candidates = np.flatnonzero(~excluded & (lower[:, 0] <= xmax) & (upper[:, 0] >= xmin)
            & (lower[:, 1] <= ymax) & (upper[:, 1] >= ymin)
            & (lower[:, 2] <= top) & (upper[:, 2] >= base))
        for index in candidates:
            clipped = _clip_height(_clip_height(triangles[index], base, True), top, False)
            if not clipped:
                continue
            projected = np.asarray(clipped)[:, :2]
            if len(projected) >= 3:
                shape = Polygon(projected)
                if shape.area == 0:
                    shape = LineString(projected)
            elif len(projected) == 2:
                shape = LineString(projected)
            else:
                shape = Point(projected[0])
            if footprint.intersects(shape):
                excluded[index] = True
    return ~excluded, ground_count, int(excluded.sum()) - ground_count


def sample_surfaces(project):
    spec = normalize_surface_sampling(project['surface_sampling'], project['bs'])
    output = {'points': [], 'meshes': [], 'requested': 0, 'excluded': 0,
              'below_ground_cells': 0, 'building_cells': 0}
    for radius in spec['radii_m']:
        mesh = geodesic_sphere(spec['center_m'], radius, spec['subdivisions'])
        vertices, faces = np.asarray(mesh['vertices_m']), np.asarray(mesh['faces'], dtype=np.int32)
        keep, ground, buildings = _outdoor_faces(vertices, faces, project['buildings'])
        output['requested'] += len(faces)
        output['excluded'] += int((~keep).sum())
        output['below_ground_cells'] += ground
        output['building_cells'] += buildings
        indices = np.flatnonzero(keep)
        faces = faces[keep]
        triangles = vertices[faces]
        centers = triangles.mean(axis=1)
        areas = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0],
                                       triangles[:, 2] - triangles[:, 0]), axis=1) / 2
        first = len(output['points'])
        for cell_id, position, area in zip(indices, centers, areas):
            output['points'].append(dict(position_m=position.tolist(), links=[], sample_kind='surface_cell',
                radius_m=radius, cell_id=int(cell_id), area_m2=float(area)))
        output['meshes'].append(dict(radius_m=radius, vertices_m=mesh['vertices_m'], faces=faces.tolist(),
                                    point_indices=list(range(first, len(output['points'])))))
    return output


def ray_chunks(total, maximum):
    total = _integer(total, 'total rays', 1)
    maximum = _integer(maximum, 'ray chunk', 1)
    while total:
        count = min(total, maximum)
        yield count
        total -= count


def weighted_gain_mean(gains, counts):
    """Linear estimator; also works on Dr.Jit GPU arrays without copying them."""
    if not gains or len(gains) != len(counts) or any(count <= 0 for count in counts):
        raise ValueError('weighted gains require matching positive sample counts')
    total = sum(counts)
    result = gains[0] * (counts[0] / total)
    for gain, count in zip(gains[1:], counts[1:]):
        result = result + gain * (count / total)
    return result


def launch_rotation(seed):
    """Haar-uniform SO(3) rotation for randomized Fibonacci quadrature.

    Uniform Euler angles are not uniform orientations. Shoemake's unit
    quaternion construction preserves solid-angle uniformity instead.
    """
    u, v, t = np.random.default_rng(seed).random(3)
    x, y = math.sqrt(1 - u) * math.sin(2 * math.pi * v), math.sqrt(1 - u) * math.cos(2 * math.pi * v)
    z, w = math.sqrt(u) * math.sin(2 * math.pi * t), math.sqrt(u) * math.cos(2 * math.pi * t)
    return np.array([
        [1 - 2*(y*y + z*z), 2*(x*y - z*w), 2*(x*z + y*w)],
        [2*(x*y + z*w), 1 - 2*(x*x + z*z), 2*(y*z - x*w)],
        [2*(x*z - y*w), 2*(y*z + x*w), 1 - 2*(x*x + y*y)]])
