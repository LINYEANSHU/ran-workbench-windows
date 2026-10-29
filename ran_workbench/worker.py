"""JSON-lines subprocess protocol for the desktop host and frozen EXE."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback


def _atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            json.dump(value, file, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local CUDA RT worker")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    protocol = sys.stdout

    def emit(event):
        protocol.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
        protocol.flush()

    try:
        request = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
        if not isinstance(request, dict) or not isinstance(request.get("project"), dict):
            raise ValueError("Input requires a project object")
        mode = request.get("mode", "coverage")
        if mode not in ("coverage", "ue", "probe", "system"):
            raise ValueError("mode must be 'coverage', 'ue', 'probe' or 'system'")
        # Native/Python library diagnostics go to stderr; stdout remains a
        # parseable protocol even if an optional dependency prints on import.
        with redirect_stdout(sys.stderr):
            if mode == 'system':
                from .system.simulator import simulate
                result = simulate(request['project'],progress=lambda event:emit({**event,'type':'progress'}))
            else:
                from .engine import simulate
                kwargs = {'probe_position_m': request.get('probe_position_m')} if mode == 'probe' else {}
                result = simulate(request["project"], mode=mode,
                                  progress=lambda event: emit({**event, "type": "progress"}), **kwargs)
        output = Path(args.output)
        _atomic_json(output, result)
        emit({"type": "done", "output": str(output), "scene_sha256": result["scene_sha256"],
              "elapsed_s": result["elapsed_s"]})
        return 0
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        emit({"type": "error", "message": str(error), "error_class": type(error).__name__})
        return 1


if __name__ == "__main__":
    from ran_workbench.process_lifetime import contain_worker_process
    contain_worker_process()
    raise SystemExit(main())
