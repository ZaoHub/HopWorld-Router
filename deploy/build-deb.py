#!/usr/bin/env python3
"""Build a Debian package from the current Router sources and write its SHA-256."""

import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parent.parent
DEBIAN = ROOT / 'deploy' / 'debian'


def install(source, destination, mode=0o644):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(mode)


def build(version, output):
    if not re.fullmatch(r'[0-9][A-Za-z0-9.+~-]*', version):
        raise ValueError('请输入有效的不带 epoch 的 Debian 版本号')
    if not shutil.which('dpkg-deb'):
        raise ValueError('请先安装 dpkg-deb')

    output.mkdir(parents=True, exist_ok=True)
    artifact = output / f'hopworld-router_{version}_all.deb'
    with tempfile.TemporaryDirectory(dir=output, prefix='.build-') as directory:
        stage = Path(directory) / 'package'
        app = stage / 'usr/lib/hopworld-router'
        for source in sorted((ROOT / 'router').rglob('*')):
            relative = source.relative_to(ROOT)
            static_asset = relative.parts[:2] == ('router', 'static') and source.suffix in {'.css', '.html', '.js', '.png'}
            if (source.is_file() and not source.is_symlink() and (source.suffix == '.py' or static_asset)
                    and not any(part.startswith('.') or part == '__pycache__' for part in relative.parts)):
                install(source, app / relative)
        install(ROOT / 'deploy/hopworld-acme-dns-hook.sh', app / 'deploy/hopworld-acme-dns-hook.sh', 0o755)
        install(ROOT / 'deploy/hopworld-install-runtime.sh', app / 'deploy/hopworld-install-runtime.sh', 0o755)
        install(ROOT / 'deploy/hopworld-runtime-update', app / 'deploy/hopworld-runtime-update', 0o755)
        install(ROOT / 'deploy/hopworld-router-check.sh', stage / 'usr/bin/hopworld-router-check', 0o755)
        install(ROOT / 'deploy/hopworld-router-uninstall', stage / 'usr/bin/hopworld-router-uninstall', 0o755)
        install(DEBIAN / 'hopworld-package-update.py', app / 'deploy/hopworld-package-update.py')
        install(DEBIAN / 'hopworld-router-package-update.sudoers',
                stage / 'etc/sudoers.d/hopworld-router-package-update', 0o440)
        install(ROOT / 'deploy/hopworld-router-runtime-update.sudoers',
                stage / 'etc/sudoers.d/hopworld-router-runtime-update', 0o440)
        install(ROOT / 'deploy/keys/runtime-ed25519.pub',
                stage / 'usr/share/hopworld-router/keys/runtime-ed25519.pub')
        install(ROOT / 'deploy/hopworld-router-runtime-update.service',
                stage / 'lib/systemd/system/hopworld-router-runtime-update.service')
        install(ROOT / 'LICENSE', stage / 'usr/share/doc/hopworld-router/copyright')
        for name in ('preinst', 'postinst', 'prerm', 'postrm'):
            install(DEBIAN / name, stage / 'DEBIAN' / name, 0o755)
        for name, relative in (
            ('control', 'DEBIAN/control'), ('hopworld-router.service', 'lib/systemd/system/hopworld-router.service'),
            ('hopworld-router-package-update.service', 'lib/systemd/system/hopworld-router-package-update.service'),
        ):
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text((DEBIAN / name).read_text().replace('@VERSION@', version))
            destination.chmod(0o644)
        staged_package = Path(directory) / artifact.name
        subprocess.run(['dpkg-deb', '--root-owner-group', '-Zxz', '--build', str(stage), str(staged_package)], check=True)
        digest = hashlib.sha256(staged_package.read_bytes()).hexdigest()
        staged_checksum = Path(directory) / f'{artifact.name}.sha256'
        staged_checksum.write_text(f'{digest}  {artifact.name}\n', encoding='ascii')
        staged_package.replace(artifact)
        staged_checksum.replace(output / staged_checksum.name)
    return artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(build(args.version, args.output))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'构建失败。{exc}\n')


if __name__ == '__main__':
    main()
