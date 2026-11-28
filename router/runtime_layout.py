"""Independent runtime version trees outside dpkg-managed paths.

Layout:
  $HOPWORLD_RUNTIME_ROOT/releases/<id>/   full app tree (router/...)
  $HOPWORLD_RUNTIME_ROOT/current          symlink → active release
  $HOPWORLD_RUNTIME_ROOT/state.json       channel, last_good, running metadata
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from router.version import VERSION

DEFAULT_RUNTIME_ROOT = Path('/var/lib/hopworld-router-runtime')
DPKG_SEED = Path('/usr/lib/hopworld-router')
UPDATE_LOCK = Path('/var/lock/hopworld-router-update.lock')
RELEASE_ID = re.compile(r'^(?:apt|git)-[A-Za-z0-9._+-]+$')
_STATE_LOCK = threading.RLock()


def runtime_root():
    configured = os.environ.get('HOPWORLD_RUNTIME_ROOT', '').strip()
    return Path(configured).resolve() if configured else DEFAULT_RUNTIME_ROOT


def dpkg_seed():
    configured = os.environ.get('HOPWORLD_DPKG_SEED', '').strip()
    return Path(configured).resolve() if configured else DPKG_SEED


def releases_dir(root=None):
    return (root or runtime_root()) / 'releases'


def current_link(root=None):
    return (root or runtime_root()) / 'current'


def state_path(root=None):
    return (root or runtime_root()) / 'state.json'


def history_path(root=None):
    return (root or runtime_root()) / 'history.jsonl'


@contextmanager
def update_lock(lock_path=None, *, blocking=False):
    """Exclusive flock shared with package and incremental updaters."""
    configured = lock_path or os.environ.get('HOPWORLD_UPDATE_LOCK', '')
    path = Path(configured) if configured else UPDATE_LOCK
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Tests / non-root environments fall back under the runtime root.
        path = runtime_root() / 'update.lock'
        path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, 'a+', encoding='utf-8')
    try:
        import fcntl
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(handle.fileno(), flags)
        except BlockingIOError as exc:
            raise ValueError('更新正在进行，请稍后再试') from exc
        yield handle
    finally:
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def _default_state():
    return {
        'channel': 'stable',
        'last_good': '',
        'running_id': '',
        'running_commit': '',
        'apt_version': os.environ.get('HOPWORLD_PACKAGE_VERSION', '').strip(),
        'migrated': False,
        'pending': '',
    }


def read_state(root=None):
    path = state_path(root)
    with _STATE_LOCK:
        if not path.is_file():
            return _default_state()
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return _default_state()
        if not isinstance(data, dict):
            return _default_state()
        base = _default_state()
        base.update({key: data[key] for key in base if key in data})
        return base


def write_state(data, root=None):
    root = root or runtime_root()
    root.mkdir(parents=True, exist_ok=True)
    path = state_path(root)
    payload = {**_default_state(), **data}
    with _STATE_LOCK:
        staged = path.with_suffix('.tmp')
        staged.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        staged.chmod(0o644)
        staged.replace(path)


def append_history(event, root=None):
    root = root or runtime_root()
    root.mkdir(parents=True, exist_ok=True)
    record = {'timestamp': int(time.time()), **event}
    with history_path(root).open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + '\n')


def runtime_meta(release_dir):
    path = Path(release_dir) / 'RUNTIME.json'
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_runtime_meta(release_dir, **fields):
    path = Path(release_dir) / 'RUNTIME.json'
    path.write_text(json.dumps(fields, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    path.chmod(0o644)


def resolve_current(root=None):
    link = current_link(root)
    if not link.is_symlink():
        return None
    try:
        target = link.resolve(strict=True)
    except OSError:
        return None
    return target if target.is_dir() else None


def _seed_fingerprint(seed: Path):
    digest = hashlib.sha256()
    router = seed / 'router'
    if not router.is_dir():
        raise ValueError('程序种子目录无效')
    for path in sorted(router.rglob('*')):
        if not path.is_file() or path.name.endswith('.pyc') or '__pycache__' in path.parts:
            continue
        digest.update(path.relative_to(seed).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _copy_seed_tree(seed: Path, destination: Path):
    if destination.exists():
        raise ValueError('运行版本目录已存在')
    destination.parent.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '.git', '.venv', 'data')
    # Never copy into dpkg paths; destination must stay under runtime root.
    shutil.copytree(seed, destination, symlinks=False, ignore=ignore)


def atomic_switch(release_dir: Path, root=None):
    """Point current → release_dir using ln -s + mv -Tf semantics."""
    root = root or runtime_root()
    release_dir = Path(release_dir).resolve()
    releases = releases_dir(root).resolve()
    if release_dir.parent != releases or not release_dir.is_dir():
        raise ValueError('运行版本目录无效')
    if release_dir.name.startswith('.') or not RELEASE_ID.fullmatch(release_dir.name):
        # allow git-<40hex> and apt-...
        if not re.fullmatch(r'(?:apt|git)-[A-Za-z0-9._+-]+', release_dir.name):
            raise ValueError('运行版本编号无效')
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as staged:
        link = Path(staged) / 'current'
        link.symlink_to(release_dir)
        os.replace(link, current_link(root))


def rollback_to(release_id: str, root=None):
    root = root or runtime_root()
    target = releases_dir(root) / release_id
    if not target.is_dir():
        raise ValueError('可回滚版本不存在')
    previous = resolve_current(root)
    atomic_switch(target, root)
    meta = runtime_meta(target)
    state = read_state(root)
    state.update(
        running_id=release_id,
        running_commit=meta.get('commit', ''),
        last_good=release_id,
        pending='',
    )
    write_state(state, root)
    append_history({'action': 'rollback', 'to': release_id, 'from': previous.name if previous else ''}, root)
    return status(root)


def migrate_from_seed(*, seed=None, root=None, package_version='', commit='', force=False):
    """Copy dpkg/Git seed into a runtime release and switch current (idempotent)."""
    root = root or runtime_root()
    seed = Path(seed or dpkg_seed()).resolve()
    if not (seed / 'router').is_dir():
        raise ValueError('未找到可迁移的程序种子')
    # Hard rule: never treat runtime root as a place to patch the seed.
    if seed.resolve() == root.resolve() or root.resolve() in seed.resolve().parents:
        raise ValueError('运行目录不能覆盖程序种子')
    if seed == DPKG_SEED.resolve() or str(seed).startswith(str(DPKG_SEED)):
        pass  # reading dpkg seed is OK; we only copy out
    fingerprint = _seed_fingerprint(seed)[:12]
    pkg = (package_version or os.environ.get('HOPWORLD_PACKAGE_VERSION') or VERSION).strip()
    release_id = f'apt-{pkg}-{fingerprint}'
    destination = releases_dir(root) / release_id
    current = resolve_current(root)
    if current and not force:
        state = read_state(root)
        state['migrated'] = True
        state['apt_version'] = pkg
        if not state.get('running_id'):
            state['running_id'] = current.name
            state['running_commit'] = runtime_meta(current).get('commit', '')
            state['last_good'] = current.name
        write_state(state, root)
        return status(root)
    if not destination.exists():
        _copy_seed_tree(seed, destination)
        write_runtime_meta(
            destination,
            id=release_id,
            source='apt-seed' if 'HOPWORLD_PACKAGE_VERSION' in os.environ or package_version else 'seed',
            package_version=pkg,
            commit=commit or '',
            fingerprint=fingerprint,
            created_at=int(time.time()),
        )
    atomic_switch(destination, root)
    state = read_state(root)
    state.update(
        migrated=True,
        apt_version=pkg,
        running_id=release_id,
        running_commit=commit or '',
        last_good=release_id,
        pending='',
    )
    write_state(state, root)
    append_history({'action': 'migrate', 'to': release_id, 'package_version': pkg}, root)
    return status(root)


def set_channel(channel, root=None):
    if channel not in ('stable', 'development'):
        raise ValueError('更新通道须为正式或开发')
    state = read_state(root)
    state['channel'] = channel
    write_state(state, root)
    append_history({'action': 'channel', 'channel': channel}, root)
    return status(root)


def list_releases(root=None):
    root = root or runtime_root()
    directory = releases_dir(root)
    if not directory.is_dir():
        return []
    items = []
    current = resolve_current(root)
    for path in sorted(directory.iterdir()):
        if not path.is_dir() or path.name.startswith('.'):
            continue
        meta = runtime_meta(path)
        items.append({
            'id': path.name,
            'commit': meta.get('commit', ''),
            'package_version': meta.get('package_version', ''),
            'source': meta.get('source', ''),
            'active': bool(current and current == path.resolve()),
            'created_at': meta.get('created_at'),
        })
    return items


def status(root=None):
    root = root or runtime_root()
    state = read_state(root)
    current = resolve_current(root)
    meta = runtime_meta(current) if current else {}
    apt = state.get('apt_version') or os.environ.get('HOPWORLD_PACKAGE_VERSION') or ''
    installation = 'package' if os.environ.get('HOPWORLD_PACKAGE_VERSION') else 'project'
    running_id = current.name if current else state.get('running_id', '')
    running_commit = meta.get('commit') or state.get('running_commit', '')
    healthy = bool(current and (current / 'router').is_dir())
    return {
        'runtime_root': str(root),
        'migrated': bool(state.get('migrated') or current),
        'channel': state.get('channel') or 'stable',
        'installation': installation,
        'apt_version': apt or VERSION,
        'running_id': running_id,
        'running_commit': running_commit,
        'running_version': meta.get('package_version') or apt or VERSION,
        'last_good': state.get('last_good') or '',
        'health': 'ok' if healthy else 'pending',
        'releases': list_releases(root),
        'dpkg_seed': str(dpkg_seed()),
    }
