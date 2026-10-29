# PyInstaller native application and console CLI; no UPX or runtime downloads.
from pathlib import Path
import os
import sys
from importlib.metadata import distribution
from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH)
sys.path.insert(0, str(root / 'tools'))
from package_policy import runtime_resource_required
legacy_launchers = os.environ.get('RAN_LEGACY_LAUNCHERS') == '1'
datas = [(str(root / 'ran_workbench/web'), 'ran_workbench/web'),
         (str(root / 'ran_workbench/system/reference'), 'ran_workbench/system/reference'),
         (str(root / 'node_modules/cesium/Build/Cesium'), 'resources/cesium')]
binaries, hiddenimports = [], []
# Read installed wheel manifests instead of importing every generated Dr.Jit
# variant during analysis (recursive module discovery initializes thousands of
# decorated native aliases). Preserve package layout for runtime import hooks.
for distribution_name, prefix in [('mitsuba','mitsuba/'),('drjit','drjit/'),('sionna-rt','sionna/rt/'),('pythreejs','pythreejs/')]:
    wheel = distribution(distribution_name)
    for entry in wheel.files or []:
        relative = str(entry).replace('\\','/')
        if not relative.startswith(prefix) or '__pycache__' in relative:
            continue
        # Workbench generates its own simulation meshes. Upstream example
        # cities/vehicle meshes are not application runtime requirements.
        if relative.startswith('sionna/rt/scenes/') and Path(relative).name != '__init__.py':
            continue
        source = Path(wheel.locate_file(entry))
        if not source.is_file() or source.suffix.lower() in ('.lib','.h','.hpp','.pyi','.exe'):
            continue
        record = (str(source),str(Path(relative).parent))
        (binaries if source.suffix.lower() in ('.dll','.pyd') else datas).append(record)
hiddenimports += ['mitsuba','drjit','sionna.rt','pythreejs']
for distribution in ('sionna-rt', 'mitsuba', 'drjit', 'numpy', 'PySide6', 'shapely', 'mapbox-earcut', 'pyproj', 'pythreejs'):
    datas += copy_metadata(distribution)

a = Analysis([str(root/'main.py')], pathex=[str(root)], binaries=binaries,
             datas=datas, hiddenimports=hiddenimports,
             excludes=['tkinter','PyQt5','PyQt6','PySide2','torch','tensorflow','jax','jaxlib','notebook','jupyterlab','pytest'],
             noarchive=False)

# Exclude foreign DLLs accidentally picked up from third-party tools in PATH (e.g. ICU/CRT mismatch)
excluded_dlls = {'icuuc.dll', 'icudt78.dll', 'ucrtbase.dll'}
a.binaries = [
    (dest, source, type_) for dest, source, type_ in a.binaries
    if 'codex-runtimes' not in str(source).lower()
    and '.codex' not in str(source).lower()
    and Path(dest).name.lower() not in excluded_dlls
    and not Path(dest).name.lower().startswith('api-ms-win-')
]

# Debug symbols/resources and non-shipped browser locales are build inputs, not runtime needs.
if not legacy_launchers:
    a.datas = [entry for entry in a.datas if runtime_resource_required(entry[0])]
    a.binaries = [entry for entry in a.binaries if runtime_resource_required(entry[0])]

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='RANWorkbench',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
launchers = [exe]
if legacy_launchers:
    for name, console in [('RANWorkbench-cli', True), ('RANCityWorkbench', False), ('RANStrategyWorkbench', False)]:
        launchers.append(EXE(pyz, a.scripts, [], exclude_binaries=True, name=name,
            debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
            console=console, disable_windowed_traceback=False))
coll = COLLECT(*launchers, a.binaries, a.datas, strip=False, upx=False, name='RANWorkbench')
