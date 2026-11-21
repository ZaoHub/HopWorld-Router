"""Manage the persistent Xray process used by the local network node."""

import json
import http.client
import os
import re
import secrets
import shutil
import signal
import socket
import stat
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
    LOCAL_EGRESS_SOCKS_PORT,
    build_local_reality_node,
    validate_public_host,
)


class XrayService:
    def __init__(self, data_dir, binary=None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.config_path = self.data_dir / 'xray-local.json'
        self.pid_path = self.data_dir / 'xray-local.pid'
        self.log_path = self.data_dir / 'xray-local.log'
        self.binary = binary
        self._lock = threading.Lock()
        self._metrics_lock = threading.Lock()
        self._metrics_dir = None
        self._process = None
        if binary is None:
            self.locate()

    def locate(self):
        bundled = self.data_dir / 'bin' / 'xray'
        found = os.environ.get('HOPWORLD_XRAY') or (str(bundled) if os.access(bundled, os.X_OK) else None) or shutil.which('xray')
        if not found and os.access('/usr/local/bin/xray', os.X_OK):
            found = '/usr/local/bin/xray'
        if found:
            self.binary = found
        return self.binary

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
        if prefer not in ('ipv4', 'ipv6', 'ipv4_first', 'dual'):
            prefer = 'ipv4'
        return {
            'enabled': False,
            'legacy_singbox_enabled': False,
            'egress_mode': 'fixed',
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
        if not isinstance(updated.get('hy2_password'), str) or len(updated['hy2_password']) < 16:
            updated['hy2_password'] = secrets.token_urlsafe(18)
        if not isinstance(updated.get('tuic_password'), str) or len(updated['tuic_password']) < 16:
            updated['tuic_password'] = secrets.token_urlsafe(18)
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
        port = int(settings['port'])
        if port == LOCAL_EGRESS_SOCKS_PORT:
            raise ValueError('TCP 端口与本机 QUIC 出口端口冲突')
        config, link = build_local_reality_node(
            settings['public_host'],
            port,
            settings['uuid'],
            settings['private_key'],
            settings['public_key'],
            settings['short_id'],
            settings.get('dest', DEFAULT_REALITY_DEST),
            settings.get('server_name', DEFAULT_REALITY_SERVER_NAME),
            settings.get('name') or DEFAULT_NODE_NAME,
            (settings.get('proxy_pool') or []) if settings.get('egress_mode') in ('auto', 'local_proxy', 'fixed') else None,
            settings.get('auto_probe_url'),
            settings.get('egress_mode') == 'local_proxy',
            settings.get('auto_interval', 60),
            fixed_upstream=settings.get('egress_mode') == 'fixed',
        )
        if ':' in settings['public_host'] or settings.get('public_host_prefer') in ('ipv6', 'dual', 'ipv4_first'):
            config['inbounds'][0]['listen'] = '::'
        path = self._metrics_path()
        if path:
            _add_metrics(config, path)
        config['inbounds'].append({
            'listen': '127.0.0.1', 'port': LOCAL_EGRESS_SOCKS_PORT, 'protocol': 'socks', 'tag': 'local-socks-in',
            'settings': {'auth': 'noauth', 'udp': True},
        })
        return config, link

    def _metrics(self):
        path = self._metrics_path()
        return _read_core_counters('/debug/vars', socket_path=path) if path else None

    def auto_observations(self):
        payload = self._metrics()
        observations = payload.get('observatory') if payload else None
        return observations if isinstance(observations, dict) else None

    def node_traffic(self):
        payload = self._metrics()
        counted = _inbound_bytes(payload, 'local-node-in')
        if counted is None:
            return None
        up, down = counted
        extra = _inbound_bytes(payload, 'local-socks-in')
        if extra is not None:
            up += extra[0]
            down += extra[1]
        try:
            instance = str(self.pid_path.stat().st_mtime_ns)
        except OSError:
            instance = None
        return {'up_bytes': up, 'down_bytes': down, 'total_bytes': up + down, 'instance': instance}

    def public_view(self, settings, running=None):
        is_running = self.running() if running is None else bool(running)
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
            'running': is_running,
            'egress_protocol_info': self._protocol_info(settings, is_running),
            'egress_mode': settings.get('egress_mode', 'default'),
            'upstream_id': settings.get('upstream_id', ''),
            'upstream_ready': settings.get('egress_mode') == 'fixed' and bool(settings.get('proxy_pool')),
            'local_proxy': settings.get('local_proxy', ''),
            'auto_pool_count': len(settings.get('proxy_pool') or []) if settings.get('egress_mode') == 'auto' else 0,
            'auto_target': settings.get('auto_target', 'google'),
            'auto_interval': settings.get('auto_interval', 60),
            'link': '',
        }
        if any(os.environ.get(key, '').strip() for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy')):
            view['egress_protocol_info']['detail'] += ' Router 服务环境检测到 HTTP_PROXY / HTTPS_PROXY；请确认当前出口配置。'
        if settings.get('enabled') and settings.get('public_host') and settings.get('public_key'):
            try:
                _, link = self.build(settings)
                view['link'] = link
            except ValueError:
                view['link'] = ''
        return view

    def _protocol_info(self, settings, running):
        if not settings.get('enabled') or not running:
            return {'tcp': '未运行', 'udp': '未运行', 'detail': '启用本地网络节点后显示配置状态。'}
        try:
            config = json.loads(self.config_path.read_text(encoding='utf-8'))
            outbounds = config['outbounds']
            if not isinstance(outbounds, list) or not outbounds:
                raise ValueError('invalid outbounds')
        except (OSError, ValueError, KeyError, TypeError):
            return {'tcp': '无法确认', 'udp': '无法确认', 'detail': '无法读取运行配置，请检查本地节点状态。'}
        tags = {item.get('tag'): item.get('protocol') for item in outbounds if isinstance(item, dict)}
        if tags.get('egress') == 'http':
            udp, detail = '不支持', '当前使用环境 HTTP 代理出口，公网 UDP 无法通过。请在服务环境中移除 HTTP_PROXY / HTTPS_PROXY 并重启服务，前提是服务器可以直连公网。'
        elif tags.get('local-proxy') == 'http':
            udp, detail = '不支持', '当前使用本机 HTTP 代理出口，UDP 无法通过。'
        elif tags.get('local-proxy') == 'socks':
            udp, detail = '待验证', 'SOCKS 出口是否支持 UDP 需要实际测试；代理扫描只验证 TCP。'
        elif 'fixed-upstream' in tags:
            udp = '已阻断' if tags['fixed-upstream'] == 'http' else '待验证'
            detail = '业务流量只走绑定的上游，上游不可用时会失败。'
            if udp == '已阻断':
                detail += ' 当前 HTTP 上游不支持 UDP。'
        elif any(str(tag).startswith('candidate-') for tag in tags if tag):
            udp, detail = '取决于上游', '自动换线的 UDP 支持取决于所选上游，网页测速不能证明 UDP 可用。'
        elif tags.get('blocked') == 'blackhole':
            detail = '绑定的上游不存在、已停用或配置不受支持。' if settings.get('egress_mode') == 'fixed' else '没有可用的出口配置。'
            return {'tcp': '已阻断', 'udp': '已阻断', 'detail': detail}
        elif tags.get('direct') == 'freedom':
            udp, detail = '已配置', '服务器直连出口已配置 TCP 和 UDP；客户端、服务器网络及防火墙仍需支持，实际可用性尚未测试。'
        else:
            return {'tcp': '无法确认', 'udp': '无法确认', 'detail': '当前出口配置无法识别，请检查本地节点状态。'}
        return {'tcp': '已配置', 'udp': udp, 'detail': detail}

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

    def apply(self, settings, *, rollback=True):
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
                try:
                    self._activate_locked(config, rollback)
                except OSError:
                    raise ValueError('无法保存或应用 Xray 配置，请检查数据目录权限') from None
                return link
            self._stop_locked()
            if self.config_path.exists():
                self.config_path.unlink()
            return ''

    def _activate_locked(self, config, rollback):
        if not self.binary:
            raise ValueError('未找到 Xray，请安装后设置 HOPWORLD_XRAY')
        with tempfile.TemporaryDirectory(dir=self.data_dir, prefix='.xray-') as directory:
            candidate = Path(directory) / 'candidate.json'
            previous = Path(directory) / 'previous.json'
            self._write_config(config, candidate)
            try:
                subprocess.run(
                    [self.binary, 'run', '-test', '-c', str(candidate)], check=True, timeout=5,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.SubprocessError):
                raise ValueError('Xray 配置校验失败，请检查内核版本和节点参数') from None
            if rollback and self._alive_locked() and self.config_path.is_file():
                shutil.copy2(self.config_path, previous)
            os.replace(candidate, self.config_path)
            try:
                self._restart_locked()
            except (OSError, ValueError) as exc:
                if previous.exists():
                    try:
                        os.replace(previous, self.config_path)
                        self._restart_locked()
                    except (OSError, ValueError):
                        self._stop_locked()
                        raise ValueError('Xray 新配置启动失败，旧配置已恢复，需重新启动服务') from exc
                    raise ValueError(f'{exc}。已恢复原有 Xray 服务') from exc
                self._stop_locked()
                self.config_path.unlink(missing_ok=True)
                raise

    def stop(self):
        if os.name == 'nt':
            return
        with self._lock:
            self._stop_locked()

    def restart(self):
        with self._lock:
            if not self._alive_locked():
                raise ValueError('这个内核没有在运行，请先启动')
            self._restart_locked()

    def log_text(self):
        return _read_log(self.log_path)

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

    def _write_config(self, config, path=None):
        payload = json.dumps(config, ensure_ascii=False, indent=2)
        fd = os.open(path or self.config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(payload)

    def _restart_locked(self):
        if not self.binary:
            raise ValueError('未找到 Xray，请安装后设置 HOPWORLD_XRAY')
        self._stop_locked()
        try:
            log = _capture_log(self.log_path)
        except OSError as exc:
            raise ValueError('无法写入 Xray 日志') from exc
        try:
            process = subprocess.Popen(
                [self.binary, 'run', '-c', str(self.config_path)],
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        except OSError as exc:
            raise ValueError('无法启动 Xray，请检查安装和执行权限') from exc
        finally:
            log.close()
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
        if self._metrics_dir is not None:
            path = Path(self._metrics_dir) / 'metrics.sock'
            try:
                if stat.S_ISSOCK(path.lstat().st_mode):
                    path.unlink()
            except FileNotFoundError:
                pass

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


def _capture_log(path):
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError('日志文件无效')
        if info.st_size > 200_000:
            os.ftruncate(fd, 0)
        return os.fdopen(fd, 'ab', closefd=True)
    except OSError:
        os.close(fd)
        raise


def _read_log(path):
    try:
        if path.is_symlink() or not path.is_file():
            return ''
        with path.open('rb') as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - 8000))
            return handle.read().decode('utf-8', 'replace')
    except OSError:
        return ''


def _add_metrics(config, path):
    config['inbounds'][0]['tag'] = 'local-node-in'
    config['stats'] = {}
    config['policy'] = {'system': {'statsInboundUplink': True, 'statsInboundDownlink': True}}
    config['metrics'] = {'tag': 'internal-metrics'}
    config['inbounds'].append({'tag': 'internal-metrics-in', 'listen': path, 'protocol': 'dokodemo-door',
                               'settings': {'address': '127.0.0.1', 'port': 80, 'network': 'tcp'}})
    routing = config.setdefault('routing', {'domainStrategy': 'AsIs', 'rules': []})
    routing['rules'].insert(0, {'type': 'field', 'inboundTag': ['internal-metrics-in'], 'outboundTag': 'internal-metrics'})


def _read_core_counters(path, *, socket_path=None, port=None, secret=None):
    conn = http.client.HTTPConnection('127.0.0.1', port=port, timeout=0.5)
    try:
        if socket_path:
            conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            conn.sock.settimeout(0.5)
            conn.sock.connect(socket_path)
        headers = {'Connection': 'close'}
        if secret:
            headers['Authorization'] = secret
        conn.request('GET', path, headers=headers)
        response = conn.getresponse()
        if response.status != 200:
            return None
        raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return None
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        conn.close()


def _inbound_bytes(payload, tag):
    try:
        traffic = payload['stats']['inbound'][tag]
        up, down = traffic['uplink'], traffic['downlink']
    except (TypeError, KeyError):
        return None
    if any(type(value) is not int or value < 0 for value in (up, down)):
        return None
    return up, down
