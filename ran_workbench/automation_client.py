"""Loopback HTTP automation client and instance discovery for RAN Workbench.

Connects to running desktop instances over 127.0.0.1 HTTP API with Bearer token authentication.
No Qt or CUDA imports.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import socket
import time
from typing import Any
import urllib.error
import urllib.request
from urllib.parse import urlparse


MAX_RESPONSE_BYTES = 128 * 1024 * 1024  # 128 MiB


class AutomationError(Exception):
    """Exception raised for automation transport or application failures."""

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        response: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.response = response


def default_endpoint_directory() -> Path:
    """Return platform default automation directory."""
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else (Path.home() / "AppData" / "Local")
        return base / "RANWorkbench" / "automation"

    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg_cache) if xdg_cache else (Path.home() / ".cache")
    return base / "RANWorkbench" / "automation"


def _is_pid_alive(pid: int) -> bool:
    """Check if process with given PID is alive without psutil.

    On Windows, uses WinDLL kernel32 with explicit 64-bit HANDLE argtypes/restype
    and use_last_error=True to prevent ctypes c_int truncation.
    """
    if type(pid) is not int or isinstance(pid, bool) or pid <= 0:
        return False

    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE

        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL

        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            err = ctypes.get_last_error()
            # ERROR_ACCESS_DENIED (5) means the process exists but cannot be opened
            return err == 5
        try:
            exit_code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True


def _validate_loopback_url(url: Any) -> str:
    """Validate that URL is strictly 127.0.0.1 loopback HTTP without credentials or queries."""
    if not isinstance(url, str):
        raise AutomationError("连接记录 URL 必须为字符串", error_code="invalid_url")

    if any(ord(c) < 32 or ord(c) == 127 for c in url):
        raise AutomationError("连接记录 URL 包含非法控制字符或换行符", error_code="invalid_url")

    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise AutomationError("连接记录 URL 格式无法解析", error_code="invalid_url") from exc

    if parsed.scheme != "http":
        raise AutomationError("连接记录 URL 协议必须为 http (仅支持本机环回)", error_code="invalid_url_scheme")

    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise AutomationError("连接记录 URL 禁止包含用户名或密码凭证", error_code="invalid_url_credentials")

    if parsed.hostname != "127.0.0.1":
        raise AutomationError("连接记录 URL 必须为 127.0.0.1 本机环回地址", error_code="invalid_url_host")

    try:
        port = parsed.port
    except ValueError:
        raise AutomationError("连接记录 URL 端口号无效", error_code="invalid_url_port")

    if port is None or port <= 0 or port > 65535:
        raise AutomationError("连接记录 URL 端口号必须在 1-65535 范围内且不能为 0", error_code="invalid_url_port")

    if parsed.query:
        raise AutomationError("连接记录 URL 禁止包含查询参数", error_code="invalid_url_query")

    if parsed.fragment:
        raise AutomationError("连接记录 URL 禁止包含片段标识", error_code="invalid_url_fragment")

    if parsed.path not in ("", "/"):
        raise AutomationError("连接记录 URL 根路径必须为空或 '/'", error_code="invalid_url_path")

    return f"http://127.0.0.1:{port}"


def _validate_record_dict(data: Any, source_label: str) -> dict[str, Any]:
    """Validate endpoint record fields and return normalized record."""
    if not isinstance(data, dict):
        raise AutomationError(f"连接记录必须为 JSON 对象: {source_label}", error_code="invalid_record")

    version = data.get("version")
    if type(version) is not int or version != 1:
        raise AutomationError(f"连接记录版本无效 (必须为 1): {source_label}", error_code="invalid_version")

    pid = data.get("pid")
    if type(pid) is not int or isinstance(pid, bool) or pid <= 0:
        raise AutomationError(f"连接记录 PID 无效: {source_label}", error_code="invalid_pid")

    token = data.get("token")
    if not isinstance(token, str) or not token.strip():
        raise AutomationError(f"连接记录缺少有效认证令牌: {source_label}", error_code="invalid_token")

    url = _validate_loopback_url(data.get("url"))

    record = dict(data)
    record["url"] = url
    record["endpoint_file"] = source_label
    return record


def _read_record_file(file_path: Path, raise_on_error: bool = False) -> dict[str, Any] | None:
    try:
        content = file_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as exc:
        if raise_on_error:
            raise AutomationError(f"无法读取或解析连接文件: {file_path.name}", error_code="invalid_endpoint_file") from exc
        return None

    if raise_on_error:
        return _validate_record_dict(data, str(file_path.resolve()))

    try:
        return _validate_record_dict(data, str(file_path.resolve()))
    except AutomationError:
        return None


def discover(endpoint_file: str | Path | None = None, pid: int | None = None) -> dict[str, Any]:
    """Discover a running RAN Workbench instance record.

    Filters stale records without deleting files. Raises AutomationError when missing or ambiguous.
    """
    if endpoint_file is not None:
        p = Path(endpoint_file)
        if not p.exists():
            raise AutomationError(f"未找到指定的连接文件: {p}", error_code="endpoint_not_found")
        rec = _read_record_file(p, raise_on_error=True)
        if not _is_pid_alive(rec["pid"]):
            raise AutomationError(
                f"PID {rec['pid']} 对应实例未运行 (陈旧连接文件: {p})",
                error_code="instance_not_running",
            )
        return rec

    directory = default_endpoint_directory()
    if pid is not None:
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise AutomationError(f"PID 参数必须为大于 0 的整数: {pid}", error_code="invalid_pid")

        if not directory.exists():
            raise AutomationError(f"自动化目录不存在: {directory}", error_code="endpoint_not_found")

        target_file = directory / f"{pid}.json"
        if target_file.exists():
            rec = _read_record_file(target_file, raise_on_error=True)
        else:
            rec = None
            for item in directory.glob("*.json"):
                candidate = _read_record_file(item, raise_on_error=False)
                if candidate and candidate.get("pid") == pid:
                    rec = candidate
                    break

        if rec is None:
            raise AutomationError(f"未找到 PID 为 {pid} 的连接记录", error_code="endpoint_not_found")
        if not _is_pid_alive(rec["pid"]):
            raise AutomationError(f"PID {pid} 对应实例未在运行", error_code="instance_not_running")
        return rec

    if not directory.exists():
        raise AutomationError(f"未找到正在运行的 RAN Workbench 实例 (目录不存在: {directory})", error_code="no_instance")

    live_records = []
    for item in sorted(directory.glob("*.json")):
        rec = _read_record_file(item, raise_on_error=False)
        if rec and _is_pid_alive(rec["pid"]):
            live_records.append(rec)

    if not live_records:
        raise AutomationError("未找到正在运行的 RAN Workbench 自动化实例", error_code="no_instance")
    if len(live_records) > 1:
        pids = [r["pid"] for r in live_records]
        raise AutomationError(
            f"发现多个正在运行的应用实例 (PID: {pids})，请通过 --pid 或 --endpoint-file 显式指定",
            error_code="ambiguous_instance",
        )

    return live_records[0]


def list_instances() -> list[dict[str, Any]]:
    """List all instances with redacted tokens and liveness status."""
    directory = default_endpoint_directory()
    if not directory.exists():
        return []

    results = []
    for item in sorted(directory.glob("*.json")):
        rec = _read_record_file(item, raise_on_error=False)
        if rec is not None:
            alive = _is_pid_alive(rec["pid"])
            redacted = {k: v for k, v in rec.items() if k != "token"}
            redacted["alive"] = alive
            results.append(redacted)

    return results


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Deny all redirects for loopback security."""

    def http_error_302(self, req, fp, code, msg, headers):
        raise AutomationError("环回接口禁止任何 HTTP 重定向", error_code="redirect_denied")

    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302


class AutomationClient:
    """Client for controlling RAN Workbench desktop instance over loopback HTTP."""

    def __init__(
        self,
        endpoint_file: str | Path | None = None,
        pid: int | None = None,
        timeout: float = 10.0,
        record: dict[str, Any] | None = None,
    ):
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise AutomationError("timeout 必须为大于 0 的有限数值", error_code="invalid_timeout")

        if record is not None:
            self.record = record
        else:
            self.record = discover(endpoint_file=endpoint_file, pid=pid)

        self.url = _validate_loopback_url(self.record["url"])
        self.token = str(self.record["token"])
        self.timeout = float(timeout)
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )

    def call(self, action: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """POST /api/command with Bearer token and JSON {action, data}."""
        if not isinstance(action, str) or not action.strip():
            raise AutomationError("action 必须为非空字符串", error_code="invalid_action")

        if data is None:
            req_data = {}
        elif isinstance(data, dict):
            req_data = data
        else:
            raise AutomationError("data 必须为字典对象", error_code="invalid_data")

        try:
            payload = json.dumps(
                {"action": action.strip(), "data": req_data},
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (ValueError, TypeError) as exc:
            raise AutomationError(f"请求数据无法序列化为严格有限 JSON: {exc}", error_code="invalid_json_payload") from exc

        target_url = f"{self.url}/api/command"
        req = urllib.request.Request(target_url, data=payload, method="POST")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json; charset=utf-8")
        req.add_header("Accept", "application/json")

        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                raw_bytes = self._read_with_limit(resp)
        except urllib.error.HTTPError as exc:
            try:
                err_bytes = self._read_with_limit(exc)
                err_data = json.loads(err_bytes.decode("utf-8"))
                if isinstance(err_data, dict):
                    raise AutomationError(
                        err_data.get("error", f"HTTP {exc.code}: {exc.reason}"),
                        error_code=err_data.get("error_code", f"http_{exc.code}"),
                        response=err_data,
                    )
            except AutomationError:
                raise
            except Exception:
                pass
            raise AutomationError(f"HTTP 错误 {exc.code}: {exc.reason}", error_code=f"http_{exc.code}") from exc
        except (socket.timeout, TimeoutError) as exc:
            raise AutomationError(f"请求超时 ({self.timeout}s)", error_code="timeout") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.timeout) or "timed out" in str(exc.reason).lower():
                raise AutomationError(f"请求超时 ({self.timeout}s)", error_code="timeout") from exc
            raise AutomationError(f"连接失败: {exc.reason}", error_code="connection_failed") from exc

        try:
            result = json.loads(raw_bytes.decode("utf-8"))
        except Exception as exc:
            raise AutomationError(f"无法解析服务端响应为 JSON: {exc}", error_code="invalid_json_response") from exc

        if not isinstance(result, dict):
            raise AutomationError("服务端响应必须为 JSON 对象", error_code="invalid_json_response")

        ok_val = result.get("ok")
        if type(ok_val) is not bool or not ok_val:
            raise AutomationError(
                result.get("error", "应用命令执行失败"),
                error_code=result.get("error_code"),
                response=result,
            )

        return result

    def _read_with_limit(self, stream) -> bytes:
        chunks = []
        total = 0
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise AutomationError("响应大小超过 128MiB 限制", error_code="response_too_large")
            chunks.append(chunk)
        return b"".join(chunks)

    def wait(self, job_id: str, timeout: float = 120.0, poll: float = 0.2) -> dict[str, Any]:
        """Poll get_state until job with job_id completes.

        Rejects replaced jobs, fails immediately on job cancellation/failure,
        refuses to claim success from idle state without matching completed job,
        and refuses to return success while busy=True.
        """
        if not isinstance(job_id, str) or not job_id.strip():
            raise AutomationError("job_id 必须为非空字符串", error_code="invalid_job_id")

        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise AutomationError("wait timeout 必须为大于 0 的有限数值", error_code="invalid_timeout")

        if (
            isinstance(poll, bool)
            or not isinstance(poll, (int, float))
            or not math.isfinite(poll)
            or poll <= 0
        ):
            raise AutomationError("wait poll 必须为大于 0 的有限数值", error_code="invalid_poll")

        start_time = time.monotonic()
        while time.monotonic() - start_time < timeout:
            res = self.call("get_state")
            state = res.get("state") or {}
            job = state.get("job")
            busy = state.get("busy", False)

            if job is None:
                raise AutomationError("当前无作业记录 (未找到或未启动)", error_code="no_job")

            current_id = job.get("id")
            if current_id != job_id:
                raise AutomationError(
                    f"作业 ID 不匹配: 预期 {job_id}，但当前作业为 {current_id} (作业已被替换)",
                    error_code="job_replaced",
                )

            status = job.get("status")
            if status == "completed":
                if busy is True:
                    time.sleep(poll)
                    continue
                return res
            if status in ("failed", "cancelled"):
                err_msg = state.get("error") or f"作业状态为 {status}"
                raise AutomationError(
                    f"作业执行失败或已取消 ({status}): {err_msg}",
                    error_code=f"job_{status}",
                    response=res,
                )

            time.sleep(poll)

        raise AutomationError(f"等待作业 {job_id} 完成超时 ({timeout}s)", error_code="timeout")
