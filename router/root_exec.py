"""Run the fixed root helper. Missing helper means the feature keeps its old error."""

import os
import shutil
import subprocess

DEFAULT_HELPER = '/usr/local/sbin/hopworld-root'


def helper_path():
    return os.environ.get('HOPWORLD_ROOT_HELPER', DEFAULT_HELPER)


def available():
    path = helper_path()
    return os.name != 'nt' and os.path.isfile(path) and os.access(path, os.X_OK)


def run(*args, timeout=180):
    if not available():
        return None
    if not args or any(not isinstance(arg, str) or not arg or any(char in arg for char in '\n\r\x00') for arg in args):
        raise ValueError('系统操作无效')
    cmd = [helper_path(), *args] if hasattr(os, 'geteuid') and os.geteuid() == 0 else ['sudo', '-n', helper_path(), *args]
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('无法执行系统操作，请先用 root 执行 deploy/hopworld-install-root.sh') from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or '').strip().splitlines()
        message = detail[-1] if detail else '系统操作失败'
        lower = message.lower()
        if 'password is required' in lower or 'not allowed' in lower or 'a terminal is required' in lower:
            message = '系统操作未授权，请先用 root 执行 deploy/hopworld-install-root.sh'
        elif len(message) > 160:
            message = message[:160] + '…'
        raise ValueError(message)
    return completed


def ensure_xray():
    return run('xray', timeout=180)


def open_port(proto, port):
    number = int(port)
    if proto not in ('tcp', 'udp') or not 1 <= number <= 65535:
        raise ValueError('端口无效')
    return run('open', proto, str(number), timeout=60)


def prepare_local_node(port):
    ran = ensure_xray()
    open_port('tcp', port)
    if shutil.which('sing-box') or os.environ.get('HOPWORLD_SINGBOX'):
        from router.local_node import quic_ports
        hy2, tuic = quic_ports(int(port))
        open_port('udp', hy2)
        open_port('udp', tuic)
    return ran


def prepare_acme():
    completed = run('acme', timeout=600)
    if completed is None:
        return None
    for line in completed.stdout.splitlines():
        if line.startswith('HOPWORLD_ACME_WEBROOT=') or line.startswith('HOPWORLD_ACME_WRAPPER='):
            key, value = line.split('=', 1)
            os.environ[key] = value
    return completed


def prepare_ikev2():
    return run('ikev2', timeout=600)
