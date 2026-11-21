"""Check the current stable GitHub series and trigger a fixed systemd updater."""

from http.client import HTTPConnection, HTTPException
import ipaddress
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

from router.release_channel import API, deb_package, stable_release
from router.version import RELEASE_MAJOR, VERSION


REPO = Path('/opt/hopworld-router')
APP_ROOT = Path(__file__).resolve().parents[1]
STATE = Path('/var/lib/hopworld-router-updates/page-status.json')
PACKAGE_STATE = Path('/var/lib/hopworld-router-package-updates/page-status.json')
UNIT = 'hopworld-router-update.service'
PACKAGE_UNIT = 'hopworld-router-package-update.service'
GIT_MARKER = Path('/etc/hopworld-router/stable-release-updater')
PACKAGE_HELPER = Path('/usr/lib/hopworld-router/deploy/hopworld-package-update.py')
PACKAGE_UPDATE_MESSAGE = '此安装由 APT 管理，请由服务器管理员安装经校验的软件包'
_LOCK = threading.Lock()
_cache = {'state': 'idle', 'changed_files': [], 'error': ''}
_checked_at = 0
_checking = False
_starting_until = 0


def _json(url):
    request = Request(url, headers={'User-Agent': 'HopWorld-Router', 'Accept': 'application/vnd.github+json'})
    with urlopen(request, timeout=8) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('更新信息过大')
    return json.loads(raw)


def current_target():
    return 'package' if os.environ.get('HOPWORLD_PACKAGE_VERSION') else 'project'


def _unit():
    return PACKAGE_UNIT if current_target() == 'package' else UNIT


def _check():
    release = stable_release(_json)
    if current_target() == 'package':
        current = os.environ['HOPWORLD_PACKAGE_VERSION']
        if not release:
            return {'state': 'no_release', 'current': current, 'changed_files': [], 'error': ''}
        target, name, _, _ = deb_package(release)
        comparison = subprocess.run(['/usr/bin/dpkg', '--compare-versions', target, 'gt', current], timeout=5)
        if comparison.returncode not in (0, 1):
            raise ValueError('Debian 版本无法比较')
        return {'state': 'available' if comparison.returncode == 0 else 'current', 'current': current,
                'target': target, 'release': release['tag_name'], 'changed_files': [name], 'error': ''}

    current = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=REPO, check=True, capture_output=True,
                             text=True, timeout=5).stdout.strip()
    if not re.fullmatch('[0-9a-f]{40}', current):
        raise ValueError('本地 Git 版本无效')
    if not release:
        return {'state': 'no_release', 'current': current[:12], 'changed_files': [], 'error': ''}
    tag = release['tag_name']
    target = _json(API + '/commits/' + tag)['sha']
    if not re.fullmatch('[0-9a-f]{40}', target):
        raise ValueError('GitHub 版本无效')
    result = {'state': 'current', 'changed_files': [], 'error': '', 'current': current[:12],
              'target': target[:12], 'release': tag}
    if current != target:
        info = _json(API + '/compare/' + current + '...' + target)
        if info.get('status') == 'behind':
            return result
        if info.get('status') != 'ahead':
            raise ValueError('当前版本与正式发布分支分叉，须按原部署方式处理')
        files = info.get('files')
        if not isinstance(files, list):
            raise ValueError('无法读取变更文件')
        result.update(state='available', changed_files=[
            item['filename'] for item in files if isinstance(item, dict) and isinstance(item.get('filename'), str)
        ])
    return result


def _refresh():
    global _cache, _checking, _checked_at
    try:
        value = _check()
    except ValueError as exc:
        value = {'state': 'unavailable', 'changed_files': [], 'error': str(exc)}
    except (OSError, AttributeError, KeyError, TypeError, subprocess.SubprocessError):
        value = {'state': 'unavailable', 'changed_files': [], 'error': '无法检查 GitHub 更新，请检查网络或服务器安装'}
    with _LOCK:
        _cache = value
        _checking = False
        _checked_at = time.time()


def _unit_running():
    try:
        result = subprocess.run(['/usr/bin/systemctl', 'is-active', _unit()], capture_output=True, text=True, timeout=3)
        return result.stdout.strip() in ('active', 'activating', 'reloading')
    except (OSError, subprocess.SubprocessError):
        return False


def require_service_ready(timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            field = subprocess.check_output(['/usr/bin/systemctl', 'show', '-p', 'MainPID', 'hopworld-router'],
                                            text=True, timeout=2).strip()
            pid = int(field.removeprefix('MainPID='))
            if pid <= 1:
                raise ValueError('服务未启动')
            settings = dict(entry.split(b'=', 1) for entry in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
                            if entry.startswith((b'HOPWORLD_HOST=', b'HOPWORLD_PORT=')))
            port = int(settings.get(b'HOPWORLD_PORT', b'8080'))
            host = settings.get(b'HOPWORLD_HOST', b'127.0.0.1').decode()
            if host in ('0.0.0.0', 'localhost'):
                host = '127.0.0.1'
            elif host == '::':
                host = '::1'
            ipaddress.ip_address(host)
            connection = HTTPConnection(host, port, timeout=2)
            try:
                connection.request('GET', '/health/ready')
                if connection.getresponse().status == 200:
                    return
            finally:
                connection.close()
        except (OSError, ValueError, HTTPException, subprocess.SubprocessError):
            pass
        time.sleep(0.5)
    raise ValueError('更新后管理服务未通过健康检查')


def _unmanaged_status():
    common = {'state': 'unavailable', 'managed': False, 'changed_files': [], 'current': VERSION}
    if version := os.environ.get('HOPWORLD_PACKAGE_VERSION'):
        if PACKAGE_HELPER.is_file():
            return None
        return {**common, 'channel': 'APT', 'current': version, 'error': PACKAGE_UPDATE_MESSAGE}
    if APP_ROOT != REPO.resolve():
        source = (APP_ROOT / '.git').exists()
        return {**common, 'channel': '本地源码' if source else '手动部署',
                'error': '请在 Git 工作区更新源码。' if source else '请通过原部署方式更新 Router。'}
    if not GIT_MARKER.is_file():
        return {**common, 'channel': 'GitHub', 'error': '服务器尚未安装 V1 稳定发布更新服务'}
    return None


def worker_result():
    if _unmanaged_status():
        return None
    path = PACKAGE_STATE if current_target() == 'package' else STATE
    try:
        value = json.loads(path.read_text())
        if value.get('state') in ('complete', 'failed') and re.fullmatch(r'[0-9a-f-]{12,64}', value.get('id', '')):
            return value
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return None


def status(*, refresh=False):
    global _checking
    if unmanaged := _unmanaged_status():
        return unmanaged
    channel = f'GitHub Releases · V{RELEASE_MAJOR}' + (' / APT' if current_target() == 'package' else ' / Git')
    base = {'channel': channel, 'managed': True, 'current': os.environ.get('HOPWORLD_PACKAGE_VERSION', VERSION)}
    if _unit_running() or time.monotonic() < _starting_until:
        return {**base, 'state': 'updating', 'changed_files': [], 'error': ''}
    with _LOCK:
        if refresh and not _checking and time.time() - _checked_at >= 2:
            _checking = True
            threading.Thread(target=_refresh, daemon=True).start()
        if _checking:
            return {**base, 'state': 'checking', 'changed_files': [], 'error': ''}
        return {**base, **_cache, 'checked_at': _checked_at}


def start():
    global _checked_at, _starting_until, _cache
    if unmanaged := _unmanaged_status():
        raise ValueError(unmanaged['error'])
    with _LOCK:
        if time.monotonic() < _starting_until or _unit_running():
            raise ValueError('更新正在进行')
        try:
            result = subprocess.run(['/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'start', '--no-block', _unit()],
                                    capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError('页面更新服务不可用，请检查服务器安装') from exc
        if result.returncode:
            raise ValueError('页面更新服务不可用，请检查服务器安装')
        _starting_until = time.monotonic() + 3
        _checked_at = 0
        _cache = {'state': 'idle', 'changed_files': [], 'error': ''}
