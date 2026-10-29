"""Project revisions and job state, independent of Qt and GPU libraries."""
from copy import deepcopy
from uuid import uuid4
from .model import default_project, validate_project, project_fingerprint


class Controller:
    def __init__(self, project=None):
        self.project = validate_project(project if project is not None else default_project())
        self.revision = 0
        self.result = None
        self.probe_result = None
        self.busy = False
        self.progress = {}
        self.project_path = ''
        self.dirty = False
        self.error = ''
        self.job = {'id': None, 'mode': None, 'status': 'idle'}

    def idle(self):
        if self.busy:
            raise ValueError('计算进行中，请先取消或等待完成')

    def update(self, project):
        self.idle()
        validated = validate_project(project)
        self.project = validated
        self.revision += 1
        self.result = None
        self.probe_result = None
        self.progress = {}
        self.dirty = True
        self.error = ''
        self.job = {'id': None, 'mode': None, 'status': 'idle'}

    def replace(self, project, path=''):
        self.update(project)
        self.project_path = str(path)
        self.dirty = False

    def begin(self, mode):
        self.idle()
        if mode not in ('coverage', 'ue', 'probe'):
            raise ValueError('未知计算模式')
        if not self.project['bs']:
            raise ValueError('请至少部署一个 BS')
        if mode == 'ue' and not self.project['ues']:
            raise ValueError('请至少部署一个 UE')
        self.project = validate_project(self.project)
        self.busy = True
        if mode != 'probe':
            self.result = None
        self.probe_result = None
        self.error = ''
        self.progress = {'stage': '准备 GPU 计算', 'completed': 0, 'total': 1}
        self.job = {'id': uuid4().hex, 'mode': mode, 'status': 'running'}

    def request_cancel(self):
        if self.busy:
            self.job['status'] = 'cancelling'
            self.progress = {'stage': 'cancelling', 'completed': 0, 'total': 1}

    def complete(self, result):
        self.busy = False
        if result.get('scene_sha256') != project_fingerprint(self.project):
            self.result = None
            self.error = '计算结果与当前场景版本不一致，结果已丢弃'
            self.job['status'] = 'failed'
            raise ValueError(self.error)
        if self.job.get('mode') == 'probe':
            self.probe_result = result
        else:
            self.result = result
        self.progress = {'stage': '计算完成', 'completed': 1, 'total': 1}
        self.error = ''
        self.job['status'] = 'completed'

    def fail(self, message):
        self.busy = False
        self.error = str(message)
        self.progress = {'stage': self.error, 'completed': 0, 'total': 1}
        self.job['status'] = 'cancelled' if self.job['status'] == 'cancelling' or self.error == '计算已取消' else 'failed'

    def snapshot(self):
        from .geometry import propagation_geometry
        return {'project': deepcopy(self.project), 'revision': self.revision,
                'scene_geometry': propagation_geometry(self.project),
                'result': self.result, 'probe_result': self.probe_result,
                'busy': self.busy, 'progress': dict(self.progress),
                'project_path': self.project_path, 'dirty': self.dirty, 'error': self.error,
                'job': dict(self.job)}
