"""Exercise real Qt child-process pipes and cancellation without initializing RF."""
import json
from pathlib import Path
import sys
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from ran_workbench import jobs

_app = QCoreApplication.instance() or QCoreApplication([])


def wait_job(runner, timeout=6000, cancel=False):
    events = {'progress': [], 'result': None, 'error': None}
    loop, timer = QEventLoop(), QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    def complete(value):
        events['result'] = value
        loop.quit()
    def fail(value):
        events['error'] = value
        loop.quit()
    runner.progress.connect(lambda value: events['progress'].append(value))
    runner.completed.connect(complete)
    runner.failed.connect(fail)
    runner.start({},'coverage')
    if cancel:
        QTimer.singleShot(100, runner.cancel)
    timer.start(timeout)
    if events['result'] is None and events['error'] is None:
        loop.exec()
    timer.stop()
    runner.shutdown()
    return events


def test_real_worker_pipes_and_result(tmp_path, monkeypatch):
    script = tmp_path/'protocol_child.py'
    script.write_text('''import json,sys,time
from pathlib import Path
p=Path(sys.argv[1]); output=Path(sys.argv[2])
assert json.loads(p.read_text(encoding='utf-8'))['mode']=='coverage'
sys.stdout.write('{"type":"pro'); sys.stdout.flush()
time.sleep(.03)
sys.stdout.write('gress","stage":"GPU","completed":1,"total":2}\\n');sys.stdout.flush()
output.write_text(json.dumps({'points':[{'position_m':[1,2,3]}]}),encoding='utf-8')
''',encoding='utf-8')
    monkeypatch.setattr(jobs,'worker_command',lambda inp,out:(sys.executable,[str(script),str(inp),str(out)]))
    runner = jobs.JobRunner()
    events = wait_job(runner)
    assert events['error'] is None
    assert events['progress'][0]['completed'] == 1
    assert events['result']['points'][0]['position_m'] == [1,2,3]
    assert runner.process is None


def test_cancel_releases_real_child(tmp_path, monkeypatch):
    script = tmp_path/'long_child.py'
    script.write_text('import time; time.sleep(60)',encoding='utf-8')
    monkeypatch.setattr(jobs,'worker_command',lambda inp,out:(sys.executable,[str(script)]))
    runner = jobs.JobRunner()
    events = wait_job(runner,cancel=True)
    assert events['error'] == '计算已取消'
    assert events['result'] is None and runner.process is None


def test_missing_worker_fails_explicitly(monkeypatch):
    monkeypatch.setattr(jobs,'worker_command',lambda inp,out:('Z:/nonexistent/ran-worker.exe',[]))
    runner = jobs.JobRunner()
    events = wait_job(runner)
    assert '无法启动' in events['error']
    assert runner.process is None


def test_cancel_and_restart_job(tmp_path, monkeypatch):
    long_script = tmp_path / 'long_child.py'
    long_script.write_text('import time; time.sleep(60)', encoding='utf-8')
    quick_script = tmp_path / 'quick_child.py'
    quick_script.write_text(
        "import sys, json\n"
        "from pathlib import Path\n"
        "Path(sys.argv[2]).write_text(json.dumps({'points': [{'position_m': [0,0,0]}]}), encoding='utf-8')\n",
        encoding='utf-8'
    )
    monkeypatch.setattr(jobs, 'worker_command', lambda inp, out: (sys.executable, [str(long_script)]))
    runner = jobs.JobRunner()
    events1 = wait_job(runner, cancel=True)
    assert events1['error'] == '计算已取消'
    assert runner.process is None

    monkeypatch.setattr(jobs, 'worker_command', lambda inp, out: (sys.executable, [str(quick_script), str(inp), str(out)]))
    events2 = wait_job(runner, cancel=False)
    assert events2['error'] is None
    assert events2['result']['points'][0]['position_m'] == [0, 0, 0]
    assert runner.process is None


def test_worker_streams_initialization():
    if sys.platform != 'win32':
        import pytest
        pytest.skip('worker_streams handles win32 console and pipe handles')
    import subprocess
    code = (
        "import sys, main; "
        "sys.stdout = None; sys.stderr = None; "
        "main.worker_streams(); "
        "assert sys.stdout is not None and hasattr(sys.stdout, 'write'); "
        "assert sys.stderr is not None and hasattr(sys.stderr, 'write')"
    )
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    assert result.returncode == 0, f"Child failed: {result.stderr}"


def test_worker_streams_reconfigure_existing_non_utf8_pipes():
    if sys.platform != 'win32':
        import pytest
        pytest.skip('Windows worker/CLI pipe encoding')
    import subprocess
    code = (
        "import sys, main; "
        "sys.stdout.reconfigure(encoding='gbk'); "
        "sys.stderr.reconfigure(encoding='gbk'); "
        "main.worker_streams(); "
        "print('热力图建模'); print('计算完成', file=sys.stderr)"
    )
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, timeout=15)
    assert result.returncode == 0
    assert result.stdout.strip() == '热力图建模'.encode('utf-8')
    assert result.stderr.strip() == '计算完成'.encode('utf-8')


def test_cancel_before_qprocess_started_never_runs_the_worker_body(tmp_path, monkeypatch):
    script = tmp_path / 'fast_child.py'
    script.write_text("import sys,time\nfrom pathlib import Path\ntime.sleep(.05)\nPath(sys.argv[2]).write_text('{}')\ntime.sleep(.1)\n", encoding='utf-8')
    monkeypatch.setattr(jobs, 'worker_command', lambda inp, out: (sys.executable, [str(script), str(inp), str(out)]))
    runner = jobs.JobRunner()
    loop = QEventLoop()
    runner.failed.connect(loop.quit)
    runner.start({}, 'coverage')
    runner.cancel()
    QTimer.singleShot(6000, loop.quit)
    loop.exec()
    runner.shutdown()
    assert not (runner.folder / 'result.json').exists()
