"""Windows app entry; frozen executable also owns the local GPU worker mode."""
import sys


def worker_streams():
    """A windowed EXE can still use the pipes explicitly supplied by QProcess."""
    if sys.platform != 'win32':
        return
    import ctypes
    import msvcrt
    import os
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetStdHandle.argtypes = [ctypes.c_ulong]
    kernel.GetStdHandle.restype = ctypes.c_void_p
    kernel.AttachConsole.argtypes = [ctypes.c_ulong]
    kernel.AttachConsole.restype = ctypes.c_int
    ATTACH_PARENT_PROCESS = 0xFFFFFFFF
    for name, identifier in [('stdout', -11), ('stderr', -12)]:
        if getattr(sys, name) is None:
            handle = kernel.GetStdHandle(identifier & 0xffffffff)
            if not handle or handle == ctypes.c_void_p(-1).value:
                kernel.AttachConsole(ATTACH_PARENT_PROCESS)
                handle = kernel.GetStdHandle(identifier & 0xffffffff)
            if handle and handle != ctypes.c_void_p(-1).value:
                try:
                    descriptor = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
                    setattr(sys, name, os.fdopen(descriptor, 'w', encoding='utf-8', buffering=1))
                except OSError:
                    pass
            if getattr(sys, name) is None:
                setattr(sys, name, open(os.devnull, 'w', encoding='utf-8'))
        # Frozen Python can keep locale-encoded inherited pipes even when
        # PYTHONUTF8 is set. Qt and the worker protocol always decode UTF-8.
        stream = getattr(sys, name)
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', line_buffering=True)


if __name__ == '__main__':
    # Dispatch spawned CPU reducers before any Qt or CUDA imports, including
    # PyInstaller's --multiprocessing-fork entry in the windowed executable.
    import multiprocessing
    multiprocessing.freeze_support()
    if '--system-cli' in sys.argv:
        worker_streams()
        from ran_workbench.system.cli import main
        raise SystemExit(main([a for a in sys.argv[1:] if a!='--system-cli']))
    from pathlib import Path
    if '--worker' in sys.argv:
        worker_streams()
        from ran_workbench.process_lifetime import contain_worker_process
        try:
            contain_worker_process()
        except OSError as error:
            import json
            print(json.dumps({'type': 'error', 'message': '无法保护计算子进程：' + str(error)}, ensure_ascii=False), flush=True)
            raise SystemExit(1)
        from ran_workbench.worker import main
        raise SystemExit(main([a for a in sys.argv[1:] if a != '--worker']))
    from pathlib import Path
    cli_executable = bool(getattr(sys, 'frozen', False)) and Path(sys.executable).stem.lower() == 'ranworkbench-cli'
    if '--cli' in sys.argv or cli_executable:
        worker_streams()
        from ran_workbench.cli import main
        raise SystemExit(main([a for a in sys.argv[1:] if a != '--cli']))
    if bool(getattr(sys, 'frozen', False)):
        stem = Path(sys.executable).stem.lower()
        if stem == 'ranstrategyworkbench' and '--strategy' not in sys.argv:
            sys.argv.append('--strategy')
        elif stem == 'rancityworkbench' and '--workspace' not in sys.argv:
            sys.argv.extend(['--workspace', 'Modeling'])
    from ran_workbench.desktop import main
    raise SystemExit(main())
