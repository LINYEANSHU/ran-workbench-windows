"""Audit software-only Windows folder/ZIP and create a checksummed archive."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

MODEL_SUFFIXES = ('.ran.json', '.ran-sim.json', '.city.json', '.geojson', '.obj',
                  '.stl', '.step', '.stp', '.f3d', '.blend', '.glb', '.gltf', '.ply')


def audit_package(folder):
    folder = Path(folder).resolve()
    for name in ('RANWorkbench.exe', '_internal', 'LICENSE', 'NOTICE',
                 'README.md', 'README.zh-CN.md', 'THIRD_PARTY_LICENSES', 'THIRD_PARTY_NOTICES.md'):
        if not (folder / name).exists():
            raise ValueError(f'Missing release component: {name}')
    for name in ('models', 'data', 'examples', '.git', '.env', 'city-default.json'):
        if (folder / name).exists():
            raise ValueError(f'Private/non-software release path: {name}')
    files = []
    for p in folder.rglob('*'):
        if p.is_symlink() or getattr(p, 'is_junction', lambda: False)():
            raise ValueError(f'Linked release path: {p}')
        if p.is_file():
            name = p.name.lower()
            if name.endswith(MODEL_SUFFIXES) or name.startswith('.env') or ('endpoint' in name and name.endswith('.json')):
                raise ValueError(f'Private model/credential in release: {p.relative_to(folder)}')
            files.append(p)
    licenses = folder / 'THIRD_PARTY_LICENSES'
    for name in ('Python', 'pyinstaller', 'PySide6', 'cesium', 'mitsuba', 'drjit', 'sionna-rt', 'numpy', 'scipy'):
        if not any(p.is_file() for p in (licenses / name).rglob('*')):
            raise ValueError(f'Missing dependency license: {name}')
    return files


def make_archive(folder, destination):
    folder, destination = Path(folder).resolve(), Path(destination).resolve()
    files = audit_package(folder)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for p in sorted(files):
            archive.write(p, Path(folder.name) / p.relative_to(folder))
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None, 'ZIP CRC check failed'
        assert len(archive.infolist()) == len(files)
        assert not any(name.lower().endswith(MODEL_SUFFIXES) for name in archive.namelist())
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix('.zip.sha256').write_text(f'{digest}  {destination.name}\n', encoding='ascii')
    return dict(files=len(files), unpacked_bytes=sum(p.stat().st_size for p in files),
                archive_bytes=destination.stat().st_size, sha256=digest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--zip', type=Path)
    args = parser.parse_args()
    result = make_archive(args.folder, args.zip) if args.zip else {'files':len(audit_package(args.folder))}
    print(json.dumps(result, indent=2))
