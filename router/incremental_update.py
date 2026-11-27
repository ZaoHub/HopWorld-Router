"""Check GitHub main file diffs against the active runtime commit.

Apply path requires signed CI runtime artifacts (Phase B). Phase A exposes
preview-only checks so the update center UI can list changed files.
"""

from __future__ import annotations

import json
import os
import re
from urllib.request import Request, urlopen

from router.release_channel import API, REPOSITORY
from router.runtime_layout import read_state, resolve_current, runtime_meta, status as runtime_status
from router.version import RELEASE_MAJOR

COMMIT = re.compile(r'^[0-9a-f]{7,40}$')
ALLOWED_PREFIXES = ('router/', 'deploy/hopworld-', 'deploy/debian/')


def _json(url):
    request = Request(url, headers={'User-Agent': 'HopWorld-Router', 'Accept': 'application/vnd.github+json'})
    with urlopen(request, timeout=12) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('更新信息过大')
    return json.loads(raw)


def _baseline_commit(root=None):
    current = resolve_current(root)
    if current:
        commit = runtime_meta(current).get('commit')
        if isinstance(commit, str) and COMMIT.fullmatch(commit):
            return commit
    state = read_state(root)
    commit = state.get('running_commit')
    if isinstance(commit, str) and COMMIT.fullmatch(commit):
        return commit
    configured = os.environ.get('HOPWORLD_SOURCE_COMMIT', '').strip().lower()
    if COMMIT.fullmatch(configured):
        return configured
    return ''


def _allowed(path):
    return any(path == prefix.rstrip('/') or path.startswith(prefix) for prefix in ALLOWED_PREFIXES)


def _map_status(status):
    return {'added': 'add', 'removed': 'delete', 'renamed': 'rename', 'modified': 'modify',
            'changed': 'modify', 'copied': 'add'}.get(status, 'modify')


def check(*, root=None, fetch=_json):
    """Compare active runtime commit to GitHub main; return file ops for preview."""
    runtime = runtime_status(root)
    channel = runtime.get('channel') or 'stable'
    tip_payload = fetch(API + '/commits/main')
    tip = tip_payload.get('sha') if isinstance(tip_payload, dict) else None
    if not isinstance(tip, str) or not COMMIT.fullmatch(tip):
        raise ValueError('无法读取 GitHub main 提交')
    baseline = _baseline_commit(root)
    result = {
        'state': 'current',
        'channel': channel,
        'baseline_commit': baseline,
        'target_commit': tip,
        'changed_files': [],
        'changes': [],
        'apply_ready': False,
        'error': '',
        'note': '',
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
    result.update(
        state='available',
        changes=changes,
        changed_files=[item['path'] for item in changes],
        apply_ready=False,
        note='已列出与 main 的文件差异。应用增量需经 CI 签名的运行时产物（尚未启用自动安装）。',
    )
    return {**result, 'repository': REPOSITORY, 'release_major': RELEASE_MAJOR}


def apply(*_args, **_kwargs):
    raise ValueError('增量应用需要已签名的 CI 运行时产物，当前版本仅支持检查与预览')
