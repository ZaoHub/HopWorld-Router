"""Password-gated removal for the documented systemd installations."""

import argparse
from contextlib import closing
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import warnings

from router.admin_auth import ADMIN_ACCOUNT_KEY, ADMIN_PASSWORD_KEY, DEFAULT_PASSWORD, matches_password


UNIT = 'hopworld-router.service'
ENV_FILE = Path('/etc/hopworld-router.env')
DATA = Path('/var/lib/hopworld-router')
PACKAGE_ROOT = Path('/usr/lib/hopworld-router')
GIT_ROOT = Path('/opt/hopworld-router')
GIT_UNIT = Path('/etc/systemd/system') / UNIT


def auth_verifier(database, configured):
    if database.is_symlink():
        raise ValueError('数据库为符号链接，请先核实数据目录')
    if not database.exists():
        return ''
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        rows = dict(db.execute('SELECT key, value FROM settings WHERE key IN (?, ?)', (ADMIN_ACCOUNT_KEY, ADMIN_PASSWORD_KEY)))
    if ADMIN_ACCOUNT_KEY in rows:
        account = json.loads(rows[ADMIN_ACCOUNT_KEY])
        if not isinstance(account, list) or len(account) != 2 or not account[1]:
            raise ValueError('管理员密码记录无法读取')
        return account[1]
    return rows.get(ADMIN_PASSWORD_KEY, '') if configured == DEFAULT_PASSWORD else ''


def runtime_config(pid):
    if pid > 1:
        fields = Path(f'/proc/{pid}/environ').read_bytes().decode().split('\0')
        return dict(field.split('=', 1) for field in fields if '=' in field)
    if not ENV_FILE.exists():
        return {}
    settings = {}
    for line in ENV_FILE.read_text().splitlines():
        name, sep, value = line.strip().partition('=')
        if not sep or name.startswith(('#', ';')):
            continue
        value = value.strip()
        if value.endswith('\\') or (value.startswith(('"', "'")) and value.count(value[0]) < 2):
            raise ValueError('存在多行环境配置，请在服务运行时执行，以读取有效配置')
        if name.strip() not in ('HOPWORLD_ADMIN_PASSWORD', 'HOPWORLD_DATA'):
            continue
        if '\\' in value or (value.startswith(('"', "'")) and (value[-1] != value[0] or value.count(value[0]) != 2)):
            raise ValueError('存在转义环境配置，请在服务运行时执行，以读取有效配置')
        if value.startswith(('"', "'")):
            value = value[1:-1]
        settings[name.strip()] = value
    return settings


def uninstall():
    if os.geteuid() != 0 or not sys.stdin.isatty():
        raise ValueError('请在交互式终端使用 sudo 执行完整卸载')
    output = subprocess.check_output(
        ['systemctl', 'show', UNIT, '-p', 'MainPID', '-p', 'WorkingDirectory', '-p', 'FragmentPath', '-p', 'DropInPaths'],
        text=True,
    )
    unit = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    directory = Path(unit.get('WorkingDirectory', ''))
    fragment = unit.get('FragmentPath', '')
    packaged = directory == PACKAGE_ROOT and fragment in (f'/lib/systemd/system/{UNIT}', f'/usr/lib/systemd/system/{UNIT}')
    git = directory == GIT_ROOT and fragment == str(GIT_UNIT)
    if not (packaged or git) or unit.get('DropInPaths'):
        raise ValueError('完整卸载仅适用于标准 Debian 软件包或 /opt/hopworld-router 安装')
    pid = int(unit.get('MainPID', '0'))
    if ENV_FILE.is_symlink() or directory.is_symlink() or DATA.is_symlink():
        raise ValueError('发现符号链接，已停止自动卸载')
    config = runtime_config(pid)
    if Path(config.get('HOPWORLD_DATA', str(DATA) if packaged else '')) != DATA:
        raise ValueError('检测到非默认数据目录，请先核实并单独处理该目录')
    database = DATA / 'router.db'
    configured = config.get('HOPWORLD_ADMIN_PASSWORD') or DEFAULT_PASSWORD
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        password = getpass.getpass('当前 Router 管理员密码：')
    verifier = auth_verifier(database, configured)
    if not matches_password(configured, verifier, password):
        raise ValueError('管理员密码不正确')
    print(f'将卸载 Router 并永久删除 {DATA}（含节点、订阅和证书缓存）与 {ENV_FILE}。')
    if input('输入 UNINSTALL 确认完整卸载：') != 'UNINSTALL':
        print('已取消卸载。')
        return

    mounts = [line.split()[4] for line in Path('/proc/self/mountinfo').read_text().splitlines()]
    for target in (DATA, directory) if git else (DATA,):
        if any(point == str(target) or point.startswith(f'{target}/') for point in mounts):
            raise ValueError('应用目录或数据目录下存在挂载点，请先核实挂载')
    subprocess.run(['systemctl', 'stop', UNIT], check=True)
    if auth_verifier(database, configured) != verifier:
        if pid > 1:
            subprocess.run(['systemctl', 'start', UNIT], check=True)
        raise ValueError('账号在验证后发生变化，请重新执行卸载')
    if packaged:
        subprocess.run(['dpkg', '--purge', 'hopworld-router'], check=True)
    else:
        subprocess.run(['systemctl', 'disable', UNIT], check=True)
        GIT_UNIT.unlink()
        Path('/etc/sudoers.d/hopworld-router-autostart').unlink(missing_ok=True)
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        shutil.rmtree(directory)
    if DATA.exists():
        shutil.rmtree(DATA)
    ENV_FILE.unlink(missing_ok=True)
    print('HopWorld Router 已完整卸载。')


if __name__ == '__main__':
    argparse.ArgumentParser(description='验证 Router 管理员密码后完整卸载标准 systemd 安装。').parse_args()
    try:
        uninstall()
    except KeyboardInterrupt:
        sys.exit('\n已中止卸载。')
    except (OSError, ValueError, EOFError, sqlite3.Error, subprocess.SubprocessError, getpass.GetPassWarning) as exc:
        sys.exit(f'完整卸载已停止。{exc}')
