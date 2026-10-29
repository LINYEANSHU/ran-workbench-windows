"""Tests for ran_workbench.project_io.

Covers non-GUI GeoJSON scene import preparation, CSV/JSON result export,
and atomic checked project save.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from ran_workbench.geometry import contains_point
from ran_workbench import model
from ran_workbench import project_io


def _sample_ring(west: float = 114.165, south: float = 22.295, width: float = 0.001) -> list[list[float]]:
    return [
        [west, south],
        [west + width, south],
        [west + width, south + width],
        [west, south + width],
        [west, south],
    ]


def _sample_result() -> dict:
    return {
        "scene_sha256": "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        "points": [
            {
                "position_m": [-10.0, 20.0, 1.5],
                "links": [
                    {
                        "bs_id": "BS-01",
                        "incoherent_dbm": -75.5,
                        "coherent_dbm": -74.2,
                        "coherent_zero": False,
                        "wideband_dbm": -73.0,
                        "path_count": 5,
                        "has_los": True,
                        "rms_delay_ns": 12.4,
                    }
                ],
            }
        ],
        "ues": [
            {
                "ue_id": "UE-01",
                "position_m": [15.0, -25.0, 1.5],
                "links": [
                    {
                        "bs_id": "BS-01",
                        "incoherent_dbm": -82.0,
                        "coherent_dbm": -81.0,
                        "coherent_zero": True,
                        "wideband_dbm": -80.0,
                        "path_count": 2,
                        "has_los": False,
                        "rms_delay_ns": 32.1,
                    }
                ],
            }
        ],
    }


def test_import_project_valid_wgs84(tmp_path: Path):
    geojson_data = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": "bldg-1",
                "properties": {"name": "测试大楼", "height": 30.0},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [_sample_ring()],
                },
            }
        ],
    }
    geojson_path = tmp_path / "city.geojson"
    geojson_path.write_text(json.dumps(geojson_data, ensure_ascii=False), encoding="utf-8")

    project, report = project_io.import_project(geojson_path)

    assert project["schema_version"] == 2
    assert project["name"] == "city"
    assert "origin" in project
    assert -180 <= project["origin"]["longitude_deg"] <= 180
    assert -90 <= project["origin"]["latitude_deg"] <= 90

    # Buildings check
    assert len(project["buildings"]) == 1
    building = project["buildings"][0]
    assert building["height_m"] == 30.0
    assert building["material"] in {"concrete", "glass"}
    assert len(building["footprint_m"]) == 4

    # Outdoor BS and UE derivation
    assert len(project["bs"]) >= 1
    assert len(project["ues"]) >= 1
    for device in project["bs"] + project["ues"]:
        assert not contains_point(project["buildings"], device["position_m"])

    # Sampling bounds and report
    assert "sampling" in project
    assert project["source_report"]["file_name"] == "city.geojson"
    assert "vertical_note" in project["source_report"]
    assert report["imported_count"] == 1


def test_import_project_with_holes(tmp_path: Path):
    outer = _sample_ring(114.165, 22.295, 0.002)
    inner = _sample_ring(114.1655, 22.2955, 0.0005)
    geojson_data = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "带中庭建筑", "height": 25.0},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [outer, inner],
                },
            }
        ],
    }
    geojson_path = tmp_path / "courtyard.geojson"
    geojson_path.write_text(json.dumps(geojson_data, ensure_ascii=False), encoding="utf-8")

    project, report = project_io.import_project(geojson_path)
    assert len(project["buildings"]) == 1
    assert len(project["buildings"][0]["holes_m"]) == 1
    assert report["imported_count"] == 1


def test_import_project_hk1980_and_options(tmp_path: Path):
    from pyproj import Transformer

    transformer = Transformer.from_crs("EPSG:4326", "EPSG:2326", always_xy=True)
    hk_outer = [list(transformer.transform(*pt)) for pt in _sample_ring()]

    geojson_data = {
        "type": "Feature",
        "properties": {"roof_h": 40.0, "ground_z": 3.0},
        "geometry": {
            "type": "Polygon",
            "coordinates": [hk_outer],
        },
    }
    geojson_path = tmp_path / "hk_block.json"
    geojson_path.write_text(json.dumps(geojson_data, ensure_ascii=False), encoding="utf-8")

    project, report = project_io.import_project(
        geojson_path,
        options={
            "source_crs": "EPSG:2326",
            "height_field": "roof_h",
            "base_field": "ground_z",
            "default_height_m": 20.0,
        },
    )
    assert len(project["buildings"]) == 1
    assert project["buildings"][0]["height_m"] == 40.0
    assert project["buildings"][0]["base_m"] == 3.0
    assert report["imported_count"] == 1


@pytest.mark.parametrize(
    "options, expected_error",
    [
        ({"source_crs": "EPSG:3857"}, "CRS|crs|EPSG"),
        ({"source_crs": 123}, "CRS|crs|EPSG|字符串|string"),
        ({"height_field": {"not": "string"}}, "height_field|字符串|string"),
        ({"base_field": ["not_str"]}, "base_field|字符串|string"),
        ({"default_height_m": -10}, "default_height_m|positive|finite|正数|大于 0"),
        ({"default_height_m": float("nan")}, "finite|number|有限数值|有效数值"),
        ({"default_height_m": float("inf")}, "finite|number|有限数值|有效数值"),
        ("not_a_dict", "options|dict|object|字典"),
    ],
)
def test_import_project_options_validation(tmp_path: Path, options, expected_error):
    geojson_data = {
        "type": "Polygon",
        "coordinates": [_sample_ring()],
    }
    geojson_path = tmp_path / "test.geojson"
    geojson_path.write_text(json.dumps(geojson_data), encoding="utf-8")

    with pytest.raises(ValueError, match=expected_error):
        project_io.import_project(geojson_path, options=options)


def test_import_project_malformed_geojson_raises_value_error(tmp_path: Path):
    # 1. Root is a list rather than a dict
    p1 = tmp_path / "list_root.geojson"
    p1.write_text('[{"type": "Feature"}]', encoding="utf-8")
    with pytest.raises(ValueError, match="对象|FeatureCollection|Feature|root"):
        project_io.import_project(p1)

    # 2. FeatureCollection features is not a list
    p2 = tmp_path / "bad_features.geojson"
    p2.write_text('{"type": "FeatureCollection", "features": "not_a_list"}', encoding="utf-8")
    with pytest.raises(ValueError, match="features|列表|list"):
        project_io.import_project(p2)

    # 3. Non-object feature entry
    p3 = tmp_path / "bad_feature_item.geojson"
    p3.write_text('{"type": "FeatureCollection", "features": ["not_an_object"]}', encoding="utf-8")
    with pytest.raises(ValueError, match="feature|对象|object"):
        project_io.import_project(p3)

    # 4. Geometry is not an object
    p4 = tmp_path / "bad_geometry.geojson"
    p4.write_text('{"type": "Feature", "geometry": "invalid_geom_type"}', encoding="utf-8")
    with pytest.raises(ValueError, match="geometry|对象|object"):
        project_io.import_project(p4)

    # 5. Non-finite coordinates in geometry
    p5 = tmp_path / "nan_coord.geojson"
    p5.write_text(
        '{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[null, 22.0], [114.0, 22.0]]]}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="建筑|多边形|polygon|empty|有限"):
        project_io.import_project(p5)


def test_import_project_empty_or_missing_file_errors(tmp_path: Path):
    missing_path = tmp_path / "nonexistent.geojson"
    with pytest.raises(FileNotFoundError):
        project_io.import_project(missing_path)

    empty_path = tmp_path / "empty.geojson"
    empty_path.write_text('{"type": "FeatureCollection", "features": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="建筑|多边形|polygon|empty"):
        project_io.import_project(empty_path)


def test_export_result_json_success(tmp_path: Path):
    result = _sample_result()
    target_path = tmp_path / "output.json"

    export_info = project_io.export_result(result, target_path)

    assert export_info["format"] == "json"
    assert export_info["exported_path"] == str(target_path.resolve())
    assert target_path.exists()

    loaded = json.loads(target_path.read_text(encoding="utf-8"))
    assert loaded["scene_sha256"] == result["scene_sha256"]
    assert len(loaded["points"]) == 1
    assert len(loaded["ues"]) == 1


def test_export_result_csv_success_and_contract(tmp_path: Path):
    result = _sample_result()
    target_path = tmp_path / "output.csv"

    export_info = project_io.export_result(result, target_path)

    assert export_info["format"] == "csv"
    assert export_info["exported_path"] == str(target_path.resolve())
    assert target_path.exists()

    raw_bytes = target_path.read_bytes()
    assert raw_bytes.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM

    content = raw_bytes.decode("utf-8-sig")
    reader = csv.DictReader(content.splitlines())
    expected_fields = [
        "type", "ue_id", "east_m", "north_m", "up_m", "bs_id",
        "incoherent_dbm", "coherent_dbm", "coherent_zero",
        "wideband_dbm", "path_count", "has_los", "rms_delay_ns", "scene_sha256",
    ]
    assert reader.fieldnames == expected_fields

    rows = list(reader)
    assert len(rows) == 2

    # Check point link row
    point_row = rows[0]
    assert point_row["type"] == "point"
    assert point_row["ue_id"] == ""
    assert point_row["east_m"] == "-10.0"
    assert point_row["north_m"] == "20.0"
    assert point_row["up_m"] == "1.5"
    assert point_row["bs_id"] == "BS-01"
    assert point_row["coherent_zero"] == "False"
    assert point_row["scene_sha256"] == result["scene_sha256"]

    # Check UE link row
    ue_row = rows[1]
    assert ue_row["type"] == "ue"
    assert ue_row["ue_id"] == "UE-01"
    assert ue_row["coherent_zero"] == "True"


def test_export_result_format_inference_and_extension_rules(tmp_path: Path):
    result = _sample_result()

    # Extensionless with format=None defaults to JSON and appends .json
    target_default = tmp_path / "result_default"
    info_default = project_io.export_result(result, target_default)
    assert info_default["format"] == "json"
    assert info_default["exported_path"].endswith("result_default.json")
    assert (tmp_path / "result_default.json").exists()

    # Extensionless with explicit format="json" appends .json
    target_explicit_json = tmp_path / "result_explicit_json"
    info_json = project_io.export_result(result, target_explicit_json, format="json")
    assert info_json["format"] == "json"
    assert info_json["exported_path"].endswith("result_explicit_json.json")
    assert (tmp_path / "result_explicit_json.json").exists()

    # Extensionless with explicit format="csv" appends .csv
    target_explicit_csv = tmp_path / "result_add_csv"
    info_csv = project_io.export_result(result, target_explicit_csv, format="csv")
    assert info_csv["format"] == "csv"
    assert info_csv["exported_path"].endswith("result_add_csv.csv")
    assert (tmp_path / "result_add_csv.csv").exists()

    # Conflicting format and extension raises ValueError
    with pytest.raises(ValueError, match="[Ff]ormat|[Ee]xtension|conflict|不支持|冲突"):
        project_io.export_result(result, tmp_path / "data.csv", format="json")

    with pytest.raises(ValueError, match="[Ff]ormat|[Ee]xtension|conflict|不支持|冲突"):
        project_io.export_result(result, tmp_path / "data.json", format="csv")

    # Unsupported extension
    with pytest.raises(ValueError, match="[Ff]ormat|[Ee]xtension|不支持"):
        project_io.export_result(result, tmp_path / "data.xyz")

    # Unsupported format
    with pytest.raises(ValueError, match="[Ff]ormat|[Ee]xtension|不支持"):
        project_io.export_result(result, tmp_path / "data.json", format="xml")


def test_export_result_empty_result_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="[Ee]mpty|result|请先完成|空"):
        project_io.export_result({}, tmp_path / "out.json")

    with pytest.raises(ValueError, match="[Ee]mpty|result|请先完成|空"):
        project_io.export_result(None, tmp_path / "out.json")


def test_export_result_overwrite_protection_and_atomic_replace(tmp_path: Path):
    result = _sample_result()
    target = tmp_path / "protected.json"
    target.write_text("existing content", encoding="utf-8")

    # Overwrite=False should fail without modifying existing file
    with pytest.raises(FileExistsError):
        project_io.export_result(result, target, overwrite=False)
    assert target.read_text(encoding="utf-8") == "existing content"

    # Overwrite=True should succeed
    project_io.export_result(result, target, overwrite=True)
    assert "scene_sha256" in target.read_text(encoding="utf-8")


def test_export_result_atomic_replace_cleanup_on_serialization_failure(tmp_path: Path):
    target = tmp_path / "keep_me.json"
    target.write_text("critical original data", encoding="utf-8")

    # Invalid non-finite float causes JSON serialization failure
    bad_result = {"nan_val": float("nan")}

    with pytest.raises(ValueError):
        project_io.export_result(bad_result, target, overwrite=True)

    # Original file remains untouched
    assert target.read_text(encoding="utf-8") == "critical original data"

    # No leftover .tmp files
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert len(tmp_files) == 0


def test_checked_save_project_atomic_and_overwrite_protection(tmp_path: Path):
    proj = model.default_project()
    target = tmp_path / "saved.ran.json"

    saved = project_io.checked_save_project(proj, target, overwrite=False)
    assert saved["saved_path"] == str(target.resolve())
    assert target.exists()

    loaded = model.load_project(target)
    assert loaded["schema_version"] == 2

    # Overwrite=False on existing file raises FileExistsError
    with pytest.raises(FileExistsError):
        project_io.checked_save_project(proj, target, overwrite=False)

    # Overwrite=True succeeds
    saved_again = project_io.checked_save_project(proj, target, overwrite=True)
    assert saved_again["saved_path"] == str(target.resolve())

    # Invalid project raises ValueError and does not create corrupt file
    bad_proj = {"schema_version": 999}
    bad_target = tmp_path / "bad.ran.json"
    with pytest.raises(ValueError):
        project_io.checked_save_project(bad_proj, bad_target, overwrite=False)
    assert not bad_target.exists()
