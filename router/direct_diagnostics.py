"""Check one Router entrance from an independently routed client."""

import argparse
import errno
import ipaddress
import json
import socket
import ssl
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

from router.local_node import validate_public_host
from router.probe import HYSTERIA_SCHEMES, probe


def tcp_check(address, port, tls_name=None):
    try:
        with socket.create_connection((address, port), timeout=4) as connection:
            if tls_name:
                with ssl.create_default_context().wrap_socket(connection, server_hostname=tls_name):
                    return 'https_verified'
            return 'open'
    except ssl.SSLCertVerificationError:
        return 'tls_certificate_invalid'
    except ssl.SSLError:
        return 'tls_handshake_failed'
    except ConnectionRefusedError:
        return 'refused'
    except TimeoutError:
        return 'timeout'
    except OSError as exc:
        return 'no_route' if exc.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH) else 'connection_failed'


def check_hysteria(uri, host, address, binary):
    parts = urlsplit(uri)
    if parts.scheme not in HYSTERIA_SCHEMES or parts.hostname != host or not parts.port:
        raise ValueError('节点链接必须属于同一个检测入口，并包含 Hysteria 2 端口')
    ip = f'[{address}]' if ':' in address else address
    query = parse_qsl(parts.query, keep_blank_values=True)
    if not any(name == 'sni' and value for name, value in query):
        query = [(name, value) for name, value in query if name != 'sni'] + [('sni', host)]
    userinfo = parts.netloc.rpartition('@')[0]
    link = parts._replace(netloc=f'{userinfo}@{ip}:{parts.port}', query=urlencode(query)).geturl()
    result = probe(link, core='hysteria2', client_binary=binary, timeout=8)
    return {'udp_port': parts.port, 'status': result['status'], 'detail': result['detail']}


def main():
    parser = argparse.ArgumentParser(description='在独立的外网客户端检查一台 HopWorld Router 的公开入口')
    parser.add_argument('host', help='你管理的公网域名、IPv4 或 IPv6')
    parser.add_argument('--https-port', default=443, type=int)
    parser.add_argument('--hysteria-link-file', type=Path, help='仅本机可读的 Hysteria 2 节点链接文件')
    parser.add_argument('--hysteria-bin', default='hysteria')
    args = parser.parse_args()
    try:
        host = validate_public_host(args.host)
        if not 1 <= args.https_port <= 65535:
            raise ValueError('HTTPS 端口须为 1 至 65535')
        uri = args.hysteria_link_file.read_text().strip() if args.hysteria_link_file else ''
        targets = sorted({str(ipaddress.ip_address(row[4][0])) for row in socket.getaddrinfo(
            host, None, type=socket.SOCK_STREAM) if ipaddress.ip_address(row[4][0]).is_global
            and not ipaddress.ip_address(row[4][0]).is_multicast})
        if not targets:
            raise ValueError('该入口没有可检测的公网地址')
        report = []
        for target in targets:
            tcp = {port: tcp_check(target, port, host if port == args.https_port else None)
                   for port in sorted({80, 443, args.https_port})}
            row = {'family': f'IPv{ipaddress.ip_address(target).version}', 'address': target, 'tcp': tcp}
            if uri:
                row['hysteria2'] = check_hysteria(uri, host, target, args.hysteria_bin)
            report.append(row)
    except (ValueError, OSError) as exc:
        parser.exit(2, str(exc) + '\n')
    print(json.dumps({'target': host, 'results': report}, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
