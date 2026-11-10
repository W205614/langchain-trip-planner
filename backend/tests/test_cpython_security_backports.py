"""Exercise the installed CPython backports, without external DNS or HTTP."""
import http.client
import io
import os
import poplib
import socket

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get('TRIP_RUNTIME_ABI_CHECK') != '1',
    reason='Explicit rebuilt CPython image checks only',
)


@pytest.mark.parametrize('host', ['127.0.0.1\0.example.test', b'127.0.0.1\0.example.test'])
def test_getaddrinfo_rejects_embedded_host_nul(host):
    with pytest.raises(ValueError, match='host must not contain null'):
        socket.getaddrinfo(host, 80, flags=socket.AI_NUMERICHOST)


@pytest.mark.parametrize('port', ['80\0http', b'80\0http'])
def test_getaddrinfo_rejects_embedded_port_nul(port):
    with pytest.raises(ValueError, match='port must not contain null'):
        socket.getaddrinfo('127.0.0.1', port, flags=socket.AI_NUMERICHOST)


@pytest.mark.parametrize('host,port', [('127.0.0.1', 80), (b'127.0.0.1', '80'), ('127.0.0.1', b'80')])
def test_getaddrinfo_keeps_numeric_address_and_port_contract(host, port):
    result = socket.getaddrinfo(host, port, flags=socket.AI_NUMERICHOST | socket.AI_NUMERICSERV)
    assert result
    assert all(row[4][:2] == ('127.0.0.1', 80) for row in result)


class ResponseSocket:
    def __init__(self, wire):
        self.wire = wire

    def makefile(self, mode):
        assert mode == 'rb'
        return io.BytesIO(self.wire)


@pytest.mark.parametrize('length', [b'+2', b'2_0', b'-2', b'0x2'])
def test_http_client_does_not_interpret_non_decimal_content_lengths(length):
    response = http.client.HTTPResponse(ResponseSocket(
        b'HTTP/1.1 200 OK\r\nContent-Length: ' + length + b'\r\n\r\nOK'))
    response.begin()
    assert response.length is None
    assert response.will_close
    assert response.read() == b'OK'


@pytest.mark.parametrize('length', [b'2', b' 2 ', b'\t2\t'])
def test_http_client_preserves_optional_content_length_whitespace(length):
    response = http.client.HTTPResponse(ResponseSocket(
        b'HTTP/1.1 200 OK\r\nContent-Length: ' + length + b'\r\n\r\nOK'))
    response.begin()
    assert response.length == 2
    assert response.read() == b'OK'


@pytest.mark.parametrize('size', [b'+2', b'0x2', b'2_0', b'-2', b' 2'])
def test_http_client_rejects_non_hexadecimal_chunk_sizes(size):
    response = http.client.HTTPResponse(ResponseSocket(
        b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n' + size + b'\r\nOK\r\n0\r\n\r\n'))
    response.begin()
    with pytest.raises(http.client.IncompleteRead):
        response.read()


def test_http_client_keeps_chunk_extensions_and_valid_hex_sizes():
    response = http.client.HTTPResponse(ResponseSocket(
        b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2;name=value\r\nOK\r\n0\r\n\r\n'))
    response.begin()
    assert response.read() == b'OK'


class POP3Socket:
    def __init__(self):
        self.sent = []

    def sendall(self, command):
        self.sent.append(command)


def pop3_client():
    client = poplib.POP3.__new__(poplib.POP3)
    client._debugging = 0
    client.encoding = 'UTF-8'
    client.sock = POP3Socket()
    client.file = io.BytesIO(b'+OK accepted\r\n')
    return client


@pytest.mark.parametrize('method', ['user', 'pass_'])
@pytest.mark.parametrize('control', [chr(value) for value in range(32)] + ['\x7f'])
def test_pop3_rejects_every_c0_and_del_before_sending(method, control):
    client = pop3_client()
    with pytest.raises(ValueError, match='Control characters not allowed in commands'):
        getattr(client, method)('alice' + control + 'DELE 1')
    assert client.sock.sent == []


def test_pop3_rejects_crlf_command_injection_before_sending():
    client = pop3_client()
    with pytest.raises(ValueError, match='Control characters not allowed in commands'):
        client.user('alice\r\nDELE 1')
    assert client.sock.sent == []


@pytest.mark.parametrize('method,value,expected', [
    ('user', 'alice', b'USER alice\r\n'),
    ('pass_', 'secret', b'PASS secret\r\n'),
])
def test_pop3_keeps_valid_command_and_response_contract(method, value, expected):
    client = pop3_client()
    assert getattr(client, method)(value) == b'+OK accepted'
    assert client.sock.sent == [expected]
