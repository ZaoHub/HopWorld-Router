"""Manage strongSwan IKEv2 + PSK for iOS/Android system VPN."""

import os
import plistlib
import re
import secrets
import shutil
import string
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

CERT_BASENAME = 'hopworld-server'

_RUNTIME_CACHE_TTL = 1.0

from router.local_node import validate_public_host

DEFAULT_REMOTE_ID = 'hopworld.ikev2'
DEFAULT_POOL = '10.66.0.0/24'
# 只分 IPv4。带回 netmask、DHCP 或 IPv6 时，iPhone 会丢掉整包确认并立刻重发。
DEFAULT_POOL_LEASES = '10.66.0.2-10.66.0.254'
APPLY_HELPER = os.environ.get('HOPWORLD_IKEV2_APPLY', '/usr/local/sbin/hopworld-ikev2-apply')
UPDOWN_SCRIPT = os.environ.get('HOPWORLD_IKEV2_UPDOWN', '/usr/local/sbin/hopworld-ikev2-updown')
PSK_LENGTH = 10
_PSK_ALPHABET = string.ascii_letters + string.digits


def generate_psk():
    """Short alphanumeric PSK for easy entry on iPhone settings."""
    return ''.join(secrets.choice(_PSK_ALPHABET) for _ in range(PSK_LENGTH))


def _cert_der(data_dir):
    cert_path = Path(data_dir) / 'certs' / 'server.crt'
    if not cert_path.is_file():
        return None
    proc = subprocess.run(
        ['openssl', 'x509', '-in', str(cert_path), '-outform', 'DER'],
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    return proc.stdout


def build_mobileconfig(settings, data_dir=None):
    """Apple configuration profile for IKEv2/PSK (蜂窝手填易错，描述文件更稳)."""
    if not settings.get('enabled'):
        raise ValueError('IKEv2 未启用')
    psk = settings.get('psk') or ''
    if not psk:
        raise ValueError('缺少共享密钥')
    host = validate_public_host(settings['public_host'])
    remote_id = validate_remote_id(settings.get('remote_id') or host)
    vpn_uuid = str(uuid.uuid4()).upper()
    root_uuid = str(uuid.uuid4()).upper()
    ikev2 = {
        'RemoteAddress': host,
        'RemoteIdentifier': remote_id,
        'LocalIdentifier': '',
        'AuthenticationMethod': 'SharedSecret',
        'SharedSecret': psk.encode('utf-8'),
        'ChildSecurityAssociationParameters': {
            'EncryptionAlgorithm': 'AlgorithmAES-256',
            'IntegrityAlgorithm': 'SHA2-256',
        },
        'IKESecurityAssociationParameters': {
            'EncryptionAlgorithm': 'AlgorithmAES-256',
            'IntegrityAlgorithm': 'SHA2-256',
            'DiffieHellmanGroup': 19,
        },
        'DeadPeerDetectionRate': 'Medium',
        'DisableMOBIKE': True,
        'EnablePFS': False,
        'NATKeepAliveInterval': 10,
    }
    payloads = []
    vpn_payload = {
        'PayloadType': 'com.apple.vpn.managed',
        'PayloadVersion': 1,
        'PayloadIdentifier': 'com.hopworld.vpn.ikev2',
        'PayloadUUID': vpn_uuid,
        'PayloadDisplayName': 'HopWorld VPN',
        'UserDefinedName': 'HopWorld',
        'VPNType': 'IKEv2',
        'IKEv2': ikev2,
        'IPv4': {'OverridePrimary': True},
        'OnDemandEnabled': 0,
    }
    payloads.append(vpn_payload)
    profile = {
        'PayloadType': 'Configuration',
        'PayloadVersion': 1,
        'PayloadIdentifier': 'com.hopworld.profile',
        'PayloadUUID': root_uuid,
        'PayloadDisplayName': 'HopWorld IKEv2',
        'PayloadDescription': 'HopWorld Router IKEv2（蜂窝）',
        'PayloadOrganization': 'HopWorld',
        'PayloadContent': payloads,
    }
    return plistlib.dumps(profile, fmt=plistlib.FMT_XML)


def validate_psk(value):
    if not isinstance(value, str):
        raise ValueError('共享密钥无效')
    value = value.strip()
    if not re.fullmatch(r'[A-Za-z0-9]{8,32}', value):
        raise ValueError('共享密钥须为 8–32 位字母或数字')
    return value


_CONN_LINE = re.compile(
    r'hopworld-ikev2\[\d+\]:\s+ESTABLISHED\s+(?P<uptime>.+?),\s+'
    r'(?P<server>[^\s]+)\.\.\.(?P<remote>.+)$',
)
_REMOTE = re.compile(
    r'^(?P<public_ip>\d+\.\d+\.\d+\.\d+)(?:\[(?P<remote_id>[^\]]+)\])?$',
)
_VIP_LINE = re.compile(r'===\s+(?P<vip>10\.66\.\d{1,3}\.\d{1,3})/\d+')
_SA_UP = re.compile(r'Security Associations\s+\(\s*(?P<n>\d+)\s+up', re.I)


def _parse_kv_lines(text, key_filter=None):
    rows = {}
    for raw in text.splitlines():
        line = raw.strip()
        if key_filter and not line.startswith(key_filter):
            continue
        parts = {}
        for token in line.split():
            if '=' not in token:
                continue
            k, v = token.split('=', 1)
            parts[k] = v
        if key_filter == 'vip=' and 'vip' in parts:
            rows[parts['vip']] = parts
        elif key_filter == 'peer=' and 'peer' in parts:
            rows[parts['peer']] = parts
    return rows


def parse_traffic_section(text):
    """Parse ---TRAFFIC--- vip= lines into {vip: {up_bytes, down_bytes}}."""
    traffic = {}
    for vip, parts in _parse_kv_lines(text, 'vip=').items():
        up = int(parts.get('up') or 0)
        down = int(parts.get('down') or 0)
        traffic[vip] = {'up_bytes': up, 'down_bytes': down, 'total_bytes': up + down}
    return traffic


def parse_xfrm_section(text):
    """Parse ---XFRM--- peer= lines (ESP byte counters by client public IP)."""
    peers = {}
    for peer, parts in _parse_kv_lines(text, 'peer=').items():
        entry = peers.setdefault(peer, {'xfrm_up_bytes': 0, 'xfrm_down_bytes': 0})
        if 'up' in parts:
            entry['xfrm_up_bytes'] = int(parts.get('up') or 0)
        if 'down' in parts:
            entry['xfrm_down_bytes'] = int(parts.get('down') or 0)
        entry['xfrm_total_bytes'] = entry['xfrm_up_bytes'] + entry['xfrm_down_bytes']
    return peers


_HS_INIT = re.compile(r'(\d+\.\d+\.\d+\.\d+) is initiating an IKE_SA')
_HS_EST = re.compile(r'established between \S+?\.\.\.(\d+\.\d+\.\d+\.\d+)')
_HS_FROM = re.compile(r'received packet: from (\d+\.\d+\.\d+\.\d+)\[')
_HS_RETRANS = re.compile(r'retransmit of request with ID 1')
_HS_TS = re.compile(
    r'^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)'
)


# iOS 重发间隔会到 8 秒；再安静超过这点就视为手机已取消
_HANDSHAKE_FRESH_SECS = 10


def _handshake_age(stamp, now):
    if stamp is None or now is None:
        return 0
    return max(0, int((now - stamp).total_seconds()))


def _handshake_ago(stamp, now):
    secs = _handshake_age(stamp, now)
    if secs < 5:
        return '刚刚'
    if secs < 60:
        return f'{secs}秒前'
    return f'{secs // 60}分钟前'


def _handshake_event(ip, phase, label, stamp, now):
    return {
        'public_ip': ip,
        'phase': phase,
        'label': label,
        'ago': _handshake_ago(stamp, now),
        'age_secs': _handshake_age(stamp, now),
    }


def _parse_log_time(line):
    match = _HS_TS.match(line.strip())
    if not match:
        return None
    raw = match.group(1).replace(' ', 'T')
    if raw.endswith('Z'):
        raw = raw[:-1] + '+00:00'
    elif len(raw) >= 5 and raw[-5] in '+-' and raw[-3] != ':':
        raw = raw[:-2] + ':' + raw[-2:]
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def parse_handshake_log(text, now=None):
    """Latest handshake phase per public IP from recent charon lines.

    UDP 500 IKE_SA_INIT is logged as "is initiating an IKE_SA".
    """
    if now is None:
        now = datetime.now(timezone.utc)
    events = {}
    last_from = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        stamp = _parse_log_time(line)
        arrived = _HS_FROM.search(line)
        if arrived:
            last_from = arrived.group(1)
        init = _HS_INIT.search(line)
        if init:
            ip = init.group(1)
            events[ip] = _handshake_event(ip, 'udp500', '正在连接 · 已收到 UDP 500', stamp, now)
            continue
        est = _HS_EST.search(line)
        if est:
            ip = est.group(1)
            current = events.get(ip)
            if not current or current.get('phase') != 'waiting':
                events[ip] = _handshake_event(ip, 'up', '服务器已建立', stamp, now)
            continue
        if _HS_RETRANS.search(line) and last_from:
            events[last_from] = _handshake_event(
                last_from, 'waiting', '正在连接 · 手机未确认', stamp, now,
            )
    return events


def _client_bytes(client):
    return int(client.get('total_bytes') or 0) + int(client.get('tunnel_bytes') or 0)


def apply_handshake(clients, events):
    """Show a row as soon as UDP 500 is seen. Drop it once the phone stops."""
    seen = set()
    kept = []
    for client in clients:
        ip = client.get('public_ip') or ''
        event = events.get(ip)
        if event:
            seen.add(ip)
        fresh = event is not None and int(event.get('age_secs') or 0) <= _HANDSHAKE_FRESH_SECS
        if event and event['phase'] in ('udp500', 'waiting') and fresh:
            client['handshake'] = event['phase']
            client['net_status'] = 'connecting'
            client['net_label'] = event['label']
            if event.get('ago'):
                client['uptime'] = event['ago']
            kept.append(client)
            continue
        # 手机已取消、也没有流量：服务器上残留的 SA 不再显示
        if _client_bytes(client) == 0 and not (event and event.get('phase') == 'up' and fresh):
            continue
        kept.append(client)
    for ip, event in events.items():
        if ip in seen or event['phase'] == 'up':
            continue
        if int(event.get('age_secs') or 0) > _HANDSHAKE_FRESH_SECS:
            continue
        kept.append({
            'public_ip': ip,
            'virtual_ip': '',
            'remote_id': '',
            'uptime': event.get('ago') or '刚刚',
            'up_bytes': 0,
            'down_bytes': 0,
            'total_bytes': 0,
            'handshake': event['phase'],
            'net_status': 'connecting',
            'net_label': event['label'],
        })
    clients[:] = kept
    return clients


def client_net_status(client):
    total = int(client.get('total_bytes') or 0)
    tunnel = int(client.get('tunnel_bytes') or 0)
    if total == 0 and tunnel == 0:
        return 'no_traffic', '已连接但无流量'
    if total == 0 and tunnel > 0:
        return 'low_traffic', '隧道有数据·转发未计数'
    if total < 8192:
        return 'low_traffic', '流量偏少'
    return 'ok', '有流量'


def build_connectivity_diagnosis(clients, health, running, enabled):
    if not enabled:
        return []
    items = []
    if not running:
        items.append({'level': 'error', 'text': 'strongSwan 未运行，手机无法建立隧道。'})
    elif not clients:
        items.append({'level': 'info', 'text': '暂无在线设备；请在手机打开 VPN 后刷新本页。'})
    if health.get('status_ok') is False:
        items.append({'level': 'warn', 'text': '无法读取隧道状态，请检查 hopworld-ikev2-apply 与 sudo 权限。'})
    if health.get('ip_forward') is False:
        items.append({'level': 'error', 'text': '服务器未开启 IP 转发，会导致 VPN 无法上网。'})
    if health.get('nat_masquerade') is False:
        items.append({'level': 'error', 'text': 'NAT 未就绪：在右侧 IKEv2 卡片点「启用」重新应用。'})
    for client in clients:
        code, _label = client_net_status(client)
        vip = client.get('virtual_ip') or client.get('public_ip') or '—'
        if client.get('handshake') == 'udp500':
            items.append({'level': 'info', 'text': f'{vip} 已打到 UDP 500，正在握手。'})
            continue
        if client.get('handshake') == 'waiting':
            who = client.get('public_ip') or vip
            items.insert(0, {
                'level': 'warn',
                'title': '系统 VPN 连不上',
                'text': (
                    f'{who} 已经连上服务器，密钥也通过了，但手机没有收下确认里的地址配置，'
                    '所以会停在「正在连接」。请先关掉手机上的 VPN，确认远程 ID 与本页一致，再打开。'
                ),
            })
            continue
        if code == 'no_traffic':
            items.append({
                'level': 'warn',
                'text': (
                    f'设备 {vip} 已连接但无上网流量：请确认 iPhone「用戶認證=无」、'
                    f'关闭「使用证书」、填写共享密钥；断开 VPN 后重连，并试 Safari 打开 http://1.1.1.1'
                ),
            })
        elif code == 'low_traffic':
            items.append({
                'level': 'info',
                'text': f'设备 {vip} 流量很少，若仍无法上网请换蜂窝网络测试或改用订阅代理节点。',
            })
    items.append({
        'level': 'info',
        'text': (
            '系统 VPN 全隧道走服务器公网出口上网；VLESS 订阅/本地网络节点需在代理客户端单独连接，'
            '不会自动并入 IKEv2。'
        ),
    })
    items.append({
        'level': 'warn',
        'text': (
            'iPhone 开着 IKEv2 全隧道时，用 Safari 访问本机公网 IP（8080/SSH）会先进隧道再回环，'
            '常表现为管理页与 40300 超时；请在手机上先关闭 VPN，或用电脑直连公网打开后台。'
        ),
    })
    return items


def merge_client_traffic(clients, traffic, xfrm):
    for client in clients:
        vip = client.get('virtual_ip') or ''
        pub = client.get('public_ip') or ''
        t = traffic.get(vip, {})
        up = int(t.get('up_bytes') or 0)
        down = int(t.get('down_bytes') or 0)
        tunnel_bytes = 0
        if pub and pub in xfrm:
            x = xfrm[pub]
            tunnel_bytes = int(x.get('xfrm_total_bytes') or 0)
            if up + down == 0:
                up = int(x.get('xfrm_up_bytes') or 0)
                down = int(x.get('xfrm_down_bytes') or 0)
        client['up_bytes'] = up
        client['down_bytes'] = down
        client['total_bytes'] = up + down
        client['tunnel_bytes'] = tunnel_bytes
    return clients


def parse_strongswan_status(text):
    """Parse `strongswan status` output for hopworld-ikev2 clients."""
    up = _SA_UP.search(text)
    if up and int(up.group('n')) == 0:
        return []
    clients = []
    pending = None
    for raw in text.splitlines():
        line = raw.strip()
        match = _CONN_LINE.search(line)
        if match:
            pending = {
                'uptime': match.group('uptime').strip(),
                'server': match.group('server').strip(),
                'public_ip': '',
                'remote_id': '',
            }
            remote = match.group('remote').strip()
            parts = _REMOTE.match(remote)
            if parts:
                pending['public_ip'] = parts.group('public_ip')
                pending['remote_id'] = parts.group('remote_id') or ''
            else:
                pending['public_ip'] = remote
            continue
        if pending:
            vip = _VIP_LINE.search(line)
            if vip:
                pending['virtual_ip'] = vip.group('vip')
                clients.append(pending)
                pending = None
    return clients


def parse_swanctl_leases(text):
    """Parse `swanctl --list-sas` for IKEv2 virtual IPs (strongSwan 5.7)."""
    vip_by_remote = {}
    current_remote = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('local') and 'hopworld' not in line.lower() and '@' in line:
            continue
        if line.endswith(':') and line[0].isalpha() and '#' in line:
            current_remote = None
            continue
        peer = re.search(
            r"remote\s+'([^']+)'\s+@\s+(\S+)\[(\d+)\]\s+\[([0-9a-fA-F.:]+)\]",
            line,
        )
        if peer:
            vip_by_remote[peer.group(1)] = peer.group(4)
            continue
        peer_no_vip = re.search(r"remote\s+'([^']+)'\s+@\s+(\S+)\[(\d+)\]", line)
        if peer_no_vip:
            current_remote = peer_no_vip.group(1)
            continue
        if current_remote and 'remote' in line and '[' in line:
            m = re.search(r'\[([0-9a-fA-F.:]+)\]\s*$', line)
            if m:
                vip_by_remote[current_remote] = m.group(1)
    return vip_by_remote


def parse_swanctl_clients(text):
    """Build monitor rows from `swanctl --list-sas` when stroke status has no VIP lines."""
    clients = []
    block = {}
    for raw in text.splitlines():
        line = raw.strip()
        est = re.search(r'hopworld-ikev2:\s+#\d+,\s+ESTABLISHED.*established\s+(.+?)\s+ago', line)
        if est:
            block = {'uptime': est.group(1).strip() + ' ago'}
            continue
        loc = re.search(r"local\s+'([^']+)'\s+@\s+(\S+)\[", line)
        if loc:
            block['server'] = f"{loc.group(2)}[{loc.group(1)}]"
            continue
        rem = re.search(
            r"remote\s+'([^']+)'\s+@\s+(\d+\.\d+\.\d+\.\d+)\[(\d+)\](?:\s+\[([^\]]+)\])?",
            line,
        )
        if rem:
            block['remote_id'] = rem.group(1)
            block['public_ip'] = rem.group(2)
            if rem.group(4):
                block['virtual_ip'] = rem.group(4)
            block.setdefault('virtual_ip', '')
            clients.append(block)
            block = {}
    return clients


def merge_client_virtual_ips(clients, leases):
    for client in clients:
        rid = client.get('remote_id') or ''
        pub = client.get('public_ip') or ''
        vip = leases.get(rid)
        if not vip and rid:
            for key, val in leases.items():
                if key in rid or rid in key:
                    vip = val
                    break
        if vip and (not client.get('virtual_ip') or client.get('virtual_ip') in ('', '0.0.0.0')):
            client['virtual_ip'] = vip
    return clients


def validate_remote_id(value):
    if not isinstance(value, str):
        raise ValueError('Remote ID 无效')
    value = value.strip()
    if not value or len(value) > 64 or '\x00' in value or ' ' in value:
        raise ValueError('Remote ID 无效')
    if not re.fullmatch(r'[A-Za-z0-9._@-]+', value):
        raise ValueError('Remote ID 只能包含字母、数字和 . _ @ -')
    return value


class Ikev2Service:
    def __init__(self, data_dir, apply_helper=None):
        self.data_dir = Path(data_dir) / 'ikev2'
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.conf_path = self.data_dir / 'ipsec.conf'
        self.secrets_path = self.data_dir / 'ipsec.secrets'
        self.apply_helper = apply_helper or APPLY_HELPER
        self._lock = threading.Lock()
        self._runtime_cache = None
        self._runtime_cache_at = 0.0
        self._runtime_lock = threading.Lock()
        self._last_nat_sync_at = 0.0
        self._nat_sync_lock = threading.Lock()

    def default_settings(self, public_host=''):
        host = ''
        if public_host:
            try:
                host = validate_public_host(public_host)
            except ValueError:
                host = ''
        return {
            'enabled': False,
            'public_host': host,
            'remote_id': DEFAULT_REMOTE_ID,
            'psk': '',
            'pool': DEFAULT_POOL,
        }

    def ensure_credentials(self, settings, rotate_psk=False, new_psk=None):
        updated = dict(settings)
        updated.setdefault('remote_id', DEFAULT_REMOTE_ID)
        updated.setdefault('pool', DEFAULT_POOL)
        if rotate_psk:
            updated['psk'] = generate_psk()
        elif new_psk is not None:
            updated['psk'] = validate_psk(new_psk)
        elif not updated.get('psk'):
            updated['psk'] = generate_psk()
        else:
            updated['psk'] = validate_psk(updated['psk'])
        try:
            updated['remote_id'] = validate_remote_id(updated.get('remote_id') or DEFAULT_REMOTE_ID)
        except ValueError:
            updated['remote_id'] = DEFAULT_REMOTE_ID
        return updated

    def public_view(self, settings, running=None):
        enabled = bool(settings.get('enabled'))
        running = self.running() if running is None else bool(running)
        clients, health = self.runtime_snapshot() if enabled else ([], {})
        for client in clients:
            code, label = client_net_status(client)
            client['net_status'] = code
            client['net_label'] = label
        apply_handshake(clients, health.get('handshakes') or {})
        diagnosis = build_connectivity_diagnosis(clients, health, running, enabled) if enabled else []
        return {
            'enabled': enabled,
            'public_host': settings.get('public_host') or '',
            'remote_id': settings.get('remote_id') or DEFAULT_REMOTE_ID,
            'psk': settings.get('psk') or '' if enabled else '',
            'pool': settings.get('pool') or DEFAULT_POOL,
            'running': running,
            'user_auth': 'None',
            'server': settings.get('public_host') or '',
            'clients': clients,
            'client_count': len(clients),
            'health': health,
            'diagnosis': diagnosis,
        }

    def runtime_snapshot(self):
        now = time.monotonic()
        with self._runtime_lock:
            if (
                self._runtime_cache is not None
                and now - self._runtime_cache_at < _RUNTIME_CACHE_TTL
            ):
                return self._runtime_cache
        snapshot = self._runtime_snapshot_uncached(allow_heal=True)
        with self._runtime_lock:
            self._runtime_cache = snapshot
            self._runtime_cache_at = time.monotonic()
        return snapshot

    def maybe_heal_vpn_nat(self, clients):
        """Re-apply per-client NAT/routes when tunnel is up but counters stay at zero."""
        if not clients:
            return False
        if not any(client_net_status(c)[0] == 'no_traffic' for c in clients):
            return False
        now = time.monotonic()
        with self._nat_sync_lock:
            if now - self._last_nat_sync_at < 60:
                return False
            self._last_nat_sync_at = now
        try:
            self._run_helper('sync-clients')
        except ValueError:
            return False
        with self._runtime_lock:
            self._runtime_cache = None
        return True

    def _runtime_snapshot_uncached(self, allow_heal=True):
        raw = self._query_helper_output()
        if raw is None:
            return [], {'status_ok': False}
        handshake_text = ''
        if '---HANDSHAKE---' in raw:
            raw, _, handshake_text = raw.rpartition('---HANDSHAKE---')
        health = {
            'status_ok': True,
            'ip_forward': False,
            'nat_masquerade': False,
            'handshakes': parse_handshake_log(handshake_text),
        }
        status_text = raw
        status_body = raw
        traffic_text = ''
        xfrm_text = ''
        if '---HOPWORLD---' in raw:
            meta, _, rest = raw.partition('---STATUS---')
            status_body = rest
            for line in meta.splitlines():
                line = line.strip()
                if line.startswith('forward='):
                    health['ip_forward'] = line.split('=', 1)[1].strip() == '1'
                elif line.startswith('nat='):
                    health['nat_masquerade'] = line.split('=', 1)[1].strip() == 'ok'
        lease_text = ''
        if '---LEASES---' in status_body:
            status_body, _, lease_chunk = status_body.partition('---LEASES---')
            if '---TRAFFIC---' in lease_chunk:
                lease_text, _, traffic_text = lease_chunk.partition('---TRAFFIC---')
            else:
                lease_text = lease_chunk
        elif '---TRAFFIC---' in status_body:
            status_body, _, traffic_text = status_body.partition('---TRAFFIC---')
        if '---XFRM---' in traffic_text:
            traffic_text, _, xfrm_text = traffic_text.partition('---XFRM---')
        elif '---XFRM---' in status_body:
            status_body, _, xfrm_text = status_body.partition('---XFRM---')
        clients = parse_strongswan_status(status_body)
        if lease_text.strip():
            if 'ESTABLISHED' not in lease_text:
                clients = []
            else:
                swanctl_rows = parse_swanctl_clients(lease_text)
                if swanctl_rows:
                    clients = swanctl_rows
                else:
                    merge_client_virtual_ips(clients, parse_swanctl_leases(lease_text))
        merge_client_traffic(
            clients,
            parse_traffic_section(traffic_text),
            parse_xfrm_section(xfrm_text),
        )
        if allow_heal and self.maybe_heal_vpn_nat(clients):
            return self._runtime_snapshot_uncached(allow_heal=False)
        return clients, health

    def _query_helper_output(self):
        try:
            completed = subprocess.run(
                ['sudo', '-n', self.apply_helper, 'status', str(self.data_dir)],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0 and not (completed.stdout or '').strip():
            return None
        return (completed.stdout or completed.stderr or '').strip()

    def running(self):
        for unit in ('strongswan', 'strongswan-starter', 'ipsec'):
            try:
                completed = subprocess.run(
                    ['systemctl', 'is-active', unit],
                    capture_output=True, text=True, timeout=5, check=False,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if completed.stdout.strip() == 'active':
                return True
        return False

    def apply(self, settings):
        with self._runtime_lock:
            self._runtime_cache = None
        with self._lock:
            if settings.get('enabled'):
                self._write_files(settings)
                self._run_helper('apply')
            else:
                self._write_disabled_marker()
                self._run_helper('disable')

    def _write_files(self, settings):
        public_host = validate_public_host(settings['public_host'])
        remote_id = validate_remote_id(settings['remote_id'])
        psk = validate_psk(settings.get('psk') or '')
        pool = settings.get('pool') or DEFAULT_POOL
        pool_addrs = DEFAULT_POOL_LEASES if pool.strip() == DEFAULT_POOL else pool
        local_id = remote_id.lstrip('@')
        conf = f'''# Managed by HopWorld Router — do not edit by hand
conn hopworld-bypass-host
    authby=never
    auto=route
    type=passthrough
    left={public_host}
    leftsubnet={public_host}/32
    rightsubnet={public_host}/32
'''
        swanctl = f'''# Managed by HopWorld Router — do not edit by hand
connections {{
    hopworld-ikev2 {{
        version = 2
        mobike = no
        send_certreq = no
        encap = yes
        local_addrs = {public_host}
        proposals = aes256-sha256-modp2048,aes256-sha256-ecp256
        fragmentation = yes
        local {{
            auth = psk
            id = {local_id}
        }}
        remote {{
            auth = psk
            id = %any
        }}
        children {{
            hopworld-net {{
                mode = tunnel
                local_ts = 0.0.0.0/0
                esp_proposals = aes256-sha256
                updown = {UPDOWN_SCRIPT}
                dpd_action = clear
            }}
        }}
        pools = hopworld-v4
    }}
}}
pools {{
    hopworld-v4 {{
        addrs = {pool_addrs}
        dns = 1.1.1.1
    }}
}}
secrets {{
    ike-hopworld {{
        id-local = {local_id}
        id-remote = %any
        secret = "{psk}"
    }}
    ike-any {{
        secret = "{psk}"
    }}
}}
'''
        secrets = f'''# Managed by HopWorld Router — do not edit by hand
%any %any : PSK "{psk}"
'''
        self._atomic_write(self.conf_path, conf, 0o644)
        self._atomic_write(self.secrets_path, secrets, 0o600)
        self._atomic_write(self.data_dir / 'swanctl.conf', swanctl, 0o644)
        meta = f'public_host={public_host}\npool={pool}\n'
        self._atomic_write(self.data_dir / 'meta.env', meta, 0o600)

    def _write_disabled_marker(self):
        self._atomic_write(self.conf_path, '# Managed by HopWorld Router — disabled\n', 0o644)
        self._atomic_write(self.secrets_path, '# Managed by HopWorld Router — disabled\n', 0o600)
        self._atomic_write(self.data_dir / 'swanctl.conf', '# Managed by HopWorld Router — disabled\n', 0o644)

    def _run_helper(self, action):
        helper = self.apply_helper
        if not helper or not Path(helper).exists():
            if shutil.which('ipsec') is None and shutil.which('strongswan') is None:
                raise ValueError('未安装 strongSwan，请先在服务器安装并配置 hopworld-ikev2-apply')
            raise ValueError(f'缺少应用脚本 {helper}')
        try:
            completed = subprocess.run(
                ['sudo', '-n', helper, action, str(self.data_dir)],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError as exc:
            raise ValueError('无法执行 sudo/应用脚本') from exc
        except subprocess.SubprocessError as exc:
            raise ValueError('应用 IKEv2 配置失败') from exc
        out = (completed.stdout or '').strip()
        err = (completed.stderr or '').strip()
        if completed.returncode != 0:
            detail = err or out
            if action == 'apply' and self._apply_output_ok(out, err):
                return out
            raise ValueError(self._friendly_apply_error(detail))
        return completed.stdout

    @staticmethod
    def _apply_output_ok(stdout, stderr):
        joined = f'{stdout}\n{stderr}'.strip().lower()
        if joined.endswith('ok') or '\nok' in joined or joined == 'ok':
            return True
        if 'loaded connection' in joined and 'hopworld-ikev2' in joined:
            return True
        return False

    @staticmethod
    def _friendly_apply_error(detail):
        detail = (detail or '').strip()
        if not detail:
            return '应用 IKEv2 配置失败'
        lower = detail.lower()
        if 'missing ikev2 config' in lower:
            return 'IKEv2 配置文件缺失'
        if 'strongswan not installed' in lower:
            return '未安装 strongSwan，请先在服务器安装 VPN 组件'
        if 'loaded pool' in lower or 'successfully loaded' in lower:
            return 'VPN 已在服务器加载；若侧栏显示已启用可忽略此提示'
        if len(detail) > 160:
            return '应用 VPN 配置未完成，请查看服务器日志或稍后重试'
        return detail

    @staticmethod
    def _atomic_write(path, content, mode):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_suffix(path.suffix + '.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        try:
            os.write(fd, content.encode('utf-8'))
        finally:
            os.close(fd)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
