#!/usr/bin/env python3
"""Install an authenticated same-series Router package using APT."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from router.core_install import _open
from router.release_channel import deb_package, stable_release
from router.system_update import _json, require_service_ready

STATE = Path('/var/lib/hopworld-router-package-updates')
MAX_BYTES = 32 * 1024 * 1024


def write_state(data, state):
    path = STATE / 'page-status.tmp'
    path.write_text(json.dumps({**data, 'state': state, 'timestamp': int(time.time())}))
    path.chmod(0o644)
    path.replace(STATE / 'page-status.json')


def install():
    record = {'id': uuid.uuid4().hex, 'target': '', 'current': ''}
    write_state(record, 'running')
    try:
        current = subprocess.check_output(['/usr/bin/dpkg-query', '-W', '-f=${Version}', 'hopworld-router'],
                                          text=True, timeout=5).strip()
        record['current'] = current
        release = stable_release(_json)
        if not release:
            write_state(record, 'complete')
            return
        version, name, url, checksum = deb_package(release)
        compare = subprocess.run(['/usr/bin/dpkg', '--compare-versions', version, 'gt', current], timeout=5)
        if compare.returncode == 1:
            write_state(record, 'complete')
            return
        compare.check_returncode()
        record['target'] = version
        with tempfile.TemporaryDirectory(dir=STATE, prefix='apt-') as folder:
            Path(folder).chmod(0o755)
            path = Path(folder) / name
            with _open(Request(url, headers={'User-Agent': 'HopWorld-Router'}), 25) as response:
                blob = response.read(MAX_BYTES + 1)
            if len(blob) > MAX_BYTES or hashlib.sha256(blob).hexdigest() != checksum:
                raise ValueError('发布包 SHA-256 校验失败')
            path.write_bytes(blob)
            path.chmod(0o644)
            for field, expected in (('Package', 'hopworld-router'), ('Version', version), ('Architecture', 'all')):
                value = subprocess.check_output(['/usr/bin/dpkg-deb', '-f', path, field], text=True, timeout=5)
                if value.strip() != expected:
                    raise ValueError('Debian 软件包元数据不匹配')
            subprocess.run(['/usr/bin/apt-get', '--yes', '--no-remove', '-o', 'Dpkg::Use-Pty=0', 'install', path],
                           env={**os.environ, 'DEBIAN_FRONTEND': 'noninteractive'}, check=True, timeout=600)
        require_service_ready()
    except Exception:
        write_state(record, 'failed')
        raise
    write_state(record, 'complete')


if __name__ == '__main__':
    install()
