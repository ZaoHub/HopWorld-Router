"""Encrypt and restore the durable state of a stopped HopWorld Router."""

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
from zipfile import BadZipFile, ZIP_STORED, ZipFile

from router.acme import ACME_KEY, MAX_CERTIFICATE_PEM, MAX_PRIVATE_KEY_PEM, cert_paths, validate_key_pair
from router.database_backup import data_lock, snapshot, validated_database
from router.local_node import validate_public_host

FORMAT = b'HopWorld Router backup v1'
DATABASE = 'router.db'
CERTIFICATE_SIZES = {'fullchain.pem': MAX_CERTIFICATE_PEM, 'privkey.pem': MAX_PRIVATE_KEY_PEM}
AGE_RECIPIENT = re.compile(r'age1[023456789acdefghjklmnpqrstuvwxyz]{58}')


def _age():
    if os.name != 'posix':
        raise ValueError('请在 Linux、macOS 或 WSL 中操作加密备份')
    binary = shutil.which('age')
    if not binary:
        raise ValueError('请先安装 age 并将其加入 PATH')
    return binary


def _private_file(path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb')


def _read_certificate(path):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as file:
        size = CERTIFICATE_SIZES[path.name]
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            raise ValueError('证书与私钥必须是普通文件')
        content = file.read(size + 1)
        if not 0 < len(content) <= size:
            raise ValueError('证书或私钥为空或超过大小限制')
        return content


def _directory(path):
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError('证书目录必须是真实目录')
    return path.is_dir()


def _stage_certificates(data, stage):
    root = data / 'certs'
    if not _directory(root):
        return
    for domain in sorted(root.iterdir()):
        if domain.name.startswith('.'):
            continue
        if validate_public_host(domain.name) != domain.name or not _directory(domain):
            raise ValueError('证书域名目录无效')
        for manual in (False, True):
            cert, key = cert_paths(data, domain.name, manual=manual)
            if not _directory(cert.parent) or not any(os.path.lexists(path) for path in (cert, key)):
                continue
            copied = cert_paths(stage, domain.name, manual=manual)
            for src, dest in zip((cert, key), copied):
                dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with _private_file(dest) as output:
                    output.write(_read_certificate(src))
            validate_key_pair(*copied)


def _certificate_reference(stage, destination=None):
    with closing(sqlite3.connect(stage / DATABASE)) as db, db:
        db.execute('PRAGMA trusted_schema=OFF')
        row = db.execute('SELECT value FROM settings WHERE key=?', (ACME_KEY,)).fetchone()
        if not row:
            return
        settings = json.loads(row[0])
        if not isinstance(settings, dict):
            raise ValueError('备份中的证书设置无效')
        if not (settings.get('cert_path') or settings.get('key_path')):
            return
        domain = validate_public_host(settings.get('domain'))
        manual = settings.get('source') == 'manual'
        cert, key = cert_paths(stage, domain, manual=manual)
        if not cert.is_file() or not key.is_file():
            raise ValueError('当前证书或私钥缺失，无法生成或恢复完整备份')
        if destination is not None:
            new_cert, new_key = cert_paths(destination, domain, manual=manual)
            settings.update(cert_path=str(new_cert), key_path=str(new_key),
                            certificate_revision=hashlib.sha256(cert.read_bytes()).hexdigest())
            db.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(settings, ensure_ascii=False), ACME_KEY))


def create_archive(archive, data_dir, recipient):
    age = _age()
    if not AGE_RECIPIENT.fullmatch(recipient):
        raise ValueError('请输入 age-keygen 生成的公开接收者（age1…）')
    source = data_dir / DATABASE
    if source.is_symlink() or not source.is_file():
        raise ValueError('数据目录中缺少 Router 数据库文件')
    if os.path.lexists(archive):
        raise ValueError('备份文件已存在，请使用新的文件名')
    with data_lock(data_dir), tempfile.TemporaryDirectory(prefix='hopworld-backup-') as directory:
        stage = Path(directory)
        with validated_database(source) as database:
            snapshot(database, stage / DATABASE)
        _stage_certificates(data_dir, stage)
        _certificate_reference(stage)
        with tempfile.NamedTemporaryFile(dir=archive.parent, prefix='.hopworld-', suffix='.age') as output:
            try:
                with subprocess.Popen([age, '--encrypt', '--recipient', recipient], stdin=subprocess.PIPE,
                                      stdout=output, stderr=subprocess.DEVNULL) as process:
                    with ZipFile(process.stdin, 'w') as bundle:
                        bundle.comment = FORMAT
                        bundle.write(stage / DATABASE, DATABASE)
                        for path in sorted((stage / 'certs').rglob('*.pem')):
                            bundle.write(path, path.relative_to(stage).as_posix())
            except BrokenPipeError:
                raise ValueError('age 加密失败，请检查公开接收者与存储空间') from None
            if process.returncode:
                raise ValueError('age 加密失败，请检查公开接收者与存储空间')
            os.fsync(output.fileno())
            os.link(output.name, archive)


def _extract_bundle(decrypted, stage):
    with ZipFile(decrypted) as bundle:
        if bundle.comment != FORMAT:
            raise ValueError('加密备份格式不受支持')
        names = set()
        pairs = set()
        for member in bundle.infolist():
            name = member.filename
            if name in names or member.compress_type != ZIP_STORED or member.compress_size != member.file_size:
                raise ValueError('备份包含重复或无效的文件条目')
            path = stage / name
            if name != DATABASE:
                parts = PurePosixPath(name).parts
                if (len(parts) not in (3, 4) or parts[0] != 'certs' or '/'.join(parts) != name
                        or (len(parts) == 4 and parts[2] != 'manual') or parts[-1] not in CERTIFICATE_SIZES
                        or validate_public_host(parts[1]) != parts[1]
                        or not 0 < member.file_size <= CERTIFICATE_SIZES[parts[-1]]):
                    raise ValueError('备份包含无效的证书文件路径或大小')
                pairs.add(path.parent)
            names.add(name)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with bundle.open(member) as source, _private_file(path) as output:
                shutil.copyfileobj(source, output)
        if DATABASE not in names:
            raise ValueError('加密备份缺少 Router 数据库')
        for directory in pairs:
            cert = directory / 'fullchain.pem'
            key = directory / 'privkey.pem'
            if not cert.is_file() or not key.is_file():
                raise ValueError('加密备份中的证书和私钥未成对保存')
            validate_key_pair(cert, key)


def restore_archive(archive, data_dir, identity):
    age = _age()
    target = data_dir.parent.resolve() / data_dir.name
    if os.path.lexists(target):
        raise ValueError('恢复目录已存在，请指定新的数据目录')
    if not archive.is_file() or not identity.is_file():
        raise ValueError('请选择有效的加密备份文件和 age 私钥文件')
    with tempfile.TemporaryDirectory(dir=target.parent, prefix='.hopworld-restore-') as directory:
        stage = Path(directory) / 'data'
        stage.mkdir(mode=0o700)
        with archive.open('rb') as encrypted, tempfile.TemporaryFile(dir=directory) as decrypted:
            result = subprocess.run([age, '--decrypt', '--identity', str(identity.resolve())], stdin=encrypted,
                                    stdout=decrypted, stderr=subprocess.DEVNULL)
            if result.returncode:
                raise ValueError('age 解密失败，请检查私钥、备份文件和存储空间')
            decrypted.seek(0)
            _extract_bundle(decrypted, stage)
        with validated_database(stage / DATABASE):
            pass
        _certificate_reference(stage, target)
        # Reserve the new name without ever replacing an existing data directory.
        target.mkdir(mode=0o700)
        try:
            os.replace(stage, target)
        except OSError:
            if not any(target.iterdir()):
                target.rmdir()
            raise


def main():
    parser = argparse.ArgumentParser(description='HopWorld Router 加密备份与新目录恢复')
    modes = parser.add_subparsers(dest='action', required=True)
    create = modes.add_parser('create', help='停服后备份数据库、证书和私钥')
    create.add_argument('--data-dir', type=Path, default=Path(os.environ.get('HOPWORLD_DATA', './data')))
    create.add_argument('--recipient', required=True, help='age 公开接收者（age1…）')
    restore = modes.add_parser('restore', help='解密并恢复至新的数据目录')
    restore.add_argument('--data-dir', type=Path, required=True)
    restore.add_argument('--identity', type=Path, required=True, help='age 私钥文件')
    for mode in (create, restore):
        mode.add_argument('archive', type=Path, help='加密备份文件路径')
    args = parser.parse_args()
    try:
        if args.action == 'create':
            create_archive(args.archive, args.data_dir, args.recipient)
            print(f'加密备份已保存到 {args.archive}')
        else:
            restore_archive(args.archive, args.data_dir, args.identity)
            print(f'数据已恢复到 {args.data_dir}')
    except (OSError, ValueError, BadZipFile, sqlite3.Error) as exc:
        parser.exit(1, f'操作失败。{exc}\n')


if __name__ == '__main__':
    main()
