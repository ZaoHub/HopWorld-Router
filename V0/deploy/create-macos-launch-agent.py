#!/usr/bin/env python3
"""Create a private per-user launchd configuration; loading it is a separate step."""

import getpass
import os
from pathlib import Path
import plistlib
import sys

LABEL = 'com.hopworld.router.local'


def main():
    if sys.platform != 'darwin':
        raise SystemExit('此配置仅用于 macOS。')
    root = Path(__file__).resolve().parent.parent
    python = root / '.venv/bin/python'
    if not python.is_file():
        raise SystemExit('请先在项目目录创建 .venv 并安装依赖。')
    agent = Path.home() / 'Library/LaunchAgents' / f'{LABEL}.plist'
    if agent.exists():
        raise SystemExit(f'配置已存在：{agent}。请先按说明卸载，避免覆盖。')
    password = getpass.getpass('设置管理密码（至少 16 字符，不能包含冒号）：')
    if len(password) < 16 or ':' in password:
        raise SystemExit('密码必须至少 16 字符且不包含冒号。')
    if password != getpass.getpass('再次输入管理密码：'):
        raise SystemExit('两次输入的密码不一致。')
    data = root / 'data'
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    agent.parent.mkdir(parents=True, exist_ok=True)
    config = {
        'Label': LABEL,
        'ProgramArguments': [str(python), '-m', 'router.server'],
        'WorkingDirectory': str(root),
        'EnvironmentVariables': {
            'HOPWORLD_ADMIN_PASSWORD': password,
            'HOPWORLD_HOST': '127.0.0.1',
            'HOPWORLD_DATA': str(data),
            'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
        },
        'RunAtLoad': True,
        'KeepAlive': {'SuccessfulExit': False},
        'StandardOutPath': str(data / 'launchd.stdout.log'),
        'StandardErrorPath': str(data / 'launchd.stderr.log'),
    }
    try:
        payload = plistlib.dumps(config)
    except ValueError:
        raise SystemExit('配置包含无法写入 plist 的字符，请更换密码或路径。')
    try:
        fd = os.open(agent, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit(f'配置已存在：{agent}。请先按说明卸载，避免覆盖。')
    with os.fdopen(fd, 'wb') as output:
        output.write(payload)
    print(f'配置已创建：{agent}。请按文档手动加载。')


if __name__ == '__main__':
    main()
