"""Observe DNS replies and exit addresses through the local publisher, never by direct fallback."""

import base64
from contextlib import contextmanager
from http.client import HTTPConnection, HTTPException, HTTPSConnection
import ipaddress
import json
from pathlib import Path
import secrets
import socket
import ssl
import struct
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

from router.probe import singbox_outbound
from router.public_endpoint import IPV4_ENDPOINTS, IPV6_ENDPOINTS


@contextmanager
def publisher_client(binary, link, bind):
    proxy = singbox_outbound(link)
    proxy.update(server='::1' if bind == '::' else '127.0.0.1', tag='publisher')
    with socket.socket() as reserve:
        reserve.bind(('127.0.0.1', 0))
        port = reserve.getsockname()[1]
    password = secrets.token_urlsafe(24)
    authorization = base64.b64encode(f'diagnostics:{password}'.encode()).decode()
    config = {
        'log': {'disabled': True},
        'inbounds': [{'type': 'http', 'listen': '127.0.0.1', 'listen_port': port,
                      'users': [{'username': 'diagnostics', 'password': password}]}],
        'outbounds': [proxy],
        'route': {'final': 'publisher'},
    }
    with tempfile.TemporaryDirectory(prefix='hopworld-security-probe-') as directory:
        path = Path(directory) / 'config.json'
        path.write_text(json.dumps(config))
        with subprocess.Popen([binary, 'run', '-c', str(path)], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as process:
            try:
                deadline = time.monotonic() + 3
                while process.poll() is None and time.monotonic() < deadline:
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=.15):
                            break
                    except OSError:
                        time.sleep(.05)
                else:
                    raise ValueError('无法启动本地诊断客户端')
                yield port, {'Proxy-Authorization': f'Basic {authorization}'}
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()


def dns_answer(port, qtype, *, hostname='example.com', headers=None):
    query_id = secrets.randbelow(65536)
    labels = b''.join(bytes([len(label)]) + label.encode('ascii') for label in hostname.split('.')) + b'\0'
    question = labels + struct.pack('!HH', qtype, 1)
    query = struct.pack('!6H', query_id, 0x0100, 1, 0, 0, 0) + question
    # The publisher intercepts the DNS destination; this documentation-range IP is not a resolver.
    connection = HTTPConnection('127.0.0.1', port, timeout=4)
    connection.set_tunnel('192.0.2.53', 53, headers=headers)
    try:
        connection.connect()
        connection.sock.sendall(struct.pack('!H', len(query)) + query)
        with connection.sock.makefile('rb') as stream:
            size = stream.read(2)
            if len(size) != 2:
                raise ValueError('DNS 响应不完整')
            expected = struct.unpack('!H', size)[0]
            reply = stream.read(expected)
        if len(reply) != expected or expected < 12:
            raise ValueError('DNS 响应不完整')
        response_id, flags, count, answers, _, _ = struct.unpack('!6H', reply[:12])
        if (response_id != query_id or flags & 0xFA0F != 0x8000 or count != 1 or not answers
                or reply[12:12 + len(question)].lower() != question.lower()):
            raise ValueError('DNS 响应未通过诊断校验')
        offset = 12 + len(question)
        matches = 0
        for _ in range(answers):
            while offset < len(reply):
                label = reply[offset]
                offset += 1
                if not label or label & 0xC0 == 0xC0:
                    offset += int(bool(label))
                    break
                if label > 63:
                    raise ValueError('DNS 答案格式无效')
                offset += label
            if offset + 10 > len(reply):
                raise ValueError('DNS 答案不完整')
            kind, record_class, _, length = struct.unpack_from('!HHIH', reply, offset)
            offset += 10 + length
            if offset > len(reply):
                raise ValueError('DNS 答案不完整')
            if kind == qtype and record_class == 1 and length == (4 if qtype == 1 else 16):
                matches += 1
        if not matches:
            raise ValueError('DNS 响应没有所请求的地址记录')
        return matches
    finally:
        connection.close()


def observed_address(port, url, family, *, headers):
    target = urlsplit(url)
    connection = HTTPSConnection('127.0.0.1', port, timeout=4, context=ssl.create_default_context())
    connection.set_tunnel(target.hostname, target.port or 443, headers=headers)
    try:
        connection.request('GET', target.path or '/', headers={'User-Agent': 'HopWorld-Router'})
        with connection.getresponse() as response:
            if response.status != 200:
                raise ValueError('出口观察点未返回有效响应')
            address = ipaddress.ip_address(response.read(65).decode('ascii').strip())
        if address.version != family or not address.is_global or address.is_multicast:
            raise ValueError('出口观察点返回的地址族或地址范围无效')
        return str(address)
    finally:
        connection.close()


def observe_publisher(binary, link, bind):
    checks = []
    with publisher_client(binary, link, bind) as (port, headers):
        for label, kind in (('DNS_A', 1), ('DNS_AAAA', 28)):
            try:
                count = dns_answer(port, kind, headers=headers)
                checks.append((label, 'PASS', f'发布入口收到 DNS 响应，答案数 {count}'))
            except (OSError, HTTPException, ValueError):
                checks.append((label, 'FAIL', '查询失败。检查上游、解析器地址和 TLS 证书。'))
        for family, url in ((4, IPV4_ENDPOINTS[0]), (6, IPV6_ENDPOINTS[0])):
            try:
                address = observed_address(port, url, family, headers=headers)
                checks.append((f'IPV{family}', 'INCONCLUSIVE', f'出口观察地址 {address}，仍需比对独立上游证据。'))
            except (OSError, HTTPException, ValueError):
                checks.append((f'IPV{family}', 'INCONCLUSIVE', '观察点未返回地址，需核对上游能力和阻断行为。'))
    return checks
