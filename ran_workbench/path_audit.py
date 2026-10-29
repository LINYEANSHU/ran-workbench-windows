"""Independent geometric diagnostics for exported Sionna path vertices.

Diagnostics never fabricate, snap, or silently remove a physical path. In
particular, diffuse scattering has no mirror-angle constraint. A thin slab
does preserve the ray direction. All checks use the actual intersected mesh
triangle, not the visually nearest building.
"""
from __future__ import annotations

import math

import numpy as np


def _segment_distance(point, a, b):
    edge = b - a
    length2 = float(edge @ edge)
    t = min(1.0, max(0.0, float((point - a) @ edge) / length2)) if length2 else 0.0
    return float(np.linalg.norm(point - a - t * edge))


def _triangle_distance(point, triangle):
    a, b, c = triangle
    n = np.cross(b - a, c - a)
    size = float(np.linalg.norm(n))
    if size == 0:
        return min(_segment_distance(point, a, b), _segment_distance(point, b, c),
                   _segment_distance(point, c, a))
    n /= size
    signed = float((point - a) @ n)
    projected = point - signed * n
    tests = [float(np.cross(v - u, projected - u) @ n)
             for u, v in ((a, b), (b, c), (c, a))]
    if min(tests) >= -1e-8:
        return abs(signed)
    return min(_segment_distance(point, a, b), _segment_distance(point, b, c),
               _segment_distance(point, c, a))


def audit_path(vertices, interaction_types, delay_ns, surfaces):
    points = np.asarray(vertices, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 2 or not np.isfinite(points).all():
        raise ValueError('Path vertices must be finite ENU triples')
    if len(interaction_types) != len(points) - 2 or len(surfaces) != len(interaction_types):
        raise ValueError('Each interior vertex needs exactly one interaction and surface')
    segments = np.diff(points, axis=0)
    lengths = np.linalg.norm(segments, axis=1)
    length = float(lengths.sum())
    delay_error = abs(float(delay_ns) * 1e-9 * 299_792_458 - length)
    warnings = []
    # Single precision RT positions/delays have errors proportional to scale.
    tolerance = max(0.03, length * 2e-5)
    if delay_error > tolerance:
        warnings.append('Reported delay does not match geometric path length')
    if np.any(lengths < 0.005):
        warnings.append('Path contains a near-zero-length segment')
    result = []
    for index, (kind, surface) in enumerate(zip(interaction_types, surfaces), 1):
        record = {key: value for key, value in surface.items() if key not in ('triangle_m', 'physical_edges_m')}
        record.update(type=kind, position_m=points[index].tolist())
        if kind == 'diffraction':
            warnings.append(f'Bend {index}: diffraction is disabled by the face-only propagation policy')
        if 'physical_edges_m' in surface:
            from .path_geometry import edge_distances
            edges = surface['physical_edges_m']
            distances = edge_distances(points[index:index + 1], edges)
            clearance = float(distances.min()) if len(edges) else None
            record['edge_clearance_m'] = clearance
            edge_tolerance = surface.get('position_tolerance_m', 0.0001)
            from .path_geometry import FACE_EDGE_EXCLUSION_M
            if clearance is not None and clearance <= max(edge_tolerance, FACE_EDGE_EXCLUSION_M):
                warnings.append(f'Bend {index}: interaction lies within the excluded physical-edge band')
            if kind in ('specular', 'diffuse') and clearance is not None and clearance <= edge_tolerance:
                warnings.append(f'Bend {index}: reflection lies on a physical edge without a unique surface normal')
            if kind == 'diffraction':
                interior = edge_distances(points[index:index + 1], edges, interior_margin=edge_tolerance)
                if not len(edges) or float(interior.min()) > edge_tolerance:
                    warnings.append(f'Bend {index}: diffraction is not on a physical mesh edge')
                else:
                    record['edge_m'] = np.asarray(edges[int(interior.argmin())], dtype=float).tolist()
        triangle = surface.get('triangle_m')
        if triangle is None:
            record['surface_error_m'] = None
            warnings.append(f'Bend {index}: intersected mesh triangle is unavailable')
        else:
            triangle = np.asarray(triangle, dtype=float)
            error = _triangle_distance(points[index], triangle)
            record['surface_error_m'] = error
            if error > tolerance:
                warnings.append(f'Bend {index}: interaction is outside the reported triangle')
            if kind == 'diffraction':
                edge_error = min(_segment_distance(points[index], a, b)
                                 for a, b in zip(triangle, np.roll(triangle, -1, axis=0)))
                record['edge_error_m'] = edge_error
                if edge_error > tolerance:
                    warnings.append(f'Bend {index}: diffraction vertex is not on a triangle edge')
        if lengths[index - 1] > 0 and lengths[index] > 0:
            incoming = segments[index - 1] / lengths[index - 1]
            outgoing = segments[index] / lengths[index]
            n = np.asarray(surface.get('normal', [0, 0, 0]), dtype=float)
            normal_size = float(np.linalg.norm(n))
            if normal_size:
                n /= normal_size
                if kind in ('specular', 'diffuse') and float((-incoming @ n) * (outgoing @ n)) < -1e-5:
                    warnings.append(f'Bend {index}: reflection crosses to the opposite surface hemisphere')
                if kind == 'specular':
                    reflected = incoming - 2 * float(incoming @ n) * n
                    angular_error = math.degrees(math.acos(float(np.clip(reflected @ outgoing, -1, 1))))
                    record['reflection_error_deg'] = angular_error
                    if angular_error > 0.5:
                        warnings.append(f'Bend {index}: specular reflection violates the mirror law')
            if kind == 'transmission':
                angular_error = math.degrees(math.acos(float(np.clip(incoming @ outgoing, -1, 1))))
                record['transmission_error_deg'] = angular_error
                if angular_error > 0.5:
                    warnings.append(f'Bend {index}: thin-layer transmission unexpectedly changes direction')
        result.append(record)
    return {'length_m': length, 'delay_length_error_m': delay_error,
            'geometry_valid': not warnings, 'geometry_warnings': warnings,
            'interactions': result}
