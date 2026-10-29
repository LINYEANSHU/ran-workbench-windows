"""Export software into a fresh, history-free publication directory.

This is an allowlist, not a copy of the working tree followed by deletion.
Never run git push from the private development repository for a public release.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT_FILES = {'main.py', 'README.md', 'README.zh-CN.md', 'LICENSE', 'NOTICE',
              'THIRD_PARTY_NOTICES.md', 'CONTRIBUTING.md', '.gitignore',
              '.gitattributes', 'package.json', 'package-lock.json',
              'requirements-lock.txt', 'pytest.ini', 'RANWorkbench.spec'}
REFERENCE_FILES = {'PDSCH_table1.json', 'eesm_beta_table.json', 'manifest.json',
                   'LICENSE-sionna', 'Apache-2.0.txt'}
TOOLS = {'build_windows.py', 'package_policy.py', 'publish_source.py',
         'fetch_nr_reference.py', 'verify_public_release.py'}
DOCS = {'windows-guide.md', 'windows-guide.zh-CN.md'}
PRIVATE_SUFFIXES = ('.ran.json', '.ran-sim.json', '.city.json', '.geojson', '.obj',
                    '.stl', '.step', '.stp', '.f3d', '.blend', '.glb', '.gltf', '.ply')


def allowed(relative):
    p = Path(relative)
    parts = p.parts
    if any((part.startswith('.') and part != '.github') or part in {'__pycache__', 'node_modules'} for part in parts[:-1]):
        return False
    if str(p).lower().endswith(PRIVATE_SUFFIXES):
        return False
    if len(parts) == 1:
        return p.name in ROOT_FILES
    if parts[:3] == ('ran_workbench', 'system', 'reference'):
        return len(parts) == 4 and p.name in REFERENCE_FILES
    if parts[0] == 'ran_workbench':
        return p.suffix in {'.py', '.js', '.html', '.css'}
    if parts[0] == 'tests':
        return p.suffix in {'.py', '.cjs'} and p.name != 'test_guangzhou_scene_data.py'
    if parts[0] == 'tools':
        return len(parts) == 2 and p.name in TOOLS
    if parts[0] == 'docs':
        return len(parts) == 2 and p.name in DOCS
    if parts[:2] == ('assets', 'readme'):
        return len(parts) == 3 and p.name in {'workbench-en.png', 'workbench-zh.png', 'workflow.svg'}
    if parts[:2] == ('.github', 'workflows'):
        return len(parts) == 3 and p.name == 'windows.yml'
    return False


def audit_source(root):
    root = Path(root).resolve()
    files = []
    for p in root.rglob('*'):
        relative = p.relative_to(root)
        if relative.parts[0] == '.git':
            continue
        if p.is_symlink() or getattr(p, 'is_junction', lambda: False)():
            raise ValueError(f'Publication contains a link: {relative}')
        if p.is_file():
            if not allowed(relative):
                raise ValueError(f'Not approved for publication: {relative}')
            files.append(p)
    return {'files': len(files), 'bytes': sum(p.stat().st_size for p in files),
            'sha256': {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in sorted(files)}}


def export_source(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(destination)
    destination.mkdir(parents=True)
    # Walk approved top-level paths only: never descend into private datasets,
    # old evidence, build products or another repository's history.
    candidates = [source / name for name in ROOT_FILES]
    for directory in ('ran_workbench', 'tests', 'tools', 'docs', 'assets/readme', '.github/workflows'):
        base = source / directory
        if base.is_dir():
            candidates.extend(base.rglob('*'))
    for p in candidates:
        relative = p.relative_to(source)
        if not p.is_file() or not allowed(relative):
            continue
        if p.is_symlink() or not p.resolve().is_relative_to(source):
            raise ValueError(f'Refusing linked source file: {relative}')
        # Also reject junction/symlink ancestors, including in-root links.
        if any(parent.is_symlink() or getattr(parent, 'is_junction', lambda: False)()
               for parent in p.parents if parent != source and parent.is_relative_to(source)):
            raise ValueError(f'Refusing linked source directory: {relative}')
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
    return audit_source(destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--audit-only', action='store_true')
    args = parser.parse_args()
    result = audit_source(args.destination) if args.audit_only else export_source(args.source, args.destination)
    print(json.dumps({k: v for k, v in result.items() if k != 'sha256'}, indent=2))
