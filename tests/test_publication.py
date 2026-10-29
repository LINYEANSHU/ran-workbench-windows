from pathlib import Path
import pytest

from tools.publish_source import export_source, audit_source
from tools.verify_public_release import audit_package


def test_export_keeps_code_but_never_private_models_or_symlinks(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    for name, body in {
        'main.py': 'print("software")',
        'README.md': '# Software',
        'ran_workbench/model.py': '# model schema code, not a user model',
        'ran_workbench/web/index.html': '<html></html>',
        'ran_workbench/accidental.ran.json': '{"private":true}',
        'ran_workbench/private.geojson': '{}',
        'models/private.obj': 'private',
        'data/city.json': '{}',
        'docs/evidence/private.json': '{}',
        '.git/config': 'private',
        '.env': 'SECRET',
    }.items():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding='utf-8')
    destination = tmp_path / 'public'
    export_source(source, destination)
    assert (destination / 'ran_workbench/model.py').is_file()
    assert (destination / 'ran_workbench/web/index.html').is_file()
    assert not (destination / 'models').exists()
    assert not (destination / '.git').exists()
    assert not list(destination.rglob('*.ran.json'))
    assert not list(destination.rglob('*.geojson'))
    assert not (destination / '.env').exists()
    audit_source(destination)


def test_export_refuses_existing_destination_and_unapproved_reference_json(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'main.py').write_text('pass')
    private = source / 'ran_workbench/system/reference/private.json'
    private.parent.mkdir(parents=True)
    private.write_text('{}')
    destination = tmp_path / 'public'
    export_source(source, destination)
    assert not (destination / private.relative_to(source)).exists()
    with pytest.raises(FileExistsError):
        export_source(source, destination)


def test_audit_rejects_data_even_if_manually_added(tmp_path):
    (tmp_path / 'hidden.ran.json').write_text('{}')
    with pytest.raises(ValueError, match='hidden.ran.json'):
        audit_source(tmp_path)


def test_binary_audit_checks_nested_model_files_even_under_licenses(tmp_path):
    for name in ('RANWorkbench.exe', 'LICENSE', 'NOTICE', 'README.md', 'README.zh-CN.md', 'THIRD_PARTY_NOTICES.md'):
        (tmp_path / name).write_text('software')
    (tmp_path / '_internal').mkdir()
    for dependency in ('Python', 'pyinstaller', 'PySide6', 'cesium', 'mitsuba', 'drjit', 'sionna-rt', 'numpy', 'scipy'):
        folder = tmp_path / 'THIRD_PARTY_LICENSES' / dependency
        folder.mkdir(parents=True)
        (folder / 'LICENSE').write_text('license')
    assert len(audit_package(tmp_path)) == 15
    hidden = tmp_path / 'THIRD_PARTY_LICENSES' / 'private.ran.json'
    hidden.write_text('{}')
    with pytest.raises(ValueError, match='Private model'):
        audit_package(tmp_path)
