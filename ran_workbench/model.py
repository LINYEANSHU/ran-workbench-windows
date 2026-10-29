"""Validated, serializable scene projects. No GUI or GPU initialization here."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import tempfile


_SOLVER_DEFAULTS = {"max_depth": 3, "samples_per_src": 1_000_000, "batch_size": 256,
                    "diffuse_reflection": False, "refraction": False,
                    "scattering_coefficient": 0.1, "seed": 42, "los": True,
                    "specular_reflection": True}
_SAMPLING_DEFAULTS = {"min_m": [-160, -120, 1.5], "max_m": [160, 120, 81.5],
                      "spacing_m": [10, 10, 10], "limit": 250_000}


def _copy_json(value, label="project"):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label} must contain finite numbers only")
        return value
    if isinstance(value, (list, tuple)):
        return [_copy_json(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"{label} must have string object keys")
        return {key: _copy_json(item, f"{label}.{key}") for key, item in value.items()}
    raise ValueError(f"{label} contains a non-JSON value")


def _text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def default_project() -> dict:
    """Return the six clearly labelled synthetic blocks, with outdoor devices."""
    blocks = [(-60, 22, 36, 44, 42, "concrete"), (0, 22, 38, 44, 64, "concrete"),
              (65, 22, 42, 44, 52, "glass"), (-55, -72, 42, 36, 32, "concrete"),
              (12, -72, 36, 36, 48, "concrete"), (82, -72, 42, 36, 72, "glass")]
    buildings = []
    for index, (x, y, width, depth, height, material) in enumerate(blocks, 1):
        buildings.append({"id": f"B-{index:02d}", "name": f"建筑 {index}",
                          "footprint_m": [[x - width / 2, y - depth / 2], [x + width / 2, y - depth / 2],
                                          [x + width / 2, y + depth / 2], [x - width / 2, y + depth / 2]],
                          "holes_m": [], "base_m": 0, "height_m": height, "material": material,
                          "thickness_m": 0.2, "source": {"kind": "synthetic", "material_assumed": True}})
    return validate_project({"schema_version": 2, "name": "合成街区",
                             "origin": {"longitude_deg": 114.165, "latitude_deg": 22.295, "ellipsoid_height_m": 0},
                             "frequency_hz": 3_500_000_000, "bandwidth_hz": 20_000_000,
                             "buildings": buildings,
                             "bs": [{"id": "BS-01", "name": "BS 1", "position_m": [-115, -55, 25],
                                     "power_dbm": 30, "orientation_deg": [0, 0, 0], "pattern": "iso"}],
                             "ues": [{"id": "UE-01", "name": "UE 1", "position_m": [0, -20, 1.5]}],
                             "sampling": _SAMPLING_DEFAULTS, "solver": _SOLVER_DEFAULTS})


def validate_project(project: dict) -> dict:
    """Return a normalized deep copy or raise ValueError before scene creation."""
    from .geometry import _integer, _normalize_building_geometry, _number, _sampling_spec, _vector, contains_point

    if not isinstance(project, dict):
        raise ValueError("project must be an object")
    result = _copy_json(project)
    if type(result.get("schema_version")) is not int or result["schema_version"] != 2:
        raise ValueError("project schema_version must be 2")
    result["name"] = _text(result.get("name"), "project name")
    origin = result.get("origin")
    if not isinstance(origin, dict):
        raise ValueError("project origin must be an object")
    for key, minimum, maximum in [("longitude_deg", -180, 180), ("latitude_deg", -90, 90)]:
        origin[key] = _number(origin.get(key), f"origin {key}")
        if not minimum <= origin[key] <= maximum:
            raise ValueError(f"origin {key} is out of range")
    origin["ellipsoid_height_m"] = _number(origin.get("ellipsoid_height_m", 0), "origin ellipsoid_height_m")
    for key in ("frequency_hz", "bandwidth_hz"):
        result[key] = _number(result.get(key), key)
        if result[key] <= 0:
            raise ValueError(f"{key} must be positive")
    identifiers = set()
    for group, maximum in (("buildings", None), ("bs", 5), ("ues", 64)):
        objects = result.setdefault(group, [])
        if not isinstance(objects, list):
            raise ValueError(f"{group} must be an array")
        if maximum is not None and len(objects) > maximum:
            raise ValueError(f"{group} maximum count is {maximum}")
        for item in objects:
            if not isinstance(item, dict):
                raise ValueError(f"{group} entries must be objects")
            item["id"] = _text(item.get("id"), f"{group} id")
            if item["id"] in identifiers:
                raise ValueError(f"duplicate ID {item['id']}; IDs must be unique across all groups")
            identifiers.add(item["id"])
            item["name"] = _text(item.get("name", item["id"]), f"{item['id']} name")
            if group == "buildings":
                item.update(_normalize_building_geometry(item))
                item["material"] = _text(item.get("material", "concrete"), "building material")
                if item["material"] not in {"concrete", "glass"}:
                    raise ValueError("building material must be concrete or glass")
                item["thickness_m"] = _number(item.get("thickness_m", 0.2), "building thickness_m")
                if item["thickness_m"] <= 0:
                    raise ValueError("building thickness_m must be positive")
                if not isinstance(item.setdefault("source", {"kind": "manual", "material_assumed": True}), dict):
                    raise ValueError("building source must be an object")
            else:
                item["position_m"] = _vector(item.get("position_m"), 3, f"{item['id']} position_m")
                if item["position_m"][2] < 0:
                    raise ValueError(f"{item['id']} must be above or on ground (z >= 0)")
                if group == "bs":
                    from .network import normalize_bs_radio
                    normalize_bs_radio(item)
                    item["power_dbm"] = _number(item.get("power_dbm", 30), f"{item['id']} power_dbm")
                    item["orientation_deg"] = _vector(item.get("orientation_deg", [0, 0, 0]), 3, "BS orientation_deg")
                    item["pattern"] = _text(item.get("pattern", "iso"), "BS pattern")
                    if item["pattern"] not in {"iso", "tr38901"}:
                        raise ValueError("BS pattern must be iso or tr38901")
    from .network import normalize_network
    result['network'] = normalize_network(result.get('network'))
    for device in result["bs"] + result["ues"]:
        if contains_point(result["buildings"], device["position_m"]):
            raise ValueError(f"{device['id']} is inside or on a building; choose an outdoor position")
    spec, _, _ = _sampling_spec(result.get("sampling", _SAMPLING_DEFAULTS))
    result["sampling"] = spec
    if 'surface_sampling' in result:
        from .spherical import normalize_surface_sampling
        result['surface_sampling'] = normalize_surface_sampling(result['surface_sampling'], result['bs'])
    solver = result.setdefault("solver", {})
    if not isinstance(solver, dict):
        raise ValueError("solver must be an object")
    # Migrate legacy full projects; the removed option is never exposed,
    # fingerprinted or saved. Native solvers disable unsupported events.
    solver.pop('diffraction', None)
    solver.pop('edge_diffraction', None)
    for key, value in _SOLVER_DEFAULTS.items():
        solver.setdefault(key, value)
    for key, minimum, maximum in (("max_depth", 0, 10), ("samples_per_src", 1, 10_000_000),
                                   ("batch_size", 1, 1024), ("seed", 0, 2**32 - 1)):
        solver[key] = _integer(solver[key], f"solver {key}", minimum, maximum)
    for key in ("los", "specular_reflection", "diffuse_reflection", "refraction"):
        if type(solver[key]) is not bool:
            raise ValueError(f"solver {key} must be boolean")
    solver["scattering_coefficient"] = _number(solver["scattering_coefficient"], "solver scattering_coefficient")
    if not 0 <= solver["scattering_coefficient"] <= 1:
        raise ValueError("solver scattering_coefficient must be in [0, 1]")
    return result


def project_fingerprint(project: dict) -> str:
    encoded = json.dumps(validate_project(project), sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"JSON number must be finite: {value}")


def load_project(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8-sig") as stream:
        project = json.load(stream, parse_constant=_invalid_constant, object_pairs_hook=_unique_object)
    return validate_project(project)


def save_project(project: dict, path: str | Path, *, overwrite: bool = True) -> None:
    """Validate, flush to a sibling temporary file, then replace the destination."""
    normalized = validate_project(project)
    payload = json.dumps(normalized, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    destination = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=destination.parent,
                                         prefix=f".{destination.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        from .atomic_files import publish_file
        publish_file(temporary, destination, overwrite=overwrite)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
