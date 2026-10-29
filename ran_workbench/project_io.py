"""Non-GUI project scene import preparation, CSV/JSON export, and checked atomic save.

Extracts core I/O logic from desktop.py into pure Python functions.
No Qt or GPU/CUDA dependencies.
"""

from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any

from pyproj import Transformer

from .geometry import contains_point
from .importers import import_geojson
from .model import default_project, save_project, validate_project


def _ensure_finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} 必须为有效数值")
    val = float(value)
    if not math.isfinite(val):
        raise ValueError(f"{label} 必须为有限数值")
    return val


def import_project(path: str | Path, options: dict[str, Any] | None = None) -> tuple[dict, dict]:
    """Import planar GeoJSON footprints and prepare a validated scene project.

    Derived outdoor BS/UE devices, sampling bounds, and CRS conversion mirror desktop.py.
    Returns:
        tuple of (validated_project, report)
    """
    if options is None:
        opts = {}
    elif isinstance(options, dict):
        opts = options
    else:
        raise ValueError("options 必须是字典对象")

    src_crs_raw = opts.get("source_crs", "EPSG:4326")
    if not isinstance(src_crs_raw, str):
        raise ValueError("source_crs 必须为字符串")
    source_crs = src_crs_raw.strip().upper()
    if source_crs not in {"EPSG:4326", "EPSG:2326"}:
        raise ValueError(f"不支持的 source_crs: {source_crs}，仅支持 EPSG:4326 或 EPSG:2326")

    hf = opts.get("height_field", "height")
    if not isinstance(hf, str):
        raise ValueError("height_field 必须是字符串")
    height_field = hf.strip()

    bf = opts.get("base_field", "")
    if not isinstance(bf, str):
        raise ValueError("base_field 必须是字符串")
    base_field = bf.strip()

    default_height_val = opts.get("default_height_m", 20.0)
    default_height_m = _ensure_finite_number(default_height_val, "default_height_m")
    if default_height_m <= 0:
        raise ValueError("default_height_m 必须为大于 0 的正数")

    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"文件未找到: {file_path}")

    raw_text = file_path.read_text(encoding="utf-8-sig")
    try:
        data = json.loads(raw_text)
    except Exception as exc:
        raise ValueError(f"GeoJSON 文件不是合法的 JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("GeoJSON 根节点必须为对象 (FeatureCollection 或 Feature)")

    root_type = data.get("type")
    if root_type == "FeatureCollection":
        features = data.get("features")
        if not isinstance(features, list):
            raise ValueError("GeoJSON FeatureCollection 的 features 属性必须是列表")
        for idx, feat in enumerate(features):
            if not isinstance(feat, dict):
                raise ValueError(f"GeoJSON feature[{idx}] 必须为对象")
    elif root_type == "Feature":
        features = [data]
    elif root_type in {"Polygon", "MultiPolygon"}:
        features = [{"type": "Feature", "properties": {}, "geometry": data}]
    else:
        raise ValueError(f"不支持的 GeoJSON 类型: {root_type}")

    for idx, feat in enumerate(features):
        geom = feat.get("geometry")
        if geom is not None and not isinstance(geom, dict):
            raise ValueError(f"GeoJSON feature[{idx}] 的 geometry 必须为对象")

    coordinates = []

    def collect(value):
        if isinstance(value, list) and len(value) >= 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value[:2]):
            pt = value[:2]
            if not all(math.isfinite(float(coord)) for coord in pt):
                raise ValueError(f"GeoJSON 包含非有限坐标值: {pt}")
            coordinates.append([float(pt[0]), float(pt[1])])
        elif isinstance(value, list):
            for child in value:
                collect(child)
        elif value is not None and not isinstance(value, (int, float, str, bool, dict)):
            raise ValueError("GeoJSON 包含非法坐标结构")

    for feat in features:
        geom = feat.get("geometry")
        if geom:
            coords = geom.get("coordinates")
            if coords is not None:
                if not isinstance(coords, list):
                    raise ValueError("GeoJSON coordinates 必须为列表")
                collect(coords)

    if not coordinates:
        raise ValueError("文件中没有可导入的建筑多边形")

    x = (min(v[0] for v in coordinates) + max(v[0] for v in coordinates)) / 2
    y = (min(v[1] for v in coordinates) + max(v[1] for v in coordinates)) / 2

    lon, lat = Transformer.from_crs(source_crs, "EPSG:4326", always_xy=True).transform(x, y)
    origin = {"longitude_deg": lon, "latitude_deg": lat, "ellipsoid_height_m": 0.0}

    buildings, report = import_geojson(
        data,
        origin,
        source_crs=source_crs,
        height_field=height_field,
        base_field=base_field,
        default_height_m=default_height_m,
    )
    if not buildings:
        raise ValueError("没有成功导入建筑")

    proj = default_project()
    proj.update(name=file_path.stem, origin=origin, buildings=buildings)

    points = [v for b in buildings for v in b["footprint_m"]]
    xmin, xmax = min(v[0] for v in points), max(v[0] for v in points)
    ymin, ymax = min(v[1] for v in points), max(v[1] for v in points)
    top = max(b["base_m"] + b["height_m"] for b in buildings)
    spacing = max(10, math.ceil(max(xmax - xmin + 60, ymax - ymin + 60) / 30))

    proj["sampling"].update(
        min_m=[xmin - 20, ymin - 20, 1.5],
        max_m=[xmax + 20, ymax + 20, min(201.5, top + 20)],
        spacing_m=[spacing, spacing, 20],
    )
    proj["bs"] = [dict(default_project()["bs"][0], position_m=[xmin - 30, ymin - 30, 25])]
    proj["ues"] = [dict(default_project()["ues"][0], position_m=[xmin - 25, ymin - 25, 1.5])]
    proj["source_report"] = dict(
        report,
        file_name=file_path.name,
        vertical_note="底面/高度字段按局部相对高程处理，未做 HKPD 到椭球高转换",
    )

    validated = validate_project(proj)
    return validated, report


def export_result(
    result: dict[str, Any] | None,
    path: str | Path,
    format: str | None = None,
    overwrite: bool = False,
) -> dict[str, str]:
    """Export coverage / UE propagation result to JSON or CSV.

    Ensures strict finite numbers and same CSV schema as desktop.py.
    Appends .json or .csv consistently when given an extensionless path.
    Uses temporary sibling file replace to prevent corrupting existing files on failure.
    """
    if not result or not isinstance(result, dict):
        raise ValueError("请先完成覆盖或 UE 计算（计算结果为空）")

    target = Path(path)
    suffix = target.suffix.lower()

    if format is not None:
        fmt = str(format).strip().lower()
        if fmt not in {"csv", "json"}:
            raise ValueError(f"不支持的导出格式：{format}，仅支持 csv 或 json")
    else:
        fmt = None

    if not suffix:
        export_format = fmt if fmt is not None else "json"
        target = target.with_suffix(f".{export_format}")
    else:
        if suffix not in {".csv", ".json"}:
            raise ValueError(f"不支持的导出文件扩展名：{suffix}，仅支持 .json 或 .csv")
        ext_format = suffix[1:]
        if fmt is not None and fmt != ext_format:
            raise ValueError(f"导出格式 {format} 与文件扩展名 {suffix} 冲突")
        export_format = ext_format

    if target.exists() and not overwrite:
        raise FileExistsError(f"目标文件已存在且未指定 overwrite=True: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None

    try:
        if export_format == "json":
            # Strict finite JSON: allow_nan=False raises ValueError on NaN/Inf
            payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                newline="\n",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        else:
            fields = [
                "type", "ue_id", "east_m", "north_m", "up_m", "bs_id",
                "incoherent_dbm", "coherent_dbm", "coherent_zero",
                "wideband_dbm", "path_count", "has_los", "rms_delay_ns", "scene_sha256",
            ]
            network_fields = ['channel','signal_dbm','interference_dbm','noise_dbm','sinr_db','snr_db',
                'cooperative_dbm','cooperation_gain_db','cooperative_snr_db','carrier_joint_dbm',
                'carrier_joint_zero','carrier_joint_gain_db','wideband_joint_dbm','wideband_joint_zero',
                'wideband_joint_gain_db','serving_bs_id','unresolved_interferers',
                'total_received_dbm','requested_bs_count','resolved_bs_count'] if result.get('network_model') else []
            fields += network_fields
            if result.get('metric_kind') == 'surface_average_noncoherent':
                fields += ['sample_kind', 'radius_m', 'cell_id', 'area_m2', 'metric_kind',
                           'path_gain', 'mc_standard_error_db_approx', 'positive_replicates', 'estimate_status']
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8-sig",
                newline="",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                for category, key in [("point", "points"), ("ue", "ues")]:
                    for point in result.get(key, []):
                        for link in point.get("links", []):
                            row = {k: link.get(k) for k in fields if k in link}
                            if network_fields:
                                network = point.get('network', {})
                                entry = next((n for n in network.get('per_bs', []) if n['bs_id'] == link['bs_id']), {})
                                row.update({key: entry.get(key) for key in network_fields})
                                row['serving_bs_id'] = network.get('serving_bs_id')
                                row['unresolved_interferers'] = '|'.join(entry.get('unresolved_interferers', []))
                                for field in ('total_received_dbm','requested_bs_count','resolved_bs_count'):
                                    row[field] = network.get(field)
                            for key in ('sample_kind', 'radius_m', 'cell_id', 'area_m2'):
                                if key in fields:
                                    row[key] = point.get(key)
                            row.update(
                                type=category,
                                ue_id=point.get("ue_id", ""),
                                east_m=point["position_m"][0],
                                north_m=point["position_m"][1],
                                up_m=point["position_m"][2],
                                scene_sha256=result.get("scene_sha256", ""),
                            )
                            for k in fields:
                                val = row.get(k)
                                if val is None:
                                    row[k] = ""
                                elif isinstance(val, float):
                                    if not math.isfinite(val):
                                        raise ValueError(f"CSV 字段 {k} 必须为有限数值")
                            writer.writerow(row)
                stream.flush()
                os.fsync(stream.fileno())

        from .atomic_files import publish_file
        publish_file(temporary, target, overwrite=overwrite)
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass

    return {
        "exported_path": str(target.resolve()),
        "format": export_format,
    }


def checked_save_project(project: dict, path: str | Path, overwrite: bool = False) -> dict[str, str]:
    """Atomic project save with existing-file protection."""
    target = Path(path)
    if target.exists() and not overwrite:
        raise FileExistsError(f"目标文件已存在且未指定 overwrite=True: {target}")

    save_project(project, target, overwrite=overwrite)
    return {"saved_path": str(target.resolve())}
