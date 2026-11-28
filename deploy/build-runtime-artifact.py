#!/usr/bin/env python3
"""Build a signed HopWorld runtime tree artifact for incremental updates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT_RE = re.compile(r'^[0-9a-f]{40}$')
INCLUDE_PREFIXES = ('router/',)
INCLUDE_FILES = (
    'deploy/hopworld-acme-dns-hook.sh',
    'deploy/hopworld-sync-management-cert.sh',
    'deploy/hopworld-certbot-renew-hook.sh',
    'deploy/hopworld-install-runtime.sh',
)


def _git_sha():
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()


def _sha256(path: Path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_files():
    for path in sorted(ROOT.rglob('*')):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(ROOT).as_posix()
        if any(part.startswith('.') or part == '__pycache__' for part in path.relative_to(ROOT).parts):
            continue
        if relative.endswith('.pyc'):
            continue
        if relative in INCLUDE_FILES or any(relative.startswith(prefix) for prefix in INCLUDE_PREFIXES):
            if relative.startswith('router/') and path.suffix not in {'.py', '.css', '.html', '.js', '.png'}:
                continue
            yield relative, path


def _sign(manifest: Path, signature: Path, private_key: Path):
    subprocess.run(
        ['openssl', 'pkeyutl', '-sign', '-inkey', str(private_key), '-rawin',
         '-in', str(manifest), '-out', str(signature)],
        check=True, capture_output=True,
    )


def build(output: Path, *, commit='', private_key: Path | None = None):
    commit = (commit or os.environ.get('GITHUB_SHA') or _git_sha()).lower()
    if not COMMIT_RE.fullmatch(commit):
        raise ValueError('Commit SHA 无效')
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f'hopworld-runtime-{commit}.tar.gz'
    manifest_path = output / f'hopworld-runtime-{commit}.manifest.json'
    signature_path = output / f'hopworld-runtime-{commit}.manifest.json.sig'
    files = []
    with tempfile.TemporaryDirectory() as temporary:
        stage = Path(temporary) / 'tree'
        for relative, source in _iter_files():
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
            if source.suffix == '.sh' or relative.endswith('hopworld-router-uninstall'):
                destination.chmod(0o755)
            files.append({'path': relative, 'sha256': _sha256(destination), 'op': 'add'})
        with tarfile.open(archive, 'w:gz') as tar:
            tar.add(stage, arcname='.')
    payload = {
        'commit': commit,
        'compatible_package_major': int((ROOT / 'router' / 'version.py').read_text().split("VERSION = '", 1)[1][0]),
        'archive': archive.name,
        'archive_sha256': _sha256(archive),
        'files': files,
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    key = private_key or Path(os.environ.get('HOPWORLD_RUNTIME_SIGNING_KEY', ROOT / 'deploy/keys/runtime-ed25519.pem'))
    if not key.is_file():
        raise ValueError(f'缺少签名私钥：{key}')
    _sign(manifest_path, signature_path, key)
    return archive, manifest_path, signature_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--commit', default='')
    parser.add_argument('--private-key', type=Path)
    args = parser.parse_args()
    try:
        archive, manifest, signature = build(args.output, commit=args.commit, private_key=args.private_key)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'构建失败。{exc}\n')
    print(archive)
    print(manifest)
    print(signature)


if __name__ == '__main__':
    main()
