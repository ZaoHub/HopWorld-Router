"""Render and run authenticated publishers without a local proxy-core dependency."""

import base64
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import uuid
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from router.acme import HYSTERIA_CERTIFICATE_NAME
from router.core_process import CoreProcess
from router.local_node import (
    _hysteria_link, _link_host, _protocol_label, _tuic_link, build_local_reality_node,
    build_quic_links, build_singbox_config,
)
from router.xray_service import _add_metrics, _inbound_bytes, _read_core_counters


# program, environment override, command, default port, TLS, compatible fixed upstream
PUBLISHERS = {
    'xray-core': ('xray', 'HOPWORLD_XRAY', ('run',), 8443, False, 'xray'),
    'sing-box': ('sing-box', 'HOPWORLD_SINGBOX', ('run',), 8444, True, 'socks'),
    'mihomo': ('mihomo', 'HOPWORLD_MIHOMO', (), 8388, False, 'socks'),
    'hysteria2': ('hysteria', 'HOPWORLD_HYSTERIA', ('server', '--disable-update-check'), 8442, True, 'socks'),
    'shadowsocks-rust': ('ssserver', 'HOPWORLD_SSSERVER', (), 8389, False, None),
    'tuic-server': ('tuic-server', 'HOPWORLD_TUIC_SERVER', (), 8446, True, None),
}

COMPATIBLE_PUBLISHERS = {
    'hysteria2': ('sing-box', 'hysteria2'),
    'tuic': ('sing-box', 'tuic-server'),
    'ss': ('mihomo', 'shadowsocks-rust'),
}

def publisher_core(profile_id):
    return profile_id.partition('@')[0]


def singbox_ports(profile):
    port = profile['port']
    return {
        'hysteria2': port,
        'tuic': profile.get('tuic_port', port + 1 if port < 65535 else port - 1),
    }


def publisher_ports(core_id, profile):
    if core_id == 'sing-box':
        return {protocol: port for protocol, port in singbox_ports(profile).items()
                if protocol in profile.get('quic_protocols', ('hysteria2', 'tuic'))}
    for protocol, cores in COMPATIBLE_PUBLISHERS.items():
        if core_id in cores:
            return {protocol: profile['port']}
    return {}


def profile_credentials(profile):
    profile.setdefault('uuid', str(uuid.uuid4()))
    profile.setdefault('password', secrets.token_urlsafe(24))
    profile.setdefault('tuic_password', secrets.token_urlsafe(24))
    profile.setdefault('short_id', secrets.token_hex(4))


def publisher_bind(profile):
    if not profile['public_listener']:
        return '127.0.0.1'
    return '::' if profile.get('address_family') in ('ipv6', 'dual') else '0.0.0.0'


def _socks_options(proxy):
    server = proxy['settings']['servers'][0]
    options = {'server': server['address'], 'server_port': server['port']}
    if users := server.get('users'):
        options.update(username=users[0]['user'], password=users[0]['pass'])
    return options


def render_publisher(core_id, profile, certificate=None, proxy=None, *, certificate_pin=''):
    config, links = _render_publisher(core_id, profile, certificate, proxy, certificate_pin=certificate_pin)
    names = profile.get('link_names', {})
    for index, link in enumerate(links):
        parts = urlsplit(link)
        if parts.scheme in names:
            links[index] = parts._replace(fragment=names[parts.scheme]).geturl()
    if (core_id == 'xray-core' and profile.get('address_mode') == 'auto_ip'
            and profile.get('address_family') == 'dual' and (ipv6 := profile.get('public_host_ipv6'))):
        parts = urlsplit(links[0])
        credentials, _, _ = parts.netloc.rpartition('@')
        name = unquote(parts.fragment)
        links = [
            parts._replace(fragment=quote(f'{name} · IPv4')).geturl(),
            parts._replace(netloc=f'{credentials}@[{ipv6}]:{parts.port}',
                           fragment=quote(f'{name} · IPv6')).geturl(),
        ]
    return config, links


def _render_publisher(core_id, profile, certificate=None, proxy=None, *, certificate_pin=''):
    host, port, password = profile['public_host'], profile['port'], profile['password']
    bind = publisher_bind(profile)
    fixed = profile['egress'] == 'fixed'
    fixed_kind = PUBLISHERS[core_id][5]
    if profile['egress'] not in ('direct', 'fixed'):
        raise ValueError('请选择出口方式')
    if fixed and (not proxy or not fixed_kind or (fixed_kind == 'socks' and proxy['protocol'] != 'socks')):
        raise ValueError('绑定的上游不可用或协议不兼容')
    if PUBLISHERS[core_id][4] and not certificate:
        raise ValueError('请先在「域名与证书」配置与公网域名匹配的有效证书')

    if core_id == 'xray-core':
        config, uri = build_local_reality_node(
            host, port, profile['uuid'], profile['private_key'], profile['public_key'], profile['short_id'],
            name=profile.get('name', 'Xray · Reality'), proxy_pool=[proxy] if fixed else [], fixed_upstream=True,
            **{key: profile[key] for key in ('dest', 'server_name') if key in profile},
        )
        config['inbounds'][0]['listen'] = bind
        if not fixed:
            config['outbounds'] = [{'protocol': 'freedom', 'tag': 'direct'}]
        return config, [uri]

    socks = _socks_options(proxy) if fixed else {}
    if core_id == 'sing-box':
        ports = tuple(singbox_ports(profile).values())
        enabled = publisher_ports(core_id, profile)
        config = build_singbox_config(host, port, profile['uuid'], password, profile['tuic_password'],
                                      *certificate, ports=ports)
        config['inbounds'] = [inbound for inbound in config['inbounds'] if inbound['type'] in enabled]
        for inbound in config['inbounds']:
            inbound['listen'] = bind
        config['outbounds'] = ([{'type': 'socks', 'tag': 'egress', 'version': '5',
                                 'domain_resolver': 'system', **socks}]
                               if fixed else [{'type': 'direct', 'tag': 'egress'}])
        if fixed:
            config['dns'] = {'servers': [{'type': 'local', 'tag': 'system'}]}
        links = build_quic_links(host, port, profile['uuid'], password, profile['tuic_password'],
                                 profile.get('name', 'sing-box'), ports=ports)
        return config, [link for link in links if urlsplit(link).scheme in enabled]

    if core_id == 'hysteria2':
        name = _protocol_label(profile['name'], '原生 Hysteria2') if 'name' in profile else '原生 Hysteria2'
        if fixed:
            address = f"{_link_host(socks.pop('server'))}:{socks.pop('server_port')}"
            egress = {'name': 'egress', 'type': 'socks5', 'socks5': {'addr': address, **socks}}
        else:
            egress = {'name': 'egress', 'type': 'direct'}
        listen_host = '' if bind == '::' and profile.get('address_family') == 'dual' else _link_host(bind)
        config = {
            'listen': f'{listen_host}:{port}', 'tls': {'cert': certificate[0], 'key': certificate[1]},
            'auth': {'type': 'password', 'password': password}, 'outbounds': [egress],
        }
        if profile.get('tls_mode') == 'ip_acme':
            # The public link substitutes the certified address, while the node ID stays stable.
            return config, [_hysteria_link(HYSTERIA_CERTIFICATE_NAME, port, password, name, sni='')]
        return config, [_hysteria_link(host, port, password, name,
                                       sni=HYSTERIA_CERTIFICATE_NAME if certificate_pin else None,
                                       certificate_pin=certificate_pin)]

    if core_id == 'tuic-server':
        config = {
            'server': f'{_link_host(bind)}:{port}', 'users': {profile['uuid']: password},
            'certificate': certificate[0], 'private_key': certificate[1], 'alpn': ['h3'],
            'congestion_control': 'bbr', 'zero_rtt_handshake': False,
        }
        return config, [_tuic_link(host, port, profile['uuid'], password, '原生 TUIC')]

    cipher = 'aes-256-gcm'
    if core_id == 'mihomo':
        egress = 'DIRECT'
        config = {'allow-lan': profile['public_listener'], 'mode': 'rule', 'log-level': 'warning'}
        if fixed:
            egress = 'upstream'
            config['proxies'] = [{'name': egress, 'type': 'socks5', 'udp': True,
                                  'port': socks.pop('server_port'), **socks}]
        config['listeners'] = [{'name': 'shadowsocks-in', 'type': 'shadowsocks', 'listen': bind, 'port': port,
                                'cipher': cipher, 'password': password, 'udp': True, 'proxy': egress}]
        config['rules'] = [f'MATCH,{egress}']
        name = 'mihomo · Shadowsocks'
    else:
        config = {'server': bind, 'server_port': port, 'method': cipher, 'password': password, 'mode': 'tcp_and_udp'}
        name = 'shadowsocks-rust'
    credentials = base64.urlsafe_b64encode(f'{cipher}:{password}'.encode()).decode().rstrip('=')
    return config, [f'ss://{credentials}@{_link_host(host)}:{port}#{quote(name)}']


@lru_cache(maxsize=16)
def _has_clash_api(binary, modified_ns):
    try:
        result = subprocess.run([binary, 'version'], capture_output=True, text=True, timeout=1)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and any(
        'with_clash_api' in line.partition(':')[2].strip().split(',')
        for line in result.stdout.splitlines() if line.startswith('Tags:')
    )


def _hysteria_counters(payload):
    if payload is None:
        return None
    up = down = 0
    for value in payload.values():
        if not isinstance(value, dict) or any(type(value.get(key)) is not int or value[key] < 0 for key in ('rx', 'tx')):
            return None
        up += value['tx']
        down += value['rx']
    return up, down


class IndependentCore(CoreProcess):
    def __init__(self, data_dir, core_id, instance=''):
        program, env, command, *_ = PUBLISHERS[core_id]
        self.core_id = core_id
        stem = f'{core_id}-independent' + (f'-{instance}' if instance else '')
        if core_id == 'mihomo':
            command = ('-d', str(Path(data_dir) / (stem if instance else 'mihomo')))
        super().__init__(data_dir, None, program=program, stem=stem, env=env,
                         command=command, config_flag='-f' if core_id == 'mihomo' else '-c')
        self._stats_dir = None
        self._stats_port = None
        self._stats_secret = secrets.token_urlsafe(32)

    def _stats_socket(self):
        if self._stats_dir is None:
            self._stats_dir = tempfile.TemporaryDirectory(prefix='hwr-traffic-', dir='/tmp')
        return str(Path(self._stats_dir.name) / 'stats.sock')

    def _enable_traffic_counters(self, config):
        if self.core_id == 'xray-core':
            _add_metrics(config, self._stats_socket())
        elif self.core_id == 'mihomo':
            config['external-controller-unix'] = self._stats_socket()
            config['secret'] = self._stats_secret
        elif self.core_id == 'hysteria2' or (self.core_id == 'sing-box' and _has_clash_api(self.binary, Path(self.binary).stat().st_mtime_ns)):
            if self._stats_port is None:
                with socket.socket() as reserve:
                    reserve.bind(('127.0.0.1', 0))
                    self._stats_port = reserve.getsockname()[1]
            address = f'127.0.0.1:{self._stats_port}'
            if self.core_id == 'hysteria2':
                config['trafficStats'] = {'listen': address, 'secret': self._stats_secret}
            else:
                config.setdefault('experimental', {})['clash_api'] = {'external_controller': address, 'secret': self._stats_secret}

    def _stop_locked(self):
        super()._stop_locked()
        if self._stats_dir is not None:
            (Path(self._stats_dir.name) / 'stats.sock').unlink(missing_ok=True)

    def traffic_counters(self):
        with self._lock:
            if not self._alive_locked():
                return None
            if self.core_id == 'xray-core' and self._stats_dir is not None:
                counters = _inbound_bytes(_read_core_counters('/debug/vars', socket_path=self._stats_socket()), 'local-node-in')
            elif self.core_id == 'hysteria2' and self._stats_port:
                payload = _read_core_counters('/traffic', port=self._stats_port, secret=self._stats_secret)
                counters = _hysteria_counters(payload)
            elif self.core_id == 'mihomo' and self._stats_dir is not None:
                payload = _read_core_counters('/connections', socket_path=self._stats_socket(), secret='Bearer ' + self._stats_secret)
                counters = (payload.get('uploadTotal'), payload.get('downloadTotal')) if payload is not None else None
            elif self.core_id == 'sing-box' and self._stats_port:
                payload = _read_core_counters('/connections', port=self._stats_port, secret='Bearer ' + self._stats_secret)
                counters = (payload.get('uploadTotal'), payload.get('downloadTotal')) if payload is not None else None
            else:
                return None
            try:
                epoch = str(self.pid_path.stat().st_mtime_ns)
            except OSError:
                return None
            return (epoch, *counters) if counters is not None else None

    def apply_profile(self, config, links, certificate=None, *, bind='127.0.0.1', security_revision=None):
        with self._lock:
            binary = self.locate()
            if os.name == 'nt' or not binary or not shutil.which(binary):
                raise ValueError(f'请先安装可在本机运行的 {self.program}')
            try:
                self._enable_traffic_counters(config)
                payload = json.dumps(config, indent=2).encode()
                revision = [Path(path).stat().st_mtime_ns for path in certificate] if certificate else []
                metadata = {'links': links, 'certificate_revision': revision}
                if security_revision is not None:
                    metadata['security_revision'] = security_revision
                meta = json.dumps(metadata).encode()
                if self._alive_locked() and self._matches(payload, meta):
                    return
                self._validate(payload)
                previous = (self.config_path.read_bytes(), self.meta_path.read_bytes()) if self._alive_locked() else None
                try:
                    self._stop_locked()
                    self._check_ports(links, bind)
                    self._write(self.config_path, payload)
                    self._write(self.meta_path, meta)
                    self._restart_locked()
                except (OSError, ValueError) as error:
                    self._stop_locked()
                    if previous:
                        self._write(self.config_path, previous[0])
                        self._write(self.meta_path, previous[1])
                        self._restart_locked()
                    raise error
            except OSError as error:
                raise ValueError('内核配置或证书文件无法读取，请检查数据目录权限') from error

    def _check_ports(self, links, host):
        for uri in links:
            endpoint = urlsplit(uri)
            transports = ('TCP',) if endpoint.scheme == 'vless' else ('TCP', 'UDP') if endpoint.scheme == 'ss' else ('UDP',)
            for transport in transports:
                family = socket.AF_INET6 if ':' in host else socket.AF_INET
                with socket.socket(family, socket.SOCK_STREAM if transport == 'TCP' else socket.SOCK_DGRAM) as probe:
                    if transport == 'TCP':
                        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    try:
                        probe.bind((host, endpoint.port))
                    except OSError as error:
                        raise ValueError(f'{transport} 端口 {endpoint.port} 无法监听，请检查端口占用或权限') from error

    def _validate(self, payload):
        arguments = {'xray-core': ('run', '-test'), 'sing-box': ('check',), 'mihomo': (*self.command, '-t')}
        command = arguments.get(self.core_id)
        if command is None:
            return
        with tempfile.NamedTemporaryFile(suffix='.json', dir=self.data_dir) as candidate:
            candidate.write(payload)
            candidate.flush()
            try:
                subprocess.run([self.binary, *command, self.config_flag, candidate.name], check=True, timeout=6,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except subprocess.SubprocessError as error:
                raise ValueError(f'{self.program} 配置校验失败，请检查内核版本和证书') from error

    def security_revision(self):
        with self._lock:
            if self._alive_locked():
                try:
                    metadata = json.loads(self.meta_path.read_text())
                    if isinstance(metadata, dict):
                        return metadata.get('security_revision')
                except (OSError, ValueError):
                    pass
            return None

    def links(self):
        with self._lock:
            if not self._alive_locked():
                return []
            try:
                return json.loads(self.meta_path.read_text())['links']
            except (OSError, ValueError, KeyError):
                return []

    def ports(self):
        ports = [urlsplit(uri).port for uri in self.links()]
        if self.core_id == 'xray-core':
            return ports, []
        if self.core_id in ('mihomo', 'shadowsocks-rust'):
            return ports, ports
        return [], ports
