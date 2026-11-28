"""Check and apply signed runtime increments from GitHub main."""

from __future__ import annotations

import json
import os
import re
import subprocess
import stat
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

from router.release_channel import API, REPOSITORY
from router.runtime_artifact import install_artifact
from router.runtime_layout import (
    append_history, atomic_switch, read_state, resolve_current, runtime_meta, runtime_root,
    status as runtime_status, update_lock, write_state,
)
from router.version import RELEASE_MAJOR

COMMIT = re.compile(r'^[0-9a-f]{7,40}$')
FULL_COMMIT = re.compile(r'^[0-9a-f]{40}$')
ALLOWED_PREFIXES = ('router/', 'deploy/hopworld-', 'deploy/debian/')
RUNTIME_RELEASE_TAG = os.environ.get('HOPWORLD_RUNTIME_RELEASE_TAG', 'runtime-v1').strip() or 'runtime-v1'
PENDING = 'pending-apply.json'
UNIT = 'hopworld-router-runtime-update.service'
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


def _json(url):
    request = Request(url, headers={'User-Agent': 'HopWorld-Router', 'Accept': 'application/vnd.github+json'})
    with urlopen(request, timeout=12) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('更新信息过大')
    return json.loads(raw)


def _download(url, destination: Path, *, limit=MAX_ARTIFACT_BYTES):
    request = Request(url, headers={'User-Agent': 'HopWorld-Router', 'Accept': 'application/octet-stream'})
    with urlopen(request, timeout=60) as response, destination.open('wb') as handle:
        total = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise ValueError('运行时产物过大')
            handle.write(chunk)


def _baseline_commit(root=None):
    current = resolve_current(root)
    if current:
        commit = runtime_meta(current).get('commit')
        if isinstance(commit, str) and COMMIT.fullmatch(commit):
            return commit.lower()
    state = read_state(root)
    commit = state.get('running_commit')
    if isinstance(commit, str) and COMMIT.fullmatch(commit):
        return commit.lower()
    configured = os.environ.get('HOPWORLD_SOURCE_COMMIT', '').strip().lower()
    if COMMIT.fullmatch(configured):
        return configured
    return ''


def _allowed(path):
    return any(path == prefix.rstrip('/') or path.startswith(prefix) for prefix in ALLOWED_PREFIXES)


def _map_status(status):
    return {'added': 'add', 'removed': 'delete', 'renamed': 'rename', 'modified': 'modify',
            'changed': 'modify', 'copied': 'add'}.get(status, 'modify')


def _artifact_urls(commit, fetch=_json):
    if not FULL_COMMIT.fullmatch(commit):
        raise ValueError('目标 Commit 无效')
    release = fetch(API + f'/releases/tags/{RUNTIME_RELEASE_TAG}')
    assets = release.get('assets') if isinstance(release, dict) else None
    if not isinstance(assets, list):
        raise ValueError('未找到已签名的运行时发布')
    wanted = {
        'archive': f'hopworld-runtime-{commit}.tar.gz',
        'manifest': f'hopworld-runtime-{commit}.manifest.json',
        'signature': f'hopworld-runtime-{commit}.manifest.json.sig',
    }
    found = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get('name')
        url = asset.get('browser_download_url')
        for key, expected in wanted.items():
            if name == expected and isinstance(url, str) and url.startswith('https://'):
                found[key] = url
    missing = [key for key in wanted if key not in found]
    if missing:
        raise ValueError('目标提交尚无已签名运行时产物，请等待 CI 发布')
    return found


def check(*, root=None, fetch=_json):
    """Compare active runtime commit to GitHub main; detect signed artifact readiness."""
    runtime = runtime_status(root)
    channel = runtime.get('channel') or 'stable'
    tip_payload = fetch(API + '/commits/main')
    tip = tip_payload.get('sha') if isinstance(tip_payload, dict) else None
    if not isinstance(tip, str) or not FULL_COMMIT.fullmatch(tip):
        raise ValueError('无法读取 GitHub main 提交')
    tip = tip.lower()
    baseline = _baseline_commit(root)
    result = {
        'state': 'current',
        'channel': channel,
        'baseline_commit': baseline,
        'target_commit': tip,
        'changed_files': [],
        'changes': [],
        'apply_ready': False,
        'artifact': False,
        'error': '',
        'note': '',
        'repository': REPOSITORY,
        'release_major': RELEASE_MAJOR,
    }
    if channel == 'stable':
        result.update(
            state='blocked',
            note='正式通道默认不自动跟踪 main。可在更新设置中切换到开发通道后再检查文件。',
        )
        return result
    if not baseline:
        result.update(
            state='unavailable',
            error='尚无运行基线 Commit。请先完成运行时迁移或配置 HOPWORLD_SOURCE_COMMIT。',
        )
        return result
    if baseline.startswith(tip) or tip.startswith(baseline):
        result['note'] = '本地运行 Commit 已与 main 一致'
        return result
    info = fetch(API + f'/compare/{baseline}...{tip}')
    if not isinstance(info, dict):
        raise ValueError('无法比较 GitHub 提交')
    files = info.get('files')
    if not isinstance(files, list):
        raise ValueError('无法读取变更文件')
    changes = []
    for item in files:
        if not isinstance(item, dict):
            continue
        path = item.get('filename')
        if not isinstance(path, str) or not _allowed(path):
            continue
        op = _map_status(item.get('status') or 'modified')
        entry = {'path': path, 'op': op, 'sha256': ''}
        if op == 'rename' and isinstance(item.get('previous_filename'), str):
            entry['from'] = item['previous_filename']
        changes.append(entry)
    if not changes:
        result['note'] = 'main 有提交，但没有可增量的程序文件变更'
        return result
    artifact = False
    note = '已列出与 main 的文件差异。'
    try:
        _artifact_urls(tip, fetch=fetch)
        artifact = True
        note += '已找到签名运行时产物，可以应用。'
    except ValueError as exc:
        note += f' {exc}'
    result.update(
        state='available',
        changes=changes,
        changed_files=[item['path'] for item in changes],
        apply_ready=artifact,
        artifact=artifact,
        note=note,
    )
    return result


def _pending_path(root):
    directory = Path(root) if root is not None else Path(os.environ.get('HOPWORLD_DATA') or '/var/lib/hopworld-router')
    return directory / PENDING


def write_pending(commit, root=None):
    if not isinstance(commit, str) or not FULL_COMMIT.fullmatch(commit):
        raise ValueError('目标 Commit 无效')
    path = _pending_path(root)
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.pending-', delete=False, mode='w',
                                         encoding='utf-8') as staged:
            staged_path = Path(staged.name)
            try:
                json.dump({'commit': commit}, staged)
                staged.flush()
                staged_path.replace(path)
            finally:
                staged_path.unlink(missing_ok=True)
    except OSError as exc:
        raise ValueError('无法写入运行时更新请求') from exc
    return path


def apply(*, root=None, fetch=_json, start_unit=True):
    """Queue a signed runtime apply and start the root updater when available."""
    preview = check(root=root, fetch=fetch)
    if preview.get('state') != 'available' or not preview.get('apply_ready'):
        raise ValueError(preview.get('error') or preview.get('note') or '当前没有可应用的增量更新')
    commit = preview['target_commit']
    write_pending(commit, root=root)
    if not start_unit:
        return {'ok': True, 'queued': commit, 'started': False}
    _start_worker(UNIT)
    return {'ok': True, 'queued': commit, 'started': True}


def _start_worker(unit):
    try:
        result = subprocess.run(['/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'start', '--no-block', unit],
                                capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('页面运行时服务不可用，请检查服务器安装') from exc
    if result.returncode:
        raise ValueError('页面运行时服务不可用，请检查服务器安装')


def start_migration():
    _start_worker('hopworld-router-runtime-migrate.service')


def apply_local(manifest: Path, signature: Path, archive: Path, *, root=None, public_key=None, expected_commit=None):
    """Direct apply used by the root worker and tests."""
    root = root or runtime_root()
    with update_lock(Path(root) / 'update.lock', blocking=False):
        release_id, commit = install_artifact(
            manifest, signature, archive, root=root, public_key=public_key, expected_commit=expected_commit)
        state = read_state(root)
        state.update(running_id=release_id, running_commit=commit, last_good=release_id, pending='', migrated=True)
        write_state(state, root)
        append_history({'action': 'incremental', 'to': release_id, 'commit': commit}, root)
        return {'release_id': release_id, 'commit': commit}


def run_pending(*, root=None, fetch=_json, download=_download, restart_and_check=None):
    """Apply a signed queued update, restoring the previous runtime on a failed restart."""
    path = _pending_path(root)
    root = root or runtime_root()
    previous = resolve_current(root)
    if restart_and_check and not previous:
        raise ValueError('请先迁移到独立运行时')
    before = read_state(root)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as handle:
            request_stat = os.fstat(handle.fileno())
            if not stat.S_ISREG(request_stat.st_mode):
                raise ValueError('待应用请求不是普通文件')
            raw = handle.read(4097)
            if len(raw) > 4096:
                raise ValueError('待应用请求过大')
            request = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('无法读取待应用的增量更新') from exc
    commit = request.get('commit') if isinstance(request, dict) else None
    if not isinstance(commit, str) or not FULL_COMMIT.fullmatch(commit):
        raise ValueError('待应用 Commit 无效')
    # Only consume the request read by this worker. An intervening request can be left queued.
    if path.exists() and path.stat().st_ino == request_stat.st_ino:
        path.unlink()
    urls = _artifact_urls(commit, fetch=fetch)
    with tempfile.TemporaryDirectory(dir=root) as temporary:
        folder = Path(temporary)
        archive = folder / f'hopworld-runtime-{commit}.tar.gz'
        manifest = folder / f'hopworld-runtime-{commit}.manifest.json'
        signature = folder / f'hopworld-runtime-{commit}.manifest.json.sig'
        download(urls['archive'], archive)
        download(urls['manifest'], manifest)
        download(urls['signature'], signature)
        try:
            result = apply_local(manifest, signature, archive, root=root, expected_commit=commit)
            if restart_and_check:
                restart_and_check()
        except Exception:
            if restart_and_check and resolve_current(root) != previous:
                atomic_switch(previous, root)
                write_state(before, root)
                append_history({'action': 'rollback', 'to': previous.name, 'failed_commit': commit}, root)
                restart_and_check()
            raise
    return result
