"""Bounded CPU reductions alongside one CUDA producer; no native RT imports."""
from __future__ import annotations

from collections import deque
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
import math
import multiprocessing as mp
import os
import sys
import threading
import time
from types import SimpleNamespace


@contextmanager
def receiver_batch_targets(scene, positions, mi, dr):
    """Upload the workbench's zero-oriented, centered 1×1 RX batch at once.

    Sionna 2.2's default targets() builds many individual scatter operations
    per receiver. Receiver objects remain in the scene for Paths bookkeeping.
    The override is local to this scene and restored even if tracing fails.
    """
    if scene.rx_array.array_size != 1:
        yield
        return
    import numpy as np
    original = scene.targets
    had_override = 'targets' in vars(scene)
    points = mi.Point3f(np.asarray(positions, dtype=np.float32).T)
    zero = dr.zeros(mi.Point3f, len(positions))
    velocity = dr.zeros(mi.Vector3f, len(positions))

    def targets(synthetic_array, return_velocities):
        if not synthetic_array:
            return original(synthetic_array, return_velocities)
        return points, zero, zero, velocity if return_velocities else None

    scene.targets = targets
    try:
        yield
    finally:
        if had_override:
            scene.targets = original
        else:
            del scene.targets


def cpu_worker_count(points, batch_size, logical_cpus=None):
    """Reserve CPU capacity for the GPU driver/UI; amortize spawn on big jobs."""
    if points < 4096:
        return 1
    cpus = (os.cpu_count() or 1) if logical_cpus is None else logical_cpus
    return min(8, max(1, cpus // 2), max(1, math.ceil(points / batch_size)))


@dataclass
class CoveragePaths:
    coefficients: object
    delays: object
    valid: object
    receiver_indices: object
    path_indices: object
    interaction_types: object
    shapes: object
    primitives: object
    vertices: object

    def cir(self, **_kwargs):
        return self.coefficients, self.delays

    @property
    def _rx_ind(self):
        return self.receiver_indices

    @property
    def _path_ind(self):
        return self.path_indices

    @property
    def _paths_buffer(self):
        return SimpleNamespace(interaction_types=self.interaction_types,
            shapes=self.shapes, primitives=self.primitives,
            vertices_x=self.vertices[..., 0], vertices_y=self.vertices[..., 1],
            vertices_z=self.vertices[..., 2])


def download_coverage_paths(paths):
    """Own every transferred array before the CUDA paths object is released."""
    import numpy as np
    coefficients, delays = paths.cir(normalize_delays=False, out_type='numpy')
    if coefficients.shape[-2] == 0:
        # Sionna intentionally never creates sparse indices for an empty CIR.
        empty = np.empty((0, 0), dtype=np.uint32)
        indices = np.empty(0, dtype=np.int64)
        return CoveragePaths(np.array(coefficients, copy=True), np.array(delays, copy=True),
            np.array(paths.valid, copy=True), indices, indices.copy(), empty,
            empty.copy(), empty.copy(), np.empty((0, 0, 3), dtype=np.float32))
    buffer = paths._paths_buffer
    return CoveragePaths(*(np.array(value, copy=True) for value in (
        coefficients, delays, paths.valid, paths._rx_ind, paths._path_ind,
        buffer.interaction_types, buffer.shapes, buffer.primitives,
        np.stack([np.asarray(buffer.vertices_x), np.asarray(buffer.vertices_y),
                  np.asarray(buffer.vertices_z)], axis=-1))))


_catalog = None


def _initialize_reducer(catalog):
    global _catalog
    _catalog = catalog
    # Qt cancels the CUDA parent with TerminateProcess. The inherited parent
    # sentinel also reaps CPU workers if their parent cannot run pool.shutdown.
    parent = mp.parent_process()
    if parent is not None:
        def watch_parent():
            parent.join()
            os._exit(0)
        threading.Thread(target=watch_parent, daemon=True,
                         name='coverage-parent-watch').start()


def reduce_coverage_batch(paths, bs, receivers, bandwidth_hz, catalog=None):
    from .engine import _extract_links
    start = time.perf_counter()
    warnings = []
    links = _extract_links(paths, bs, receivers, bandwidth_hz, False, warnings,
                           _catalog if catalog is None else catalog)
    return dict(links=links, warnings=warnings, pid=os.getpid(),
                cuda_loaded='mitsuba' in sys.modules or 'sionna.rt' in sys.modules,
                cpu_elapsed_s=time.perf_counter() - start)


class CoveragePipeline:
    """Keep a bounded number of CPU batches; merge in stable submission order."""
    def __init__(self, catalog, workers=1, max_pending=None, executor=None):
        self.catalog = catalog
        self.workers = max(1, workers)
        self.max_pending = max(1, max_pending or 2 * self.workers)
        self._executor = executor
        self._pending = deque()
        self.peak_pending = 0
        self.worker_pids = set()
        self.cpu_elapsed_s = 0.0

    def __enter__(self):
        if self.workers > 1 and self._executor is None:
            self._executor = ProcessPoolExecutor(max_workers=self.workers,
                mp_context=mp.get_context('spawn'), initializer=_initialize_reducer,
                initargs=(self.catalog,))
        return self

    def _collect(self, block=False):
        ready = []
        while self._pending and (block or self._pending[0][1].done()):
            first, future = self._pending.popleft()
            value = dict(future.result(), first=first)
            self.worker_pids.add(value['pid'])
            self.cpu_elapsed_s += value['cpu_elapsed_s']
            ready.append(value)
            block = False
        return ready

    def submit(self, first, paths, bs, receivers, bandwidth_hz):
        if self._executor is None:
            value = dict(reduce_coverage_batch(paths, bs, receivers, bandwidth_hz,
                                               self.catalog), first=first)
            self.worker_pids.add(value['pid'])
            self.cpu_elapsed_s += value['cpu_elapsed_s']
            return [value]
        ready = self._collect(block=len(self._pending) >= self.max_pending)
        future = self._executor.submit(reduce_coverage_batch, paths, bs, receivers, bandwidth_hz)
        self._pending.append((first, future))
        self.peak_pending = max(self.peak_pending, len(self._pending))
        return ready + self._collect()

    def finish(self):
        ready = []
        while self._pending:
            ready.extend(self._collect(block=True))
        return ready

    def __exit__(self, *_exc):
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
