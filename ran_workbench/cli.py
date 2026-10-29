"""Standard-library CLI for controlling RAN Workbench over loopback HTTP.

Guarantees JSON stdout for all commands and routine error reporting without tracebacks.
No Qt or CUDA dependencies.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from . import automation_client
from .automation_client import AutomationClient, AutomationError


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _print_json(data: Any) -> None:
    _configure_stdout()
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ran_workbench.cli",
        description="RAN Workbench 桌面实例本地自动化 CLI",
    )
    parser.add_argument("--endpoint-file", type=str, default=None, help="显式指定连接记录文件路径")
    parser.add_argument("--pid", type=int, default=None, help="显式指定桌面实例进程 PID")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP 单次请求超时时间（秒，默认 10）")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # instances
    subparsers.add_parser("instances", help="列出发现的桌面实例（令牌脱敏）")

    # call
    call_parser = subparsers.add_parser("call", help="执行通用底层动作")
    call_parser.add_argument("action", type=str, help="动作名称（例如 capabilities, view 等）")
    call_data_group = call_parser.add_mutually_exclusive_group()
    call_data_group.add_argument("--data", type=str, default=None, help="内联 JSON 格式数据对象")
    call_data_group.add_argument("--data-file", type=str, default=None, help="包含 JSON 数据对象的文件路径")

    # state
    state_parser = subparsers.add_parser("state", help="获取应用及场景当前状态")
    state_parser.add_argument("--include-result", action="store_true", help="状态中是否包含完整计算结果快照")

    # project
    subparsers.add_parser("project", help="获取当前完整项目结构")

    # result
    subparsers.add_parser("result", help="获取当前完整计算结果")

    # set-project
    set_proj_parser = subparsers.add_parser("set-project", help="加载并替换整个项目场景")
    set_proj_parser.add_argument("path", type=str, help="RAN 项目文件路径")
    set_proj_parser.add_argument("--expected-revision", type=int, default=None, help="期望的场景修订版本号")

    # new
    new_parser = subparsers.add_parser("new", help="创建新空白场景")
    new_parser.add_argument("--discard-changes", action="store_true", help="放弃未保存的修改")

    # open
    open_parser = subparsers.add_parser("open", help="打开已有的场景项目文件")
    open_parser.add_argument("path", type=str, help="项目文件路径")
    open_parser.add_argument("--discard-changes", action="store_true", help="放弃未保存的修改")

    # save
    save_parser = subparsers.add_parser("save", help="保存当前场景项目")
    save_parser.add_argument("path", type=str, nargs="?", default=None, help="保存路径（留空则保存至当前项目路径）")
    save_parser.add_argument("--overwrite", action="store_true", help="覆盖已存在的文件")

    # import-geojson
    import_parser = subparsers.add_parser("import-geojson", help="导入 GeoJSON 轮廓重置场景")
    import_parser.add_argument("path", type=str, help="GeoJSON 文件路径")
    import_parser.add_argument("--source-crs", type=str, default="EPSG:4326", choices=["EPSG:4326", "EPSG:2326"], help="源坐标系")
    import_parser.add_argument("--height-field", type=str, default="height", help="高度属性字段名称")
    import_parser.add_argument("--base-field", type=str, default="", help="底面高程属性字段名称")
    import_parser.add_argument("--default-height", type=float, default=20.0, help="默认建筑高度（米）")
    import_parser.add_argument("--discard-changes", action="store_true", help="放弃未保存的修改")

    # run
    run_parser = subparsers.add_parser("run", help="启动 GPU 传播计算作业")
    run_parser.add_argument("mode", choices=["coverage", "ue"], help="计算模式 (coverage 或 ue)")
    run_parser.add_argument("--wait", action="store_true", help="等待计算作业完成")
    run_parser.add_argument("--wait-timeout", type=float, default=120.0, help="等待计算超时时间（秒，默认 120）")

    # wait
    wait_parser = subparsers.add_parser("wait", help="等待正在执行的计算作业")
    wait_parser.add_argument("--job-id", type=str, default=None, help="目标作业 ID（默认当前正在运行的作业）")
    wait_parser.add_argument("--wait-timeout", type=float, default=120.0, help="等待计算超时时间（秒，默认 120）")

    # cancel
    subparsers.add_parser("cancel", help="取消当前正在执行的计算作业")

    # export
    export_parser = subparsers.add_parser("export", help="导出计算结果到 CSV 或 JSON 文件")
    export_parser.add_argument("path", type=str, help="导出文件路径")
    export_parser.add_argument("--format", type=str, default=None, choices=["csv", "json"], help="导出格式")
    export_parser.add_argument("--overwrite", action="store_true", help="覆盖已存在的目标文件")

    # close
    close_parser = subparsers.add_parser("close", help="退出桌面应用实例")
    close_parser.add_argument("--discard-changes", action="store_true", help="放弃未保存的修改")
    close_parser.add_argument("--cancel-running", action="store_true", help="若有计算正在进行则强制取消并退出")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "instances":
            records = automation_client.list_instances()
            _print_json({"ok": True, "instances": records})
            return 0

        client = AutomationClient(
            endpoint_file=args.endpoint_file,
            pid=args.pid,
            timeout=args.timeout,
        )

        if args.command == "call":
            if args.data is not None:
                try:
                    data = json.loads(args.data)
                except Exception as exc:
                    raise AutomationError(f"--data 参数不是有效的 JSON 对象: {exc}", error_code="invalid_json_data")
            elif args.data_file is not None:
                try:
                    content = Path(args.data_file).read_text(encoding="utf-8")
                    data = json.loads(content)
                except Exception as exc:
                    raise AutomationError(f"无法从 --data-file 读取有效 JSON 对象: {exc}", error_code="invalid_data_file")
            else:
                data = {}

            if not isinstance(data, dict):
                raise AutomationError("call 数据载荷必须为 JSON 对象", error_code="invalid_data_payload")

            res = client.call(args.action, data)
            _print_json(res)
            return 0

        if args.command == "state":
            data = {"include_result": True} if args.include_result else {}
            res = client.call("get_state", data)
            _print_json(res)
            return 0

        if args.command == "project":
            res = client.call("get_project", {})
            _print_json(res)
            return 0

        if args.command == "result":
            res = client.call("get_result", {})
            _print_json(res)
            return 0

        if args.command == "set-project":
            from .model import load_project
            try:
                project = load_project(args.path)
            except (OSError, ValueError, TypeError, KeyError) as error:
                raise AutomationError(f"无法读取项目文件: {error}", error_code="invalid_project_file") from error
            data = {"project": project}
            if args.expected_revision is not None:
                data["expected_revision"] = int(args.expected_revision)
            res = client.call("set_project", data)
            _print_json(res)
            return 0

        if args.command == "new":
            data = {"discard_changes": True} if args.discard_changes else {}
            res = client.call("new", data)
            _print_json(res)
            return 0

        if args.command == "open":
            data = {
                "path": str(Path(args.path).resolve()),
                "discard_changes": bool(args.discard_changes),
            }
            res = client.call("open", data)
            _print_json(res)
            return 0

        if args.command == "save":
            data = {"overwrite": bool(args.overwrite)}
            if args.path is not None:
                data["path"] = str(Path(args.path).resolve())
            res = client.call("save", data)
            _print_json(res)
            return 0

        if args.command == "import-geojson":
            data = {
                "path": str(Path(args.path).resolve()),
                "source_crs": args.source_crs,
                "height_field": args.height_field,
                "base_field": args.base_field,
                "default_height_m": float(args.default_height),
                "discard_changes": bool(args.discard_changes),
            }
            res = client.call("import_geojson", data)
            _print_json(res)
            return 0

        if args.command == "run":
            data = {"mode": args.mode}
            res = client.call("run", data)
            if args.wait:
                job_id = None
                if isinstance(res, dict):
                    job_id = res.get("job_id") or (res.get("state") or {}).get("job", {}).get("id")
                if not job_id:
                    st = client.call("get_state", {})
                    job_id = ((st.get("state") or {}).get("job") or {}).get("id")
                if not job_id:
                    raise AutomationError("未能提取启动的作业 ID", error_code="no_job_id")
                res = client.wait(job_id, timeout=float(args.wait_timeout))
            _print_json(res)
            return 0

        if args.command == "wait":
            job_id = args.job_id
            if not job_id:
                st = client.call("get_state", {})
                job = (st.get("state") or {}).get("job")
                if not job or not job.get("id"):
                    raise AutomationError("当前无正在运行的作业", error_code="no_job")
                job_id = job["id"]
            res = client.wait(job_id, timeout=float(args.wait_timeout))
            _print_json(res)
            return 0

        if args.command == "cancel":
            res = client.call("cancel", {})
            _print_json(res)
            return 0

        if args.command == "export":
            data = {
                "path": str(Path(args.path).resolve()),
                "overwrite": bool(args.overwrite),
            }
            if args.format:
                data["format"] = args.format
            res = client.call("export", data)
            _print_json(res)
            return 0

        if args.command == "close":
            data = {
                "discard_changes": bool(args.discard_changes),
                "cancel_running": bool(args.cancel_running),
            }
            res = client.call("close", data)
            _print_json(res)
            return 0

        raise AutomationError(f"未知的命令: {args.command}", error_code="unknown_command")

    except AutomationError as err:
        payload: dict[str, Any] = {"ok": False, "error": str(err)}
        if err.error_code:
            payload["error_code"] = err.error_code
        if err.response:
            payload["response"] = err.response
        _print_json(payload)
        return 1
    except Exception as err:
        payload = {"ok": False, "error": str(err), "error_code": "error"}
        _print_json(payload)
        return 1


if __name__ == "__main__":
    sys.exit(main())
