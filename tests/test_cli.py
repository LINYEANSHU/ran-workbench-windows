"""Tests for ran_workbench.cli.

Covers standard CLI invocations, global connection flags, command to action/data mappings,
JSON stdout guarantees, error formatting without tracebacks, and wait polling.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ran_workbench.automation_client import AutomationError
from ran_workbench import cli


def test_cli_instances(capsys):
    mock_instances = [
        {"pid": 1234, "url": "http://127.0.0.1:8001", "started_at": "2026-10-25T10:00:00Z"}
    ]
    with patch("ran_workbench.automation_client.list_instances", return_value=mock_instances):
        exit_code = cli.main(["instances"])

    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["instances"] == mock_instances


def test_cli_call_generic_inline_and_file(capsys, tmp_path):
    mock_client = MagicMock()
    mock_client.call.return_value = {"ok": True, "result": "generic_done"}

    with patch("ran_workbench.cli.AutomationClient", return_value=mock_client):
        # 1. Inline JSON data
        exit_code = cli.main(["call", "capabilities", "--data", '{"fast": true}'])
        assert exit_code == 0
        mock_client.call.assert_called_with("capabilities", {"fast": True})

        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is True
        assert out["result"] == "generic_done"

        # 2. File JSON data
        data_file = tmp_path / "command_data.json"
        data_file.write_text('{"from_file": 123}', encoding="utf-8")

        exit_code = cli.main(["call", "capabilities", "--data-file", str(data_file)])
        assert exit_code == 0
        mock_client.call.assert_called_with("capabilities", {"from_file": 123})


def test_cli_state_and_include_result(capsys):
    mock_client = MagicMock()
    mock_client.call.return_value = {
        "ok": True,
        "state": {
            "project": {},
            "revision": 1,
            "dirty": False,
            "busy": False,
            "progress": 0,
            "error": "",
            "project_path": "",
            "job": None,
            "result_summary": {},
            "result": None,
        },
    }

    with patch("ran_workbench.cli.AutomationClient", return_value=mock_client):
        exit_code = cli.main(["state"])
        assert exit_code == 0
        mock_client.call.assert_called_with("get_state", {})

        exit_code = cli.main(["state", "--include-result"])
        assert exit_code == 0
        mock_client.call.assert_called_with("get_state", {"include_result": True})


def test_cli_project_and_result(capsys):
    mock_client = MagicMock()
    mock_client.call.side_effect = [
        {"ok": True, "project": {"name": "test_proj"}},
        {"ok": True, "result": {"points": []}},
    ]

    with patch("ran_workbench.cli.AutomationClient", return_value=mock_client):
        exit_code = cli.main(["project"])
        assert exit_code == 0
        mock_client.call.assert_called_with("get_project", {})
        assert json.loads(capsys.readouterr().out)["project"]["name"] == "test_proj"

        exit_code = cli.main(["result"])
        assert exit_code == 0
        mock_client.call.assert_called_with("get_result", {})
        assert "result" in json.loads(capsys.readouterr().out)


def test_cli_new_open_save(capsys):
    mock_client = MagicMock()
    mock_client.call.return_value = {"ok": True}

    with patch("ran_workbench.cli.AutomationClient", return_value=mock_client):
        exit_code = cli.main(["new", "--discard-changes"])
        assert exit_code == 0
        mock_client.call.assert_called_with("new", {"discard_changes": True})

        exit_code = cli.main(["open", "existing.ran.json", "--discard-changes"])
        assert exit_code == 0
        mock_client.call.assert_called_with(
            "open",
            {"path": str(Path("existing.ran.json").resolve()), "discard_changes": True},
        )

        exit_code = cli.main(["save", "saved.ran.json", "--overwrite"])
        assert exit_code == 0
        mock_client.call.assert_called_with(
            "save",
            {"path": str(Path("saved.ran.json").resolve()), "overwrite": True},
        )

        # Save without path
        exit_code = cli.main(["save"])
        assert exit_code == 0
        mock_client.call.assert_called_with(
            "save",
            {"overwrite": False},
        )


def test_cli_import_geojson_parameter_mapping(capsys):
    mock_client = MagicMock()
    mock_client.call.return_value = {"ok": True, "report": {"imported_count": 5}}

    with patch("ran_workbench.cli.AutomationClient", return_value=mock_client):
        exit_code = cli.main([
            "import-geojson",
            "hk.geojson",
            "--source-crs", "EPSG:2326",
            "--height-field", "roof_h",
            "--base-field", "ground_z",
            "--default-height", "25.5",
            "--discard-changes",
        ])
        assert exit_code == 0
        mock_client.call.assert_called_with(
            "import_geojson",
            {
                "path": str(Path("hk.geojson").resolve()),
                "source_crs": "EPSG:2326",
                "height_field": "roof_h",
                "base_field": "ground_z",
                "default_height_m": 25.5,
                "discard_changes": True,
            },
        )


def test_cli_run_without_wait(capsys):
    mock_client = MagicMock()
    mock_client.call.return_value = {
        "ok": True,
        "state": {"busy": True, "job": {"id": "job-777", "status": "running"}},
    }

    with patch("ran_workbench.cli.AutomationClient", return_value=mock_client):
        exit_code = cli.main(["run", "coverage"])
        assert exit_code == 0
        mock_client.call.assert_called_with("run", {"mode": "coverage"})
        mock_client.wait.assert_not_called()


def test_cli_run_with_wait(capsys):
    mock_client = MagicMock()
    mock_client.call.return_value = {
        "ok": True,
        "state": {"busy": True, "job": {"id": "job-888", "status": "running"}},
    }
    mock_client.wait.return_value = {
        "ok": True,
        "state": {"busy": False, "job": {"id": "job-888", "status": "completed"}},
    }

    with patch("ran_workbench.cli.AutomationClient", return_value=mock_client):
        exit_code = cli.main(["run", "ue", "--wait", "--wait-timeout", "45"])
        assert exit_code == 0
        mock_client.call.assert_called_with("run", {"mode": "ue"})
        mock_client.wait.assert_called_with("job-888", timeout=45.0)
        out = json.loads(capsys.readouterr().out)
        assert out["state"]["job"]["status"] == "completed"


def test_cli_wait_command(capsys):
    mock_client = MagicMock()
    mock_client.call.return_value = {
        "ok": True,
        "state": {"job": {"id": "job-active", "status": "running"}},
    }
    mock_client.wait.return_value = {
        "ok": True,
        "state": {"job": {"id": "job-active", "status": "completed"}},
    }

    with patch("ran_workbench.cli.AutomationClient", return_value=mock_client):
        # Explicit job-id
        exit_code = cli.main(["wait", "--job-id", "job-999", "--wait-timeout", "30"])
        assert exit_code == 0
        mock_client.wait.assert_called_with("job-999", timeout=30.0)

        # Implicit job-id from get_state
        exit_code = cli.main(["wait"])
        assert exit_code == 0
        mock_client.call.assert_called_with("get_state", {})
        mock_client.wait.assert_called_with("job-active", timeout=120.0)


def test_cli_cancel(capsys):
    mock_client = MagicMock()
    mock_client.call.return_value = {"ok": True}

    with patch("ran_workbench.cli.AutomationClient", return_value=mock_client):
        exit_code = cli.main(["cancel"])
        assert exit_code == 0
        mock_client.call.assert_called_with("cancel", {})


def test_cli_export(capsys):
    mock_client = MagicMock()
    mock_client.call.return_value = {"ok": True, "exported_path": "out.csv", "format": "csv"}

    with patch("ran_workbench.cli.AutomationClient", return_value=mock_client):
        exit_code = cli.main(["export", "out.csv", "--format", "csv", "--overwrite"])
        assert exit_code == 0
        mock_client.call.assert_called_with(
            "export",
            {
                "path": str(Path("out.csv").resolve()),
                "format": "csv",
                "overwrite": True,
            },
        )


def test_cli_close(capsys):
    mock_client = MagicMock()
    mock_client.call.return_value = {"ok": True}

    with patch("ran_workbench.cli.AutomationClient", return_value=mock_client):
        exit_code = cli.main(["close", "--discard-changes", "--cancel-running"])
        assert exit_code == 0
        mock_client.call.assert_called_with(
            "close",
            {"discard_changes": True, "cancel_running": True},
        )


def test_cli_error_returns_json_and_exit_1(capsys):
    mock_client = MagicMock()
    mock_client.call.side_effect = AutomationError("进程通信错误", error_code="conn_err")

    with patch("ran_workbench.cli.AutomationClient", return_value=mock_client):
        exit_code = cli.main(["state"])

    assert exit_code == 1
    captured = capsys.readouterr()
    # No traceback in stderr
    assert "Traceback" not in captured.err
    # JSON error in stdout
    err_obj = json.loads(captured.out)
    assert err_obj["ok"] is False
    assert err_obj["error"] == "进程通信错误"
    assert err_obj["error_code"] == "conn_err"


def test_cli_invalid_command_usage_exit_2(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["nonexistent-command"])
    assert exc_info.value.code == 2
