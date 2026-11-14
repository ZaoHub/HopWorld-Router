"""Manage the persistent Xray process used by the local network node."""

import json
import http.client
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import uuid
import weakref
from pathlib import Path

from router.local_node import (
    DEFAULT_LOCAL_PORT,
    DEFAULT_NODE_NAME,
    DEFAULT_REALITY_DEST,
    DEFAULT_REALITY_SERVER_NAME,
    build_local_reality_node,
    validate_public_host,
)


class XrayService:
    def __init__(self, data_dir, binary=None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config_path = self.data_dir / 'xray-local.json'
        self.pid_path = self.data_dir / 'xray-local.pid'
        self.binary = binary or os.environ.get('HOPWORLD_XRAY') or shutil.which('xray')
        self._lock = threading.Lock()
        self._metrics_lock = threading.Lock()
        self._metrics_dir = None
        self._process = None

    def generate_keys(self):
        if not self.binary:
            raise ValueError('未找到 Xray，请安装后设置 HOPWORLD_XRAY')
        try:
            completed = subprocess.run(
                [self.binary, 'x25519'],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError('无法生成 Reality 密钥') from exc
        private_key = public_key = None
        for line in completed.stdout.splitlines():
            lower = line.lower()
            if 'private' in lower:
                private_key = line.split(':', 1)[-1].strip()
            elif 'public' in lower or lower.split(':', 1)[0].strip() == 'password':
                public_key = line.split(':', 1)[-1].strip()
        if not private_key or not public_key:
            raise ValueError('无法解析 Reality 密钥')
        return private_key, public_key

    def default_settings(self, public_host=''):
        host = ''
        if public_host:
            try:
                host = validate_public_host(public_host)
            except ValueError:
                host = ''
        auto_env = os.environ.get('HOPWORLD_PUBLIC_HOST_AUTO', '').strip().lower()
        prefer = os.environ.get('HOPWORLD_PUBLIC_HOST_PREFER', 'ipv4').strip().lower()
        if prefer not in ('ipv4', 'ipv6', 'ipv4_first'):
            prefer = 'ipv4'
        return {
            'enabled': False,
            'port': DEFAULT_LOCAL_PORT,
            'public_host': host,
            'auto_public_host': auto_env in ('1', 'true', 'yes', 'on'),
            'ddns_enabled': os.environ.get('HOPWORLD_DDNS', '').strip().lower() in ('1', 'true', 'yes', 'on'),
            'ddns_ipv4': '',
            'ddns_ipv6': '',
            'ddns_updated_at': 0,
            'public_host_prefer': prefer,
            'public_host_detected_at': 0,
            'uuid': str(uuid.uuid4()),
            'private_key': '',
            'public_key': '',
            'short_id': secrets.token_hex(4),
            'dest': DEFAULT_REALITY_DEST,
            'server_name': DEFAULT_REALITY_SERVER_NAME,
            'name': DEFAULT_NODE_NAME,
        }

    def ensure_credentials(self, settings):
        if os.name == 'nt':
            raise ValueError('Windows 原生环境暂不支持本地网络节点，请使用 WSL 2 或 Docker')
        updated = dict(settings)
        if not updated.get('uuid'):
            updated['uuid'] = str(uuid.uuid4())
        if not updated.get('short_id') or not re.fullmatch(r'[0-9a-fA-F]{2,16}', str(updated['short_id'])):
            updated['short_id'] = secrets.token_hex(4)
        if not updated.get('private_key') or not updated.get('public_key'):
            private_key, public_key = self.generate_keys()
            updated['private_key'] = private_key
            updated['public_key'] = public_key
        updated.setdefault('dest', DEFAULT_REALITY_DEST)
        updated.setdefault('server_name', DEFAULT_REALITY_SERVER_NAME)
        updated.setdefault('port', DEFAULT_LOCAL_PORT)
        updated.setdefault('name', DEFAULT_NODE_NAME)
        return updated

    def _metrics_path(self):
        if os.name == 'nt':
            return None
        with self._metrics_lock:
            if self._metrics_dir is None:
                try:
                    self._metrics_dir = tempfile.mkdtemp(prefix='hwr-metrics-', dir='/tmp')
                    weakref.finalize(self, shutil.rmtree, self._metrics_dir, ignore_errors=True)
                except OSError:
                    return None
            path = os.path.join(self._metrics_dir, 'metrics.sock')
            return path if len(os.fsencode(path)) < 100 else None

    def build(self, settings):
        config, link = build_local_reality_node(
            settings['public_host'],
            int(settings['port']),
            settings['uuid'],
            settings['private_key'],
            settings['public_key'],
            settings['short_id'],
            settings.get('dest', DEFAULT_REALITY_DEST),
            settings.get('server_name', DEFAULT_REALITY_SERVER_NAME),
            settings.get('name') or DEFAULT_NODE_NAME,
            (settings.get('proxy_pool') or []) if settings.get('egress_mode') in ('auto', 'local_proxy') else None,
            settings.get('auto_probe_url'),
            settings.get('egress_mode') == 'local_proxy',
            settings.get('auto_interval', 60),
        )
        if settings.get('egress_mode') == 'auto' and settings.get('proxy_pool'):
            path = self._metrics_path()
            if path:
                config['metrics'] = {'tag': 'internal-metrics'}
                config['inbounds'].append({'tag': 'internal-metrics-in', 'listen': path, 'protocol': 'dokodemo-door', 'settings': {'address': '127.0.0.1', 'port': 80, 'network': 'tcp'}})
                config['routing']['rules'].insert(0, {'type': 'field', 'inboundTag': ['internal-metrics-in'], 'outboundTag': 'internal-metrics'})
        return config, link

    def auto_observations(self):
        path = self._metrics_path()
        if not path:
            return None
        conn = http.client.HTTPConnection('localhost', timeout=0.5)
        try:
            conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            conn.sock.settimeout(0.5)
            conn.sock.connect(path)
            conn.request('GET', '/debug/vars', headers={'Connection': 'close'})
            response = conn.getresponse()
            if response.status != 200:
                return None
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                return None
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return None
            observations = payload.get('observatory')
            return observations if isinstance(observations, dict) else None
        except (OSError, ValueError, http.client.HTTPException):
            return None
        finally:
            conn.close()

    def public_view(self, settings, running=None):
        view = {
            'enabled': bool(settings.get('enabled')),
            'port': int(settings.get('port') or DEFAULT_LOCAL_PORT),
            'public_host': settings.get('public_host') or '',
            'auto_public_host': bool(settings.get('auto_public_host')),
            'ddns_enabled': bool(settings.get('ddns_enabled')),
            'public_host_prefer': settings.get('public_host_prefer') or 'ipv4',
            'public_host_detected_at': int(settings.get('public_host_detected_at') or 0),
            'name': settings.get('name') or DEFAULT_NODE_NAME,
            'dest': settings.get('dest') or DEFAULT_REALITY_DEST,
            'server_name': settings.get('server_name') or DEFAULT_REALITY_SERVER_NAME,
            'running': self.running() if running is None else bool(running),
            'egress_mode': settings.get('egress_mode', 'default'),
            'local_proxy': settings.get('local_proxy', ''),
            'auto_pool_count': len(settings.get('proxy_pool') or []) if settings.get('egress_mode') == 'auto' else 0,
            'auto_target': settings.get('auto_target', 'google'),
            'auto_interval': settings.get('auto_interval', 60),
            'link': '',
        }
        if settings.get('enabled') and settings.get('public_host') and settings.get('public_key'):
            try:
                _, link = self.build(settings)
                view['link'] = link
            except ValueError:
                view['link'] = ''
        return view

    def running(self):
        if os.name == 'nt':
            return False
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return True
            pid = self._read_pid()
            if pid is None:
                return False
            try:
                os.kill(pid, 0)
            except OSError:
                self._clear_pid()
                return False
            return True

    def apply(self, settings):
        with self._lock:
            if os.name == 'nt':
                if settings.get('enabled'):
                    raise ValueError('Windows 原生环境暂不支持本地网络节点，请使用 WSL 2 或 Docker')
                return ''
            if settings.get('enabled'):
                config, link = self.build(settings)
                payload = json.dumps(config, ensure_ascii=False, indent=2).encode('utf-8')
                if self._config_matches(payload) and self._alive_locked():
                    return link
                self._write_config(config)
                self._restart_locked()
                return link
            self._stop_locked()
            if self.config_path.exists():
                self.config_path.unlink()
            return ''

    def stop(self):
        if os.name == 'nt':
            return
        with self._lock:
            self._stop_locked()

    def _config_matches(self, payload):
        try:
            return self.config_path.read_bytes() == payload
        except OSError:
            return False

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

    def _write_config(self, config):
        payload = json.dumps(config, ensure_ascii=False, indent=2)
        fd = os.open(self.config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, payload.encode('utf-8'))
        finally:
            os.close(fd)

    def _restart_locked(self):
        if not self.binary:
            raise ValueError('未找到 Xray，请安装后设置 HOPWORLD_XRAY')
        self._stop_locked()
        try:
            process = subprocess.Popen(
                [self.binary, 'run', '-c', str(self.config_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise ValueError('无法启动 Xray，请检查安装和执行权限') from exc
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        else:
            raise ValueError('Xray 启动后立即退出，请检查配置和端口占用')
        self._process = process
        self.pid_path.write_text(str(process.pid), encoding='utf-8')
        os.chmod(self.pid_path, 0o600)

    def _stop_locked(self):
        if self._process is not None and self._process.poll() is None:
            self._terminate(self._process.pid)
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._kill(self._process.pid)
                self._process.wait(timeout=5)
        else:
            pid = self._read_pid()
            if pid is not None:
                self._terminate(pid)
                for _ in range(20):
                    try:
                        os.kill(pid, 0)
                    except OSError:
                        break
                    else:
                        import time
                        time.sleep(0.1)
                else:
                    self._kill(pid)
        self._process = None
        self._clear_pid()

    def _read_pid(self):
        try:
            return int(self.pid_path.read_text(encoding='utf-8').strip())
        except (OSError, ValueError):
            return None

    def _clear_pid(self):
        try:
            self.pid_path.unlink()
        except OSError:
            pass

    @staticmethod
    def _terminate(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    @staticmethod
    def _kill(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
