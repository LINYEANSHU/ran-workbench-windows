"""Real ZIP/HTTP fixtures pin integrity, recovery and disk-budget behavior."""
import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.fixture
def range_server():
    state = {'body': bytes(range(256)) * 8, 'etag': '"v1"', 'mode': 'ok', 'gets': 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_HEAD(self):
            self.send_response(200)
            self.send_header('Content-Length', str(len(state['body'])))
            self.send_header('ETag', state['etag'])
            self.end_headers()

        def do_GET(self):
            state['gets'] += 1
            if state['mode'] == 'fail_after_one' and state['gets'] > 1:
                self.send_error(503)
                return
            start, end = map(int, self.headers['Range'].split('=')[1].split('-'))
            body = state['body'][start:end + 1]
            self.send_response(200 if state['mode'] == 'ignore_range' else 206)
            self.send_header('Content-Range', f'bytes {start}-{end}/{len(state["body"])}')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('ETag', '"changed"' if state['mode'] == 'changed' else state['etag'])
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}/official.zip', state
    server.shutdown()
    server.server_close()
    thread.join()


def test_remote_zip_directory_and_member_bytes_are_original(range_server):
    from ran_workbench.city_data import HttpRangeFile, zip_inventory
    url, state = range_server
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('BUILDING/model.gltf', b'{"asset":{"version":"2.0"}}')
    state['body'] = stream.getvalue()
    inventory = zip_inventory(url)
    assert inventory['uncompressed_bytes'] == 27
    assert inventory['entries'][0]['path'] == 'BUILDING/model.gltf'
    with HttpRangeFile(url) as remote, zipfile.ZipFile(remote) as archive:
        assert archive.read('BUILDING/model.gltf') == b'{"asset":{"version":"2.0"}}'


@pytest.mark.parametrize('mode', ['ignore_range', 'changed'])
def test_remote_range_refuses_ignored_range_or_changed_version(range_server, mode):
    from ran_workbench.city_data import HttpRangeFile
    url, state = range_server
    state['mode'] = mode
    with HttpRangeFile(url) as remote:
        with pytest.raises((ValueError, OSError), match='[Rr]ange|[Vv]ersion|ETag'):
            remote.read(64)


def test_download_recovers_verified_chunks_after_interruption(range_server, tmp_path):
    from ran_workbench.city_data import StorageBudget, download_archive
    url, state = range_server
    state['mode'] = 'fail_after_one'
    budget = StorageBudget(tmp_path, 2_000_000, 0)
    destination = tmp_path / 'model.zip'
    with pytest.raises(OSError):
        download_archive(url, destination, budget, chunk_bytes=512, workers=1, retries=1)
    assert not destination.exists()
    state.update(mode='ok', gets=0)
    report = download_archive(url, destination, budget, chunk_bytes=512, workers=1)
    assert destination.read_bytes() == bytes(range(256)) * 8
    assert report['resumed_chunks'] >= 1
    assert report['bytes'] == 2048
    assert not destination.with_suffix('.zip.part').exists()


def test_storage_budget_counts_partial_files_before_allocating(tmp_path):
    from ran_workbench.city_data import StorageBudget
    (tmp_path / 'old.part').write_bytes(b'a' * 90)
    budget = StorageBudget(tmp_path, 100, 0)
    with pytest.raises(ValueError, match='budget|Budget'):
        budget.ensure(11)
    budget.ensure(10)


@pytest.mark.parametrize('member', ['../escaped.bin', '/escaped.bin', 'C:/escaped.bin', 'a/../../escaped.bin'])
def test_zip_extraction_rejects_path_escape_before_writing(tmp_path, member):
    from ran_workbench.city_data import StorageBudget, extract_archive
    archive = tmp_path / 'unsafe.zip'
    with zipfile.ZipFile(archive, 'w') as writer:
        writer.writestr(member, b'bad')
    with pytest.raises(ValueError, match='path|Path'):
        extract_archive(archive, tmp_path / 'raw', StorageBudget(tmp_path, 100_000, 0))
    assert not (tmp_path / 'raw').exists()


def test_extraction_records_checksums_and_does_not_accept_corrupt_payload(tmp_path):
    from ran_workbench.city_data import StorageBudget, extract_archive
    archive = tmp_path / 'valid.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_STORED) as writer:
        writer.writestr('BUILDING/m.bin', b'original payload')
    report = extract_archive(archive, tmp_path / 'raw', StorageBudget(tmp_path, 100_000, 0))
    assert report['complete'] is True
    assert report['files'][0]['bytes'] == 16
    assert len(report['files'][0]['sha256']) == 64
    body = archive.read_bytes().replace(b'original payload', b'corrupt! payload')
    archive.write_bytes(body)
    with pytest.raises(zipfile.BadZipFile):
        extract_archive(archive, tmp_path / 'other', StorageBudget(tmp_path, 100_000, 0))
    assert not (tmp_path / 'other' / 'BUILDING' / 'm.bin').exists()


def test_acquisition_plan_accounts_for_archives_raw_and_derived_budget():
    from ran_workbench.city_data import plan_tiles
    candidates = [{'tile': '11-SW-8D', 'bank': 'south', 'priority': 0},
                  {'tile': '11-SW-4B', 'bank': 'north', 'priority': 0},
                  {'tile': '11-SW-8C', 'bank': 'south', 'priority': 1}]
    inventories = {x['tile']: {'bytes': 20, 'uncompressed_bytes': 25} for x in candidates}
    plan = plan_tiles(candidates, inventories, limit_bytes=110, existing_bytes=5, derived_budget=10)
    assert {x['tile'] for x in plan['tiles']} == {'11-SW-8D', '11-SW-4B'}
    assert plan['planned_bytes'] == 105
    assert plan['excluded'][0]['tile'] == '11-SW-8C'


def test_replacing_unverified_file_is_budgeted_before_writing(tmp_path):
    from ran_workbench.city_data import StorageBudget, directory_bytes, extract_archive
    archive = tmp_path / 'new.zip'
    with zipfile.ZipFile(archive, 'w') as writer:
        writer.writestr('m.bin', b'x' * 50_000)
    destination = tmp_path / 'raw'
    destination.mkdir()
    (destination / 'm.bin').write_bytes(b'old')
    budget = StorageBudget(tmp_path, directory_bytes(tmp_path) + 20_000, 0)
    with pytest.raises(ValueError, match='budget'):
        extract_archive(archive, destination, budget)
    assert (destination / 'm.bin').read_bytes() == b'old'


def test_local_city_route_can_only_read_files_inside_its_dataset(tmp_path):
    from ran_workbench.city_data import resolve_city_route
    (tmp_path / 'model.glb').write_bytes(b'mesh')
    assert resolve_city_route(tmp_path, '/city-data/model.glb') == tmp_path / 'model.glb'
    with pytest.raises(ValueError, match='path|root'):
        resolve_city_route(tmp_path, '/city-data/%2e%2e/private.glb')
    (tmp_path / 'program.exe').write_bytes(b'not an asset')
    with pytest.raises(ValueError, match='type'):
        resolve_city_route(tmp_path, '/city-data/program.exe')
