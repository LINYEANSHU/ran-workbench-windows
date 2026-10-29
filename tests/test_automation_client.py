"""Tests for ran_workbench.automation_client.

Covers endpoint discovery, loopback HTTP security validation, client command dispatch,
Chinese UTF-8 serialization, timeout/error handling, and job polling.
"""

from __future__ import annotations

import http.server
import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlparse

import pytest

from ran_workbench import automation_client
from ran_workbench.automation_client import AutomationClient, AutomationError


class MockAutomationServer:
    """Threaded standard-library HTTP server on 127.0.0.1 for client testing."""

    def __init__(self, token="test-secret-token"):
        self.token = token
        self.received_requests = []
        self.handler_callback = None
        self.response_status = 200
        self.response_body = {"ok": True}
        self.delay = 0.0

        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                if server.delay > 0:
                    time.sleep(server.delay)

                auth = self.headers.get("Authorization", "")
                content_type = self.headers.get("Content-Type", "")
                content_len = int(self.headers.get("Content-Length", 0))
                raw_body = self.rfile.read(content_len)

                parsed_json = None
                if raw_body:
                    try:
                        parsed_json = json.loads(raw_body.decode("utf-8"))
                    except Exception:
                        parsed_json = None

                req_info = {
                    "path": self.path,
                    "auth": auth,
                    "content_type": content_type,
                    "raw_body": raw_body,
                    "json": parsed_json,
                }
                server.received_requests.append(req_info)

                if server.handler_callback:
                    status, body_bytes = server.handler_callback(req_info)
                else:
                    status = server.response_status
                    body_bytes = json.dumps(server.response_body, ensure_ascii=False).encode("utf-8")

                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body_bytes)))
                self.end_headers()
                self.wfile.write(body_bytes)

            def log_message(self, format, *args):
                pass  # Silence stderr in tests

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def shutdown(self):
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture
def mock_server():
    server = MockAutomationServer()
    try:
        yield server
    finally:
        server.shutdown()


def test_is_pid_alive_non_mocked():
    # Real current process must be detected alive
    assert automation_client._is_pid_alive(os.getpid()) is True
    # Guaranteed nonexistent PID
    assert automation_client._is_pid_alive(999999999) is False
    # Non-positive or boolean PIDs must be False
    assert automation_client._is_pid_alive(0) is False
    assert automation_client._is_pid_alive(-10) is False
    assert automation_client._is_pid_alive(True) is False
    assert automation_client._is_pid_alive(False) is False


def test_default_endpoint_directory_structure(monkeypatch, tmp_path):
    if os.name == "nt":
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
        d = automation_client.default_endpoint_directory()
        assert "RANWorkbench" in str(d)
        assert "automation" in str(d)
    else:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        d = automation_client.default_endpoint_directory()
        assert str(d).startswith(str(tmp_path / "cache"))
        assert "RANWorkbench" in str(d)


def test_discover_explicit_file(tmp_path):
    ep_file = tmp_path / "instance.json"
    ep_data = {
        "version": 1,
        "pid": os.getpid(),
        "url": "http://127.0.0.1:9099",
        "token": "tok123",
        "started_at": "2026-10-25T10:00:00Z",
    }
    ep_file.write_text(json.dumps(ep_data), encoding="utf-8")

    rec = automation_client.discover(endpoint_file=ep_file)
    assert rec["pid"] == os.getpid()
    assert rec["url"] == "http://127.0.0.1:9099"
    assert rec["token"] == "tok123"
    assert Path(rec["endpoint_file"]).resolve() == ep_file.resolve()


def test_discover_explicit_pid(monkeypatch, tmp_path):
    monkeypatch.setattr(automation_client, "default_endpoint_directory", lambda: tmp_path)
    my_pid = os.getpid()
    ep_file = tmp_path / f"{my_pid}.json"
    ep_data = {
        "version": 1,
        "pid": my_pid,
        "url": "http://127.0.0.1:9098",
        "token": "pidtok",
    }
    ep_file.write_text(json.dumps(ep_data), encoding="utf-8")

    rec = automation_client.discover(pid=my_pid)
    assert rec["pid"] == my_pid
    assert rec["url"] == "http://127.0.0.1:9098"


def test_discover_single_live_instance(monkeypatch, tmp_path):
    monkeypatch.setattr(automation_client, "default_endpoint_directory", lambda: tmp_path)
    my_pid = os.getpid()
    ep_file = tmp_path / f"{my_pid}.json"
    ep_data = {
        "version": 1,
        "pid": my_pid,
        "url": "http://127.0.0.1:9097",
        "token": "autotok",
    }
    ep_file.write_text(json.dumps(ep_data), encoding="utf-8")

    rec = automation_client.discover()
    assert rec["pid"] == my_pid


def test_discover_multiple_live_instances_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(automation_client, "default_endpoint_directory", lambda: tmp_path)
    monkeypatch.setattr(automation_client, "_is_pid_alive", lambda p: True)

    (tmp_path / "101.json").write_text(
        json.dumps({"version": 1, "pid": 101, "url": "http://127.0.0.1:9001", "token": "t1"}),
        encoding="utf-8",
    )
    (tmp_path / "102.json").write_text(
        json.dumps({"version": 1, "pid": 102, "url": "http://127.0.0.1:9002", "token": "t2"}),
        encoding="utf-8",
    )

    with pytest.raises(AutomationError) as exc_info:
        automation_client.discover()
    assert exc_info.value.error_code == "ambiguous_instance"


def test_discover_filters_stale_dead_instance_without_deleting_file(monkeypatch, tmp_path):
    monkeypatch.setattr(automation_client, "default_endpoint_directory", lambda: tmp_path)

    # PID 999999999 is dead; current PID is alive
    dead_file = tmp_path / "999999999.json"
    dead_file.write_text(
        json.dumps({"version": 1, "pid": 999999999, "url": "http://127.0.0.1:9001", "token": "dead"}),
        encoding="utf-8",
    )

    live_file = tmp_path / f"{os.getpid()}.json"
    live_file.write_text(
        json.dumps({"version": 1, "pid": os.getpid(), "url": "http://127.0.0.1:9002", "token": "live"}),
        encoding="utf-8",
    )

    rec = automation_client.discover()
    assert rec["pid"] == os.getpid()

    # Crucial: dead instance record file must NOT be deleted by discover
    assert dead_file.exists()


def test_discover_stale_explicit_endpoint_error(tmp_path):
    stale_file = tmp_path / "stale.json"
    # Guaranteed dead pid
    stale_file.write_text(
        json.dumps({"version": 1, "pid": 999999999, "url": "http://127.0.0.1:9001", "token": "stale"}),
        encoding="utf-8",
    )
    with pytest.raises(AutomationError) as exc_info:
        automation_client.discover(endpoint_file=stale_file)
    assert exc_info.value.error_code == "instance_not_running"


@pytest.mark.parametrize(
    "bad_url, expected_code",
    [
        ("http://example.com:8080", "invalid_url_host"),
        ("http://192.168.1.5:8080", "invalid_url_host"),
        ("http://user:pass@127.0.0.1:8080", "invalid_url_credentials"),
        ("http://127.0.0.1:8080/?query=1", "invalid_url_query"),
        ("http://127.0.0.1:8080/#fragment", "invalid_url_fragment"),
        ("http://127.0.0.1:8080/non_root_path", "invalid_url_path"),
        ("https://127.0.0.1:8080", "invalid_url_scheme"),
        ("ftp://127.0.0.1:8080", "invalid_url_scheme"),
        ("http://127.0.0.1:0", "invalid_url_port"),
        ("http://127.0.0.1:8080\n", "invalid_url"),
    ],
)
def test_discover_rejects_non_loopback_or_dangerous_urls(tmp_path, bad_url, expected_code):
    ep_file = tmp_path / "bad.json"
    ep_file.write_text(
        json.dumps({"version": 1, "pid": os.getpid(), "url": bad_url, "token": "secret_tok"}),
        encoding="utf-8",
    )
    with pytest.raises(AutomationError) as exc_info:
        automation_client.discover(endpoint_file=ep_file)

    err = exc_info.value
    assert err.error_code == expected_code
    # Token must NEVER be revealed in error message
    assert "secret_tok" not in str(err)
    if "user:pass" in bad_url:
        assert "user:pass" not in str(err)


@pytest.mark.parametrize(
    "bad_record, expected_code",
    [
        ({"version": 2, "pid": os.getpid(), "url": "http://127.0.0.1:8080", "token": "t"}, "invalid_version"),
        ({"version": 1, "pid": "123", "url": "http://127.0.0.1:8080", "token": "t"}, "invalid_pid"),
        ({"version": 1, "pid": True, "url": "http://127.0.0.1:8080", "token": "t"}, "invalid_pid"),
        ({"version": 1, "pid": 0, "url": "http://127.0.0.1:8080", "token": "t"}, "invalid_pid"),
        ({"version": 1, "pid": os.getpid(), "url": "http://127.0.0.1:8080", "token": ""}, "invalid_token"),
    ],
)
def test_discover_rejects_malformed_record_fields(tmp_path, bad_record, expected_code):
    ep_file = tmp_path / "malformed.json"
    ep_file.write_text(json.dumps(bad_record), encoding="utf-8")

    with pytest.raises(AutomationError) as exc_info:
        automation_client.discover(endpoint_file=ep_file)
    assert exc_info.value.error_code == expected_code


def test_list_instances_redacts_tokens(monkeypatch, tmp_path):
    monkeypatch.setattr(automation_client, "default_endpoint_directory", lambda: tmp_path)
    (tmp_path / f"{os.getpid()}.json").write_text(
        json.dumps({"version": 1, "pid": os.getpid(), "url": "http://127.0.0.1:8000", "token": "super_secret_token"}),
        encoding="utf-8",
    )
    instances = automation_client.list_instances()
    assert len(instances) == 1
    inst = instances[0]
    assert inst["pid"] == os.getpid()
    assert "token" not in inst
    assert "super_secret_token" not in str(instances)


def test_client_init_and_input_validation(tmp_path):
    ep_file = tmp_path / "active.json"
    ep_file.write_text(
        json.dumps({"version": 1, "pid": os.getpid(), "url": "http://127.0.0.1:8080", "token": "t"}),
        encoding="utf-8",
    )

    # Invalid timeout in init
    with pytest.raises(AutomationError) as exc:
        AutomationClient(endpoint_file=ep_file, timeout=-1)
    assert exc.value.error_code == "invalid_timeout"

    with pytest.raises(AutomationError) as exc:
        AutomationClient(endpoint_file=ep_file, timeout=float("nan"))
    assert exc.value.error_code == "invalid_timeout"

    client = AutomationClient(endpoint_file=ep_file)

    # Invalid action in call
    with pytest.raises(AutomationError) as exc:
        client.call("")
    assert exc.value.error_code == "invalid_action"

    # Invalid data in call
    with pytest.raises(AutomationError) as exc:
        client.call("act", "not_dict")
    assert exc.value.error_code == "invalid_data"

    # Strict JSON: non-finite float in data
    with pytest.raises(AutomationError) as exc:
        client.call("act", {"val": float("nan")})
    assert exc.value.error_code == "invalid_json_payload"

    # Invalid wait arguments
    with pytest.raises(AutomationError) as exc:
        client.wait("", timeout=10)
    assert exc.value.error_code == "invalid_job_id"

    with pytest.raises(AutomationError) as exc:
        client.wait("job-1", timeout=0)
    assert exc.value.error_code == "invalid_timeout"

    with pytest.raises(AutomationError) as exc:
        client.wait("job-1", poll=-0.1)
    assert exc.value.error_code == "invalid_poll"


def test_client_call_success_and_chinese_utf8(mock_server, tmp_path):
    ep_file = tmp_path / "active.json"
    ep_file.write_text(
        json.dumps({
            "version": 1,
            "pid": os.getpid(),
            "url": mock_server.url,
            "token": "tok_chinese_123",
        }),
        encoding="utf-8",
    )

    mock_server.response_body = {"ok": True, "state": {"name": "城市三维传播", "buildings": []}}

    client = AutomationClient(endpoint_file=ep_file)
    response = client.call("import_geojson", {"path": "测试建筑.geojson", "comment": "中文参数"})

    assert response["ok"] is True
    assert response["state"]["name"] == "城市三维传播"

    # Verify what server received
    req = mock_server.received_requests[-1]
    assert req["path"] == "/api/command"
    assert req["auth"] == "Bearer tok_chinese_123"
    assert req["json"]["action"] == "import_geojson"
    assert req["json"]["data"]["path"] == "测试建筑.geojson"
    assert req["json"]["data"]["comment"] == "中文参数"


def test_client_call_requires_boolean_ok(mock_server, tmp_path):
    ep_file = tmp_path / "active.json"
    ep_file.write_text(
        json.dumps({"version": 1, "pid": os.getpid(), "url": mock_server.url, "token": "t"}),
        encoding="utf-8",
    )

    # Truthy non-boolean ok must be rejected
    mock_server.response_body = {"ok": "true", "result": 123}

    client = AutomationClient(endpoint_file=ep_file)
    with pytest.raises(AutomationError):
        client.call("get_state")


def test_client_call_application_error_raises_automation_error(mock_server, tmp_path):
    ep_file = tmp_path / "active.json"
    ep_file.write_text(
        json.dumps({"version": 1, "pid": os.getpid(), "url": mock_server.url, "token": "t"}),
        encoding="utf-8",
    )

    mock_server.response_body = {
        "ok": False,
        "error": "未保存修改冲突",
        "error_code": "unsaved_changes",
    }

    client = AutomationClient(endpoint_file=ep_file)
    with pytest.raises(AutomationError) as exc_info:
        client.call("open", {"path": "other.ran.json"})

    err = exc_info.value
    assert err.error_code == "unsaved_changes"
    assert "未保存修改冲突" in str(err)
    assert err.response["ok"] is False


def test_client_call_http_error(mock_server, tmp_path):
    ep_file = tmp_path / "active.json"
    ep_file.write_text(
        json.dumps({"version": 1, "pid": os.getpid(), "url": mock_server.url, "token": "t"}),
        encoding="utf-8",
    )

    mock_server.response_status = 401
    mock_server.response_body = {"ok": False, "error": "Unauthorized"}

    client = AutomationClient(endpoint_file=ep_file)
    with pytest.raises(AutomationError) as exc_info:
        client.call("get_state")
    assert "401" in str(exc_info.value) or exc_info.value.error_code == "http_401"


def test_client_call_timeout(mock_server, tmp_path):
    ep_file = tmp_path / "active.json"
    ep_file.write_text(
        json.dumps({"version": 1, "pid": os.getpid(), "url": mock_server.url, "token": "t"}),
        encoding="utf-8",
    )

    mock_server.delay = 0.5
    client = AutomationClient(endpoint_file=ep_file, timeout=0.1)

    with pytest.raises(AutomationError) as exc_info:
        client.call("get_state")
    assert exc_info.value.error_code == "timeout"


def test_client_wait_completed_and_busy_handling(mock_server, tmp_path):
    ep_file = tmp_path / "active.json"
    ep_file.write_text(
        json.dumps({"version": 1, "pid": os.getpid(), "url": mock_server.url, "token": "t"}),
        encoding="utf-8",
    )

    calls = 0

    def handler(req):
        nonlocal calls
        calls += 1
        if calls == 1:
            # Running
            state = {"busy": True, "job": {"id": "job-100", "status": "running", "mode": "coverage"}}
        elif calls == 2:
            # Completed but still busy -> client must NOT return yet
            state = {"busy": True, "job": {"id": "job-100", "status": "completed", "mode": "coverage"}}
        else:
            # Completed and not busy -> client returns
            state = {"busy": False, "job": {"id": "job-100", "status": "completed", "mode": "coverage"}}
        return 200, json.dumps({"ok": True, "state": state}).encode("utf-8")

    mock_server.handler_callback = handler
    client = AutomationClient(endpoint_file=ep_file)

    res = client.wait("job-100", timeout=5, poll=0.05)
    assert res["ok"] is True
    assert res["state"]["job"]["status"] == "completed"
    assert res["state"]["busy"] is False
    assert calls >= 3


def test_client_wait_job_failed_or_cancelled(mock_server, tmp_path):
    ep_file = tmp_path / "active.json"
    ep_file.write_text(
        json.dumps({"version": 1, "pid": os.getpid(), "url": mock_server.url, "token": "t"}),
        encoding="utf-8",
    )

    mock_server.response_body = {
        "ok": True,
        "state": {
            "busy": False,
            "job": {"id": "job-fail", "status": "failed", "mode": "ue"},
            "error": "GPU out of memory",
        },
    }

    client = AutomationClient(endpoint_file=ep_file)
    with pytest.raises(AutomationError) as exc_info:
        client.wait("job-fail", timeout=2, poll=0.05)
    assert exc_info.value.error_code == "job_failed"


def test_client_wait_job_replaced_or_mismatched(mock_server, tmp_path):
    ep_file = tmp_path / "active.json"
    ep_file.write_text(
        json.dumps({"version": 1, "pid": os.getpid(), "url": mock_server.url, "token": "t"}),
        encoding="utf-8",
    )

    mock_server.response_body = {
        "ok": True,
        "state": {
            "busy": True,
            "job": {"id": "job-DIFFERENT", "status": "running", "mode": "coverage"},
        },
    }

    client = AutomationClient(endpoint_file=ep_file)
    with pytest.raises(AutomationError) as exc_info:
        client.wait("job-original", timeout=2, poll=0.05)
    assert exc_info.value.error_code == "job_replaced"


def test_client_wait_idle_without_completed_job(mock_server, tmp_path):
    ep_file = tmp_path / "active.json"
    ep_file.write_text(
        json.dumps({"version": 1, "pid": os.getpid(), "url": mock_server.url, "token": "t"}),
        encoding="utf-8",
    )

    mock_server.response_body = {
        "ok": True,
        "state": {
            "busy": False,
            "job": None,
        },
    }

    client = AutomationClient(endpoint_file=ep_file)
    with pytest.raises(AutomationError) as exc_info:
        client.wait("job-missing", timeout=2, poll=0.05)
    assert exc_info.value.error_code == "no_job"


def test_client_wait_timeout(mock_server, tmp_path):
    ep_file = tmp_path / "active.json"
    ep_file.write_text(
        json.dumps({"version": 1, "pid": os.getpid(), "url": mock_server.url, "token": "t"}),
        encoding="utf-8",
    )

    mock_server.response_body = {
        "ok": True,
        "state": {
            "busy": True,
            "job": {"id": "job-slow", "status": "running", "mode": "coverage"},
        },
    }

    client = AutomationClient(endpoint_file=ep_file)
    with pytest.raises(AutomationError) as exc_info:
        client.wait("job-slow", timeout=0.15, poll=0.05)
    assert exc_info.value.error_code == "timeout"
