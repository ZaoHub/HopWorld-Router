"""Control only the installed service that owns the current Router process."""

import os
import platform
import re
import subprocess


SYSTEMCTL = '/usr/bin/systemctl'
UNIT = 'hopworld-router.service'
LAUNCHCTL = '/bin/launchctl'
AGENT = 'com.hopworld.router.local'


def _run(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def _service():
    platform_name = platform.system()
    if platform_name == 'Linux':
        for prefix in ((), ('--user',)):
            argv = [SYSTEMCTL, *prefix]
            result = _run([*argv, 'show', '-p', 'MainPID', UNIT])
            if result and result.returncode == 0 and result.stdout.strip() == f'MainPID={os.getpid()}':
                return ('user' if prefix else 'system', argv, UNIT)
    if platform_name == 'Darwin':
        domain = f'gui/{os.getuid()}'
        service = f'{domain}/{AGENT}'
        result = _run([LAUNCHCTL, 'print', service])
        if result and result.returncode == 0 and re.search(rf'^\s*pid = {os.getpid()}\s*$', result.stdout, re.MULTILINE):
            return ('launchd', [LAUNCHCTL], domain)
    return None


def status(service=None):
    service = service or _service()
    if not service:
        return {'enabled': None, 'writable': False, 'label': '开机自启动', 'detail': '当前以手动方式运行，或没有可管理的服务'}
    kind, argv, target = service
    if kind == 'launchd':
        result = _run([*argv, 'print-disabled', target])
        enabled = None if not result or result.returncode else not re.search(
            rf'"{re.escape(AGENT)}"\s*=>\s*(?:disabled|true)\b', result.stdout,
        )
    else:
        result = _run([*argv, 'is-enabled', target])
        state = result.stdout.strip() if result else ''
        enabled = {'enabled': True, 'disabled': False, 'enabled-runtime': False}.get(state)
    writable = enabled is not None
    if writable and kind == 'system' and os.geteuid() != 0:
        writable = all(
            result is not None and result.returncode == 0
            for verb in ('enable', 'disable')
            for result in [_run(['/usr/bin/sudo', '-n', '-l', *argv, verb, target])]
        )
    detail = '' if writable else '当前服务未授予页面修改权限' if enabled is not None else '无法读取服务自启动状态'
    return {'enabled': enabled, 'writable': writable,
            'label': '开机自启动' if kind == 'system' else '登录后启动', 'detail': detail}


def set_enabled(enabled):
    if type(enabled) is not bool:
        raise ValueError('自启动设置无效')
    service = _service()
    if not service:
        raise ValueError('当前 Router 未注册为系统或用户服务')
    before = status(service)
    if not before['writable']:
        raise ValueError(before['detail'])
    if before['enabled'] == enabled:
        return before
    kind, argv, target = service
    verb = 'enable' if enabled else 'disable'
    if kind == 'launchd':
        target = f'{target}/{AGENT}'
    if kind == 'system' and os.geteuid() != 0:
        argv = ['/usr/bin/sudo', '-n', *argv]
    result = _run([*argv, verb, target])
    if not result or result.returncode:
        raise ValueError('服务管理器未能修改自启动状态')
    after = status(service)
    if after['enabled'] != enabled:
        raise ValueError('未能确认新的自启动状态')
    return after
