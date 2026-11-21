"""Publish an authenticated node with the standalone Hysteria 2 server."""

import json
import os
from pathlib import Path

from router.core_process import CoreProcess
from router.local_node import build_native_hysteria, native_hysteria_port


class HysteriaService(CoreProcess):
    def __init__(self, data_dir, binary=None):
        super().__init__(data_dir, binary, program='hysteria', stem='hysteria-local',
                         env='HOPWORLD_HYSTERIA', command=('server', '--disable-update-check'))

    def link(self, settings):
        with self._lock:
            if not settings.get('enabled') or not settings.get('native_hysteria_enabled') or not self._alive_locked():
                return ''
            try:
                config = json.loads(self.config_path.read_text(encoding='utf-8'))
                tls = config['tls']
                expected, link = build_native_hysteria(settings, tls['cert'], tls['key'])
                meta = self.meta_path.read_text(encoding='utf-8')
                host = json.loads(meta)['host'] if meta.startswith('{') else meta
                if config == expected and host == settings['public_host']:
                    return link
            except (OSError, ValueError, KeyError, TypeError):
                pass
            return ''

    def apply(self, settings, certificate=None):
        with self._lock:
            self._error = ''
            if not settings.get('enabled') or not settings.get('native_hysteria_enabled'):
                self._stop_locked()
                return
            try:
                if os.name == 'nt' or not self.locate():
                    raise ValueError('未找到原生 Hysteria 2，请先安装内核')
                if not certificate or not all(Path(path).is_file() for path in certificate):
                    raise ValueError('原生 Hysteria 2 需要与公网域名匹配的有效证书，请先在「域名与证书」配置')
                config, _ = build_native_hysteria(settings, *certificate)
                payload = json.dumps(config, indent=2).encode('utf-8')
                meta = json.dumps({'host': settings['public_host'],
                                   'certificate_revision': [Path(path).stat().st_mtime_ns for path in certificate]}).encode()
                if not self._matches(payload, meta) or not self._alive_locked():
                    self._write(self.config_path, payload)
                    self._write(self.meta_path, meta)
                    self._restart_locked()
            except (OSError, ValueError) as exc:
                self._stop_locked()
                self._error = str(exc) if isinstance(exc, ValueError) else '无法写入 Hysteria 2 配置，请检查数据目录权限'
                raise ValueError(self._error) from None

    def public_view(self, settings):
        enabled = bool(settings.get('native_hysteria_enabled'))
        return {
            'enabled': enabled,
            'running': bool(self.link(settings)),
            'port': native_hysteria_port(settings['port']) if enabled else None,
            'error': self._error if enabled and settings.get('enabled') else '',
        }
