"""Real CLI, HTTP transport and ApplicationCommands project replacement."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication

from ran_workbench.automation_commands import ApplicationCommands
from ran_workbench.automation_server import AutomationServer
from ran_workbench.controller import Controller
from ran_workbench.model import default_project, save_project


@pytest.fixture
def application_cli(tmp_path):
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = Controller()
    api = ApplicationCommands(controller, object())
    server = AutomationServer(lambda request, reply: reply(api.execute(request)),
                              endpoint_file=tmp_path / '连接.json')

    def run(*arguments):
        completed = []
        errors = []

        def invoke():
            try:
                completed.append(subprocess.run(
                    [sys.executable, '-m', 'ran_workbench.cli', '--endpoint-file',
                     str(server.endpoint_file), *map(str, arguments)],
                    cwd=Path(__file__).resolve().parents[1], capture_output=True,
                    text=True, encoding='utf-8', timeout=15))
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=invoke)
        worker.start()
        deadline = time.monotonic() + 18
        while worker.is_alive() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.001)
        worker.join(.1)
        assert not worker.is_alive(), 'CLI stalled while Qt was processing HTTP events'
        assert not errors, errors
        process = completed[0]
        assert 'Traceback' not in process.stderr
        return process.returncode, json.loads(process.stdout)

    try:
        yield run, controller, api
    finally:
        server.stop()
        app.processEvents()


@pytest.mark.parametrize('revision_arguments', [[], ['--expected-revision', '0']])
def test_set_project_replaces_real_application_from_unicode_file(application_cli, tmp_path, revision_arguments):
    run, controller, _ = application_cli
    scene = default_project()
    scene['name'] = '珠江新城修改稿'
    path = tmp_path / '中文目录' / '新场景.ran.json'
    path.parent.mkdir()
    save_project(scene, path)

    status, response = run('set-project', path, *revision_arguments)

    assert status == 0, response
    assert response['ok'] is True
    assert controller.project == scene
    assert controller.revision == 1 and controller.dirty
    # set-project is an edit with revision protection, not an "open" operation.
    assert controller.project_path == ''


def test_set_project_stale_revision_preserves_newer_edit(application_cli, tmp_path):
    run, controller, api = application_cli
    path = tmp_path / 'stale.ran.json'
    save_project(default_project(), path)
    assert api.execute({'action': 'update_project', 'data': {'patch': {'name': 'newer edit'}}})['ok']
    before = controller.snapshot()

    status, response = run('set-project', path, '--expected-revision', '0')

    assert status == 1
    assert response['error_code'] == 'REVISION_CONFLICT'
    assert controller.snapshot() == before


@pytest.mark.parametrize('contents', [None, '{invalid', '[]', '{"name":"one","name":"two"}', '{"value":NaN}'])
def test_set_project_bad_file_does_not_change_application(application_cli, tmp_path, contents):
    run, controller, _ = application_cli
    path = tmp_path / '错误场景.ran.json'
    if contents is not None:
        path.write_text(contents, encoding='utf-8')
    before = deepcopy(controller.snapshot())

    status, response = run('set-project', path, '--expected-revision', '0')

    assert status == 1 and response['ok'] is False
    assert response['error_code'] == 'invalid_project_file'
    assert controller.snapshot() == before
