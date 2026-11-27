import hashlib
import io
import json
import os
import stat
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.request import Request

from router.core_install import _Paused, _TUIC_DIGESTS, _asset, check_update, install_core, pause_download, uninstall_core
from router.system_info import core_inventory
from router.xray_service import XrayService


class _Response:
    def __init__(self, data, headers=None):
        self._data = data
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit=-1):
        if limit is None or limit < 0:
            limit = len(self._data)
        chunk, self._data = self._data[:limit], self._data[limit:]
        return chunk


class CoreInstallTests(unittest.TestCase):
    def test_asset_names_follow_the_official_release_layout(self):
        self.assertEqual(_asset('xray-core', 'v26.11.9', 'Darwin', 'arm64')[0], 'Xray-macos-arm64-v8a.zip')
        self.assertEqual(_asset('sing-box', 'v1.14.4', 'Linux', 'x86_64')[0], 'sing-box-1.14.4-linux-amd64.tar.gz')
        self.assertEqual(_asset('mihomo', 'v1.19.34', 'Linux', 'aarch64')[0], 'mihomo-linux-arm64-v1.19.34.gz')
        self.assertEqual(_asset('mihomo', 'v1.19.34', 'Linux', 'x86_64')[0], 'mihomo-linux-amd64-v1-v1.19.34.gz')
        self.assertEqual(_asset('hysteria2', 'app/v2.14.0', 'Darwin', 'arm64')[0], 'hysteria-darwin-arm64')
        self.assertEqual(
            _asset('shadowsocks-rust', 'v1.25.0', 'Linux', 'x86_64')[0],
            'shadowsocks-v1.25.0.x86_64-unknown-linux-musl.tar.xz',
        )
        self.assertEqual(
            _asset('shadowsocks-rust', 'v1.25.0', 'Linux', 'aarch64')[0],
            'shadowsocks-v1.25.0.aarch64-unknown-linux-musl.tar.xz',
        )
        self.assertEqual(
            _asset('shadowsocks-rust', 'v1.25.0', 'Darwin', 'arm64')[0],
            'shadowsocks-v1.25.0.aarch64-apple-darwin.tar.xz',
        )
        with self.assertRaises(ValueError):
            _asset('xray-core', 'v1', 'Windows', 'amd64')

    def test_install_checks_digest_and_keeps_only_the_binary(self):
        payload = b'#!/bin/sh\nprintf "ok\\n"\n'
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as bundle:
            bundle.writestr('geoip.dat', b'nope')
            bundle.writestr('xray', payload)
        blob = archive.getvalue()
        digest = 'sha256:' + hashlib.sha256(blob).hexdigest()
        release = json.dumps({
            'tag_name': 'v9.9.9',
            'assets': [{
                'name': 'Xray-macos-arm64-v8a.zip',
                'digest': digest,
                'browser_download_url': 'https://github.com/XTLS/Xray-core/releases/download/v9.9.9/Xray-macos-arm64-v8a.zip',
            }],
        }).encode()

        def opener(request, timeout):
            self.assertIsInstance(request, Request)
            self.assertLessEqual(timeout, 60)
            if 'api.github.com' in request.full_url:
                return _Response(release)
            return _Response(blob, {'Content-Length': str(len(blob))})

        events = []
        with tempfile.TemporaryDirectory() as tmp:
            result = install_core(tmp, 'xray-core', opener=opener, system='Darwin', machine='arm64', progress=events.append)
            downloaded = [event for event in events if event['stage'] == 'download' and event['total']]
            self.assertEqual(downloaded[-1], {'stage': 'download', 'received': len(blob), 'total': len(blob)})
            self.assertEqual([event['stage'] for event in events if event['stage'] != 'download'], ['release', 'check', 'extract', 'write'])
            installed = Path(result['path'])
            self.assertEqual(installed.read_bytes(), payload)
            self.assertTrue(installed.stat().st_mode & stat.S_IXUSR)
            self.assertEqual(result['version'], 'v9.9.9')
            rows = {row['id']: row for row in core_inventory(SimpleNamespace(
                data_dir=tmp,
                xray=SimpleNamespace(binary='', running=lambda: False),
                singbox=SimpleNamespace(binary='', running=lambda: False),
            ))}
            self.assertEqual(rows['xray-core']['path'], str(installed))
            self.assertEqual(rows['xray-core']['status'], 'stopped')

    def test_pause_keeps_partial_bytes_and_resume_continues(self):
        payload = b'#!/bin/sh\nprintf "ok\\n"\n'
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as bundle:
            bundle.writestr('xray', payload)
        blob = archive.getvalue()
        digest = 'sha256:' + hashlib.sha256(blob).hexdigest()
        release = json.dumps({
            'tag_name': 'v9.9.9',
            'assets': [{
                'name': 'Xray-macos-arm64-v8a.zip',
                'digest': digest,
                'browser_download_url': 'https://github.com/XTLS/Xray-core/releases/download/v9.9.9/Xray-macos-arm64-v8a.zip',
            }],
        }).encode()

        class Gate(_Response):
            def __init__(self, data):
                super().__init__(data, {'Content-Length': str(len(data))})
                self.status = 200
                self.started = threading.Event()
                self._closed = False

            def read(self, limit=-1):
                self.started.set()
                if self._data:
                    chunk, self._data = self._data[:64], self._data[64:]
                    return chunk
                while not self._closed:
                    time.sleep(0.01)
                raise OSError('closed')

            def close(self):
                self._closed = True

        holder = {}

        def opener(request, timeout):
            if 'api.github.com' in request.full_url:
                return _Response(release)
            holder['gate'] = Gate(blob)
            return holder['gate']

        caught = []

        def run(tmp):
            try:
                install_core(tmp, 'xray-core', opener=opener, system='Darwin', machine='arm64')
            except Exception as exc:
                caught.append(exc)

        with tempfile.TemporaryDirectory() as tmp:
            worker = threading.Thread(target=run, args=(tmp,))
            worker.start()
            for _ in range(100):
                if holder.get('gate') and holder['gate'].started.is_set():
                    break
                time.sleep(0.01)
            pause_download('xray-core')
            worker.join(2)
            self.assertIsInstance(caught[0], _Paused)
            self.assertGreater(caught[0].received, 0)
            partial = Path(tmp) / 'bin' / 'xray.partial'
            self.assertGreater(partial.stat().st_size, 0)

            def resume(request, timeout):
                if 'api.github.com' in request.full_url:
                    return _Response(release)
                header = request.get_header('Range')
                start = int(header.split('=', 1)[1].split('-', 1)[0])
                rest = blob[start:]
                response = _Response(rest, {
                    'Content-Range': f'bytes {start}-{start + len(rest) - 1}/{len(blob)}',
                    'Content-Length': str(len(rest)),
                })
                response.status = 206
                return response

            result = install_core(tmp, 'xray-core', opener=resume, system='Darwin', machine='arm64')
            self.assertEqual(Path(result['path']).read_bytes(), payload)
            self.assertFalse(partial.exists())

    def test_check_update_compares_versions_and_skips_a_current_copy(self):
        def opener(request, timeout):
            tag = 'app/v2.14.0' if 'hysteria' in request.full_url else 'v1.10.0'
            return _Response(json.dumps({'tag_name': tag}).encode())

        self.assertFalse(check_update('hysteria2', 'v2.14.0', opener)['update'])
        self.assertTrue(check_update('xray-core', 'v1.9.0', opener)['update'])
        self.assertFalse(check_update('xray-core', 'v1.10.0', opener)['update'])
        self.assertTrue(check_update('xray-core', None, opener)['update'])

        release = json.dumps({
            'tag_name': 'v9.9.9',
            'assets': [{
                'name': 'Xray-macos-arm64-v8a.zip',
                'digest': 'sha256:' + 'ab' * 32,
                'browser_download_url': 'https://github.com/XTLS/Xray-core/releases/download/v9.9.9/Xray-macos-arm64-v8a.zip',
            }],
        }).encode()

        def release_only(request, timeout):
            self.assertIn('api.github.com', request.full_url)
            return _Response(release)

        with tempfile.TemporaryDirectory() as tmp:
            result = install_core(tmp, 'xray-core', opener=release_only, system='Darwin', machine='arm64', current='v9.9.9')
            self.assertTrue(result['uptodate'])
            self.assertFalse((Path(tmp) / 'bin' / 'xray').exists())

    def test_rejects_a_bad_digest_without_writing(self):
        blob = b'not-the-release'
        release = json.dumps({
            'tag_name': 'v9.9.9',
            'assets': [{
                'name': 'Xray-macos-arm64-v8a.zip',
                'digest': 'sha256:' + '0' * 64,
                'browser_download_url': 'https://github.com/XTLS/Xray-core/releases/download/v9.9.9/Xray-macos-arm64-v8a.zip',
            }],
        }).encode()

        def opener(request, timeout):
            return _Response(release if 'api.github.com' in request.full_url else blob)

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, '校验失败'):
                install_core(tmp, 'xray-core', opener=opener, system='Darwin', machine='arm64')
            self.assertFalse((Path(tmp) / 'bin' / 'xray').exists())

    def test_tuic_download_requires_the_pinned_release_and_digest(self):
        for machine in ('aarch64', 'x86_64'):
            linux_name, _, _ = _asset('tuic-server', 'v1.0.0', 'Linux', machine)
            self.assertEqual(linux_name, f'tuic-server-1.0.0-{machine}-unknown-linux-musl')
            self.assertIn(linux_name, _TUIC_DIGESTS)
            self.assertIn(linux_name.replace('tuic-server-', 'tuic-client-'), _TUIC_DIGESTS)
        name, _, _ = _asset('tuic-server', 'v1.0.0', 'Darwin', 'arm64')
        self.assertEqual(name, 'tuic-server-1.0.0-aarch64-apple-darwin')
        client_name = name.replace('tuic-server-', 'tuic-client-')
        blob = b'test TUIC binary'
        digest = hashlib.sha256(blob).hexdigest()
        assets = [{'name': filename, 'digest': f'sha256:{digest}',
                   'browser_download_url': f'https://github.com/Siglud/tuic/releases/download/v1.0.0/{filename}'}
                  for filename in (name, client_name)]

        def opener(request, timeout):
            if 'api.github.com' in request.full_url:
                self.assertTrue(request.full_url.endswith('/Siglud/tuic/releases/tags/v1.0.0'))
                return _Response(json.dumps({'tag_name': 'v1.0.0', 'assets': assets}).encode())
            return _Response(blob)

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, '锁定的 SHA256'):
                install_core(tmp, 'tuic-server', opener=opener, system='Darwin', machine='arm64')
            with patch.dict(_TUIC_DIGESTS, {name: digest, client_name: digest}):
                result = install_core(tmp, 'tuic-server', opener=opener, system='Darwin', machine='arm64')
                server = Path(result['path'])
                client = server.with_name('tuic-client')
                self.assertEqual(server.read_bytes(), blob)
                self.assertEqual(client.read_bytes(), blob)
                client.unlink()
                server.write_bytes(b'preserve existing server')
                with self.assertRaisesRegex(ValueError, 'incompatible client'):
                    install_core(tmp, 'tuic-server', opener=opener, system='Darwin', machine='arm64', current='1.0.0',
                                 check_binary=Mock(side_effect=ValueError('incompatible client')))
                self.assertFalse(client.exists())
                self.assertEqual(server.read_bytes(), b'preserve existing server')
                check_binary = Mock()
                repaired = install_core(tmp, 'tuic-server', opener=opener, system='Darwin', machine='arm64', current='1.0.0',
                                        check_binary=check_binary)
                self.assertTrue(repaired['client_only'])
                self.assertEqual(check_binary.call_args.args[0].name, 'tuic-client')
                self.assertEqual(client.read_bytes(), blob)
                self.assertEqual(server.read_bytes(), b'preserve existing server')

    def test_inventory_keeps_an_environment_override_ahead_of_the_installed_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / 'bin' / 'xray'
            bundled.parent.mkdir()
            bundled.write_text('#!/bin/sh\n')
            bundled.chmod(0o755)
            override = Path(tmp) / 'override'
            override.write_text('#!/bin/sh\n')
            override.chmod(0o755)
            store = SimpleNamespace(
                data_dir=tmp,
                xray=SimpleNamespace(binary=str(bundled), running=lambda: False),
                singbox=SimpleNamespace(binary='', running=lambda: False),
            )
            with patch.dict(os.environ, {'HOPWORLD_XRAY': str(override)}):
                rows = {row['id']: row for row in core_inventory(store)}
            self.assertEqual(rows['xray-core']['path'], str(override))

    def test_uninstall_removes_only_the_bundled_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled = root / 'bin' / 'hysteria'
            bundled.parent.mkdir()
            bundled.write_bytes(b'installed')
            (root / 'bin' / 'hysteria.download').write_bytes(b'partial')
            other = root / 'bin' / 'xray'
            other.write_bytes(b'keep')
            outside = root / 'system-hysteria'
            outside.write_bytes(b'system')
            result = uninstall_core(tmp, 'hysteria2')
            self.assertEqual(result['path'], str(bundled))
            self.assertFalse(bundled.exists())
            self.assertFalse((root / 'bin' / 'hysteria.download').exists())
            self.assertEqual(other.read_bytes(), b'keep')
            self.assertEqual(outside.read_bytes(), b'system')
            with self.assertRaisesRegex(ValueError, '不会删除系统里的程序'):
                uninstall_core(tmp, 'hysteria2')

    def test_uninstall_refuses_a_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / 'outside'
            outside.write_bytes(b'stay')
            link = root / 'bin' / 'xray'
            link.parent.mkdir()
            link.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, '不会删除系统里的程序'):
                uninstall_core(tmp, 'xray-core')
            self.assertEqual(outside.read_bytes(), b'stay')
            self.assertTrue(link.is_symlink())

    def test_locate_prefers_the_installed_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / 'bin' / 'xray'
            binary.parent.mkdir()
            binary.write_text('#!/bin/sh\n')
            binary.chmod(0o755)
            service = XrayService(tmp)
            with patch.dict(os.environ, {'HOPWORLD_XRAY': ''}):
                self.assertEqual(service.locate(), str(binary))
