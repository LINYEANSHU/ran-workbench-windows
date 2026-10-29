"""Closed ENU building solids and bounded receiver sampling.

Exterior rings are counterclockwise and courtyard rings clockwise. Courtyards
remain empty, while all actual building faces belong to the closed solid.
"""

from __future__ import annotations

import math
from itertools import product

import numpy as np
from shapely import intersects_xy
from shapely.geometry import Point, Polygon
from shapely.validation import explain_validity


MAX_SAMPLE_POINTS = 250_000


def propagation_geometry(project: dict) -> dict:
    """One ground extent for native meshes, exports and the rendered scene.

    Receiver sampling bounds are not reflective domain walls. The current
    flat-ground assumption extends 100 m beyond all sampled/scene objects.
    """
    positions = [project['sampling']['min_m'], project['sampling']['max_m']]
    surface = project.get('surface_sampling', {})
    if surface.get('enabled'):
        radius, center = max(surface['radii_m']), surface['center_m']
        positions += [[center[0] - radius, center[1] - radius, 0],
                      [center[0] + radius, center[1] + radius, 0]]
    positions += [device['position_m'] for group in ('bs', 'ues') for device in project[group]]
    positions += [point for building in project['buildings'] for point in building['footprint_m']]
    return {'ground_bounds_m': {
        'min_m': [min(p[0] for p in positions) - 100, min(p[1] for p in positions) - 100, 0],
        'max_m': [max(p[0] for p in positions) + 100, max(p[1] for p in positions) + 100, 0]}}


def _number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"{label} must be a finite number") from None
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _vector(value, size: int, label: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise ValueError(f"{label} must contain {size} coordinates")
    return [_number(item, f"{label}[{index}]") for index, item in enumerate(value)]


def _integer(value, label: str, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} maximum is {maximum}")
    return value


def _ring(value, label: str, clockwise: bool) -> list[list[float]]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be a polygon ring")
    ring = [_vector(point, 2, label) for point in value]
    if len(ring) > 1 and ring[-1] == ring[0]:
        ring.pop()
    if len(ring) < 3 or len({tuple(point) for point in ring}) < 3:
        raise ValueError(f"{label} requires three distinct vertices")
    if len({tuple(point) for point in ring}) != len(ring):
        raise ValueError(f"{label} has repeated vertices")
    # Translate before the shoelace sum to reduce cancellation at large offsets.
    ox, oy = ring[0]
    area2 = math.fsum((a[0] - ox) * (b[1] - oy) - (b[0] - ox) * (a[1] - oy)
                      for a, b in zip(ring, ring[1:] + ring[:1]))
    if not math.isfinite(area2) or area2 == 0:
        raise ValueError(f"{label} polygon area must be positive and finite")
    # Earcut may omit straight-line intermediate vertices from its caps. Remove
    # only redundant points on a segment so walls and caps share identical edges.
    # Backtracking vertices are retained and rejected by polygon validity below.
    while len(ring) > 3:
        kept = []
        for index, middle in enumerate(ring):
            previous, following = ring[index - 1], ring[(index + 1) % len(ring)]
            ax, ay = middle[0] - previous[0], middle[1] - previous[1]
            bx, by = following[0] - middle[0], following[1] - middle[1]
            if ax * by - ay * bx == 0 and ax * bx + ay * by >= 0:
                continue
            kept.append(middle)
        if len(kept) == len(ring):
            break
        if len(kept) < 3:
            raise ValueError(f"{label} polygon requires three corners")
        ring = kept
    if (area2 < 0) != clockwise:
        # Keep the first vertex stable for editing and serialization.
        ring = ring[:1] + list(reversed(ring[1:]))
    return ring


def _normalize_building_geometry(building: dict) -> dict:
    if not isinstance(building, dict):
        raise ValueError("building must be an object")
    exterior = _ring(building.get("footprint_m"), "building footprint", clockwise=False)
    raw_holes = building.get("holes_m", [])
    if not isinstance(raw_holes, (list, tuple)):
        raise ValueError("building holes_m must be an array")
    holes = [_ring(ring, f"building hole {index + 1}", clockwise=True)
             for index, ring in enumerate(raw_holes)]
    polygon = Polygon(exterior, holes)
    if not polygon.is_valid:
        raise ValueError(f"Invalid building polygon: {explain_validity(polygon)}")
    if not math.isfinite(polygon.area) or polygon.area <= 0:
        raise ValueError("building polygon area must be positive and finite")
    shell = Polygon(exterior)
    for hole in holes:
        inner = Polygon(hole)
        if not shell.contains(inner) or not shell.boundary.disjoint(inner):
            raise ValueError("building hole must lie strictly inside the footprint")
    base = _number(building.get("base_m", 0), "building base_m")
    height = _number(building.get("height_m"), "building height_m")
    if height <= 0 or not math.isfinite(base + height) or base + height <= base:
        raise ValueError("building height_m must be positive with a finite distinct top")
    return {"footprint_m": exterior, "holes_m": holes, "base_m": base, "height_m": height}


def _solid(building: dict):
    normalized = _normalize_building_geometry(building)
    polygon = Polygon(normalized["footprint_m"], normalized["holes_m"])
    return normalized, polygon, normalized["base_m"], normalized["base_m"] + normalized["height_m"]


def building_mesh(building: dict) -> dict:
    """Triangulate a concave footprint with courtyards into an outward solid."""
    import mapbox_earcut

    normalized, _, base, top = _solid(building)
    rings = [normalized["footprint_m"], *normalized["holes_m"]]
    flat = [point for ring in rings for point in ring]
    xy = np.asarray(flat, dtype=np.float64)
    ends = np.cumsum([len(ring) for ring in rings], dtype=np.uint32)
    triangles = mapbox_earcut.triangulate_float64(xy, ends).reshape((-1, 3))
    if not len(triangles):
        raise ValueError("building polygon triangulation produced no faces")
    count = len(flat)
    vertices = [[x, y, base] for x, y in flat] + [[x, y, top] for x, y in flat]
    faces = []
    for raw in triangles:
        a, b, c = map(int, raw)
        pa, pb, pc = xy[[a, b, c]]
        cross = (pb[0] - pa[0]) * (pc[1] - pa[1]) - (pb[1] - pa[1]) * (pc[0] - pa[0])
        if cross == 0:
            continue
        if cross < 0:
            b, c = c, b
        faces.extend([[a, c, b], [a + count, b + count, c + count]])
    offset = 0
    for ring in rings:
        for index in range(len(ring)):
            a = offset + index
            b = offset + (index + 1) % len(ring)
            faces.extend([[a, b, b + count], [a, b + count, a + count]])
        offset += len(ring)
    return {"vertices": vertices, "faces": faces}


def contains_point(buildings: list[dict], position: list[float]) -> bool:
    """Return whether a point belongs to any closed building solid."""
    x, y, z = _vector(position, 3, "position_m")
    if not isinstance(buildings, (list, tuple)):
        raise ValueError("buildings must be an array")
    point = Point(x, y)
    for building in buildings:
        _, polygon, base, top = _solid(building)
        if base <= z <= top and polygon.covers(point):
            return True
    return False


def circle_footprint(center: list[float], radius: float, segments: int = 48) -> list[list[float]]:
    center = _vector(center, 2, "circle center")
    radius = _number(radius, "circle radius")
    if radius <= 0:
        raise ValueError("circle radius must be positive")
    segments = _integer(segments, "circle segments", 3, 4096)
    ring = [[center[0] + radius * math.cos(2 * math.pi * i / segments),
             center[1] + radius * math.sin(2 * math.pi * i / segments)] for i in range(segments)]
    return _ring(ring, "circle footprint", clockwise=False)


def _sampling_spec(sampling: dict) -> tuple[dict, list[int], int]:
    """Validate count before constructing any array of sample positions."""
    if not isinstance(sampling, dict):
        raise ValueError("sampling must be an object")
    lower = _vector(sampling.get("min_m"), 3, "sampling min_m")
    upper = _vector(sampling.get("max_m"), 3, "sampling max_m")
    spacing = _vector(sampling.get("spacing_m"), 3, "sampling spacing_m")
    limit = _integer(sampling.get("limit", MAX_SAMPLE_POINTS), "sampling limit", 1, MAX_SAMPLE_POINTS)
    if lower[2] < 0 or upper[2] < 0:
        raise ValueError("sampling z must be above or on ground (z >= 0)")
    counts = []
    requested = 1
    for low, high, step in zip(lower, upper, spacing):
        if high < low:
            raise ValueError("sampling max_m must be >= min_m on every axis")
        if step <= 0:
            raise ValueError("sampling spacing_m must be positive")
        ratio = (high - low) / step
        if not math.isfinite(ratio) or ratio >= limit:
            raise ValueError(f"sampling requested count exceeds limit {limit}")
        nearest = round(ratio)
        if math.isclose(ratio, nearest, rel_tol=0, abs_tol=1e-10 * max(1, abs(ratio))):
            ratio = nearest
        count = math.floor(ratio) + 1
        requested *= count
        if requested > limit:
            raise ValueError(f"sampling requested count {requested} exceeds limit {limit}")
        counts.append(count)
    return {"min_m": lower, "max_m": upper, "spacing_m": spacing, "limit": limit}, counts, requested


def sample_points(project: dict) -> dict:
    """Generate a bounded Cartesian grid, excluding all closed building solids."""
    if not isinstance(project, dict):
        raise ValueError("project must be an object")
    spec, counts, requested = _sampling_spec(project.get("sampling"))
    buildings = project.get("buildings", [])
    if not isinstance(buildings, list):
        raise ValueError("buildings must be an array")
    solids = [_solid(building) for building in buildings]
    axes = []
    for low, high, step, count in zip(spec["min_m"], spec["max_m"], spec["spacing_m"], counts):
        values = [low + index * step for index in range(count)]
        ratio = (high - low) / step
        if math.isclose(ratio, count - 1, rel_tol=0, abs_tol=1e-10 * max(1, abs(ratio))):
            values[-1] = high
        axes.append(values)
    positions = np.empty((requested, 3), dtype=np.float64)
    for index, coordinates in enumerate(product(*axes)):
        positions[index] = coordinates
    excluded = np.zeros(requested, dtype=bool)
    for _, polygon, base, top in solids:
        vertical = (positions[:, 2] >= base) & (positions[:, 2] <= top)
        excluded |= vertical & intersects_xy(polygon, positions[:, 0], positions[:, 1])
    return {"positions_m": positions[~excluded].tolist(), "requested": requested,
            "excluded": int(excluded.sum())}
