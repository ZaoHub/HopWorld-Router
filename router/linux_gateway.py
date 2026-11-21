"""Run the Linux LAN gateway in two dedicated network namespaces."""

import argparse
from contextlib import ExitStack
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time

from router.egress_guard import IP, NFT, TABLE, _check_route, _exec, interface_name, namespace_lock, render_isolated_table
from router.gateway_namespace import create_worker, verify_worker_identity, worker_authority, worker_command
from router.gateway_preflight import SYSCTL, TUN_DEVICE, host_preflight
from router.security_dns import SING_BOX_VERSION, build_secure_dns_config, endpoint_ip, valid_port, validate_resolver


TUN = 'hwr-tun0'
TUN_ADDRESSES = ('198.18.254.1/30', 'fd94:bc9e:26ea::1/126')
ROUTE_TABLE = '23941'
RULE_PRIORITY = '10141'
NEIGHBOR_DISCOVERY = 'ip6 hoplimit 255 icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert }'
BLOCKED = render_isolated_table(('counter drop',), ('counter drop',), ingress=('counter drop',))


@dataclass(frozen=True)
class Gateway:
    worker: str
    edge: str
    lan: str
    worker_transit: str
    edge_transit: str
    uplink: str
    lan_networks: tuple
    transit_ip: str
    worker_uid: int
    worker_gid: int
    upstream: dict
    resolver: dict

    @classmethod
    def from_json(cls, request):
        if not isinstance(request, dict) or set(request) != set(cls.__dataclass_fields__):
            raise ValueError('网关配置字段无效')
        for key in ('worker', 'edge'):
            name = request[key]
            if not isinstance(name, str) or not re.fullmatch(r'hwr-sec-[a-z0-9][a-z0-9-]{0,23}', name):
                raise ValueError('网关需使用两个专用 hwr-sec-* 网络命名空间')
        if request['worker'] == request['edge']:
            raise ValueError('TUN 进程与外侧出口防火墙必须位于不同的网络命名空间')
        for key in ('lan', 'worker_transit', 'edge_transit', 'uplink'):
            if interface_name(request[key]) == TUN:
                raise ValueError('网关 TUN 名称由程序保留')
        if request['lan'] == request['worker_transit'] or request['edge_transit'] == request['uplink']:
            raise ValueError('同一命名空间内须分开使用 LAN、转接或外网接口')
        if any(type(request[key]) is not int or not 1 <= request[key] < 2**31 or request[key] == 65534
               for key in ('worker_uid', 'worker_gid')):
            raise ValueError('网关须使用专用的无特权 UID / GID')
        entries = request['lan_networks']
        if not isinstance(entries, list) or len(entries) != 2 or not all(isinstance(entry, str) for entry in entries):
            raise ValueError('需指定 LAN 的 IPv4 / IPv6 网段')
        networks = sorted(map(ipaddress.ip_network, entries), key=lambda address: address.version)
        for network, family in zip(networks, (4, 6)):
            if (network.version != family or network.prefixlen < (8 if family == 4 else 32)
                    or network.is_multicast or network.is_loopback or network.is_link_local
                    or any(network.overlaps(ipaddress.ip_interface(address).network) for address in TUN_ADDRESSES)):
                raise ValueError('LAN 网段必须明确区分双栈，且不可与保留 TUN 网段重叠')
        upstream = request['upstream']
        if not isinstance(upstream, dict) or not {'address', 'port'} <= set(upstream) <= {'address', 'port', 'username', 'password'}:
            raise ValueError('需指定固定地址 SOCKS5 上游')
        if ('username' in upstream) != ('password' in upstream) or any(
            not isinstance(upstream[k], str) or not 1 <= len(upstream[k].encode()) <= 255
            for k in ('username', 'password') if k in upstream
        ):
            raise ValueError('SOCKS5 用户名和密码必须成对提供，各 1–255 字节')
        upstream = {**upstream, 'address': endpoint_ip(upstream['address']), 'port': valid_port(upstream['port'])}
        if upstream['port'] in (53, 853):
            raise ValueError('SOCKS5 端口不能使用 53 或 853')
        transit = ipaddress.ip_address(endpoint_ip(request['transit_ip']))
        if (transit.version != ipaddress.ip_address(upstream['address']).version
                or transit == ipaddress.ip_address(upstream['address'])
                or any(transit in network or ipaddress.ip_address(upstream['address']) in network for network in networks)):
            raise ValueError('转接源 IP 与 SOCKS5 上游须采用同一地址族，并与 LAN 网段分开')
        return cls(**{**request, 'lan_networks': tuple(networks), 'transit_ip': str(transit), 'upstream': upstream,
                      'resolver': validate_resolver(request['resolver'])})

    def config(self):
        proxy = {
            'type': 'socks', 'tag': 'egress', 'version': '5', 'server': self.upstream['address'],
            'server_port': self.upstream['port'], 'bind_interface': self.worker_transit,
            ('inet6_bind_address' if ':' in self.transit_ip else 'inet4_bind_address'): self.transit_ip,
            **{key: self.upstream[key] for key in ('username', 'password') if key in self.upstream},
        }
        return build_secure_dns_config({
            'inbounds': [{'type': 'tun', 'tag': 'gateway', 'interface_name': TUN, 'address': list(TUN_ADDRESSES),
                          'stack': 'gvisor', 'dns_mode': 'disabled', 'auto_route': False, 'mtu': 1400}],
            'outbounds': [proxy],
        }, self.resolver, udp_over_tcp=True)

    def rules(self):
        family = 'ip6' if ':' in self.transit_ip else 'ip'
        address, port = self.upstream['address'], self.upstream['port']
        destination = f'{family} saddr {self.transit_ip} {family} daddr {address} tcp dport {port}'
        response = f'{family} saddr {address} {family} daddr {self.transit_ip} tcp sport {port}'
        worker_output = [f'oifname "{self.worker_transit}" {destination} counter accept',
                         f'oifname {{ "{self.worker_transit}", "{self.lan}" }} {NEIGHBOR_DISCOVERY} counter accept',
                         'counter drop']
        worker_forward = []
        for network, protocol in zip(self.lan_networks, ('ip', 'ip6')):
            worker_forward.append(f'iifname "{self.lan}" oifname "{TUN}" {protocol} saddr {network} counter accept')
            worker_forward.append(f'iifname "{TUN}" oifname "{self.lan}" {protocol} daddr {network} counter accept')
        edge_forward = [
            f'iifname "{self.edge_transit}" oifname "{self.uplink}" {destination} ct direction original counter accept',
            f'iifname "{self.uplink}" oifname "{self.edge_transit}" {response} ct direction reply ct state established counter accept',
            'counter drop',
        ]
        edge_output = [f'oifname {{ "{self.edge_transit}", "{self.uplink}" }} {NEIGHBOR_DISCOVERY} counter accept', 'counter drop']
        edge_input = [f'iifname {{ "{self.edge_transit}", "{self.uplink}" }} {NEIGHBOR_DISCOVERY} counter accept', 'counter drop']
        return {
            self.worker: render_isolated_table(worker_output, [*worker_forward, 'counter drop']),
            self.edge: render_isolated_table(edge_output, edge_forward,
                                             nat=f'oifname "{self.uplink}" {destination} masquerade', ingress=edge_input),
        }


def _preflight(gateway, authority, binary, config_path):
    worker, edge = gateway.worker, gateway.edge
    if not TUN_DEVICE.is_char_device():
        raise ValueError('缺少 Linux TUN 设备')
    verify_worker_identity(gateway, authority)
    command = worker_command(gateway, authority, ['/usr/bin/true'], net_admin=True)
    if subprocess.run(command, pass_fds=(authority,), capture_output=True, timeout=5).returncode:
        raise ValueError('网关身份无法配置所需能力或退出信号，请运行部署预检')
    transits = []
    for namespace, interfaces in ((worker, (gateway.lan, gateway.worker_transit)),
                                  (edge, (gateway.edge_transit, gateway.uplink))):
        links = {link['ifname']: link for link in json.loads(_exec(namespace, [IP, '-j', '-d', 'link', 'show']))}
        if set(links) - {'lo'} != set(interfaces) or any('UP' not in links[name]['flags'] for name in interfaces):
            raise ValueError('两个专用命名空间必须只包含声明中的网卡，且网卡须已启用')
        transit = gateway.worker_transit if namespace == worker else gateway.edge_transit
        transits.append(links[transit])
        if _exec(namespace, [SYSCTL, '-n', 'net.ipv4.ip_forward', 'net.ipv6.conf.all.forwarding']).split() != ['1', '1']:
            raise ValueError('请在两个专用命名空间内启用 IPv4 / IPv6 转发')
    worker_link, edge_link = transits
    for link, peer in ((worker_link, edge_link), (edge_link, worker_link)):
        if link.get('linkinfo', {}).get('info_kind') != 'veth' or link.get('link_index') != peer['ifindex']:
            raise ValueError('工作和外侧防火墙之间须使用一对专用 veth 网卡')
    for namespace, interface in ((worker, gateway.worker_transit), (edge, gateway.uplink)):
        _check_route(namespace, interface, gateway.upstream['address'])
    peer_ip = ipaddress.ip_address(gateway.transit_ip)
    family = f'-{peer_ip.version}'
    route = json.loads(_exec(edge, [IP, family, '-j', 'route', 'get', str(peer_ip)]))[0]
    if route.get('dev') != gateway.edge_transit or route.get('gateway'):
        raise ValueError('外侧防火墙需通过专用直连转接接口访问 TUN 进程')
    assigned = json.loads(_exec(worker, [IP, family, '-j', 'address', 'show', 'dev', gateway.worker_transit]))[0]
    if not any(record['local'] == str(peer_ip) for record in assigned.get('addr_info', ())):
        raise ValueError('网关转接源 IP 须绑定到指定转接接口')
    setup_lan = json.loads(_exec(worker, [IP, '-j', 'address', 'show', 'dev', gateway.lan]))[0]
    assigned_networks = {ipaddress.ip_interface(f"{record['local']}/{record['prefixlen']}").network
                         for record in setup_lan.get('addr_info', ())}
    if not set(gateway.lan_networks) <= assigned_networks:
        raise ValueError('LAN 接口须已配置声明中的 IPv4 和 IPv6 网段')
    for family in ('-4', '-6'):
        rules = json.loads(_exec(worker, [IP, family, '-j', 'rule', 'show']))
        if any(rule.get('priority') == int(RULE_PRIORITY) for rule in rules):
            raise ValueError('专用网关策略路由优先级已被占用')
    for namespace, ruleset in gateway.rules().items():
        _exec(namespace, [NFT, '--check', '-f', '-'], rules=ruleset)
    for arguments in (('version',), ('check', '-c', config_path)):
        command = worker_command(gateway, authority, [binary, *arguments])
        result = subprocess.run(command, pass_fds=(authority,), capture_output=True, text=True, timeout=5)
        if result.returncode:
            raise ValueError('专用网关进程或 sing-box 配置未通过预检')
        if arguments[0] == 'version' and result.stdout.splitlines()[:1] != [f'sing-box version {SING_BOX_VERSION}']:
            raise ValueError(f'Linux 网关需要 sing-box {SING_BOX_VERSION}')


def _remove_route(namespace, command):
    try:
        _exec(namespace, command)
    except ValueError:
        if TUN not in command:
            raise
        links = json.loads(_exec(namespace, [IP, '-j', 'link', 'show']))
        if any(link.get('ifname') == TUN for link in links):
            raise


def _route_lan(gateway, stack):
    for family in ('-4', '-6'):
        commands = [
            [IP, family, 'route', 'add', 'unreachable', 'default', 'metric', '32760', 'table', ROUTE_TABLE],
            [IP, family, 'route', 'add', 'default', 'dev', TUN, 'table', ROUTE_TABLE],
            [IP, family, 'rule', 'add', 'priority', RULE_PRIORITY, 'iif', gateway.lan, 'lookup', ROUTE_TABLE],
        ]
        for command in commands:
            _exec(gateway.worker, command)
            cleanup = [*command[:3], 'del', *command[4:]]
            stack.callback(_remove_route, gateway.worker, cleanup)


def _stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def launch(gateway, binary, *, check_only=False):
    # The namespace paths and privileged binaries are supplied by the local Linux operator.
    binary = str(Path(binary).resolve(strict=True))
    with ExitStack() as stack, tempfile.TemporaryDirectory(prefix='hwr-gateway-') as directory:
        for namespace in sorted((gateway.edge, gateway.worker)):
            stack.enter_context(namespace_lock(namespace))
        authority = stack.enter_context(worker_authority(gateway))
        path = Path(directory) / 'gateway.json'
        path.write_text(json.dumps(gateway.config()))
        path.chmod(0o600)
        os.chown(directory, gateway.worker_uid, gateway.worker_gid)
        os.chown(path, gateway.worker_uid, gateway.worker_gid)
        _preflight(gateway, authority, binary, str(path))
        if check_only:
            return
        # Keep the outer guard active independently of the TUN process or the worker-side nftables.
        for namespace in (gateway.edge, gateway.worker):
            _exec(namespace, [NFT, '-f', '-'], rules=BLOCKED)
        rules = gateway.rules()
        try:
            _exec(gateway.worker, [NFT, '-f', '-'], rules=rules[gateway.worker])
            with ExitStack() as running:
                command = worker_command(gateway, authority, [binary, 'run', '-c', str(path)], net_admin=True)
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, pass_fds=(authority,))
                running.callback(_stop, process)
                deadline = time.monotonic() + 8
                while process.poll() is None and time.monotonic() < deadline:
                    try:
                        links = json.loads(_exec(gateway.worker, [IP, '-j', 'link', 'show', 'dev', TUN]))
                        if links and 'UP' in links[0]['flags']:
                            break
                    except ValueError:
                        pass
                    time.sleep(.1)
                else:
                    raise ValueError('TUN 未在时限内启动，网关保持阻断')
                _route_lan(gateway, running)
                running.callback(_exec, gateway.edge, [NFT, '-f', '-'], rules=BLOCKED)
                _exec(gateway.edge, [NFT, '-f', '-'], rules=rules[gateway.edge])
                print('ACTIVE_UNVERIFIED', flush=True)
                if process.wait() != 0:
                    raise ValueError('TUN 进程已退出，网关已阻断')
        finally:
            _exec(gateway.worker, [NFT, '-f', '-'], rules=BLOCKED)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('preflight', 'plan', 'create-worker', 'check', 'run'))
    parser.add_argument('--config', help='网关拓扑 JSON，preflight 无需配置文件', type=Path)
    parser.add_argument('--sing-box', type=Path)
    arguments = parser.parse_args()

    def shutdown(signum, _frame):
        signal.signal(signum, signal.SIG_IGN)
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, shutdown)
    try:
        if arguments.action == 'preflight':
            report = host_preflight()
            print(json.dumps(report, ensure_ascii=False, indent=2))
            raise SystemExit({'READY_FOR_TOPOLOGY': 0, 'BLOCKED': 1, 'NEEDS_ROOT': 2}[report['status']])
        if arguments.config is None:
            parser.error(f'{arguments.action} 需要 --config')
        gateway = Gateway.from_json(json.loads(arguments.config.read_text()))
        if arguments.action == 'plan':
            for namespace, rules in gateway.rules().items():
                print(f'# {namespace}\n{rules}')
            return
        if arguments.action == 'create-worker':
            create_worker(gateway)
            return
        if arguments.sing_box is None:
            raise ValueError('预检和运行需要提供 sing-box 程序路径')
        launch(gateway, arguments.sing_box, check_only=arguments.action == 'check')
        if arguments.action == 'check':
            print('CHECKED_CONFIG')
    except KeyboardInterrupt:
        pass
    except (KeyError, OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
