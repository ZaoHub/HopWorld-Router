"""Persist update preferences and history for the management console."""

from pathlib import Path
import re
import sqlite3
import subprocess
import time

from router import system_update
from router.core_install import check_update, install_core
from router.independent_core import PUBLISHERS
from router.system_info import CORES, core_inventory


TARGETS = ('project', 'package', *PUBLISHERS)
CORE_INTERVAL = 6 * 3600


class UpdateCenter:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    feature TEXT PRIMARY KEY, revision INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS update_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp INTEGER NOT NULL, target TEXT NOT NULL,
                    state TEXT NOT NULL, current_version TEXT NOT NULL, target_version TEXT NOT NULL,
                    detail TEXT NOT NULL, event_key TEXT UNIQUE
                );
                CREATE TABLE IF NOT EXISTS update_targets (
                    target TEXT PRIMARY KEY, automatic INTEGER NOT NULL DEFAULT 0, checked_at INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO schema_migrations VALUES ('updates', 1);
            """)

    def preferences(self):
        with self.store.connect() as db:
            rows = db.execute('SELECT target,automatic FROM update_targets').fetchall()
        preferences = dict.fromkeys(TARGETS, False)
        preferences.update((row['target'], bool(row['automatic'])) for row in rows if row['target'] in TARGETS)
        if system_update.current_target() == 'project':
            preferences['project'] = self.store.update_prefs()['auto_apply']
        return preferences

    def save(self, data):
        target = data.get('target')
        automatic = data.get('automatic')
        if set(data) != {'target', 'automatic'} or target not in TARGETS or type(automatic) is not bool:
            raise ValueError('更新设置无效')
        if target == 'project':
            return self.store.save_update_prefs(automatic or self.store.update_prefs()['auto_check'], automatic)
        with self.store._local_lock, self.store.connect() as db:
            db.execute(
                'INSERT INTO update_targets(target,automatic) VALUES (?,?) '
                'ON CONFLICT(target) DO UPDATE SET automatic=excluded.automatic, '
                'checked_at=CASE WHEN excluded.automatic>update_targets.automatic THEN 0 ELSE update_targets.checked_at END',
                (target, automatic),
            )
        return {'target': target, 'automatic': automatic}

    def record(self, target, state, *, current='', version='', detail='', key=None, timestamp=None):
        with self.store.connect() as db:
            db.execute(
                'INSERT OR IGNORE INTO update_events(timestamp,target,state,current_version,target_version,detail,event_key) '
                'VALUES (?,?,?,?,?,?,?)',
                (int(time.time()) if timestamp is None else timestamp, target, state,
                 str(current or '')[:120], str(version or '')[:120], str(detail or '')[:400], key),
            )
            db.execute('DELETE FROM update_events WHERE id < COALESCE('
                       '(SELECT id FROM update_events ORDER BY id DESC LIMIT 1 OFFSET 299),0)')

    def _cursor(self, db):
        row = db.execute("SELECT value FROM settings WHERE key='update_history_read'").fetchone()
        return int(row['value']) if row and row['value'].isdigit() else 0

    def history(self):
        self.import_worker_result()
        with self.store.connect() as db:
            cursor = self._cursor(db)
            history = [dict(row) for row in db.execute(
                'SELECT id,timestamp,target,state,current_version,target_version,detail '
                'FROM update_events ORDER BY id DESC LIMIT 80',
            )]
            unread = db.execute('SELECT count(*) FROM update_events WHERE id>?', (cursor,)).fetchone()[0]
        return {'events': history, 'unread': unread, 'read_through': cursor}

    def mark_read(self, seen):
        if type(seen) is not int or seen < 0 or seen > 2**63 - 1:
            raise ValueError('记录位置无效')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            latest = db.execute('SELECT COALESCE(MAX(id),0) FROM update_events').fetchone()[0]
            cursor = max(self._cursor(db), min(seen, latest))
            db.execute("INSERT OR REPLACE INTO settings VALUES ('update_history_read',?)", (str(cursor),))

    def import_worker_result(self):
        result = system_update.worker_result()
        if result:
            target = system_update.current_target()
            failed = result['state'] == 'failed'
            self.record(target, 'failed' if failed else 'completed', current=result.get('current'),
                        version=result.get('target'),
                        detail='更新失败，请检查服务日志' if failed else '更新任务已完成',
                        key=f'worker:{target}:{result["id"]}',
                        timestamp=result['timestamp'] if type(result.get('timestamp')) is int else None)
        return result

    def project_status(self, *, refresh=False):
        target = system_update.current_target()
        status = system_update.status(refresh=refresh)
        result = self.import_worker_result()
        if status['state'] == 'idle' and result:
            status = {**status, 'state': 'failed' if result['state'] == 'failed' else 'current',
                      'error': '更新失败，请检查服务日志' if result['state'] == 'failed' else '',
                      'finished_at': result.get('timestamp', 0)}
        if status['state'] in ('available', 'unavailable') and status.get('checked_at'):
            self.record(target, status['state'], current=status.get('current'), version=status.get('release') or status.get('target'),
                        detail=status.get('error') or '发现同主版本的新发布',
                        key=f'release:{target}:{status.get("current")}:{status.get("target")}:{status["state"]}:{status["error"]}')
        return status

    def start_project(self, status=None):
        target = system_update.current_target()
        status = status or self.project_status()
        version = status.get('release') or status.get('target')
        try:
            system_update.start()
        except ValueError as exc:
            self.record(target, 'failed', version=version, detail=str(exc),
                        key=f'launcher:{target}:{version}:{int(time.time()) // 3600}:{exc}')
            raise
        self.record(target, 'started', current=status.get('current'), version=version)

    def core_check(self, core_id, row):
        try:
            result = check_update(core_id, row.get('version') if row['installed'] else None)
        except ValueError as exc:
            self.record(core_id, 'failed', detail=str(exc), key=f'core-check:{core_id}:{int(time.time()) // 3600}:{exc}')
            raise
        if result['update'] and row['installed']:
            self.record(core_id, 'available', current=result['current'], version=result['latest'],
                        detail='发现官方内核发布',
                        key=f'core-release:{core_id}:{result["current"]}:{result["latest"]}')
        return result

    def install_core(self, core_id, row, *, progress=None, automatic=False):
        if core_id not in PUBLISHERS:
            raise ValueError('没有这个内核')
        current = row.get('version') if row and row['installed'] else None
        version_arg = next(item[3] for item in CORES if item[0] == core_id)

        def check_binary(candidate):
            arg = version_arg if candidate.name == PUBLISHERS[core_id][0] else '--version'
            try:
                subprocess.run([candidate, arg], stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=5)
            except subprocess.CalledProcessError as exc:
                if match := re.search(rb'GLIBC_[0-9.]+', exc.stderr or b''):
                    required = match.group().decode('ascii')
                    raise ValueError(f'{candidate.name} 需要 {required}，当前服务器缺少此版本，原安装仍保留') from exc
                raise ValueError(f'{candidate.name} 无法在此服务器运行（退出码 {exc.returncode}），原安装仍保留') from exc
            except (OSError, subprocess.SubprocessError) as exc:
                raise ValueError(f'{candidate.name} 无法在此服务器运行，原安装仍保留') from exc

        def install_allowed():
            if automatic:
                path = self.store.data_dir / 'bin' / PUBLISHERS[core_id][0]
                if (not row or not row['path'] or path.is_symlink() or not path.is_file()
                        or path.parent.resolve() != self.store.data_dir.resolve() / 'bin'
                        or path.resolve() != Path(row['path']).resolve()
                        or not self.preferences()[core_id]):
                    return '自动更新仅适用于 Router 已安装的内核'
            if core_id == 'sing-box' and any(item['active'] for item in self.store.security.get('policies')['policies']):
                return '有加密 DNS 策略使用锁定的 sing-box 版本，请先处理版本兼容性'
            return ''

        try:
            if reason := install_allowed():
                raise ValueError(reason)
            installed = install_core(self.store.data_dir, core_id, progress=progress, current=current,
                                     finalize_lock=self.store._local_lock, check_finalize=install_allowed,
                                     check_binary=check_binary)
        except (ValueError, OSError) as exc:
            detail = str(exc) if isinstance(exc, ValueError) else '无法写入内核安装目录'
            self.record(core_id, 'failed', current=current, detail=detail,
                        key=f'core-install:{core_id}:{current}:{int(time.time()) // 3600}:{detail}' if automatic else None)
            raise ValueError(detail) from exc
        if not installed.get('uptodate'):
            running = row and row['status'] == 'running'
            self.record(core_id, 'completed', current=current, version=installed['version'],
                        detail='内核已写入磁盘，下次重启生效' if running else '内核安装完成')
        return installed

    def run_core_updates(self):
        now = int(time.time())
        with self.store.connect() as db:
            due = [row['target'] for row in db.execute(
                'SELECT target FROM update_targets WHERE automatic=1 AND checked_at<=? ORDER BY target',
                (now - CORE_INTERVAL,),
            ) if row['target'] in PUBLISHERS]
        if not due:
            return
        inventory = {row['id']: row for row in core_inventory(self.store)}
        for core_id in due:
            with self.store.connect() as db:
                if not db.execute(
                    'UPDATE update_targets SET checked_at=? WHERE target=? AND automatic=1 AND checked_at<=?',
                    (now, core_id, now - CORE_INTERVAL),
                ).rowcount:
                    continue
            row = inventory.get(core_id)
            if not row or not row['installed'] or Path(row['path']).resolve() != (
                self.store.data_dir / 'bin' / PUBLISHERS[core_id][0]
            ).resolve():
                continue
            try:
                if self.core_check(core_id, row)['update']:
                    self.install_core(core_id, row, automatic=True)
            except (ValueError, sqlite3.Error):
                continue
