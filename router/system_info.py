"""Read host information and discover locally available core binaries."""

import json
import os
import platform
import re
import shutil
import subprocess
import time
import threading
from functools import lru_cache
from pathlib import Path

from router.independent_core import IndependentCore, PUBLISHERS, publisher_core
from router.version import VERSION


CORES = (
    ('xray-core', 'Xray-core', 'xray', 'version', ('VLESS Reality',)),
    ('sing-box', 'sing-box', 'sing-box', 'version', ('Hysteria 2', 'TUIC v5')),
    ('mihomo', 'mihomo', 'mihomo', '-v', ('Shadowsocks',)),
    ('hysteria2', 'Hysteria 2', 'hysteria', 'version', ('Hysteria 2',)),
    ('shadowsocks-rust', 'Shadowsocks-rust', 'ssserver', '--version', ('Shadowsocks',)),
    ('tuic-server', 'TUIC Server', 'tuic-server', '--version', ('TUIC v5',)),
)
STARTED = time.monotonic()


@lru_cache(maxsize=16)
def _version(path, argument, modified_ns, size):
    try:
        result = subprocess.run(
            [path, argument], stdin=subprocess.DEVNULL, capture_output=True,
            text=True, errors='replace', timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode:
        return None
    match = re.search(r'\bv?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.\-]+)?\b', result.stdout + '\n' + result.stderr)
    return match.group() if match else None


def _public_ports(service, core_id):
    try:
        config = json.loads(service.config_path.read_text(encoding='utf-8'))
        if core_id == 'hysteria2':
            port = int(config['listen'].rsplit(':', 1)[-1])
            return [port] if 1 <= port <= 65535 else []
        inbounds = config['inbounds']
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []
    protocol_field = 'protocol' if core_id == 'xray-core' else 'type'
    port_field = 'port' if core_id == 'xray-core' else 'listen_port'
    protocols = ('vless',) if core_id == 'xray-core' else ('hysteria2', 'tuic')
    return sorted({
        item[port_field] for item in inbounds
        if isinstance(item, dict) and item.get(protocol_field) in protocols
        and type(item.get(port_field)) is int and 1 <= item[port_field] <= 65535
    }) if isinstance(inbounds, list) else []


_ENV_BINARIES = {core_id: spec[1] for core_id, spec in PUBLISHERS.items()}


def _bundled_binary(store, binary):
    data_dir = getattr(store, 'data_dir', None)
    if not data_dir:
        return None
    path = Path(data_dir) / 'bin' / binary
    return str(path) if os.access(path, os.X_OK) else None


def _core_path(store, core_id, binary, configured):
    override = os.environ.get(_ENV_BINARIES[core_id]) if core_id in _ENV_BINARIES else None
    if override and os.access(override, os.X_OK):
        return override
    path = _bundled_binary(store, binary)
    if path:
        return path
    path = shutil.which(configured)
    if not path and configured == binary:
        path = shutil.which('/usr/local/bin/' + binary) or shutil.which('/usr/bin/' + binary)
    return path


def core_inventory(store):
    services = {'xray-core': store.xray, 'sing-box': store.singbox, 'hysteria2': getattr(store, 'hysteria', None)}
    profiles = store.independent_profiles() if hasattr(store, 'independent_profiles') else {}
    independent = getattr(store, 'independent', {})
    rows = []
    for core_id, name, binary, argument, protocols in CORES:
        service = store.core_runtime(core_id, profiles) if independent else services.get(core_id)
        configured = (service.binary if service else None) or binary
        path = _core_path(store, core_id, binary, configured)
        version = None
        if path:
            try:
                stat = os.stat(path)
                version = _version(path, argument, stat.st_mtime_ns, stat.st_size)
            except OSError:
                path = None
        running = bool(service and service.running())
        pid = _service_pid(service, running)
        usage = _process_usage(pid) if pid else None
        status = 'running' if running else 'stopped' if path and service else 'unmanaged' if path else 'not_installed'
        standalone = isinstance(service, IndependentCore)
        if standalone:
            tcp, udp = service.ports() if running else ([], [])
        else:
            ports = _public_ports(service, core_id) if running else []
            tcp, udp = (ports, []) if core_id == 'xray-core' else ([], ports)
        instances = []
        for key, profile in profiles.items():
            if key == core_id or publisher_core(key) != core_id:
                continue
            runtime = store.publisher_runtime(key)
            active = bool(profile.get('enabled') and runtime.running())
            instances.append({'id': key, 'port': profile['port'], 'running': active})
            if active and runtime is not service:
                instance_tcp, instance_udp = runtime.ports()
                tcp = sorted(set(tcp) | set(instance_tcp))
                udp = sorted(set(udp) | set(instance_udp))
        config = getattr(service, 'config_path', None) if service else None
        rows.append({
            'id': core_id, 'name': name, 'binary': binary, 'path': path,
            'installed': path is not None,
            'version': version, 'status': status, 'managed': service is not None,
            'pid': pid,
            'uptime_seconds': usage['uptime_seconds'] if usage else None,
            'cpu_percent': usage['cpu_percent'] if usage else None,
            'memory_bytes': usage['memory_bytes'] if usage else None,
            'config_path': str(config) if config else None,
            'config_ready': bool(config and Path(config).is_file()),
            'tcp_ports': tcp, 'udp_ports': udp,
            'independent': standalone,
            'autostart': any(publisher_core(key) == core_id and profile.get('enabled')
                             for key, profile in profiles.items()) if standalone else None,
            'error': service._error if standalone else '',
            'protocols': list(protocols),
            'instances': instances,
        })
    return rows


def _etime_seconds(token):
    days = 0
    if '-' in token:
        day, token = token.split('-', 1)
        days = int(day)
    parts = [int(part) for part in token.split(':')]
    if len(parts) == 2:
        hours, minutes, seconds = 0, *parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        return None
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def _process_usage(pid):
    try:
        result = subprocess.run(
            ['ps', '-p', str(pid), '-o', 'etime=', '-o', 'pcpu=', '-o', 'rss='],
            env={**os.environ, 'LC_ALL': 'C'},
            capture_output=True, text=True, timeout=1, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    parts = result.stdout.split()
    if result.returncode or len(parts) < 3:
        return None
    try:
        uptime = _etime_seconds(parts[0])
        cpu = float(parts[1])
        memory = int(float(parts[2])) * 1024
    except ValueError:
        return None
    if uptime is None or cpu < 0 or memory < 0:
        return None
    return {'uptime_seconds': uptime, 'cpu_percent': cpu, 'memory_bytes': memory}


def _service_pid(service, running):
    if not running or service is None:
        return None
    reader = getattr(service, '_read_pid', None)
    if not callable(reader):
        return None
    try:
        pid = reader()
    except (OSError, ValueError, TypeError):
        return None
    return pid if type(pid) is int and pid > 0 else None


def _memory():
    if platform.system() == 'Darwin':
        return _darwin_memory()
    # Linux reports reclaimable cache through MemAvailable; free pages alone understate usable memory.
    try:
        counters = {}
        for line in Path('/proc/meminfo').read_text().splitlines():
            key, _, value = line.partition(':')
            if key in ('MemTotal', 'MemAvailable'):
                counters[key] = int(value.split()[0]) * 1024
        if 0 <= counters['MemAvailable'] <= counters['MemTotal']:
            return {'total_bytes': counters['MemTotal'], 'used_bytes': counters['MemTotal'] - counters['MemAvailable']}
    except (OSError, KeyError, ValueError, IndexError):
        pass
    return None


def _darwin_memory():
    env = {**os.environ, 'LC_ALL': 'C'}
    try:
        total = int(subprocess.check_output(
            ['/usr/sbin/sysctl', '-n', 'hw.memsize'], env=env, text=True, timeout=1,
        ).strip())
        stats = subprocess.check_output(['/usr/bin/vm_stat'], env=env, text=True, timeout=1)
        match = re.search(r'\(page size of (\d+) bytes\)', stats)
        if not match:
            return None
        page_size = int(match.group(1))
        pages = {key: int(value) for key, value in re.findall(r'^([^:\n]+):\s+(\d+)\.?$', stats, re.MULTILINE)}
        # Count resident app, wired and compressed memory, excluding reclaimable pages.
        app = max(0, pages['Anonymous pages'] - pages['Pages purgeable'])
        used = (app + pages['Pages wired down'] + pages['Pages occupied by compressor']) * page_size
        if total > 0 and page_size > 0 and used <= total:
            return {'total_bytes': total, 'used_bytes': used}
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        pass
    return None


_cpu_lock = threading.Lock()
_cpu_previous = None


def _cpu_usage():
    """Compare Linux host CPU counters between requests, excluding guest time."""
    global _cpu_previous
    with _cpu_lock:
        try:
            fields = Path('/proc/stat').read_text().splitlines()[0].split()
            if fields[0] != 'cpu' or len(fields) < 9:
                return None
            values = [int(value) for value in fields[1:9]]
            current = (sum(values), values[3] + values[4])
        except (OSError, IndexError, ValueError):
            return None
        previous, _cpu_previous = _cpu_previous, current
        if previous is None:
            return None
        total = current[0] - previous[0]
        idle = current[1] - previous[1]
        return round(100 * (total - idle) / total, 1) if total > 0 and 0 <= idle <= total else None


def system_info(store):
    try:
        usage = shutil.disk_usage(store.data_dir)
        disk = {'total_bytes': usage.total, 'used_bytes': usage.used}
    except OSError:
        disk = None
    try:
        load_average = os.getloadavg()[0]
    except (AttributeError, OSError):
        load_average = None
    return {
        'version': os.environ.get('HOPWORLD_PACKAGE_VERSION') or VERSION,
        'platform': platform.system(), 'architecture': platform.machine(),
        'cpu_count': os.cpu_count(), 'cpu_percent': _cpu_usage(), 'load_average': load_average,
        'memory': _memory(), 'disk': disk, 'uptime_seconds': int(time.monotonic() - STARTED),
        'time': int(time.time()), 'cores': core_inventory(store),
    }
