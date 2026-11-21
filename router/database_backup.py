"""Create consistent SQLite snapshots and restore them while the Router is offline."""

import argparse
from contextlib import closing, contextmanager
import os
from pathlib import Path
import sqlite3
import tempfile


@contextmanager
def data_lock(data_dir):
    if os.name != 'posix':
        yield
        return
    import fcntl

    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(data_dir / '.router.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('此数据目录正在使用，请停止对应的 HopWorld Router 服务') from None
        yield
    finally:
        os.close(fd)


def snapshot(source, path):
    with closing(sqlite3.connect(path)) as target:
        source.backup(target)


@contextmanager
def validated_database(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as source:
        source.execute('PRAGMA trusted_schema=OFF')
        source.execute('BEGIN')
        schema = {row[0]: row[1] for row in source.execute('SELECT name,type FROM sqlite_master')}
        if any(schema.get(table) != 'table' for table in ('settings', 'sources', 'manual_nodes', 'disabled_nodes')):
            raise ValueError('文件缺少 HopWorld Router 数据表')
        if not {'view', 'trigger'}.isdisjoint(schema.values()) or source.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('备份未通过 SQLite 完整性检查')
        for table, required in (
            ('settings', {'key', 'value'}), ('manual_nodes', {'id', 'link'}), ('disabled_nodes', {'id'}),
            ('sources', {'id', 'name', 'url', 'links', 'updated', 'error'}),
        ):
            columns = {row[1] for row in source.execute(f'PRAGMA table_info({table})')}
            if not required <= columns:
                raise ValueError('备份的 HopWorld Router 数据表结构不兼容')
        yield source


def restore(path, data_dir, *, apply=False):
    path = path.resolve()
    target_path = data_dir / 'router.db'
    if target_path.exists() and path.samefile(target_path):
        raise ValueError('请选择独立的备份文件')
    with validated_database(path) as source:
        if not apply:
            return None
        if os.name != 'posix':
            raise ValueError('请在 Linux、macOS 或 WSL 中执行停服恢复')
        with data_lock(data_dir):
            existed = target_path.exists()
            fd = os.open(target_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            rollback_path = None
            try:
                with closing(sqlite3.connect(target_path)) as target:
                    if existed:
                        fd, filename = tempfile.mkstemp(prefix='router-before-restore-', suffix='.db', dir=data_dir)
                        os.close(fd)
                        try:
                            snapshot(target, filename)
                        except (OSError, sqlite3.Error):
                            Path(filename).unlink(missing_ok=True)
                            raise
                        rollback_path = Path(filename)
                    source.backup(target)
            except (OSError, sqlite3.Error):
                if not existed:
                    target_path.unlink(missing_ok=True)
                raise
        return rollback_path


def main():
    parser = argparse.ArgumentParser(description='验证或恢复 HopWorld Router 数据库快照')
    parser.add_argument('backup', type=Path)
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('HOPWORLD_DATA', './data')))
    parser.add_argument('--apply', action='store_true', help='停服后覆盖目标数据库，覆盖前保留原库快照')
    args = parser.parse_args()
    try:
        previous = restore(args.backup, args.data_dir, apply=args.apply)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f'恢复检查失败：{exc}\n')
    if not args.apply:
        print('数据库快照通过完整性检查')
    elif previous:
        print(f'数据库已恢复。恢复前的数据库副本：{previous}')
    else:
        print('数据库已恢复')


if __name__ == '__main__':
    main()
