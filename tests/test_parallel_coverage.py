"""CPU/GPU boundary, bounded ordering and real Windows spawn regressions."""
from concurrent.futures import Future
from types import SimpleNamespace
import importlib.util
import os
import sys
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pytest


def implementation():
    assert importlib.util.find_spec('ran_workbench.parallel_coverage') is not None, \
        'The coverage pipeline must distribute CPU reductions to spawned workers'
    from ran_workbench import parallel_coverage
    return parallel_coverage


class Native:
    Point3f = staticmethod(lambda value: np.asarray(value))
    Vector3f = 'vector'


class Jit:
    @staticmethod
    def zeros(kind, count):
        return np.zeros((3, count), dtype=np.float32)


def test_batched_targets_preserve_positions_flags_and_restore_after_error():
    module = implementation()
    original = lambda *args, **kwargs: ('native', args, kwargs)
    scene = SimpleNamespace(targets=original, rx_array=SimpleNamespace(array_size=1))
    points = [[1, 2, 3], [-4, 5, 6]]
    with pytest.raises(RuntimeError, match='solver failed'):
        with module.receiver_batch_targets(scene, points, Native, Jit):
            positions, orientations, relative, velocities = scene.targets(True, True)
            np.testing.assert_array_equal(positions, np.asarray(points, dtype=np.float32).T)
            np.testing.assert_array_equal(orientations, np.zeros((3, 2)))
            np.testing.assert_array_equal(relative, np.zeros((3, 2)))
            np.testing.assert_array_equal(velocities, np.zeros((3, 2)))
            assert scene.targets(True, False)[3] is None
            assert scene.targets(False, False)[0] == 'native'
            raise RuntimeError('solver failed')
    assert scene.targets is original


def test_worker_count_avoids_small_jobs_and_respects_cpu_and_batch_limits():
    choose = implementation().cpu_worker_count
    assert choose(256, 256, 32) == 1
    assert choose(67455, 256, 32) == 8
    assert choose(67455, 256, 4) == 2
    assert choose(67455, 256, 1) == 1
    assert choose(5000, 1024, 32) <= 5


def payload():
    module = implementation()
    return module.CoveragePaths(
        coefficients=np.array([[[1 + 0j], [0.5 + 0.25j]]]),
        delays=np.array([[1e-7, 2e-7]]), valid=np.array([[True, True]]),
        receiver_indices=np.array([0, 0]), path_indices=np.array([0, 1]),
        interaction_types=np.zeros((2, 1), dtype=np.uint32),
        shapes=np.full((2, 1), 0xffffffff, dtype=np.uint32),
        primitives=np.full((2, 1), 0xffffffff, dtype=np.uint32),
        vertices=np.zeros((2, 1, 3), dtype=np.float32))


BS = dict(id='BS', power_dbm=30, position_m=[0, 0, 10])


def test_real_spawn_matches_serial_and_cpu_workers_do_not_load_cuda():
    module = implementation()
    path_data = payload()
    expected = module.reduce_coverage_batch(path_data, BS, [[10, 0, 10]], 20e6, {})
    with module.CoveragePipeline({}, workers=2, max_pending=2) as pipeline:
        results = []
        for index in range(6):
            results.extend(pipeline.submit(index, path_data, BS, [[10, 0, 10]], 20e6))
        results.extend(pipeline.finish())
        assert pipeline.peak_pending <= 2
        assert len(pipeline.worker_pids) >= 1
    assert [item['first'] for item in results] == list(range(6))
    for item in results:
        assert item['links'] == expected['links']
        assert item['pid'] != os.getpid()
        assert not item['cuda_loaded']
    assert not any(name in sys.modules for name in ('mitsuba', 'sionna.rt'))


def test_cpu_error_is_propagated_instead_of_returning_partial_results():
    module = implementation()
    with module.CoveragePipeline({}, workers=2) as pipeline:
        with pytest.raises(ValueError):
            pipeline.submit(0, payload(), BS, [[10, 0, 10], [20, 0, 10]], 20e6)
            pipeline.finish()


def test_downloaded_paths_are_owned_cpu_arrays_and_reduce_without_native_geometry():
    module = implementation()
    data = payload()
    native = SimpleNamespace(
        cir=lambda **kwargs: (data.coefficients, data.delays), valid=data.valid,
        _rx_ind=data.receiver_indices, _path_ind=data.path_indices,
        _paths_buffer=SimpleNamespace(interaction_types=data.interaction_types,
            shapes=data.shapes, primitives=data.primitives, vertices_x=data.vertices[..., 0],
            vertices_y=data.vertices[..., 1], vertices_z=data.vertices[..., 2]))
    downloaded = module.download_coverage_paths(native)
    data.coefficients[:] = 0
    assert np.any(downloaded.coefficients != 0)
    actual = module.reduce_coverage_batch(downloaded, BS, [[10, 0, 10]], 20e6, {})
    assert actual['links'][0]['path_count'] == 2


def test_empty_native_batch_needs_no_sparse_path_indices():
    module = implementation()
    native = SimpleNamespace(cir=lambda **kwargs: (np.zeros((1, 0, 1), dtype=complex),
        np.zeros((1, 0))), valid=np.zeros((1, 0), dtype=bool))
    downloaded = module.download_coverage_paths(native)
    actual = module.reduce_coverage_batch(downloaded, BS, [[10, 0, 10]], 20e6, {})
    assert actual['links'][0]['path_count'] == 0
    assert actual['links'][0]['incoherent_dbm'] is None


def test_ordered_queue_waits_at_its_bound_and_does_not_reorder_completed_batches():
    module = implementation()
    class Executor:
        def __init__(self):
            self.futures = []
            self.finished = []
        def submit(self, function, data, bs, receivers, bandwidth):
            index = len(self.futures)
            owner = self
            class Deferred(Future):
                def result(self, timeout=None):
                    if not self.done():
                        owner.finished.append(index)
                        self.set_result(module.reduce_coverage_batch(data, bs, receivers, bandwidth, {}))
                    return super().result(timeout)
            future = Deferred()
            if index % 2:
                self.finished.append(index)
                future.set_result(module.reduce_coverage_batch(data, bs, receivers, bandwidth, {}))
            self.futures.append(future)
            return future
        def shutdown(self, **kwargs):
            pass
    executor = Executor()
    with module.CoveragePipeline({}, workers=2, max_pending=2, executor=executor) as pipeline:
        all_results = []
        for index in range(9):
            all_results.extend(pipeline.submit(index, payload(), BS, [[10, 0, 10]], 20e6))
        all_results.extend(pipeline.finish())
        assert pipeline.peak_pending <= 2
    assert [value['first'] for value in all_results] == list(range(9))
    assert executor.finished[:2] == [1, 0]


def test_cpu_children_exit_when_cuda_parent_is_terminated(tmp_path):
    implementation()
    import psutil
    root = Path(__file__).resolve().parents[1]
    ready = tmp_path / 'children.json'
    script = tmp_path / 'parent.py'
    script.write_text('''import multiprocessing as mp
import sys,time,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from ran_workbench.parallel_coverage import CoveragePipeline
if __name__ == '__main__':
    mp.freeze_support()
    with CoveragePipeline({}, workers=2) as pipeline:
        jobs=[pipeline._executor.submit(time.sleep, .1) for _ in range(2)]
        for job in jobs:job.result()
        Path(sys.argv[2]).write_text(json.dumps([p.pid for p in mp.active_children()]))
        time.sleep(60)
''', encoding='utf-8')
    process = subprocess.Popen([sys.executable, str(script), str(root), str(ready)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    children = []
    try:
        end = time.monotonic() + 15
        while not ready.exists() and process.poll() is None and time.monotonic() < end:
            time.sleep(.05)
        assert ready.exists(), process.stderr.read() if process.poll() is not None else 'CPU startup timed out'
        children = [psutil.Process(pid) for pid in json.loads(ready.read_text())]
        assert len(children) == 2
        process.terminate()
        process.wait(timeout=5)
        _, alive = psutil.wait_procs(children, timeout=5)
        assert not alive, f'Orphan CPU workers: {[p.pid for p in alive]}'
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for child in children:
            try:
                if child.is_running(): child.kill()
            except psutil.NoSuchProcess:
                pass
