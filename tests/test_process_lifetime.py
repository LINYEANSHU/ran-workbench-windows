"""Windows worker lifetime must cover descendants before Python initialization."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil
import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows Job Object')


@pytest.mark.parametrize('normal_exit', [False, True])
def test_worker_exit_reaps_children_before_their_initializer(tmp_path, normal_exit):
    script = tmp_path / 'spawn_tree.py'
    script.write_text('''import json, subprocess, sys, time
from pathlib import Path
from ran_workbench.process_lifetime import contain_worker_process
contain_worker_process()
contain_worker_process()  # idempotent, no second handle may leak
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
Path(sys.argv[1]).write_text(json.dumps({'pid': child.pid}), encoding='utf-8')
if sys.argv[2] == 'normal':
    time.sleep(.5)
else:
    time.sleep(60)
''', encoding='utf-8')
    root = str(Path(__file__).resolve().parents[1])
    ready = tmp_path / 'ready.json'
    worker = subprocess.Popen([sys.executable, str(script), str(ready), 'normal' if normal_exit else 'cancel'],
                              env={**os.environ, 'PYTHONPATH': root}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    descendant = None
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and worker.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists(), worker.communicate(timeout=5)[1].decode(errors='replace')
        descendant = psutil.Process(json.loads(ready.read_text())['pid'])
        if not normal_exit:
            worker.kill()
        worker.wait(timeout=10)
        _, alive = psutil.wait_procs([descendant], timeout=5)
        assert not alive, 'Uninitialized descendant survived the worker'
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=10)
        if descendant is not None and descendant.is_running():
            descendant.kill()
            descendant.wait(timeout=10)
