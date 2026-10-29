"""Own one cancellable local compute child; no HTTP service or launcher script."""
import json
from pathlib import Path
import sys
import tempfile
import uuid
from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal


def worker_command(input_path, output_path):
    args = ['--worker', '--input', str(input_path), '--output', str(output_path)]
    if not getattr(sys, 'frozen', False):
        args.insert(0, str(Path(__file__).resolve().parents[1] / 'main.py'))
    return sys.executable, args


class JobRunner(QObject):
    progress = Signal(dict)
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None
        self.folder = None
        self.buffer = ''
        self.diagnostic = ''
        self.cancelled = False

    def start(self, project, mode, probe_position_m=None):
        if self.process is not None:
            raise ValueError('计算进程尚未结束')
        self.folder = Path(tempfile.gettempdir()) / 'ran-workbench-jobs' / uuid.uuid4().hex
        self.folder.mkdir(parents=True, exist_ok=True)
        input_path, output_path = self.folder / 'input.json', self.folder / 'result.json'
        request = {'project': project, 'mode': mode}
        if probe_position_m is not None:
            request['probe_position_m'] = probe_position_m
        input_path.write_text(json.dumps(request, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        self.cancelled, self.buffer, self.diagnostic = False, '', ''
        process = self.process = QProcess(self)
        process.setWorkingDirectory(str(self.folder))
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert('PYTHONUTF8', '1')
        process.setProcessEnvironment(environment)
        process.started.connect(self._started)
        process.readyReadStandardOutput.connect(self._stdout)
        process.readyReadStandardError.connect(self._stderr)
        process.finished.connect(self._finished)
        process.errorOccurred.connect(self._error)
        program, arguments = worker_command(input_path, output_path)
        process.start(program, arguments)

    def _started(self):
        # terminate() issued while QProcess is Starting may have no effect.
        # Honour that cancellation before the worker can initialize a pool.
        if self.cancelled and self.process is not None:
            self.process.kill()

    def _stdout(self):
        if self.process is None:
            return
        self.buffer += bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace')
        while '\n' in self.buffer:
            line, self.buffer = self.buffer.split('\n', 1)
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('type') == 'progress':
                self.progress.emit(event)
            elif event.get('type') == 'error':
                self.diagnostic += str(event.get('message', event.get('error', '计算失败'))) + '\n'

    def _stderr(self):
        if self.process is not None:
            self.diagnostic = (self.diagnostic + bytes(self.process.readAllStandardError()).decode('utf-8', errors='replace'))[-64000:]

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart and self.process is not None:
            message = self.process.errorString()
            self.process.deleteLater()
            self.process = None
            self.failed.emit('无法启动本机计算进程：' + message)

    def _finished(self, code, _status):
        self._stdout()
        self._stderr()
        process, self.process = self.process, None
        if process:
            process.deleteLater()
        if self.folder:
            (self.folder / 'diagnostic.log').write_text(self.diagnostic, encoding='utf-8')
        if self.cancelled:
            self.failed.emit('计算已取消')
            return
        output = self.folder / 'result.json'
        if code != 0 or not output.is_file():
            detail = self.diagnostic.strip().splitlines()
            last = detail[-1] if detail else f'进程退出码 {code}'
            self.failed.emit(f'GPU 计算失败：{last}\n诊断文件：{self.folder / "diagnostic.log"}')
            return
        try:
            self.completed.emit(json.loads(output.read_text(encoding='utf-8')))
        except (ValueError, OSError) as error:
            self.failed.emit('无法读取计算结果：' + str(error))

    def cancel(self):
        if self.process:
            self.cancelled = True
            # Console/windowed CUDA workers do not process WM_CLOSE reliably.
            # The worker's Job Object makes forced exit reap the entire pool.
            self.process.kill()

    def shutdown(self):
        if self.process:
            self.cancelled = True
            process = self.process
            process.kill()
            process.waitForFinished(3500)
