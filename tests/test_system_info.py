import base64
import json
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from router.server import Store, make_handler
from router.system_info import _etime_seconds, _memory, _process_usage, core_inventory
from router.version import VERSION


class SystemInfoTests(unittest.TestCase):
    def test_macos_memory_uses_reported_page_size_and_physical_compressor_pages(self):
        stats = (
            'Mach Virtual Memory Statistics: (page size of 16384 bytes)\n'
            'Anonymous pages: 400.\n'
            'Pages purgeable: 100.\n'
            'Pages wired down: 250.\n'
            'Pages stored in compressor: 700.\n'
            'Pages occupied by compressor: 120.\n'
            'File-backed pages: 350.\n'
        )
        with patch('router.system_info.platform.system', return_value='Darwin'), \
             patch('router.system_info.subprocess.check_output', side_effect=['16777216\n', stats]) as run:
            self.assertEqual(_memory(), {'total_bytes': 16777216, 'used_bytes': 670 * 16384})
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [['/usr/sbin/sysctl', '-n', 'hw.memsize'], ['/usr/bin/vm_stat']],
        )

    def test_macos_memory_unavailable_if_sampling_times_out(self):
        with patch('router.system_info.platform.system', return_value='Darwin'), \
             patch('router.system_info.subprocess.check_output', side_effect=subprocess.TimeoutExpired('vm_stat', 1)):
            self.assertIsNone(_memory())

    def test_linux_memory_counts_available_pages_as_reclaimable(self):
        with patch('router.system_info.platform.system', return_value='Linux'), \
             patch.object(Path, 'read_text', return_value='MemTotal: 4096 kB\nMemFree: 100 kB\nMemAvailable: 1024 kB\n'):
            self.assertEqual(_memory(), {'total_bytes': 4096 * 1024, 'used_bytes': 3072 * 1024})

    def test_inventory_separates_managed_processes_and_standalone_binaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            binary = directory / 'mihomo'
            binary.write_text('#!/bin/sh\nprintf "mihomo v1.2.3 linux amd64\\n"\n')
            binary.chmod(0o700)
            config = directory / 'config.json'
            config.write_text(json.dumps({'inbounds': [
                {'protocol': 'vless', 'port': 8443},
                {'protocol': 'socks', 'port': 1080},
            ]}))
            store = SimpleNamespace(
                xray=SimpleNamespace(binary='', config_path=config, running=lambda: True, _read_pid=lambda: 4321),
                singbox=SimpleNamespace(binary='', running=lambda: False),
            )
            with patch('router.system_info.shutil.which', side_effect=lambda name: str(binary) if name == 'mihomo' else None):
                rows = {row['id']: row for row in core_inventory(store)}
            self.assertEqual(len(rows), 6)
            self.assertEqual(rows['xray-core']['status'], 'running')
            self.assertEqual(rows['xray-core']['tcp_ports'], [8443])
            self.assertEqual(rows['xray-core']['pid'], 4321)
            self.assertEqual(rows['xray-core']['config_path'], str(config))
            self.assertTrue(rows['xray-core']['config_ready'])
            self.assertEqual(rows['mihomo']['status'], 'unmanaged')
            self.assertEqual(rows['mihomo']['version'], 'v1.2.3')
            self.assertEqual(rows['mihomo']['binary'], 'mihomo')
            self.assertEqual(rows['mihomo']['path'], str(binary))
            self.assertIsNone(rows['mihomo']['pid'])
            self.assertEqual(rows['tuic-server']['status'], 'not_installed')
            self.assertIsNone(rows['tuic-server']['version'])
            self.assertEqual(rows['xray-core']['protocols'], ['VLESS Reality'])
            self.assertEqual(rows['sing-box']['protocols'], ['Hysteria 2', 'TUIC v5'])
            self.assertEqual(rows['tuic-server']['protocols'], ['TUIC v5'])

    def test_process_etime_includes_days(self):
        self.assertEqual(_etime_seconds('28:05'), 28 * 60 + 5)
        self.assertEqual(_etime_seconds('3:12:28'), 3 * 3600 + 12 * 60 + 28)
        self.assertEqual(_etime_seconds('3-12:28:05'), 3 * 86400 + 12 * 3600 + 28 * 60 + 5)

    def test_process_usage_requests_each_ps_column_separately(self):
        result = subprocess.CompletedProcess([], 0, '35:13  0.5  10936\n')
        with patch('router.system_info.subprocess.run', return_value=result) as run:
            self.assertEqual(_process_usage(8877), {
                'uptime_seconds': 2113, 'cpu_percent': 0.5, 'memory_bytes': 10936 * 1024,
            })
        self.assertEqual(run.call_args.args[0], ['ps', '-p', '8877', '-o', 'etime=', '-o', 'pcpu=', '-o', 'rss='])
        self.assertEqual(run.call_args.kwargs['env']['LC_ALL'], 'C')

    def test_health_is_minimal_and_inventory_requires_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'router.db')
            with ThreadingHTTPServer(('127.0.0.1', 0), make_handler(store, 'a-long-safe-password')) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base = f'http://127.0.0.1:{server.server_port}'
                try:
                    for path in ('/health/live', '/health/ready'):
                        with urlopen(base + path, timeout=3) as response:
                            self.assertEqual(json.load(response), {'status': 'ok'})
                    with self.assertRaises(HTTPError) as unauthorized:
                        urlopen(base + '/api/v1/system', timeout=3)
                    self.assertEqual(unauthorized.exception.code, 401)
                    headers = {'Authorization': 'Basic ' + base64.b64encode(b'admin:a-long-safe-password').decode()}
                    with patch('router.system_info.shutil.which', return_value=None):
                        with urlopen(Request(base + '/api/v1/system', headers=headers), timeout=3) as response:
                            report = json.load(response)
                    self.assertEqual(report['version'], VERSION)
                    self.assertEqual(len(report['cores']), 6)
                    self.assertGreater(report['disk']['total_bytes'], 0)
                    store.path = str(Path(tmp) / 'missing.db')
                    with self.assertRaises(HTTPError) as unavailable:
                        urlopen(base + '/health/ready', timeout=3)
                    self.assertEqual(unavailable.exception.code, 503)
                    self.assertFalse(Path(store.path).exists())
                finally:
                    server.shutdown()
                    thread.join()
