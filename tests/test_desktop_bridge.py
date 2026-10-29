"""Child-process isolated regression tests for desktop Qt bridge and contract."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_stale_webchannel_commit_cannot_erase_new_api_edit(tmp_path):
    result = run_bridge_test('''
original = bridge.controller.snapshot()['project']
updated = bridge.api.execute({'action': 'update_project', 'data': {'patch': {'name': 'API edit'}, 'expected_revision': 0}})
assert updated['ok']
response = json.loads(bridge.command(json.dumps({'action': 'set_project', 'data': {'project': original, 'expected_revision': 0}})))
assert not response['ok'], response
assert bridge.controller.project['name'] == 'API edit'
assert bridge.controller.revision == 1
print(json.dumps({'ok': True}))
''', tmp_path)
    assert result['ok']


def run_bridge_test(script_body: str, tmp_path: Path) -> dict:
    runner_code = f'''
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, r"{ROOT}")
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication, QMainWindow, QFileDialog, QMessageBox
from ran_workbench.desktop import Bridge

app = QApplication.instance() or QApplication(["", "-platform", "offscreen"])
win = QMainWindow()
bridge = Bridge(win)

{script_body}
'''
    child_script = tmp_path / "run_bridge.py"
    child_script.write_text(runner_code, encoding="utf-8")
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONUTF8"] = "1"
    res = subprocess.run([sys.executable, str(child_script)], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    assert res.returncode == 0, f"Child failed (exit {res.returncode}):\\nSTDOUT:\\n{res.stdout}\\nSTDERR:\\n{res.stderr}"
    lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    assert lines, f"Child produced no stdout.\\nSTDERR:\\n{res.stderr}"
    return json.loads(lines[-1])


def test_import_geojson_report_in_response(tmp_path):
    from synthetic_inputs import write_import_fixture
    fixture_path = write_import_fixture(tmp_path / 'import.geojson')
    body = '''
geojson_path = Path(FIXTURE_PATH).resolve()
assert geojson_path.is_file(), f"Missing {geojson_path}"

QFileDialog.getOpenFileName = lambda parent, title, dir, filter: (str(geojson_path), "GeoJSON (*.geojson *.json)")

raw = bridge.command(json.dumps({
    "action": "import_geojson",
    "data": {"source_crs": "EPSG:2326", "default_height_m": 20}
}))
resp = json.loads(raw)
assert resp.get("ok") is True, f"Import failed: {resp}"
assert "report" in resp, f"Missing 'report' in response: {resp}"
rep = resp["report"]
assert rep["imported_count"] == 3
assert rep["default_height_count"] == 1
assert isinstance(rep.get("warnings"), list) and len(rep["warnings"]) > 0

print(json.dumps({"ok": True, "report": rep}))
'''
    body = body.replace('FIXTURE_PATH', repr(str(fixture_path)))
    result = run_bridge_test(body, tmp_path)
    assert result["report"]["imported_count"] == 3


def test_import_geojson_cancel_keeps_project_unchanged(tmp_path):
    body = '''
init_buildings = list(bridge.controller.project["buildings"])

# User cancels file dialog
QFileDialog.getOpenFileName = lambda parent, title, dir, filter: ("", "")

raw = bridge.command(json.dumps({"action": "import_geojson", "data": {}}))
resp = json.loads(raw)
assert resp.get("ok") is True
assert resp.get("cancelled") is True, f"Expected cancelled:True, got: {resp}"
assert bridge.controller.project["buildings"] == init_buildings

print(json.dumps({"ok": True, "cancelled": True}))
'''
    result = run_bridge_test(body, tmp_path)
    assert result["cancelled"] is True


def test_confirm_discard_cancellation(tmp_path):
    body = '''
bridge.controller.dirty = True
init_project = dict(bridge.controller.project)

# Case 1: QMessageBox Cancel
QMessageBox.question = lambda parent, title, text, buttons, default: QMessageBox.StandardButton.Cancel

for action in ["new", "open", "import_geojson"]:
    raw = bridge.command(json.dumps({"action": action}))
    resp = json.loads(raw)
    assert resp.get("ok") is True
    assert resp.get("cancelled") is True, f"Expected cancelled for {action}, got {resp}"
    assert bridge.controller.dirty is True
    assert bridge.controller.project == init_project

# Case 2: QMessageBox Save, but user cancels file dialog in save
QMessageBox.question = lambda parent, title, text, buttons, default: QMessageBox.StandardButton.Save
QFileDialog.getSaveFileName = lambda parent, title, dir, filter: ("", "")

for action in ["new", "open", "import_geojson"]:
    raw = bridge.command(json.dumps({"action": action}))
    resp = json.loads(raw)
    assert resp.get("ok") is True
    assert resp.get("cancelled") is True, f"Expected cancelled for {action} save cancel, got {resp}"
    assert bridge.controller.dirty is True
    assert bridge.controller.project == init_project

print(json.dumps({"ok": True, "cancelled": True}))
'''
    result = run_bridge_test(body, tmp_path)
    assert result["cancelled"] is True


def test_save_and_save_as_cancel(tmp_path):
    body = '''
bridge.controller.dirty = True
bridge.controller.project_path = ""

QFileDialog.getSaveFileName = lambda parent, title, dir, filter: ("", "")

raw_save = bridge.command(json.dumps({"action": "save"}))
resp_save = json.loads(raw_save)
assert resp_save.get("ok") is True
assert resp_save.get("cancelled") is True, f"Expected cancelled:True on save, got {resp_save}"
assert bridge.controller.dirty is True

raw_save_as = bridge.command(json.dumps({"action": "save_as"}))
resp_save_as = json.loads(raw_save_as)
assert resp_save_as.get("ok") is True
assert resp_save_as.get("cancelled") is True, f"Expected cancelled:True on save_as, got {resp_save_as}"
assert bridge.controller.dirty is True

print(json.dumps({"ok": True, "cancelled": True}))
'''
    result = run_bridge_test(body, tmp_path)
    assert result["cancelled"] is True


def test_export_json_chinese_path_roundtrip(tmp_path):
    body = f'''
out_json = Path(r"{str(tmp_path).replace('\\\\', '/')}") / "测试结果导出.json"

test_result = {{
    "schema_version": 2,
    "scene_sha256": "fake_sha256_12345",
    "mode": "coverage",
    "backend": "cuda_ad_mono_polarized",
    "points": [{{
        "position_m": [10.0, 20.0, 1.5],
        "links": [{{
            "bs_id": "BS-01",
            "incoherent_dbm": -60.0,
            "coherent_dbm": -62.0,
            "wideband_dbm": -61.0,
            "path_count": 1,
            "has_los": True,
            "rms_delay_ns": 4.0
        }}]
    }}],
    "ues": [],
    "sampling": {{"requested": 1, "excluded": 0, "computed": 1}},
    "elapsed_s": 0.5,
    "warnings": []
}}
bridge.controller.result = test_result

QFileDialog.getSaveFileName = lambda parent, title, dir, filter: (str(out_json), "JSON (*.json)")

raw = bridge.command(json.dumps({{"action": "export"}}))
resp = json.loads(raw)
assert resp.get("ok") is True, f"Export failed: {{resp}}"
assert resp.get("exported_path") == str(out_json), f"Mismatch exported_path: {{resp}}"
assert resp.get("format") == "json", f"Mismatch format: {{resp}}"

assert out_json.is_file(), "Exported json file missing"
loaded = json.loads(out_json.read_text(encoding="utf-8"))
assert loaded == test_result

print(json.dumps({{"ok": True, "exported_path": resp.get("exported_path"), "format": resp.get("format")}}))
'''
    result = run_bridge_test(body, tmp_path)
    assert result["format"] == "json"


def test_export_csv_chinese_path_and_coherent_zero(tmp_path):
    body = f'''
import csv
out_csv = Path(r"{str(tmp_path).replace('\\\\', '/')}") / "测试结果导出.csv"

test_result = {{
    "schema_version": 2,
    "scene_sha256": "sha256_test_csv",
    "mode": "coverage",
    "backend": "cuda_ad_mono_polarized",
    "points": [
        {{
            "position_m": [1.0, 2.0, 3.0],
            "links": [
                {{
                    "bs_id": "BS-1",
                    "incoherent_dbm": -50.0,
                    "coherent_dbm": -52.0,
                    "wideband_dbm": -51.0,
                    "path_count": 2,
                    "has_los": True,
                    "rms_delay_ns": 1.5
                }}
            ]
        }},
        {{
            "position_m": [4.0, 5.0, 6.0],
            "links": [
                {{
                    "bs_id": "BS-1",
                    "incoherent_dbm": -40.0,
                    "coherent_dbm": None,
                    "coherent_zero": True,
                    "wideband_dbm": -45.0,
                    "path_count": 2,
                    "has_los": False,
                    "rms_delay_ns": 2.0
                }}
            ]
        }},
        {{
            "position_m": [7.0, 8.0, 9.0],
            "links": [
                {{
                    "bs_id": "BS-1",
                    "incoherent_dbm": None,
                    "coherent_dbm": None,
                    "wideband_dbm": None,
                    "path_count": 0,
                    "has_los": False,
                    "rms_delay_ns": None
                }}
            ]
        }}
    ],
    "ues": [],
    "sampling": {{"requested": 3, "excluded": 0, "computed": 3}},
    "elapsed_s": 0.5,
    "warnings": []
}}
bridge.controller.result = test_result

QFileDialog.getSaveFileName = lambda parent, title, dir, filter: (str(out_csv), "CSV (*.csv)")

raw = bridge.command(json.dumps({{"action": "export"}}))
resp = json.loads(raw)
assert resp.get("ok") is True, f"Export failed: {{resp}}"
assert resp.get("exported_path") == str(out_csv), f"Mismatch exported_path: {{resp}}"
assert resp.get("format") == "csv", f"Mismatch format: {{resp}}"

assert out_csv.is_file(), "Exported csv file missing"
with out_csv.open("r", encoding="utf-8-sig") as f:
    reader = list(csv.DictReader(f))

assert len(reader) == 3
# Check header contains coherent_zero
assert "coherent_zero" in reader[0]

# Row 1: Normal
assert reader[0]["coherent_dbm"] == "-52.0"
assert reader[0]["coherent_zero"] == ""

# Row 2: Strict destructive cancellation
assert reader[1]["coherent_dbm"] == ""
assert reader[1]["coherent_zero"] == "True"
assert reader[1]["path_count"] == "2"

# Row 3: No paths
assert reader[2]["coherent_dbm"] == ""
assert reader[2]["coherent_zero"] == ""
assert reader[2]["path_count"] == "0"

print(json.dumps({{"ok": True, "rows": len(reader)}}))
'''
    result = run_bridge_test(body, tmp_path)
    assert result["rows"] == 3


def test_export_filter_appends_extension_and_rejects_unsupported(tmp_path):
    body = f'''
test_result = {{
    "schema_version": 2,
    "scene_sha256": "sha_ext_test",
    "mode": "coverage",
    "backend": "cuda_ad_mono_polarized",
    "points": [],
    "ues": [],
    "sampling": {{"requested": 0, "excluded": 0, "computed": 0}},
    "elapsed_s": 0.1,
    "warnings": []
}}
bridge.controller.result = test_result

base_no_ext = str(Path(r"{str(tmp_path).replace('\\\\', '/')}") / "export_no_ext")

# Case A: No ext + CSV filter -> appends .csv
QFileDialog.getSaveFileName = lambda parent, title, dir, filter: (base_no_ext, "CSV (*.csv)")
resp_a = json.loads(bridge.command(json.dumps({{"action": "export"}})))
assert resp_a.get("ok") is True
assert resp_a.get("format") == "csv"
assert resp_a.get("exported_path").endswith(".csv")
assert Path(resp_a["exported_path"]).is_file()

# Case B: No ext + JSON filter -> appends .json
base_no_ext_json = str(Path(r"{str(tmp_path).replace('\\\\', '/')}") / "export_no_ext_json")
QFileDialog.getSaveFileName = lambda parent, title, dir, filter: (base_no_ext_json, "JSON (*.json)")
resp_b = json.loads(bridge.command(json.dumps({{"action": "export"}})))
assert resp_b.get("ok") is True
assert resp_b.get("format") == "json"
assert resp_b.get("exported_path").endswith(".json")
assert Path(resp_b["exported_path"]).is_file()

# Case C: Unsupported extension -> rejected
unsupported = str(Path(r"{str(tmp_path).replace('\\\\', '/')}") / "export.txt")
QFileDialog.getSaveFileName = lambda parent, title, dir, filter: (unsupported, "CSV (*.csv)")
resp_c = json.loads(bridge.command(json.dumps({{"action": "export"}})))
assert resp_c.get("ok") is False
assert "不支持" in resp_c.get("error", "")

# Case D: Cancelled export
QFileDialog.getSaveFileName = lambda parent, title, dir, filter: ("", "")
resp_d = json.loads(bridge.command(json.dumps({{"action": "export"}})))
assert resp_d.get("ok") is True
assert resp_d.get("cancelled") is True

print(json.dumps({{"ok": True}}))
'''
    result = run_bridge_test(body, tmp_path)
    assert result["ok"] is True


def test_command_exception_does_not_corrupt_state(tmp_path):
    body = '''
init_proj = dict(bridge.controller.project)
raw = bridge.command(json.dumps({"action": "unknown_action_xyz"}))
resp = json.loads(raw)
assert resp.get("ok") is False
assert "未知操作" in resp.get("error")
assert bridge.controller.project == init_proj

raw2 = bridge.command("invalid json string {{{{")
resp2 = json.loads(raw2)
assert resp2.get("ok") is False

print(json.dumps({"ok": True}))
'''
    result = run_bridge_test(body, tmp_path)
    assert result["ok"] is True
