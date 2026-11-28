"""Verify and materialize signed runtime artifacts into versioned release dirs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from router.runtime_layout import atomic_switch, releases_dir, runtime_root, write_runtime_meta
from router.version import RELEASE_MAJOR

COMMIT = re.compile(r'^[0-9a-f]{40}$')
DEFAULT_PUBLIC_KEY = Path('/usr/share/hopworld-router/keys/runtime-ed25519.pub')
BUNDLED_PUBLIC_KEY = Path(__file__).resolve().parents[1] / 'deploy' / 'keys' / 'runtime-ed25519.pub'


def public_key_path():
    configured = os.environ.get('HOPWORLD_RUNTIME_PUBLIC_KEY', '').strip()
    if configured:
        return Path(configured)
    if DEFAULT_PUBLIC_KEY.is_file():
        return DEFAULT_PUBLIC_KEY
    return BUNDLED_PUBLIC_KEY


def sha256_bytes(data: bytes):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify_signature(manifest: Path, signature: Path, public_key: Path | None = None):
    key = public_key or public_key_path()
    if not key.is_file():
        raise ValueError('缺少运行时更新公钥')
    try:
        subprocess.run(
            ['openssl', 'pkeyutl', '-verify', '-pubin', '-inkey', str(key), '-rawin',
             '-in', str(manifest), '-sigfile', str(signature)],
            check=True, capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError('运行时更新签名校验失败') from exc


def load_manifest(manifest: Path, signature: Path, archive: Path, *, public_key: Path | None = None):
    verify_signature(manifest, signature, public_key=public_key)
    try:
        data = json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('运行时清单无效') from exc
    if not isinstance(data, dict):
        raise ValueError('运行时清单无效')
    commit = data.get('commit')
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        raise ValueError('运行时清单 Commit 无效')
    major = data.get('compatible_package_major')
    if major != RELEASE_MAJOR:
        raise ValueError('运行时产物与当前主版本不兼容')
    digest = data.get('archive_sha256')
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError('运行时归档摘要无效')
    if sha256_file(archive) != digest:
        raise ValueError('运行时归档校验失败')
    files = data.get('files')
    if not isinstance(files, list) or not files:
        raise ValueError('运行时清单没有文件')
    return data


def install_artifact(manifest: Path, signature: Path, archive: Path, *, root=None, public_key: Path | None = None,
                     expected_commit: str | None = None):
    """Extract a verified artifact into releases/git-<sha> and atomically switch current."""
    root = root or runtime_root()
    data = load_manifest(manifest, signature, archive, public_key=public_key)
    commit = data['commit']
    if expected_commit and commit != expected_commit:
        raise ValueError('运行时产物 Commit 与请求不符')
    release_id = f'git-{commit}'
    if not re.fullmatch(r'git-[0-9a-f]{40}', release_id):
        raise ValueError('运行版本编号无效')
    destination = releases_dir(root) / release_id
    if destination.exists() or destination.is_symlink():
        raise ValueError('目标运行版本已经存在，原安装仍保留')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        stage = Path(temporary) / 'tree'
        stage.mkdir()
        expected = {item['path']: item['sha256'] for item in data['files']}
        with tarfile.open(archive, 'r:gz') as tar:
            for member in tar:
                relative = PurePosixPath(member.name)
                if relative.is_absolute() or '..' in relative.parts:
                    raise ValueError('运行时归档路径无效')
                if member.isdir():
                    continue
                if not member.isfile() or relative.as_posix() not in expected:
                    raise ValueError(f'运行时归档成员无效：{relative}')
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, target.open('xb') as handle:
                    shutil.copyfileobj(source, handle)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
                if sha256_file(target) != expected.pop(relative.as_posix()):
                    raise ValueError(f'运行时文件校验失败：{relative}')
        if expected or not (stage / 'router').is_dir():
            raise ValueError('运行时归档缺少清单文件')
        stage.rename(destination)
    write_runtime_meta(
        destination,
        id=release_id,
        source='incremental',
        commit=commit,
        package_version='',
        created_at=__import__('time').time(),
    )
    atomic_switch(destination, root)
    return release_id, commit
