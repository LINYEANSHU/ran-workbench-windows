"""Reproducible local build using the current project's installed dependencies."""
import os
import argparse
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
from importlib.metadata import distribution

ROOT = Path(__file__).resolve().parents[1]

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dist-dir', type=Path, default=ROOT / 'dist', help='Build into a separate directory when an earlier app is open')
    parser.add_argument('--work-dir', type=Path, default=ROOT / '.cache/build', help='Isolate build state from another running build')
    parser.add_argument('--legacy-launchers', action='store_true', help='Optional compatibility EXEs and all browser locales')
    args = parser.parse_args()
    if sys.platform != 'win32':
        raise SystemExit('RAN Workbench builds support Windows only.')
    output = args.dist_dir.resolve() / 'RANWorkbench'
    if output.exists():
        raise SystemExit('Output already exists. Select a fresh --dist-dir to prevent stale files entering a release.')
    (ROOT / '.cache').mkdir(exist_ok=True)
    license_workspace = tempfile.TemporaryDirectory(prefix='release-licenses-', dir=ROOT / '.cache')
    license_cache = Path(license_workspace.name)
    # Derive license payload from installed wheels on a clean source checkout.
    # Do not depend on a previous local release or private build cache.
    for package in ('PySide6', 'PySide6_Addons', 'PySide6_Essentials', 'PySide6_Pdf',
                    'PySide6_WebEngine', 'shiboken6', 'sionna-rt', 'mitsuba', 'drjit',
                    'numpy', 'scipy', 'shapely', 'pyproj', 'mapbox-earcut', 'pillow',
                    'matplotlib', 'pythreejs', 'pyinstaller', 'packaging', 'certifi',
                    'six', 'python-dateutil', 'traitlets', 'ipywidgets', 'psutil'):
        wheel = distribution(package)
        for entry in wheel.files or []:
            parts = Path(str(entry)).parts
            if not any('license' in part.lower() or part.lower().startswith(('copying', 'notice')) for part in parts):
                continue
            source = Path(wheel.locate_file(entry))
            if source.is_file() and source.suffix.lower() not in {'.pyc', '.pyd', '.dll'}:
                # Wheel manifests sometimes include paths outside site-packages.
                safe_parts = [part for part in parts if part not in {'.', '..'}]
                target = license_cache / package / Path(*safe_parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if not python_license.is_file():
        raise SystemExit('Python runtime LICENSE.txt is required for distribution.')
    (license_cache / 'Python').mkdir(exist_ok=True)
    shutil.copy2(python_license, license_cache / 'Python' / 'LICENSE.txt')
    for name in ('LICENSE.md', 'ThirdParty.json', 'ThirdParty.extra.json'):
        source = ROOT / 'node_modules' / 'cesium' / name
        if source.is_file():
            target = license_cache / 'cesium' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    if not (ROOT/'node_modules/cesium/Build/Cesium/Cesium.js').is_file():
        raise SystemExit('Missing local CesiumJS. Install locked npm dependencies first.')
    system_root = os.environ.get('SystemRoot', r'C:\Windows')
    clean_path = [
        str(ROOT / '.venv' / 'Scripts'),
        os.path.join(sys.base_prefix, 'DLLs'),
        os.path.join(system_root, 'System32'),
        system_root,
        os.path.join(system_root, 'System32', 'Wbem'),
        os.path.join(system_root, 'System32', 'WindowsPowerShell', 'v1.0'),
    ]
    environment = dict(os.environ)
    environment['PATH'] = os.pathsep.join(clean_path)
    environment['RAN_LEGACY_LAUNCHERS'] = '1' if args.legacy_launchers else '0'
    environment['PYINSTALLER_CONFIG_DIR'] = str(ROOT/'.cache/pyinstaller')
    environment['MPLCONFIGDIR'] = str(ROOT/'.cache/matplotlib')
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm',
                    '--distpath',str(args.dist_dir.resolve()),'--workpath',str(args.work_dir.resolve()),
                    str(ROOT/'RANWorkbench.spec')],cwd=ROOT,env=environment,check=True)
    output = args.dist_dir.resolve() / 'RANWorkbench'
    for name in ('README.md', 'README.zh-CN.md', 'LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT / name, output / name)
    shutil.copytree(ROOT / 'assets' / 'readme', output / 'assets' / 'readme')
    (output / 'docs').mkdir(exist_ok=True)
    for name in ('windows-guide.md', 'windows-guide.zh-CN.md'):
        if (ROOT / 'docs' / name).is_file():
            shutil.copy2(ROOT / 'docs' / name, output / 'docs' / name)
    if license_cache.is_dir():
        shutil.copytree(license_cache, output / 'THIRD_PARTY_LICENSES', dirs_exist_ok=True)
    license_workspace.cleanup()
    import json
    files = [p for p in output.rglob('*') if p.is_file()]
    report = {'files': len(files), 'bytes': sum(p.stat().st_size for p in files),
              'legacy_launchers': args.legacy_launchers, 'browser_locales': 'all' if args.legacy_launchers else ['en-US', 'zh-CN', 'zh-TW'],
              'baseline_bytes': 953043145}
    report['reduction_bytes'] = report['baseline_bytes'] - report['bytes']
    (output/'BUILD_METRICS.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))
    print(output/'RANWorkbench.exe')
