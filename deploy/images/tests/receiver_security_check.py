"""Run inside the actual receiver image with /tmp writable; no external network."""
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import poplib
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import ProxyHandler, Request, build_opener


class ResponseSocket:
    def __init__(self, data): self.data = data
    def makefile(self, mode):
        assert mode == 'rb'
        return io.BytesIO(self.data)


class POPSocket:
    def __init__(self): self.sent = []
    def sendall(self, data): self.sent.append(data)


class ReceiverSecurity(unittest.TestCase):
    def test_runtime_identity_and_removed_components(self):
        self.assertEqual(platform.python_version(), '3.13.18')
        self.assertEqual(os.geteuid(), 1000)
        for module in ('sqlite3', '_sqlite3', 'pip', 'setuptools'):
            with self.subTest(module=module): self.assertIsNone(importlib.util.find_spec(module))
        packages = Path('/lib/apk/db/installed').read_text()
        self.assertNotIn('\nP:sqlite-libs\n', '\n' + packages)
        self.assertFalse(list(Path('/usr/lib').glob('libsqlite3*')))
        self.assertFalse(list(Path('/usr/local/lib').rglob('_sqlite3*.so')))

    def test_socket_rejects_embedded_nul_and_preserves_numeric_addresses(self):
        for host in ('127.0.0.1\0.example.test', b'127.0.0.1\0.example.test'):
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, 'host must not contain null'):
                socket.getaddrinfo(host, 80, flags=socket.AI_NUMERICHOST)
        for port in ('80\0http', b'80\0http'):
            with self.subTest(port=port), self.assertRaisesRegex(ValueError, 'port must not contain null'):
                socket.getaddrinfo('127.0.0.1', port, flags=socket.AI_NUMERICHOST)
        for host, port in [('127.0.0.1', 80), (b'127.0.0.1', '80'), ('127.0.0.1', b'80')]:
            with self.subTest(host=host, port=port):
                rows = socket.getaddrinfo(host, port, flags=socket.AI_NUMERICHOST | socket.AI_NUMERICSERV)
                self.assertTrue(rows)
                self.assertTrue(all(row[4][:2] == ('127.0.0.1', 80) for row in rows))

    def test_http_response_framing(self):
        for length in (b'+2', b'2_0', b'-2', b'0x2', b'2', b' 2 ', b'\t2\t'):
            with self.subTest(content_length=length):
                r = http.client.HTTPResponse(ResponseSocket(b'HTTP/1.1 200 OK\r\nContent-Length: ' + length + b'\r\n\r\nOK'))
                r.begin()
                self.assertEqual(r.length, 2 if length.strip() == b'2' else None)
                self.assertEqual(r.read(), b'OK')
        for size in (b'+2', b'0x2', b'2_0', b'-2', b' 2'):
            with self.subTest(chunk_size=size):
                r = http.client.HTTPResponse(ResponseSocket(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n' + size + b'\r\nOK\r\n0\r\n\r\n'))
                r.begin()
                with self.assertRaises(http.client.IncompleteRead): r.read()
        r = http.client.HTTPResponse(ResponseSocket(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2;name=value\r\nOK\r\n0\r\n\r\n'))
        r.begin()
        self.assertEqual(r.read(), b'OK')

    def test_pop3_controls_never_reach_socket_and_normal_commands_work(self):
        def client():
            c = poplib.POP3.__new__(poplib.POP3)
            c._debugging = 0
            c.encoding = 'UTF-8'
            c.sock = POPSocket()
            c.file = io.BytesIO(b'+OK accepted\r\n')
            return c
        for method in ('user', 'pass_'):
            for control in [chr(i) for i in range(32)] + ['\x7f', '\r\n']:
                with self.subTest(method=method, control=repr(control)):
                    c = client()
                    with self.assertRaisesRegex(ValueError, 'Control characters not allowed in commands'):
                        getattr(c, method)('alice' + control + 'DELE 1')
                    self.assertEqual(c.sock.sent, [])
        for method, value, wire in [('user', 'alice', b'USER alice\r\n'), ('pass_', 'secret', b'PASS secret\r\n')]:
            with self.subTest(method=method, normal=True):
                c = client()
                self.assertEqual(getattr(c, method)(value), b'+OK accepted')
                self.assertEqual(c.sock.sent, [wire])

    def test_actual_receiver_http_deduplication_and_sigterm_preserve_inbox(self):
        open_local = build_opener(ProxyHandler({})).open
        with tempfile.TemporaryDirectory(prefix='receiver-check-') as directory:
            inbox = Path(directory) / 'alerts.jsonl'
            process = subprocess.Popen([sys.executable, '/app/receiver.py'], env={**os.environ, 'ALERT_INBOX': str(inbox)}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                for _ in range(40):
                    try:
                        with open_local('http://127.0.0.1:9088/healthz', timeout=.5) as r:
                            self.assertEqual(r.read(), b'ok')
                        break
                    except OSError:
                        if process.poll() is not None: self.fail('Receiver exited before readiness')
                        time.sleep(.1)
                else: self.fail('Receiver never became ready')
                body = json.dumps({'alerts': [{'labels': {'alertname': 'ReceiverSecuritySmoke', 'job': 'isolated'}, 'status': 'firing', 'startsAt': '2026-11-10T00:00:00Z'}]}).encode()
                for expected in (1, 0):
                    with open_local(Request('http://127.0.0.1:9088/alerts', data=body, headers={'Content-Type': 'application/json'}), timeout=2) as r:
                        self.assertEqual(json.load(r), {'accepted': expected})
                before = inbox.read_bytes()
                self.assertEqual(len(before.splitlines()), 1)
                process.send_signal(signal.SIGTERM)
                self.assertEqual(process.wait(timeout=5), 0)
                self.assertEqual(inbox.read_bytes(), before)
                self.assertFalse(inbox.with_suffix('.tmp').exists())
            finally:
                if process.poll() is None: process.terminate(); process.wait(timeout=5)
                process.communicate(timeout=5)


if __name__ == '__main__': unittest.main(verbosity=2)
