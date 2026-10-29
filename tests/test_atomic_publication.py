"""Final publication must reject competing writers without exposing partial files."""
from copy import deepcopy
import csv
import errno
import json
import os
from pathlib import Path
import threading

import pytest

from ran_workbench import model, project_io
from ran_workbench.automation_commands import ApplicationCommands, failure
from ran_workbench.controller import Controller


def write_document(kind, path, name, *, overwrite=False):
    if kind == 'project':
        scene = model.default_project()
        scene['name'] = name
        return project_io.checked_save_project(scene, path, overwrite=overwrite)
    result = {'scene_sha256': name, 'points': [{'position_m': [1, 2, 3], 'links': [{'bs_id': name}]}]}
    return project_io.export_result(result, path, format=kind, overwrite=overwrite)


def read_writer(kind, path):
    if kind == 'project':
        return model.load_project(path)['name']
    if kind == 'json':
        return json.loads(path.read_text(encoding='utf-8'))['scene_sha256']
    with path.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1 and rows[0]['east_m'] == '1'
    return rows[0]['scene_sha256']


@pytest.mark.parametrize('kind', ['project', 'json', 'csv'])
def test_no_overwrite_final_publication_allows_exactly_one_writer(tmp_path, monkeypatch, kind):
    path = tmp_path / ('共享.ran.json' if kind == 'project' else '共享.' + kind)
    ready = threading.Barrier(2)
    first_published = threading.Event()
    outcomes = {}
    replace, link = os.replace, os.link

    def synchronized_publish(operation, source, destination, *args, **kwargs):
        if Path(destination) == path:
            # Both files are fully flushed and both prechecks have passed.
            ready.wait(timeout=5)
            if threading.current_thread().name == 'writer B':
                assert first_published.wait(timeout=5)
            value = operation(source, destination, *args, **kwargs)
            if threading.current_thread().name == 'writer A':
                assert read_writer(kind, path) == 'writer A'
                first_published.set()
            return value
        return operation(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, 'replace', lambda *a, **kw: synchronized_publish(replace, *a, **kw))
    monkeypatch.setattr(os, 'link', lambda *a, **kw: synchronized_publish(link, *a, **kw))

    def write(name):
        try:
            write_document(kind, path, name)
            outcomes[name] = {'ok': True}
        except Exception as error:
            outcomes[name] = failure(error)

    threads = [threading.Thread(target=write, args=(name,), name=name) for name in ('writer A', 'writer B')]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=7)
    assert not any(thread.is_alive() for thread in threads)
    assert outcomes['writer A'] == {'ok': True}, outcomes
    assert outcomes['writer B']['ok'] is False, outcomes
    assert outcomes['writer B']['error_code'] == 'FILE_EXISTS', outcomes
    assert read_writer(kind, path) == 'writer A'
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('kind', ['project', 'json', 'csv'])
def test_no_hard_link_support_fails_without_partial_destination(tmp_path, monkeypatch, kind):
    path = tmp_path / ('output.ran.json' if kind == 'project' else 'output.' + kind)

    def unsupported(*_args, **_kwargs):
        raise OSError(errno.EOPNOTSUPP, 'simulated unsupported filesystem')

    monkeypatch.setattr(os, 'link', unsupported)
    with pytest.raises(OSError, match='exclusive|hard.link|硬链接'):
        write_document(kind, path, 'writer A')
    assert list(tmp_path.iterdir()) == []
    # Explicitly authorized replace remains available on these filesystems.
    write_document(kind, path, 'writer B', overwrite=True)
    assert read_writer(kind, path) == 'writer B'


@pytest.mark.parametrize('kind', ['project', 'json', 'csv'])
def test_explicit_overwrite_failure_preserves_complete_old_content(tmp_path, monkeypatch, kind):
    path = tmp_path / ('output.ran.json' if kind == 'project' else 'output.' + kind)
    write_document(kind, path, 'old')
    previous = path.read_bytes()

    def fail_replace(*_args):
        raise OSError('simulated publication failure')

    monkeypatch.setattr(os, 'replace', fail_replace)
    with pytest.raises(OSError, match='simulated publication failure'):
        write_document(kind, path, 'new', overwrite=True)
    assert path.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [path]


def test_save_current_retains_implicit_overwrite_but_save_as_requires_permission(tmp_path):
    controller = Controller()
    api = ApplicationCommands(controller, object())
    path = tmp_path / 'current.ran.json'
    assert api.execute({'action': 'save', 'data': {'path': str(path)}})['ok']
    assert api.execute({'action': 'update_project', 'data': {'patch': {'name': 'updated'}}})['ok']
    assert api.execute({'action': 'save'})['ok']
    assert model.load_project(path)['name'] == 'updated' and not controller.dirty

    other = tmp_path / 'other.ran.json'
    model.save_project(model.default_project(), other)
    previous = other.read_bytes()
    response = api.execute({'action': 'save_as', 'data': {'path': str(other)}})
    assert response['error_code'] == 'FILE_EXISTS'
    assert other.read_bytes() == previous
    assert controller.project_path == str(path.resolve())
    assert api.execute({'action': 'save_as', 'data': {'path': str(other), 'overwrite': True}})['ok']
    assert model.load_project(other)['name'] == 'updated'


def test_concurrent_publications_to_different_targets_do_not_conflict(tmp_path):
    paths = [tmp_path / 'A.ran.json', tmp_path / 'B.ran.json']
    errors = []

    def write(path):
        try:
            write_document('project', path, path.stem)
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=write, args=(path,)) for path in paths]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert [model.load_project(path)['name'] for path in paths] == ['A.ran', 'B.ran']
