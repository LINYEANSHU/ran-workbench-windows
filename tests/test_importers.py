import copy

import pytest
from pyproj import Transformer

from ran_workbench import importers
from ran_workbench.geometry import contains_point


ORIGIN = {"longitude_deg": 114.165, "latitude_deg": 22.295, "ellipsoid_height_m": 0}


def ring(west=114.165, south=22.295, width=0.001):
    return [[west, south], [west + width, south], [west + width, south + width],
            [west, south + width], [west, south]]


def test_geojson_import_keeps_holes_and_explicit_height_and_material_assumptions():
    data = {"type": "FeatureCollection", "features": [{"type": "Feature", "id": "real-id",
            "properties": {"name": "中庭楼"}, "geometry": {"type": "Polygon", "coordinates": [
                ring(), ring(114.16525, 22.29525, 0.0005)]}}]}
    original = copy.deepcopy(data)
    buildings, report = importers.import_geojson(data, ORIGIN)
    assert report["imported_count"] == 1
    assert report["default_height_count"] == 1
    assert buildings[0]["name"] == "中庭楼"
    assert buildings[0]["height_m"] == 20
    assert buildings[0]["thickness_m"] == 0.2
    assert buildings[0]["material"] == "concrete"
    assert buildings[0]["source"]["height_assumed"] is True
    assert buildings[0]["source"]["material_assumed"] is True
    assert buildings[0]["source"]["vertical_reference"] == "local_relative"
    assert len(buildings[0]["footprint_m"]) == 4
    assert len(buildings[0]["holes_m"]) == 1
    assert not contains_point(buildings, [51.52, 55.36, 10])
    assert data == original


def test_multipolygon_field_mapping_has_unique_ids_and_relative_base():
    data = {"type": "Feature", "properties": {"H": "35.5", "Z": 4},
            "geometry": {"type": "MultiPolygon", "coordinates": [[ring()], [ring(114.167)]]}}
    buildings, report = importers.import_geojson(data, ORIGIN, height_field="H", base_field="Z")
    assert len({item["id"] for item in buildings}) == 2
    assert report["imported_count"] == 2
    assert report["default_height_count"] == 0
    assert all(item["height_m"] == 35.5 and item["base_m"] == 4 for item in buildings)
    assert all(item["source"]["height_assumed"] is False for item in buildings)


def test_hk1980_grid_and_wgs84_imports_agree_in_local_enu():
    wgs = {"type": "Polygon", "coordinates": [ring()]}
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:2326", always_xy=True)
    hk = {"type": "Polygon", "coordinates": [[list(transformer.transform(*point)) for point in ring()]]}
    wgs_buildings, _ = importers.import_geojson(wgs, ORIGIN)
    hk_buildings, report = importers.import_geojson(hk, ORIGIN, source_crs="EPSG:2326")
    assert hk_buildings[0]["footprint_m"][0] == pytest.approx([0, 0], abs=0.02)
    for first, second in zip(wgs_buildings[0]["footprint_m"], hk_buildings[0]["footprint_m"]):
        assert first == pytest.approx(second, abs=0.02)
    assert report["warnings"]


@pytest.mark.parametrize("height", [0, -10, "not-a-number", float("nan"), float("inf")])
def test_import_rejects_invalid_explicit_height_instead_of_using_default(height):
    data = {"type": "Feature", "properties": {"height": height}, "geometry": {"type": "Polygon", "coordinates": [ring()]}}
    with pytest.raises(ValueError):
        importers.import_geojson(data, ORIGIN)


@pytest.mark.parametrize("crs", ["EPSG:3857", "unknown"])
def test_import_rejects_unsupported_crs(crs):
    with pytest.raises(ValueError, match="CRS|crs|EPSG"):
        importers.import_geojson({"type": "Polygon", "coordinates": [ring()]}, ORIGIN, source_crs=crs)


def test_import_rejects_self_intersection_without_repair():
    coords = [[114.165, 22.295], [114.166, 22.296], [114.165, 22.296], [114.166, 22.295], [114.165, 22.295]]
    with pytest.raises(ValueError, match="polygon|intersection|footprint|area"):
        importers.import_geojson({"type": "Polygon", "coordinates": [coords]}, ORIGIN)
