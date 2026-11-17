"""Run sing-box so the local network egress is also reachable as Hysteria 2 and TUIC."""

import ipaddress
import json
import os
import shutil
import signal
import subprocess
import threading
from pathlib import Path

from router.local_node import build_quic_links, build_singbox_config


class SingboxService:
    def __init__(self, data_dir, binary=None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config_path = self.data_dir / 'singbox-local.json'
        self.meta_path = self.data_dir / 'singbox-local.meta.json'
        self.pid_path = self.data_dir / 'singbox-local.pid'
        self.binary = binary if binary is not None else (os.environ.get('HOPWORLD_SINGBOX') or shutil.which('sing-box'))
        self._lock = threading.Lock()
        self._process = None

    def public_view(self, settings):
        from router.local_node import quic_ports, DEFAULT_LOCAL_PORT
        port = int(settings.get('port') or DEFAULT_LOCAL_PORT)
        try:
            hy2_port, tuic_port = quic_ports(port)
        except ValueError:
            hy2_port = tuic_port = 0
        if not settings.get('enabled'):
            return {'available': False, 'error': '', 'hy2_port': hy2_port, 'tuic_port': tuic_port}
        if not self.binary:
            return {'available': False, 'error': '未找到 sing-box，Hysteria 2 和 TUIC 未加入订阅', 'hy2_port': hy2_port, 'tuic_port': tuic_port}
        if not self.running():
            return {'available': False, 'error': 'Hysteria 2 / TUIC 未在运行', 'hy2_port': hy2_port, 'tuic_port': tuic_port}
        return {'available': True, 'error': '', 'hy2_port': hy2_port, 'tuic_port': tuic_port}

    def links(self, settings):
        with self._lock:
            return self._links_locked(settings)

    def _links_locked(self, settings):
        if not settings.get('enabled') or not self._alive_locked():
            return []
        try:
            meta = json.loads(self.meta_path.read_text(encoding='utf-8'))
            hy2, tuic = build_quic_links(
                settings['public_host'], int(settings['port']), settings['uuid'],
                settings['hy2_password'], settings['tuic_password'],
                settings.get('name') or '本地网络', insecure=bool(meta['insecure']),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return []
        return [hy2, tuic]

    def apply(self, settings, certificate=None):
        with self._lock:
            if os.name == 'nt' or not settings.get('enabled') or not self.binary:
                self._stop_locked()
                return []
            cert_path, key_path, insecure = self._certificate(settings, certificate)
            config = build_singbox_config(
                settings['public_host'], int(settings['port']), settings['uuid'],
                settings['hy2_password'], settings['tuic_password'], cert_path, key_path,
            )
            payload = json.dumps(config, ensure_ascii=False, indent=2).encode('utf-8')
            meta = json.dumps({'insecure': insecure}, ensure_ascii=False).encode('utf-8')
            if self._matches(payload, meta) and self._alive_locked():
                return self._links_locked(settings)
            self._write(self.config_path, payload)
            self._write(self.meta_path, meta)
            self._restart_locked()
            return self._links_locked(settings)

    def stop(self):
        if os.name == 'nt':
            return
        with self._lock:
            self._stop_locked()

    def running(self):
        if os.name == 'nt':
            return False
        with self._lock:
            return self._alive_locked()

    def _certificate(self, settings, certificate):
        if certificate:
            cert_path, key_path = certificate
            if not Path(cert_path).is_file() or not Path(key_path).is_file():
                raise ValueError('Hysteria 2 / TUIC 证书不存在')
            return cert_path, key_path, False
        return (*self._self_signed(settings['public_host']), True)

    def _self_signed(self, host):
        directory = self.data_dir / 'quic'
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        cert = directory / 'cert.pem'
        key = directory / 'key.pem'
        marker = directory / 'host'
        if cert.is_file() and key.is_file() and marker.is_file() and marker.read_text(encoding='utf-8').strip() == host:
            return str(cert), str(key)
        openssl = shutil.which('openssl')
        if not openssl:
            raise ValueError('无法生成 Hysteria 2 / TUIC 证书，请安装 openssl 或先签发域名证书')
        try:
            san = f'IP:{host}' if _is_ip(host) else f'DNS:{host}'
            subprocess.run(
                [openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-keyout', str(key), '-out', str(cert),
                 '-days', '825', '-nodes', '-subj', '/CN=hopworld', '-addext', f'subjectAltName={san}'],
                check=True, capture_output=True, timeout=20,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError('无法生成 Hysteria 2 / TUIC 证书') from exc
        os.chmod(cert, 0o600)
        os.chmod(key, 0o600)
        marker.write_text(host, encoding='utf-8')
        os.chmod(marker, 0o600)
        return str(cert), str(key)

    def _matches(self, payload, meta):
        try:
            return self.config_path.read_bytes() == payload and self.meta_path.read_bytes() == meta
        except OSError:
            return False

    def _write(self, path, payload):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)

    def _alive_locked(self):
        if self._process is not None and self._process.poll() is None:
            return True
        pid = self._read_pid()
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _restart_locked(self):
        self._stop_locked()
        try:
            process = subprocess.Popen(
                [self.binary, 'run', '-c', str(self.config_path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
            )
        except OSError as exc:
            raise ValueError('无法启动 sing-box，请检查安装和执行权限') from exc
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        else:
            raise ValueError('sing-box 启动后立即退出，请检查配置、证书和 UDP 端口占用')
        self._process = process
        self.pid_path.write_text(str(process.pid), encoding='utf-8')
        os.chmod(self.pid_path, 0o600)

    def _stop_locked(self):
        if self._process is not None and self._process.poll() is None:
            _signal(self._process.pid, signal.SIGTERM)
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _signal(self._process.pid, signal.SIGKILL)
                self._process.wait(timeout=5)
        else:
            pid = self._read_pid()
            if pid is not None:
                _signal(pid, signal.SIGTERM)
        self._process = None
        try:
            self.pid_path.unlink()
        except OSError:
            pass

    def _read_pid(self):
        try:
            return int(self.pid_path.read_text(encoding='utf-8').strip())
        except (OSError, ValueError):
            return None


def _is_ip(host):
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _signal(pid, sig):
    try:
        os.kill(pid, sig)
    except OSError:
        pass
