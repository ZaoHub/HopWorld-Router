"""Run sing-box so the local network egress is also reachable as Hysteria 2 and TUIC."""

import json
import os
from pathlib import Path

from router.core_process import CoreProcess
from router.local_node import build_quic_links, build_singbox_config


class SingboxService(CoreProcess):
    def __init__(self, data_dir, binary=None):
        super().__init__(data_dir, binary, program='sing-box', stem='singbox-local',
                         env='HOPWORLD_SINGBOX', command=('run',))

    def public_view(self, settings):
        from router.local_node import quic_ports, DEFAULT_LOCAL_PORT
        port = int(settings.get('port') or DEFAULT_LOCAL_PORT)
        try:
            hy2_port, tuic_port = quic_ports(port)
        except ValueError:
            hy2_port = tuic_port = 0
        protocols = settings.get('legacy_quic_protocols', ('hysteria2', 'tuic'))
        hy2_port = hy2_port if 'hysteria2' in protocols else 0
        tuic_port = tuic_port if 'tuic' in protocols else 0
        missing = not self.binary
        if not settings.get('enabled'):
            return {'available': False, 'error': '', 'missing': missing, 'hy2_port': hy2_port, 'tuic_port': tuic_port}
        if missing:
            return {'available': False, 'error': '未找到 sing-box，Hysteria 2 和 TUIC 未加入订阅', 'missing': True, 'hy2_port': hy2_port, 'tuic_port': tuic_port}
        if not self.links(settings):
            return {'available': False, 'error': self._error or 'Hysteria 2 / TUIC 未在运行', 'missing': False, 'hy2_port': hy2_port, 'tuic_port': tuic_port}
        return {'available': True, 'error': '', 'missing': False, 'hy2_port': hy2_port, 'tuic_port': tuic_port}

    def links(self, settings):
        with self._lock:
            return self._links_locked(settings)

    def _links_locked(self, settings):
        if not settings.get('enabled') or not self._alive_locked():
            return []
        try:
            meta = json.loads(self.meta_path.read_text(encoding='utf-8'))
            if meta.get('host') != settings['public_host'] or meta.get('port') != int(settings['port']):
                return []
            hy2, tuic = build_quic_links(
                settings['public_host'], int(settings['port']), settings['uuid'],
                settings['hy2_password'], settings['tuic_password'],
                settings.get('name') or '本地网络',
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return []
        protocols = settings.get('legacy_quic_protocols', ('hysteria2', 'tuic'))
        return [link for protocol, link in (('hysteria2', hy2), ('tuic', tuic)) if protocol in protocols]

    def apply(self, settings, certificate=None):
        with self._lock:
            self._error = ''
            protocols = settings.get('legacy_quic_protocols', ('hysteria2', 'tuic'))
            if os.name == 'nt' or not settings.get('enabled') or not self.binary or not protocols:
                self._stop_locked()
                return []
            if not certificate or not all(Path(path).is_file() for path in certificate):
                self._error = 'Hysteria 2 / TUIC 需要与公网域名匹配的有效证书'
                self._stop_locked()
                return []
            try:
                config = build_singbox_config(
                    settings['public_host'], int(settings['port']), settings['uuid'],
                    settings['hy2_password'], settings['tuic_password'], *certificate,
                )
                config['inbounds'] = [inbound for inbound in config['inbounds'] if inbound['type'] in protocols]
                if settings.get('public_host_prefer') in ('ipv6', 'dual', 'ipv4_first'):
                    for inbound in config['inbounds']:
                        inbound['listen'] = '::'
                payload = json.dumps(config, ensure_ascii=False, indent=2).encode('utf-8')
                meta = json.dumps({
                    'host': settings['public_host'], 'port': int(settings['port']),
                    'certificate_revision': [Path(path).stat().st_mtime_ns for path in certificate],
                }).encode('utf-8')
                if not self._matches(payload, meta) or not self._alive_locked():
                    self._write(self.config_path, payload)
                    self._write(self.meta_path, meta)
                    self._restart_locked()
                return self._links_locked(settings)
            except (OSError, ValueError, KeyError):
                self._error = '无法启动 Hysteria 2 / TUIC，请检查 sing-box、证书和 UDP 端口'
                self._stop_locked()
                return []
