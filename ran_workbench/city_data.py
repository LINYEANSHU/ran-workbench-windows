"""Bounded, version-pinned download and extraction of official city archives.

HTTP transport is generic for local integration tests. Dataset plans separately
restrict acquisition URLs to the government sources selected by the user.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import threading
import time
import urllib.request
from urllib.parse import unquote
import zipfile


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.writing')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def directory_bytes(root):
    total = 0
    root = Path(root).resolve()
    for folder, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            item = Path(folder) / name
            if item.is_symlink() or not item.resolve().is_relative_to(root):
                raise ValueError(f'External link in city data: {item}')
        total += sum((Path(folder) / name).stat().st_size for name in files)
    return total


def resolve_city_route(root, route):
    root = Path(root).resolve()
    if not route.startswith('/city-data/'):
        raise ValueError('Invalid city resource path')
    tail = unquote(route[len('/city-data/'):])
    path = (root / tail).resolve()
    if not path.is_relative_to(root):
        raise ValueError('City resource path escaped its data root')
    if path.suffix.lower() not in {'.json', '.gltf', '.glb', '.bin', '.jpg', '.jpeg', '.png', '.webp'}:
        raise ValueError('Unsupported city resource type')
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


class StorageBudget:
    def __init__(self, root, limit_bytes=100_000_000_000, reserve_free_bytes=15_000_000_000):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.limit = int(limit_bytes)
        self.reserve = int(reserve_free_bytes)
        if self.limit <= 0 or self.reserve < 0:
            raise ValueError('Invalid storage budget')

    def contains(self, path):
        if not Path(path).resolve().is_relative_to(self.root):
            raise ValueError('Data path is outside the storage budget root')

    def ensure(self, additional_bytes=0):
        additional = int(additional_bytes)
        if additional < 0:
            raise ValueError('Additional budget must be nonnegative')
        used = directory_bytes(self.root)
        if used + additional > self.limit:
            raise ValueError(f'City storage budget exceeded: {used}+{additional}>{self.limit}')
        free = shutil.disk_usage(self.root).free
        if free - additional < self.reserve:
            raise ValueError(f'Insufficient disk reserve: need {additional}+{self.reserve}, available {free}')
        return used


def _request(url, method='GET', headers=None, timeout=35):
    request = urllib.request.Request(url, method=method, headers={
        'User-Agent': 'RANWorkbench/1.0 (official city data acquisition)',
        'Accept-Encoding': 'identity', **(headers or {})})
    return urllib.request.urlopen(request, timeout=timeout)


def remote_metadata(url, timeout=35):
    with _request(url, 'HEAD', timeout=timeout) as response:
        size = int(response.headers.get('Content-Length', '-1'))
        if response.status != 200 or size <= 0:
            raise ValueError('Official archive must have a positive Content-Length')
        return {'url': url, 'bytes': size, 'etag': response.headers.get('ETag'),
                'last_modified': response.headers.get('Last-Modified')}


def _range_bytes(metadata, start, count, timeout=35, retries=3):
    if start < 0 or count <= 0 or start + count > metadata['bytes']:
        raise ValueError('Invalid Range boundaries')
    end = start + count - 1
    headers = {'Range': f'bytes={start}-{end}'}
    if metadata.get('etag'):
        headers['If-Match'] = metadata['etag']
    elif metadata.get('last_modified'):
        headers['If-Unmodified-Since'] = metadata['last_modified']
    last_error = None
    for attempt in range(retries):
        try:
            with _request(metadata['url'], headers=headers, timeout=timeout) as response:
                expected = f'bytes {start}-{end}/{metadata["bytes"]}'
                if response.status != 206 or response.headers.get('Content-Range') != expected:
                    raise ValueError('Range response status or Content-Range mismatch')
                if metadata.get('etag') and response.headers.get('ETag') != metadata['etag']:
                    raise ValueError('Remote archive version/ETag changed')
                if int(response.headers.get('Content-Length', '-1')) != count:
                    raise ValueError('Range Content-Length mismatch')
                data = response.read(count + 1)
                if len(data) != count:
                    raise OSError(f'Truncated Range body: expected {count}, received {len(data)}')
                return data
        except (OSError, TimeoutError) as error:
            last_error = error
            if attempt + 1 < retries:
                time.sleep(min(4, 0.5 * 2 ** attempt))
    raise OSError(f'Archive range download failed: {last_error}') from last_error


class HttpRangeFile(io.RawIOBase):
    """Seekable ZIP directory reader; a server ignoring Range never downloads a whole archive."""
    def __init__(self, url, metadata=None, timeout=35):
        super().__init__()
        self.metadata = dict(metadata or remote_metadata(url, timeout))
        self.metadata['url'] = url
        self.size = self.metadata['bytes']
        self.position = 0
        self.timeout = timeout

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        if whence not in (0, 1, 2):
            raise ValueError('Invalid seek origin')
        at = (0 if whence == 0 else self.position if whence == 1 else self.size) + offset
        if at < 0:
            raise ValueError('Negative archive position')
        self.position = at
        return at

    def read(self, size=-1):
        count = max(0, self.size - self.position) if size < 0 else min(size, max(0, self.size - self.position))
        if not count:
            return b''
        if count > 64 * 1024 * 1024:
            raise ValueError('ZIP directory read exceeds 64 MiB; refuse unbounded download')
        data = _range_bytes(self.metadata, self.position, count, self.timeout)
        self.position += len(data)
        return data


def safe_member_path(name):
    normalized = name.replace('\\', '/')
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ':' in normalized or '\x00' in normalized or '..' in path.parts:
        raise ValueError(f'Unsafe ZIP path: {name!r}')
    if any(part.endswith((' ', '.')) for part in path.parts):
        raise ValueError(f'Ambiguous ZIP path on Windows: {name!r}')
    return path


def _checked_entries(archive):
    entries, names = [], set()
    for info in archive.infolist():
        safe_member_path(info.filename)
        folded = info.filename.replace('\\', '/').casefold()
        if folded in names:
            raise ValueError(f'Duplicate ZIP path: {info.filename}')
        names.add(folded)
        if stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
            raise ValueError('ZIP links and encrypted members are unsupported')
        if not info.is_dir():
            entries.append(info)
    return entries


def zip_inventory(url, metadata=None):
    with HttpRangeFile(url, metadata) as remote, zipfile.ZipFile(remote) as archive:
        entries = _checked_entries(archive)
        return {**remote.metadata, 'uncompressed_bytes': sum(item.file_size for item in entries),
                'files': len(entries), 'entries': [dict(path=item.filename, size=item.file_size,
                 compressed=item.compress_size, crc=item.CRC, offset=item.header_offset,
                 method=item.compress_type) for item in entries]}


def plan_tiles(candidates, inventories, limit_bytes=100_000_000_000,
               existing_bytes=0, derived_budget=8_000_000_000):
    planned = existing_bytes + derived_budget
    selected, excluded, names = [], [], set()
    for candidate in sorted(candidates, key=lambda item: item.get('priority', 0)):
        name = candidate['tile']
        if name in names:
            raise ValueError('Duplicate candidate tile')
        names.add(name)
        inventory = inventories[name]
        cost = inventory['bytes'] + inventory['uncompressed_bytes']
        if planned + cost > limit_bytes:
            excluded.append({**candidate, 'reason': 'archive + raw + derived budget exceeds 100 GB'})
        else:
            selected.append({**candidate, 'metadata': {key: inventory.get(key) for key in
                ('url', 'bytes', 'etag', 'last_modified')}, 'uncompressed_bytes': inventory['uncompressed_bytes'],
                'file_count': inventory.get('files')})
            planned += cost
    if not selected or {item['bank'] for item in selected} != {'north', 'south'}:
        raise ValueError('Budget must include both Victoria Harbour banks')
    return {'schema': 1, 'limit_bytes': limit_bytes, 'existing_bytes': existing_bytes,
            'derived_budget_bytes': derived_budget, 'planned_bytes': planned,
            'archive_bytes': sum(item['metadata']['bytes'] for item in selected),
            'uncompressed_bytes': sum(item['uncompressed_bytes'] for item in selected),
            'tiles': selected, 'excluded': excluded}


def download_archive(url, destination, budget, chunk_bytes=8 * 1024 * 1024,
                     workers=4, metadata=None, retries=3, progress=None):
    """Resume only hash-verified chunks of the exact same ETag and file length."""
    destination = Path(destination).resolve()
    budget.contains(destination)
    current = remote_metadata(url)
    if metadata and any(current.get(k) != metadata.get(k) for k in ('bytes', 'etag')):
        raise ValueError('Archive version changed after planning; rebuild the acquisition plan')
    if not current.get('etag') and not current.get('last_modified'):
        raise ValueError('Archive has no version validator; safe resume unavailable')
    checkpoint = destination.with_name(destination.name + '.ranges.json')
    finished = destination.with_name(destination.name + '.sha256.json')
    partial = destination.with_name(destination.name + '.part')
    if destination.exists():
        record = json.loads(finished.read_text('utf-8')) if finished.exists() else {}
        if record.get('metadata') == current and destination.stat().st_size == current['bytes']:
            digest = sha256_file(destination)
            if digest == record.get('sha256'):
                return {**record, 'bytes': current['bytes'], 'resumed_chunks': 0, 'cached': True}
        raise ValueError(f'Existing archive is unverified or another version: {destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    state = {'metadata': current, 'chunk_bytes': chunk_bytes, 'chunks': {}}
    if partial.exists() or checkpoint.exists():
        saved = json.loads(checkpoint.read_text('utf-8')) if checkpoint.exists() else {}
        if saved.get('metadata') != current or saved.get('chunk_bytes') != chunk_bytes:
            raise ValueError('Partial archive version or chunk size differs; retain it and rebuild the plan')
        if not partial.exists() or partial.stat().st_size != current['bytes']:
            raise ValueError('Partial archive has an unexpected file length')
        state = saved
    else:
        budget.ensure(current['bytes'] + 1024 * 1024)
        atomic_json(checkpoint, state)
        with partial.open('xb') as stream:
            stream.truncate(current['bytes'])
    chunks = math.ceil(current['bytes'] / chunk_bytes)
    with partial.open('rb') as stream:
        for index, digest in list(state['chunks'].items()):
            number = int(index)
            if number < 0 or number >= chunks:
                del state['chunks'][index]
                continue
            stream.seek(number * chunk_bytes)
            body = stream.read(min(chunk_bytes, current['bytes'] - number * chunk_bytes))
            if hashlib.sha256(body).hexdigest() != digest:
                del state['chunks'][index]
    resumed = len(state['chunks'])
    pending = [i for i in range(chunks) if str(i) not in state['chunks']]

    def receive(index):
        start = index * chunk_bytes
        body = _range_bytes(current, start, min(chunk_bytes, current['bytes'] - start), retries=retries)
        with partial.open('r+b', buffering=0) as stream:
            stream.seek(start)
            stream.write(body)
            os.fsync(stream.fileno())
        return index, hashlib.sha256(body).hexdigest()

    with ThreadPoolExecutor(max_workers=max(1, min(4, workers))) as pool:
        futures = {pool.submit(receive, index): index for index in pending}
        try:
            for future in as_completed(futures):
                index, digest = future.result()
                state['chunks'][str(index)] = digest
                atomic_json(checkpoint, state)
                if progress:
                    progress({'stage': 'download', 'path': destination.name,
                              'chunks_done': len(state['chunks']), 'chunks_total': chunks,
                              'bytes_done': sum(min(chunk_bytes, current['bytes'] - int(i) * chunk_bytes)
                                                for i in state['chunks']), 'bytes_total': current['bytes']})
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    if len(state['chunks']) != chunks:
        raise OSError('Archive has incomplete chunks')
    digest = sha256_file(partial)
    record = {'metadata': current, 'bytes': current['bytes'], 'sha256': digest,
              'resumed_chunks': resumed, 'complete': True}
    # Publish the checksum before the archive: a crash can never expose a new
    # destination archive without the information needed to validate it.
    atomic_json(finished, record)
    os.replace(partial, destination)
    checkpoint.unlink(missing_ok=True)
    budget.ensure()
    return record


def extract_archive(archive_path, destination, budget, progress=None):
    destination = Path(destination).resolve()
    budget.contains(destination)
    manifest_path = destination / 'integrity.json'
    with zipfile.ZipFile(archive_path) as archive:
        entries = _checked_entries(archive)  # Check every path before creating output.
        saved = json.loads(manifest_path.read_text('utf-8')) if manifest_path.exists() else {}
        known = {item['path']: item for item in saved.get('files', [])}
        verified = {}
        for info in entries:
            path = destination / Path(*safe_member_path(info.filename).parts)
            old = known.get(info.filename, {})
            if (path.is_file() and old.get('crc32') == info.CRC and old.get('bytes') == info.file_size
                    and path.stat().st_size == info.file_size and sha256_file(path) == old.get('sha256')):
                verified[info.filename] = old
        # A replacement coexists with the previous file until atomic publication.
        # Reserve the entire new member, including when an unverified old file exists.
        needed = sum(item.file_size for item in entries if item.filename not in verified)
        budget.ensure(needed + len(entries) * 512 + 16_384)
        report = {'complete': False, 'archive': str(Path(archive_path).resolve()), 'files': []}
        destination.mkdir(parents=True, exist_ok=True)
        for index, info in enumerate(entries):
            path = destination / Path(*safe_member_path(info.filename).parts)
            if not path.resolve().is_relative_to(destination):
                raise ValueError('ZIP path escaped data root')
            if info.filename in verified:
                record = verified[info.filename]
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(path.name + '.extracting')
                digest, total = hashlib.sha256(), 0
                try:
                    with archive.open(info) as source, temporary.open('wb') as target:
                        for body in iter(lambda: source.read(1024 * 1024), b''):
                            total += len(body)
                            if total > info.file_size:
                                raise ValueError('ZIP member exceeded declared size')
                            target.write(body)
                            digest.update(body)
                    if total != info.file_size:
                        raise ValueError('ZIP member is truncated')
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
                record = {'path': info.filename, 'bytes': total, 'crc32': info.CRC,
                          'sha256': digest.hexdigest()}
            report['files'].append(record)
            if (index + 1) % 100 == 0:
                atomic_json(manifest_path, report)
                if progress:
                    progress({'stage': 'extract', 'files_done': index + 1, 'files_total': len(entries)})
        report.update(complete=True, file_count=len(entries),
                      uncompressed_bytes=sum(item.file_size for item in entries))
        atomic_json(manifest_path, report)
    budget.ensure()
    return report
