"""Linux nftables preflight and fail-closed rules for a dedicated test namespace."""

import argparse
from contextlib import contextmanager
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from router.security_dns import endpoint_ip, valid_port


TABLE = 'hopworld_egress'
IP = '/usr/sbin/ip'
NFT = '/usr/sbin/nft'


def interface_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}', value) or value == 'lo':
        raise ValueError('请选择专用命名空间中的网络接口')
    return value


def render_rules(interface=None, upstream=None, port=None, listeners=()):
    rules = []
    if upstream is not None:
        interface = interface_name(interface)
        address = ipaddress.ip_address(endpoint_ip(upstream))
        protocol = 'ip' if address.version == 4 else 'ip6'
        port = valid_port(port)
        if port in (53, 853):
            raise ValueError('隔离测试的 SOCKS5 端口不能使用 53 或 853')
        rules.append(f'oifname "{interface}" {protocol} daddr {address} tcp dport {port} counter accept')
        ports = sorted({valid_port(item) for item in listeners})
        if any(port < 1024 for port in ports):
            raise ValueError('QUIC 监听端口必须大于等于 1024')
        if ports:
            members = ', '.join(map(str, ports))
            rules.append(f'oifname "{interface}" ct direction reply ct state established udp sport {{ {members} }} counter accept')
        if address.version == 6 or ports:
            # Neighbor discovery is needed for traffic that is already allowed by the guard.
            rules.append(f'oifname "{interface}" ip6 hoplimit 255 icmpv6 type '
                         '{ nd-neighbor-solicit, nd-neighbor-advert } counter accept')
    rules.append('counter drop')
    return render_isolated_table(rules)


def render_isolated_table(output=(), forward=(), *, nat=None, ingress=None):
    body = '\n'.join(f'        {rule}' for rule in output)
    forwarding = '\n'.join(f'        {rule}' for rule in forward)
    inbound = '\n'.join(f'        {rule}' for rule in ingress or ())
    translation = f'''
    chain postrouting {{
        type nat hook postrouting priority srcnat; policy accept;
        {nat}
    }}''' if nat else ''
    incoming = f'''
    chain input {{
        type filter hook input priority filter; policy drop;
{inbound}
    }}''' if ingress is not None else ''
    return f"""add table inet {TABLE}
delete table inet {TABLE}
table inet {TABLE} {{
    chain output {{
        type filter hook output priority filter; policy drop;
{body}
    }}
    chain forward {{
        type filter hook forward priority filter; policy drop;
{forwarding}
    }}
{incoming}{translation}
}}
"""


def _exec(namespace, command, *, rules=None):
    try:
        return subprocess.run([IP, 'netns', 'exec', namespace, *command], input=rules, text=True,
                              capture_output=True, check=True, timeout=8).stdout
    except subprocess.CalledProcessError as exc:
        raise ValueError(exc.stderr.strip() or '命名空间网络预检失败') from exc


def _scope(namespace):
    if not re.fullmatch(r'hwr-sec-[a-z0-9][a-z0-9-]{0,23}', namespace):
        raise ValueError('请使用专用的 hwr-sec-* 网络命名空间')
    if sys.platform != 'linux' or not all(Path(p).is_file() for p in (IP, NFT)):
        raise ValueError('仅支持安装了 iproute2 和 nftables 的 Linux')
    if os.geteuid() != 0:
        raise ValueError('在 Linux 测试主机上以 root 执行命名空间预检或应用')
    namespace_path = Path('/run/netns') / namespace
    if namespace_path.is_symlink():
        raise ValueError('命名空间路径不能是符号链接')
    for host in ('/proc/self/ns/net', '/proc/1/ns/net'):
        if namespace_path.samefile(host):
            raise ValueError('禁止修改 Router 或系统的网络命名空间')


def _check_route(namespace, interface, upstream):
    address = ipaddress.ip_address(upstream)
    routes = json.loads(_exec(namespace, [IP, '-j', f'-{address.version}', 'route', 'get', str(address)]))
    if len(routes) != 1 or routes[0].get('dev') != interface or routes[0].get('type', 'unicast') != 'unicast':
        raise ValueError('SOCKS5 端点在该网络命名空间没有经指定接口的单播路由')


@contextmanager
def namespace_lock(namespace):
    _scope(namespace)
    import fcntl

    fd = os.open(f'/run/lock/{namespace}.hopworld-egress', os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def change_rules(action, namespace, interface=None, upstream=None, port=None, listeners=()):
    if action in ('plan', 'check', 'apply') and (not interface or not upstream or port is None or not listeners):
        raise ValueError('请明确提供出口接口、固定 SOCKS5 IP/端口及 QUIC 监听端口')
    rules = render_rules(interface, upstream, port, listeners)
    if action == 'plan':
        return rules
    if action == 'status':
        _scope(namespace)
        return _exec(namespace, [NFT, 'list', 'table', 'inet', TABLE])
    with namespace_lock(namespace):
        blocked = render_rules()
        if action in ('apply', 'block'):
            _exec(namespace, [NFT, '-f', '-'], rules=blocked)
        if action in ('check', 'apply'):
            _check_route(namespace, interface, upstream)
            _exec(namespace, [NFT, '--check', '-f', '-'], rules=rules)
        if action == 'apply':
            try:
                _exec(namespace, [NFT, '-f', '-'], rules=rules)
                return _exec(namespace, [NFT, 'list', 'table', 'inet', TABLE])
            except (OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt):
                _exec(namespace, [NFT, '-f', '-'], rules=blocked)
                raise
        if action == 'check':
            return rules
        return _exec(namespace, [NFT, 'list', 'table', 'inet', TABLE])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'check', 'apply', 'block', 'status'))
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--interface')
    parser.add_argument('--upstream')
    parser.add_argument('--upstream-port', type=int)
    parser.add_argument('--listen-port', type=int, action='append', default=[])
    options = parser.parse_args()
    try:
        print(change_rules(options.action, options.namespace, options.interface, options.upstream,
                           options.upstream_port, options.listen_port), end='')
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
