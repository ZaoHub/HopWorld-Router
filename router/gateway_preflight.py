"""Check prerequisites for strict Linux gateway deployment before creating a topology."""

import json
import os
from pathlib import Path
import subprocess
import sys

from router.egress_guard import IP, NFT, render_isolated_table
from router.gateway_namespace import NSENTER, NS_GET_USERNS, SETPRIV, mapped_namespace, worker_privileges


SYSCTL = '/usr/sbin/sysctl'
TUN_DEVICE = Path('/dev/net/tun')
TOOLS = (IP, NFT, NSENTER, SETPRIV, SYSCTL)
KERNEL_CHECKS = ('user_network_namespace', 'worker_privileges', 'tun_kernel', 'nftables_kernel')


def gateway_requirements():
    tools = {Path(path).name: os.path.isfile(path) and os.access(path, os.X_OK) for path in TOOLS}
    return tools, TUN_DEVICE.is_char_device()


def _run(command, *, rules=None):
    return subprocess.run(command, text=True, input=rules, capture_output=True, check=True, timeout=5).stdout


def _check_namespace(pid):
    import fcntl

    target = Path('/proc') / str(pid) / 'ns'
    with (target / 'net').open('rb') as net:
        if os.path.samestat(os.fstat(net.fileno()), os.stat('/proc/self/ns/net')):
            raise ValueError('未获得独立的网络命名空间')
        owner = fcntl.ioctl(net.fileno(), NS_GET_USERNS)
        try:
            if not os.path.samestat(os.fstat(owner), (target / 'user').stat()):
                raise ValueError('无法验证网络命名空间的用户归属')
        finally:
            os.close(owner)


def host_preflight():
    checks = {}

    def report(name, status, detail):
        checks[name] = {'status': status, 'detail': detail}

    def probe(name, check, ready, blocked):
        try:
            check()
        except (OSError, ValueError, subprocess.SubprocessError):
            report(name, 'BLOCKED', blocked)
        else:
            report(name, 'PASS', ready)

    if sys.platform != 'linux':
        report('platform', 'BLOCKED', '严格网关需要 Linux 原生环境')
        return {'status': 'BLOCKED', 'checks': checks}

    tools, tun = gateway_requirements()
    for binary in TOOLS:
        name = Path(binary).name
        report(name, 'PASS' if tools[name] else 'BLOCKED', binary if tools[name] else f'缺少可执行文件 {binary}')
    report('tun_device', 'PASS' if tun else 'BLOCKED', str(TUN_DEVICE) if tun else '主机未提供 /dev/net/tun')

    def ip_json():
        commands = ([IP, '-j', '-d', 'link', 'show', 'dev', 'lo'],
                    [IP, '-4', '-j', 'rule', 'show'], [IP, '-6', '-j', 'rule', 'show'])
        for command in commands:
            if not isinstance(json.loads(_run(command)), list):
                raise ValueError('无效的 iproute2 JSON 输出')

    if tools['ip']:
        probe('iproute2_json', ip_json, '网卡和双栈策略路由支持 JSON 输出',
              '需要支持 ip -j 和双栈策略路由的 iproute2')
    if tools['setpriv']:
        def setpriv_features():
            options = _run([SETPRIV, '--help'])
            if any(option not in options for option in ('--ambient-caps', '--bounding-set', '--pdeathsig')):
                raise ValueError('缺少权限隔离或进程联动退出参数')

        probe('setpriv_options', setpriv_features, '支持能力约束和监督进程退出信号',
              '需要支持 --ambient-caps、--bounding-set 和 --pdeathsig 的 util-linux')

    for name in KERNEL_CHECKS:
        report(name, 'PENDING', '需要 root 在一次性命名空间内验证')

    if os.geteuid() == 0:
        try:
            with mapped_namespace(65534, 65534) as pid:
                _check_namespace(pid)
                report('user_network_namespace', 'PASS', '已创建独立用户与网络命名空间，并核对归属')
                target = [NSENTER, '--target', str(pid), '--net']
                command = [*target, '--user', *worker_privileges(net_admin=True)]
                if tools['nsenter'] and checks.get('setpriv_options', {}).get('status') == 'PASS':
                    probe('worker_privileges', lambda: _run([*command, '/usr/bin/true']),
                          '受限网关身份可以启动', '无法进入专用用户命名空间或设置所需能力')
                if tun and tools['ip'] and checks['worker_privileges']['status'] == 'PASS':
                    probe('tun_kernel', lambda: _run([*command, IP, 'tuntap', 'add', 'dev', 'hwr-preflight', 'mode', 'tun']),
                          '受限网关身份可在独立命名空间创建 TUN', 'TUN 存在，但受限网关身份无法创建接口')
                if tools['nft'] and tools['nsenter']:
                    rules = render_isolated_table(
                        forward=('iifname "gw-test" ip6 daddr 2001:db8::1 tcp dport 1080 ct direction original counter accept',),
                        nat='oifname "gw-test" ip6 daddr 2001:db8::1 tcp dport 1080 masquerade',
                    )
                    probe('nftables_kernel', lambda: _run([*target, NFT, '--check', '-f', '-'], rules=rules),
                          '内核支持网关所需的 inet、conntrack 和 NAT 规则',
                          'nftables 可执行文件存在，但内核或虚拟化权限不支持所需规则')
        except (OSError, ValueError, subprocess.SubprocessError):
            report('user_network_namespace', 'BLOCKED',
                   '无法创建并验证独立用户和网络命名空间。请核对宿主机内核及虚拟化限制')
        for name in KERNEL_CHECKS:
            if checks[name]['status'] == 'PENDING':
                report(name, 'SKIPPED', '依赖的系统工具、设备或命名空间未通过检查')

    states = {result['status'] for result in checks.values()}
    status = 'BLOCKED' if 'BLOCKED' in states else 'NEEDS_ROOT' if 'PENDING' in states else 'READY_FOR_TOPOLOGY'
    return {'status': status, 'checks': checks}
