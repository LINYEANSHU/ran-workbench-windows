"""Authenticated, bounded loopback HTTP integrated with Qt's event loop."""
from datetime import datetime, timezone
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sys

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QHostAddress, QTcpServer


MAX_BODY = 8 * 1024 * 1024
MAX_HEADERS = 16 * 1024
MAX_RESPONSE = 128 * 1024 * 1024


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f'duplicate JSON key: {key}')
            result[key] = value
        return result
    def constant(value):
        raise ValueError(f'non-finite JSON value: {value}')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)


class AutomationServer(QObject):
    def __init__(self, handler, endpoint_file=None, parent=None, timeout_ms=10_000):
        super().__init__(parent)
        self.handler, self.timeout_ms = handler, timeout_ms
        self.token = secrets.token_urlsafe(32)
        self.connections = {}
        self.server = QTcpServer(self)
        if not self.server.listen(QHostAddress.SpecialAddress.LocalHost, 0):
            raise OSError('无法启动本机控制接口：' + self.server.errorString())
        self.port = self.server.serverPort()
        self.url = f'http://127.0.0.1:{self.port}'
        if endpoint_file is None:
            from .automation_client import default_endpoint_directory
            endpoint_file = default_endpoint_directory() / f'{os.getpid()}.json'
        self.endpoint_file = Path(endpoint_file).expanduser().resolve()
        self.metadata = {'version': 1, 'pid': os.getpid(), 'url': self.url, 'token': self.token,
                         'started_at': datetime.now(timezone.utc).isoformat(), 'executable': sys.executable}
        try:
            self.endpoint_file.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation prevents an explicit path from replacing another live instance.
            descriptor = os.open(self.endpoint_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(self.metadata, stream, ensure_ascii=False, allow_nan=False)
        except Exception:
            self.server.close()
            raise
        self.server.newConnection.connect(self._accept)

    def _accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if len(self.connections) >= 16:
                socket.abort()
                socket.deleteLater()
                continue
            timer = QTimer(socket)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda s=socket: self._error(s, 408, 'REQUEST_TIMEOUT', '请求未在时限内完成'))
            self.connections[socket] = {'buffer': bytearray(), 'length': None, 'handled': False, 'sent': False, 'timer': timer}
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(lambda s=socket: self._remove(s))
            timer.start(self.timeout_ms)
            if socket.bytesAvailable():
                self._read(socket)

    def _remove(self, socket):
        state = self.connections.pop(socket, None)
        if state:
            state['timer'].stop()
        socket.deleteLater()

    def _error(self, socket, status, code, message):
        self._send(socket, {'ok': False, 'error_code': code, 'error': message}, status)

    def _read(self, socket):
        state = self.connections.get(socket)
        if not state or state['handled']:
            return
        state['buffer'].extend(bytes(socket.readAll()))
        if len(state['buffer']) > MAX_BODY + MAX_HEADERS:
            self._error(socket, 413, 'REQUEST_TOO_LARGE', '请求过大')
            return
        if state['length'] is None:
            end = state['buffer'].find(b'\r\n\r\n')
            if end < 0:
                if len(state['buffer']) > MAX_HEADERS:
                    self._error(socket, 431, 'HEADERS_TOO_LARGE', 'HTTP 请求头过大')
                return
            if end > MAX_HEADERS:
                self._error(socket, 431, 'HEADERS_TOO_LARGE', 'HTTP 请求头过大')
                return
            try:
                self._headers(socket, state, bytes(state['buffer'][:end]))
            except (ValueError, UnicodeError) as error:
                self._error(socket, 400, 'INVALID_HTTP', str(error))
                return
            if state['sent']:
                return
            del state['buffer'][:end + 4]
        length = state['length']
        if len(state['buffer']) < length:
            return
        if len(state['buffer']) != length:
            self._error(socket, 400, 'INVALID_HTTP', '每个连接只接受一个完整请求')
            return
        try:
            request = strict_json(bytes(state['buffer']))
            if not isinstance(request, dict):
                raise ValueError('JSON 请求必须为对象')
        except (ValueError, UnicodeError, RecursionError) as error:
            self._error(socket, 400, 'INVALID_JSON', str(error))
            return
        state['handled'] = True
        state['buffer'].clear()
        state['timer'].start(15_000)
        try:
            self.handler(request, lambda result: self._send(socket, result))
        except Exception as error:
            self._error(socket, 500, 'INTERNAL_ERROR', str(error))

    def _headers(self, socket, state, raw):
        lines = raw.decode('ascii').split('\r\n')
        if lines[0] != 'POST /api/command HTTP/1.1':
            self._error(socket, 404, 'NOT_FOUND', '仅支持 POST /api/command HTTP/1.1')
            return
        headers = {}
        for line in lines[1:]:
            key, separator, value = line.partition(':')
            if not separator or not re.fullmatch(r'[A-Za-z0-9-]+', key):
                raise ValueError('无效 HTTP 请求头')
            key = key.lower()
            if key in headers:
                raise ValueError('不接受重复 HTTP 请求头')
            headers[key] = value.strip()
        if 'origin' in headers or headers.get('host') != f'127.0.0.1:{self.port}':
            self._error(socket, 403, 'LOCAL_CLIENT_REQUIRED', '只允许本机非浏览器客户端')
            return
        if not hmac.compare_digest(headers.get('authorization', ''), 'Bearer ' + self.token):
            self._error(socket, 401, 'UNAUTHORIZED', '控制接口令牌无效')
            return
        if 'transfer-encoding' in headers or not re.fullmatch(r'[0-9]+', headers.get('content-length', '')):
            raise ValueError('需要唯一有效的 Content-Length，不接受分块请求')
        length = int(headers['content-length'])
        if length > MAX_BODY:
            self._error(socket, 413, 'REQUEST_TOO_LARGE', '请求体上限为 8 MiB')
            return
        if headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
            self._error(socket, 415, 'JSON_REQUIRED', 'Content-Type 必须为 application/json')
            return
        state['length'] = length

    def _send(self, socket, response, status=200):
        state = self.connections.get(socket)
        if not state or state['sent']:
            return
        try:
            body = json.dumps(response, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
            if len(body) > MAX_RESPONSE:
                raise ValueError('响应过大，请使用结果文件导出')
        except (ValueError, TypeError, RecursionError) as error:
            status = 500
            body = json.dumps({'ok': False, 'error_code': 'INVALID_RESPONSE', 'error': str(error)}).encode('utf-8')
        state['sent'] = state['handled'] = True
        state['timer'].stop()
        reason = {200: 'OK', 400: 'Bad Request', 401: 'Unauthorized', 403: 'Forbidden', 404: 'Not Found',
                  408: 'Request Timeout', 413: 'Content Too Large', 415: 'Unsupported Media Type',
                  431: 'Request Header Fields Too Large', 500: 'Internal Server Error'}.get(status, 'Error')
        header = f'HTTP/1.1 {status} {reason}\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n'.encode('ascii')
        socket.write(header + body)
        socket.disconnectFromHost()

    def stop(self):
        self.server.close()
        for socket in list(self.connections):
            socket.abort()
        try:
            current = json.loads(self.endpoint_file.read_text(encoding='utf-8'))
            if current.get('pid') == os.getpid() and current.get('token') == self.token:
                self.endpoint_file.unlink()
        except (OSError, ValueError):
            pass
