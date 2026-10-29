"""Non-modal application commands; shared state, no GUI or GPU imports."""
from collections import deque
from copy import deepcopy
from pathlib import Path
import time

from .model import default_project, load_project


class CommandError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def failure(error):
    default = 'FILE_EXISTS' if isinstance(error, FileExistsError) else 'NOT_FOUND' if isinstance(error, FileNotFoundError) else 'PERMISSION_DENIED' if isinstance(error, PermissionError) else 'INVALID_ARGUMENT' if isinstance(error, (ValueError, TypeError, KeyError)) else 'IO_ERROR'
    code = getattr(error, 'code', default)
    return {'ok': False, 'error': str(error), 'error_code': code}


def merge(target, patch):
    if not isinstance(patch, dict):
        raise CommandError('INVALID_ARGUMENT', 'patch 必须为对象')
    result = deepcopy(target)
    for key, value in patch.items():
        result[key] = merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else deepcopy(value)
    return result


class ApplicationCommands:
    ACTIONS = ('capabilities', 'get_state', 'get_project', 'get_result', 'get_events',
               'set_project', 'update_project', 'add_entity', 'update_entity', 'remove_entity',
               'new', 'open', 'save', 'save_as', 'import_geojson', 'run', 'cancel', 'export',
               'view', 'screenshot', 'close', 'strategy')
    MUTATIONS = {'set_project', 'update_project', 'add_entity', 'update_entity', 'remove_entity',
                 'new', 'open', 'save', 'save_as', 'import_geojson', 'run', 'export', 'close', 'view', 'strategy'}

    def __init__(self, controller, runner, emit_state=None, close=None):
        self.controller, self.runner = controller, runner
        self.emit_state = emit_state or (lambda: None)
        self.close = close
        self.closing = False
        self.events, self.sequence = deque(maxlen=256), 0

    def record(self, kind, data):
        self.sequence += 1
        self.events.append({'sequence': self.sequence, 'type': kind, 'time': time.time(), 'data': deepcopy(data)})

    def state(self, include_result=False):
        state = self.controller.snapshot()
        state['closing'] = self.closing
        result = state['result']
        state['result_summary'] = None if result is None else {
            'mode': result.get('mode'), 'scene_sha256': result.get('scene_sha256'),
            'points': len(result.get('points', [])), 'ues': len(result.get('ues', [])),
            'elapsed_s': result.get('elapsed_s'), 'backend': result.get('backend')}
        if not include_result:
            state['result'] = None
            state['probe_result'] = None
        return state

    def validate(self, request):
        if not isinstance(request, dict) or not isinstance(request.get('action'), str):
            raise CommandError('INVALID_REQUEST', '请求需要字符串 action 和对象 data')
        action, data = request['action'], request.get('data', {})
        if not isinstance(data, dict):
            raise CommandError('INVALID_REQUEST', 'data 必须为对象')
        if action not in self.ACTIONS:
            raise CommandError('UNKNOWN_ACTION', f'未知操作：{action}')
        if self.closing and action not in {'capabilities', 'get_state', 'get_project', 'get_result', 'get_events'}:
            raise CommandError('CLOSING', '应用正在退出，不再接受修改或新任务')
        if action in self.MUTATIONS and 'expected_revision' in data:
            expected = data['expected_revision']
            if type(expected) is not int:
                raise CommandError('INVALID_ARGUMENT', 'expected_revision 必须为整数')
            if expected != self.controller.revision:
                raise CommandError('REVISION_CONFLICT', f'项目修订已改变，当前为 {self.controller.revision}')
        for key in ('discard_changes', 'overwrite', 'cancel_running', 'include_result'):
            if key in data and type(data[key]) is not bool:
                raise CommandError('INVALID_ARGUMENT', f'{key} 必须为布尔值')
        return action, data

    def _idle(self):
        if self.controller.busy:
            raise CommandError('BUSY', '计算进行中，请先取消或等待完成')

    def _discard(self, data):
        self._idle()
        if self.controller.dirty and not data.get('discard_changes', False):
            raise CommandError('UNSAVED_CHANGES', '当前项目有未保存修改；请先保存或显式指定 discard_changes:true')

    @staticmethod
    def _path(data, default=''):
        value = data.get('path', default)
        if not isinstance(value, str) or not value.strip():
            raise CommandError('PATH_REQUIRED', '此操作需要显式文件 path')
        return str(Path(value).expanduser().resolve())

    def execute(self, request):
        try:
            action, data = self.validate(request)
            return self._execute(action, data)
        except (ValueError, TypeError, KeyError, OSError) as error:
            return failure(error)

    def _execute(self, action, data):
        c, extra = self.controller, {}
        if action == 'strategy':
            handler = getattr(self, 'strategy_handler', None)
            if not handler:
                raise CommandError('UNAVAILABLE', '此宿主不提供策略工作区')
            return handler(data)
        if action == 'capabilities':
            return {'ok': True, 'version': 1, 'actions': list(self.ACTIONS),
                    'view_methods': ['get', 'set', 'select', 'camera', 'tab', 'mode', 'drawing', 'form', 'scroll', 'message', 'sampling_mode', 'heatmap_plane'],
                    'groups': ['buildings', 'bs', 'ues'], 'modal_dialogs': False}
        if action == 'get_state':
            return {'ok': True, 'state': self.state(data.get('include_result', False))}
        if action == 'get_project':
            return {'ok': True, 'project': deepcopy(c.project), 'revision': c.revision}
        if action == 'get_result':
            target = data.get('target', 'coverage')
            if target not in ('coverage', 'probe'):
                raise ValueError('result target must be coverage or probe')
            result = c.probe_result if target == 'probe' else c.result
            if result is None:
                raise CommandError('NO_RESULT', '当前场景尚无计算结果')
            return {'ok': True, 'result': result, 'revision': c.revision, 'job': dict(c.job)}
        if action == 'get_events':
            after = data.get('after', 0)
            if type(after) is not int or after < 0:
                raise CommandError('INVALID_ARGUMENT', 'after 必须为非负整数')
            return {'ok': True, 'events': [e for e in self.events if e['sequence'] > after],
                    'cursor': self.sequence, 'truncated': bool(self.events and after < self.events[0]['sequence'] - 1)}
        if action in ('set_project', 'update_project'):
            self._idle()
            if action == 'update_project':
                solver_patch = data.get('patch', {}).get('solver', {}) if isinstance(data.get('patch'), dict) else {}
                if isinstance(solver_patch, dict) and any(key in solver_patch for key in ('diffraction', 'edge_diffraction')):
                    raise CommandError('INVALID_ARGUMENT', '该传播参数已删除，不能再通过接口配置。')
            project = data['project'] if action == 'set_project' else merge(c.project, data['patch'])
            c.update(project)
        elif action in ('add_entity', 'update_entity', 'remove_entity'):
            self._idle()
            group = data.get('group')
            if group not in ('buildings', 'bs', 'ues'):
                raise CommandError('INVALID_ARGUMENT', 'group 必须为 buildings、bs 或 ues')
            project = deepcopy(c.project)
            if action == 'add_entity':
                project[group].append(data['item'])
            else:
                index = next((i for i, item in enumerate(project[group]) if item['id'] == data.get('id')), None)
                if index is None:
                    raise CommandError('NOT_FOUND', '没有找到指定对象')
                if action == 'remove_entity':
                    project[group].pop(index)
                else:
                    project[group][index] = merge(project[group][index], data['patch'])
            c.update(project)
        elif action == 'new':
            self._discard(data)
            c.replace(default_project())
        elif action == 'open':
            self._discard(data)
            path = self._path(data)
            c.replace(load_project(path), path)
        elif action in ('save', 'save_as'):
            from .project_io import checked_save_project
            self._idle()
            path = self._path(data, c.project_path if action == 'save' else '')
            same = bool(c.project_path and Path(c.project_path).resolve() == Path(path))
            extra = checked_save_project(c.project, path, overwrite=same or data.get('overwrite', False))
            c.project_path, c.dirty = extra['saved_path'], False
        elif action == 'import_geojson':
            from .project_io import import_project
            self._discard(data)
            project, report = import_project(self._path(data), data)
            c.update(project)
            c.project_path = ''
            extra['report'] = report
        elif action == 'run':
            self._idle()
            mode = data.get('mode', 'coverage')
            probe = None
            if mode == 'probe':
                from .geometry import _vector, contains_point
                probe = _vector(data.get('position_m'), 3, 'probe position_m')
                if probe[2] < 0 or contains_point(c.project['buildings'], probe):
                    raise ValueError('单点精算位置必须在地面以上且位于建筑外')
            c.begin(mode)
            try:
                if mode == 'probe':
                    self.runner.start(deepcopy(c.project), mode, probe_position_m=probe)
                else:
                    self.runner.start(deepcopy(c.project), mode)
            except Exception as error:
                c.fail(str(error))
                self.emit_state()
                raise
        elif action == 'cancel':
            if c.busy:
                c.request_cancel()
                self.runner.cancel()
        elif action == 'export':
            from .project_io import export_result
            if c.result is None:
                raise CommandError('NO_RESULT', '请先完成覆盖或 UE 计算')
            extra = export_result(c.result, self._path(data), format=data.get('format'), overwrite=data.get('overwrite', False))
        elif action == 'close':
            strategy_guard = getattr(self, 'strategy_close_guard', None)
            if strategy_guard:
                strategy_guard(data)
            if c.busy and not data.get('cancel_running', False):
                raise CommandError('BUSY', '计算仍在运行；退出须指定 cancel_running:true')
            if c.dirty and not data.get('discard_changes', False):
                raise CommandError('UNSAVED_CHANGES', '退出须先保存或指定 discard_changes:true')
            if not self.close:
                raise CommandError('UNAVAILABLE', '此宿主不提供退出操作')
            self.closing = True
            try:
                self.close()
            except Exception:
                self.closing = False
                raise
            extra['closing'] = True
        else:
            raise CommandError('UNAVAILABLE', '此操作需要可见桌面宿主')
        self.emit_state()
        return {'ok': True, 'state': self.state(), **extra}
