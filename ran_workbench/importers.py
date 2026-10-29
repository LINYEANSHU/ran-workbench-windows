"""Local GeoJSON footprint ingestion with explicit CRS and height assumptions."""

from __future__ import annotations

import math

from pyproj import Transformer

from .geometry import _normalize_building_geometry, _number


def _field_number(value, label: str) -> float:
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            raise ValueError(f"{label} is not a numeric value") from None
    return _number(value, label)


def _features(data: dict) -> list[dict]:
    if not isinstance(data, dict):
        raise ValueError("GeoJSON must be an object")
    kind = data.get("type")
    if kind == "FeatureCollection":
        features = data.get("features")
        if not isinstance(features, list):
            raise ValueError("GeoJSON features must be an array")
        if any(not isinstance(feature, dict) or feature.get("type") != "Feature" for feature in features):
            raise ValueError("FeatureCollection entries must be Features")
        return features
    if kind == "Feature":
        return [data]
    if kind in {"Polygon", "MultiPolygon"}:
        return [{"type": "Feature", "properties": {}, "geometry": data}]
    raise ValueError("GeoJSON must contain Polygon or MultiPolygon features")


def import_geojson(data: dict, origin: dict, source_crs: str = "EPSG:4326", height_field: str = "height",
                   base_field: str = "", default_height_m: float = 20) -> tuple[list[dict], dict]:
    """Import planar footprints. Height/base fields are local relative metres.

    Coordinates' optional third ordinate is not used as a vertical datum. HK1980
    Grid is reprojected horizontally; no HKPD-to-ellipsoid conversion is implied.
    """
    if not isinstance(source_crs, str) or source_crs.strip().upper() not in {"EPSG:4326", "EPSG:2326"}:
        raise ValueError("source CRS must be EPSG:4326 or EPSG:2326")
    source_crs = source_crs.strip().upper()
    if not isinstance(height_field, str) or not isinstance(base_field, str):
        raise ValueError("height_field and base_field must be strings")
    if not isinstance(origin, dict):
        raise ValueError("origin must be an object")
    longitude = _number(origin.get("longitude_deg"), "origin longitude_deg")
    latitude = _number(origin.get("latitude_deg"), "origin latitude_deg")
    altitude = _number(origin.get("ellipsoid_height_m", 0), "origin ellipsoid_height_m")
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise ValueError("origin longitude/latitude is outside valid degree bounds")
    default_height_m = _number(default_height_m, "default_height_m")
    if default_height_m <= 0:
        raise ValueError("default_height_m must be positive")
    horizontal = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True)
    ecef = Transformer.from_crs("EPSG:4979", "EPSG:4978", always_xy=True)
    ox, oy, oz = ecef.transform(longitude, latitude, altitude)
    lon_r, lat_r = math.radians(longitude), math.radians(latitude)
    slon, clon = math.sin(lon_r), math.cos(lon_r)
    slat, clat = math.sin(lat_r), math.cos(lat_r)
    warnings = ["Building height/base values are local relative metres; no HKPD vertical conversion is applied."]
    imported = []
    default_count = 0
    ignored_z = False

    def project_ring(ring, label):
        nonlocal ignored_z
        if not isinstance(ring, list) or len(ring) < 4:
            raise ValueError(f"{label} must be a closed GeoJSON ring with at least four coordinates")
        points = []
        for point in ring:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                raise ValueError(f"{label} has an invalid coordinate")
            values = [_number(value, f"{label} coordinate") for value in point]
            ignored_z |= len(values) > 2
            lon, lat = horizontal.transform(values[0], values[1], errcheck=True)
            if not math.isfinite(lon) or not math.isfinite(lat) or not -180 <= lon <= 180 or not -90 <= lat <= 90:
                raise ValueError(f"{label} coordinates are outside valid CRS bounds")
            x, y, z = ecef.transform(lon, lat, altitude, errcheck=True)
            dx, dy, dz = x - ox, y - oy, z - oz
            east = -slon * dx + clon * dy
            north = -slat * clon * dx - slat * slon * dy + clat * dz
            points.append([east, north])
        if list(ring[0][:2]) != list(ring[-1][:2]):
            raise ValueError(f"{label} must be closed")
        return points

    for feature_index, feature in enumerate(_features(data), 1):
        geometry = feature.get("geometry")
        if geometry is None:
            warnings.append(f"Feature {feature_index} has null geometry and was skipped.")
            continue
        if not isinstance(geometry, dict) or geometry.get("type") not in {"Polygon", "MultiPolygon"}:
            raise ValueError(f"Feature {feature_index} geometry must be Polygon or MultiPolygon")
        properties = feature.get("properties") or {}
        if not isinstance(properties, dict):
            raise ValueError(f"Feature {feature_index} properties must be an object")
        raw_height = properties.get(height_field) if height_field else None
        assumed_height = raw_height is None or raw_height == ""
        height = default_height_m if assumed_height else _field_number(raw_height, f"Feature {feature_index} height")
        if height <= 0:
            raise ValueError(f"Feature {feature_index} height must be positive")
        raw_base = properties.get(base_field) if base_field else None
        assumed_base = raw_base is None or raw_base == ""
        base = 0.0 if assumed_base else _field_number(raw_base, f"Feature {feature_index} base")
        if base_field and assumed_base:
            warnings.append(f"Feature {feature_index}: missing base field {base_field!r}; assumed 0 m local relative base.")
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or not coordinates:
            raise ValueError(f"Feature {feature_index} has empty polygon coordinates")
        parts = [coordinates] if geometry["type"] == "Polygon" else coordinates
        for part_index, polygon in enumerate(parts, 1):
            if not isinstance(polygon, list) or not polygon:
                raise ValueError(f"Feature {feature_index} has an empty polygon")
            rings = [project_ring(ring, f"Feature {feature_index} polygon {part_index}") for ring in polygon]
            shape = _normalize_building_geometry({"footprint_m": rings[0], "holes_m": rings[1:],
                                                   "base_m": base, "height_m": height})
            identifier = f"GJ-{len(imported) + 1:04d}"
            name = str(properties.get("name") or properties.get("Name") or f"导入建筑 {feature_index}")
            if len(parts) > 1:
                name += f" / {part_index}"
            source = {"kind": "geojson", "source_crs": source_crs, "feature_index": feature_index,
                      "part_index": part_index, "height_field": height_field, "base_field": base_field,
                      "height_assumed": assumed_height, "default_height_m": default_height_m,
                      "base_assumed": assumed_base, "vertical_reference": "local_relative",
                      "material_assumed": True, "default_material": "concrete"}
            if isinstance(feature.get("id"), (str, int)) and not isinstance(feature.get("id"), bool):
                source["feature_id"] = str(feature["id"])
            imported.append({"id": identifier, "name": name, **shape, "material": "concrete",
                             "thickness_m": 0.2, "source": source})
            default_count += int(assumed_height)
    if not imported:
        raise ValueError("GeoJSON contains no importable polygon buildings")
    if ignored_z:
        warnings.append("GeoJSON coordinate altitude ordinates were ignored; use explicit relative height/base fields.")
    return imported, {"imported_count": len(imported), "default_height_count": default_count,
                      "source_crs": source_crs, "warnings": warnings}
