"""Real loopback HTTP framing exercised while the Qt event loop stays live."""
import json
from pathlib import Path
import socket
import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication


@pytest.fixture
def server(tmp_path):
    from ran_workbench.automation_server import AutomationServer
    app = QCoreApplication.instance() or QCoreApplication([])
    calls = []
    def handler(request, respond):
        calls.append(request)
        respond({'ok': True, 'echo': request})
    instance = AutomationServer(handler, endpoint_file=tmp_path / '连接.json', timeout_ms=250)
    yield app, instance, calls
    instance.stop()
    app.processEvents()


def exchange(server, raw, chunks=False):
    app, instance, _ = server
    output = []
    def client():
        with socket.create_connection(('127.0.0.1', instance.port), timeout=3) as sock:
            if chunks:
                for part in raw:
                    sock.sendall(part)
                    time.sleep(.02)
            else:
                sock.sendall(raw)
            data = b''
            while block := sock.recv(65536):
                data += block
            output.append(data)
    thread = threading.Thread(target=client)
    thread.start()
    deadline = time.monotonic() + 4
    while thread.is_alive() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.001)
    thread.join(.1)
    assert output, 'HTTP request stalled the Qt event loop'
    head, body = output[0].split(b'\r\n\r\n', 1)
    return int(head.split()[1]), json.loads(body)


def packet(server, body=b'{"action":"get_state"}', **changes):
    instance = server[1]
    headers = {'Host': f'127.0.0.1:{instance.port}', 'Content-Type': 'application/json',
               'Content-Length': str(len(body)), 'Authorization': f'Bearer {instance.token}'}
    for key, value in changes.items():
        if value is None:
            headers.pop(key, None)
        else:
            headers[key] = value
    return ('POST /api/command HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in headers.items()) + '\r\n').encode() + body


def test_partial_utf8_frame_dispatches_once_and_writes_private_endpoint(server):
    body = json.dumps({'action': 'update_project', 'data': {'patch': {'name': '测试三维场景'}}}, ensure_ascii=False).encode()
    raw = packet(server, body)
    code, result = exchange(server, [raw[:20], raw[20:-5], raw[-5:]], chunks=True)
    assert code == 200 and result['ok']
    assert result['echo']['data']['patch']['name'] == '测试三维场景'
    assert len(server[2]) == 1
    record = json.loads(server[1].endpoint_file.read_text(encoding='utf-8'))
    assert record['version'] == 1 and record['url'] == server[1].url
    assert record['token'] == server[1].token


@pytest.mark.parametrize('headers,code', [
    ({'Authorization': None}, 401), ({'Authorization': 'Bearer wrong'}, 401),
    ({'Origin': 'http://127.0.0.1'}, 403), ({'Host': 'evil.example'}, 403),
    ({'Transfer-Encoding': 'chunked'}, 400), ({'Content-Length': '8388609'}, 413),
    ({'Content-Length': '-1'}, 400), ({'Content-Type': 'text/plain'}, 415),
])
def test_rejected_http_never_dispatches(server, headers, code):
    status, response = exchange(server, packet(server, **headers))
    assert status == code and response['ok'] is False and not server[2]


@pytest.mark.parametrize('body', [b'not json', b'{"action":NaN}', b'{"action":"run","action":"cancel"}', b'[]'])
def test_malformed_or_ambiguous_json_is_rejected(server, body):
    status, result = exchange(server, packet(server, body))
    assert status == 400 and not result['ok'] and not server[2]


def test_incomplete_body_times_out_without_blocking_gui(server):
    raw = packet(server, **{'Content-Length': '200'})
    status, result = exchange(server, raw)
    assert status == 408 and not result['ok'] and not server[2]


def test_stop_removes_only_owned_endpoint(server):
    instance = server[1]
    path = instance.endpoint_file
    instance.stop()
    assert not path.exists()


def test_stop_preserves_replaced_endpoint(server):
    instance = server[1]
    path = instance.endpoint_file
    changed = json.loads(path.read_text(encoding='utf-8'))
    changed['token'] = 'belongs-to-another-instance'
    path.write_text(json.dumps(changed), encoding='utf-8')
    instance.stop()
    assert path.is_file()


def test_server_does_not_overwrite_another_endpoint(tmp_path):
    from ran_workbench.automation_server import AutomationServer
    app = QCoreApplication.instance() or QCoreApplication([])
    path = tmp_path / 'already.json'
    path.write_text('{"owner":"user"}', encoding='utf-8')
    with pytest.raises(FileExistsError):
        AutomationServer(lambda *_: None, endpoint_file=path)
    assert json.loads(path.read_text(encoding='utf-8')) == {'owner': 'user'}
    app.processEvents()
