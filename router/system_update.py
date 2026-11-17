"""Read-only release checks and a fixed systemd update trigger."""

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

REPO = Path('/opt/hopworld-router')
STATE = Path('/var/lib/hopworld-router-updates/page-status.json')
UNIT = 'hopworld-router-update.service'
API = 'https://api.github.com/repos/ZaoHub/HopWorld-Router'
_lock = threading.Lock()
_cache = {'state': 'checking', 'changed_files': [], 'error': ''}
_checked_at = 0
_checking = False
_reported_failure = None


def _json(url):
    request = Request(url, headers={'User-Agent': 'HopWorld-Router', 'Accept': 'application/vnd.github+json'})
    with urlopen(request, timeout=5) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('更新信息过大')
    return json.loads(raw)


def _check():
    current = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=REPO, check=True, capture_output=True, text=True, timeout=5).stdout.strip()
    target = _json(API + '/commits/main')['sha']
    if not re.fullmatch('[0-9a-f]{40}', current) or not re.fullmatch('[0-9a-f]{40}', target):
        raise ValueError('版本信息无效')
    if current == target:
        return {'state': 'current', 'changed_files': [], 'error': '', 'current': current[:12], 'target': target[:12]}
    info = _json(API + '/compare/' + current + '...' + target)
    if info.get('status') != 'ahead':
        raise ValueError('当前版本不能直接更新，请检查服务器安装')
    files = info.get('files')
    if not isinstance(files, list):
        raise ValueError('无法读取变更文件')
    result = [item['filename'] for item in files if isinstance(item, dict) and isinstance(item.get('filename'), str)]
    if not result:
        return {'state': 'current', 'changed_files': [], 'error': '', 'current': current[:12], 'target': target[:12]}
    if len(result) >= 300:
        result.append('（仅列出前 300 个文件）')
    return {'state': 'available', 'changed_files': result, 'error': '', 'current': current[:12], 'target': target[:12]}


def _refresh():
    global _cache, _checking, _checked_at
    try:
        value = _check()
    except Exception:
        value = {'state': 'unavailable', 'changed_files': [], 'error': '无法检查 GitHub 更新，请检查网络或服务器安装'}
    with _lock:
        _cache = value
        _checking = False
        _checked_at = time.monotonic()


def _unit_running():
    try:
        result = subprocess.run(['/usr/bin/systemctl', 'is-active', UNIT], capture_output=True, text=True, timeout=3)
        return result.stdout.strip() in ('active', 'activating', 'reloading')
    except (OSError, subprocess.SubprocessError):
        return False


def status():
    global _checking, _reported_failure, _checked_at
    if _unit_running():
        return {'state': 'updating', 'changed_files': [], 'error': ''}
    try:
        state = json.loads(STATE.read_text(encoding='utf-8'))
        failure_id = STATE.stat().st_mtime_ns
        if state.get('state') == 'failed' and failure_id != _reported_failure:
            _reported_failure = failure_id
            _checked_at = 0
            return {'state': 'failed', 'changed_files': [], 'error': '更新失败，请在服务器查看 systemd 更新日志'}
    except (OSError, ValueError, TypeError):
        pass
    with _lock:
        if not _checking and time.monotonic() - _checked_at > 30:
            _checking = True
            threading.Thread(target=_refresh, daemon=True).start()
        if _checking:
            return {'state': 'checking', 'changed_files': [], 'error': ''}
        return dict(_cache)


def start():
    global _checked_at
    if _unit_running():
        raise ValueError('更新正在进行')
    try:
        result = subprocess.run(['/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'start', '--no-block', UNIT], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('页面更新服务不可用，请检查服务器安装') from exc
    if result.returncode:
        raise ValueError('页面更新服务不可用，请检查服务器安装')
    with _lock:
        _checked_at = 0
