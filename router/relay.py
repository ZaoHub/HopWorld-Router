"""Manage a single authenticated frp client for published node listeners."""

import base64
import hashlib
import json
import os
import re
import secrets
import socket
import ssl
import subprocess
import tempfile
from http.client import HTTPConnection, HTTPException
from pathlib import Path

from router.core_process import CoreProcess
from router.local_node import validate_public_host
from router.public_endpoint import is_ip_host


def validate_relay(data, previous):
    if set(data) - {'public_ports'} != {'domain', 'server', 'port', 'token', 'ca', 'cores'}:
        raise ValueError('VPS 设置字段无效')
    domain = validate_public_host(data['domain'])
    server = validate_public_host(data['server'])
    if '.' not in domain or is_ip_host(domain):
        raise ValueError('节点入口需要使用指向 VPS 的域名')
    port = data['port']
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('VPS 控制端口无效')
    if previous and server != previous['server'] and (not data['token'] or not data['ca']):
        raise ValueError('更换 VPS 控制地址时需要重新填写令牌和 CA 证书')
    token = data['token'] or previous.get('token', '')
    if not isinstance(data['token'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', token):
        raise ValueError('FRP 令牌需包含 32 至 128 个字母、数字、连字符或下划线')
    ca = data['ca'] or previous.get('ca', '')
    if not isinstance(data['ca'], str) or len(ca) > 12000 or 'PRIVATE KEY' in ca:
        raise ValueError('请填写 VPS 的 PEM 格式 CA 证书')
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=ca)
    except (ssl.SSLError, ValueError) as exc:
        raise ValueError('无法读取 VPS 的 PEM 格式 CA 证书') from exc
    return {**data, 'enabled': True, 'domain': domain, 'server': server, 'token': token, 'ca': ca}


class RelayClient(CoreProcess):
    def __init__(self, data_dir):
        super().__init__(Path(data_dir).resolve() / 'relay', None, program='frpc', stem='frpc',
                         env='HOPWORLD_FRPC', command=())
        self.admin = None

    def apply(self, settings, mappings):
        with self._lock:
            if not settings.get('enabled') or not mappings:
                if self.pid_path.exists() or self._process is not None:
                    self._stop_locked()
                self._error = ''
                return
            if os.name == 'nt' or not self.locate():
                raise ValueError('请在 Router 主机安装 frpc 后刷新状态')
            if self.admin is None:
                with socket.socket() as listener:
                    listener.bind(('127.0.0.1', 0))
                    self.admin = {'addr': '127.0.0.1', 'port': listener.getsockname()[1],
                                  'user': 'hopworld', 'password': secrets.token_urlsafe(32)}
            ca = settings['ca'].encode()
            ca_path = self.data_dir / 'vps-ca.pem'
            config = {
                'serverAddr': settings['server'], 'serverPort': settings['port'], 'loginFailExit': False,
                'auth': {'method': 'token', 'token': settings['token'],
                         'additionalScopes': ['HeartBeats', 'NewWorkConns']},
                'transport': {'tls': {'enable': True, 'trustedCaFile': str(ca_path),
                                      'serverName': settings['server']}},
                'webServer': self.admin,
                'log': {'to': 'console', 'disablePrintColor': True},
                'proxies': [
                    {'name': mapping['name'], 'type': mapping['transport'], 'localIP': '127.0.0.1',
                     'localPort': mapping['port'], 'remotePort': mapping.get('public_port', mapping['port'])}
                    for mapping in mappings
                ],
            }
            payload = json.dumps(config, indent=2).encode()
            revision = hashlib.sha256(ca).hexdigest().encode()
            if self._alive_locked() and self._matches(payload, revision):
                return
            with tempfile.NamedTemporaryFile(suffix='.json', dir=self.data_dir) as candidate:
                candidate.write(payload)
                candidate.flush()
                try:
                    subprocess.run([self.binary, 'verify', '-c', candidate.name], check=True, timeout=5,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except (OSError, subprocess.SubprocessError) as exc:
                    raise ValueError('frpc 配置校验失败，请检查安装版本') from exc
            self._stop_locked()
            self._write(ca_path, ca)
            self._write(self.config_path, payload)
            self._write(self.meta_path, revision)
            self._restart_locked()
            self._error = ''

    def proxy_status(self, token):
        with self._lock:
            if not self.admin or not self._alive_locked():
                return {}
            port = self.admin['port']
            credentials = f"{self.admin['user']}:{self.admin['password']}".encode()
        connection = HTTPConnection('127.0.0.1', port, timeout=0.6)
        try:
            connection.request('GET', '/api/status',
                               headers={'Authorization': 'Basic ' + base64.b64encode(credentials).decode()})
            response = connection.getresponse()
            if response.status != 200:
                return {}
            status = json.loads(response.read(32768))
            return {row['name']: {'registered': row['status'] == 'running',
                                  'error': self.redact(row.get('err', ''), token)[:300]}
                    for transport in ('tcp', 'udp') for row in status.get(transport, [])}
        except (HTTPException, OSError, ValueError, TypeError, KeyError, AttributeError):
            return {}
        finally:
            connection.close()

    def redact(self, text, token):
        for secret in (token, (self.admin or {}).get('password')):
            if secret:
                text = text.replace(secret, '[已隐藏]')
        return text
