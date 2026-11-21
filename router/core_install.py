"""Download an official core release into the router data directory."""

import gzip
from contextlib import nullcontext
import hashlib
import io
import json
import os
import platform
import re
import shutil
import tarfile
import tempfile
import threading
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_BYTES = 80 * 1024 * 1024
_ALLOWED_HOSTS = frozenset({
    'github.com', 'api.github.com', 'objects.githubusercontent.com',
    'release-assets.githubusercontent.com', 'github-releases.githubusercontent.com',
})
_INSTALL_LOCK = threading.Lock()
_JOBS = {}


class _Paused(Exception):
    def __init__(self, received, total):
        self.received = received
        self.total = total


class _Cancelled(Exception):
    pass


class _Job:
    def __init__(self):
        self.pause = threading.Event()
        self.cancel = threading.Event()
        self.response = None
_TUIC_TAG = 'v1.0.0'
_TUIC_DIGESTS = {
    'tuic-server-1.0.0-aarch64-apple-darwin': '9533342f89a99c2373ab8c23cd781c4e1bb3b03e2c1d0489c71f3b5af5984f06',
    'tuic-server-1.0.0-x86_64-apple-darwin': '990ab24b4020a3d6099d01d414e4449e5e451375ca23d84a22de4998ef1c59b8',
    'tuic-server-1.0.0-aarch64-unknown-linux-musl': '2ab196f2c1a0de1c626c2610818a6b05e101712d8372f6a92d4656581dfb0f1f',
    'tuic-server-1.0.0-x86_64-unknown-linux-musl': '869b3934d41961abc5807d8126793a173197242499bb3350d28b715f7cc37362',
    'tuic-client-1.0.0-aarch64-apple-darwin': '25cda50a3229631725f9c306829c98e003ddaea73a77db228bd11ff0d092da49',
    'tuic-client-1.0.0-x86_64-apple-darwin': '311f284548d81dc56114c64d3f36d0411f7523cfcf66e87cedade66f0a2ac880',
    'tuic-client-1.0.0-aarch64-unknown-linux-musl': '308b81f999ca2bd8ca4aa7d219e073f851399f7a9de663d4f630fcc57228b895',
    'tuic-client-1.0.0-x86_64-unknown-linux-musl': '26280caf5f26b5a380f88b5c283a4048ccc9967077ea9cbf1e6678ea7698313b',
}


def _platform_key(system, machine):
    system_name = (system or platform.system()).lower()
    machine_name = (machine or platform.machine()).lower()
    os_name = {'darwin': 'darwin', 'linux': 'linux'}.get(system_name)
    arch = {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'amd64', 'amd64': 'amd64'}.get(machine_name)
    if not os_name or not arch:
        raise ValueError('当前系统不能从页面安装内核')
    return os_name, arch


def _asset(core_id, tag, system, machine):
    os_name, arch = _platform_key(system, machine)
    version = tag[1:] if tag.startswith('v') else tag
    if core_id == 'xray-core':
        name = {
            ('darwin', 'arm64'): 'Xray-macos-arm64-v8a.zip',
            ('darwin', 'amd64'): 'Xray-macos-64.zip',
            ('linux', 'amd64'): 'Xray-linux-64.zip',
            ('linux', 'arm64'): 'Xray-linux-arm64-v8a.zip',
        }[(os_name, arch)]
        return name, 'zip', 'xray'
    if core_id == 'sing-box':
        target = {'darwin': 'darwin', 'linux': 'linux'}[os_name] + '-' + arch
        return f'sing-box-{version}-{target}.tar.gz', 'tar', 'sing-box'
    if core_id == 'mihomo':
        target = {'darwin': 'darwin', 'linux': 'linux'}[os_name] + '-' + arch
        return f'mihomo-{target}-{tag}.gz', 'gz', 'mihomo'
    if core_id == 'hysteria2':
        target = {'darwin': 'darwin', 'linux': 'linux'}[os_name] + '-' + arch
        return f'hysteria-{target}', 'raw', 'hysteria'
    if core_id in ('shadowsocks-rust', 'tuic-server'):
        # The static Linux builds also run on hosts with older glibc.
        target = {
            ('darwin', 'arm64'): 'aarch64-apple-darwin',
            ('darwin', 'amd64'): 'x86_64-apple-darwin',
            ('linux', 'amd64'): 'x86_64-unknown-linux-musl',
            ('linux', 'arm64'): 'aarch64-unknown-linux-musl',
        }[(os_name, arch)]
        if core_id == 'tuic-server':
            if tag != _TUIC_TAG:
                raise ValueError('TUIC Server 版本尚未锁定')
            return f'tuic-server-{version}-{target}', 'raw', 'tuic-server'
        return f'shadowsocks-v{version}.{target}.tar.xz', 'tar', 'ssserver'
    raise ValueError('没有这个内核')


_BINARIES = {
    'xray-core': 'xray',
    'sing-box': 'sing-box',
    'mihomo': 'mihomo',
    'hysteria2': 'hysteria',
    'shadowsocks-rust': 'ssserver',
    'tuic-server': 'tuic-server',
}
_CLIENTS = {'shadowsocks-rust': ('sslocal', 'HOPWORLD_SSLOCAL'), 'tuic-server': ('tuic-client', 'HOPWORLD_TUIC_CLIENT')}


def core_client_binary(data_dir, core_id, server_binary=None):
    if core_id not in _CLIENTS:
        return server_binary
    name, env = _CLIENTS[core_id]
    candidates = [os.environ.get(env), str(Path(data_dir) / 'bin' / name)]
    if server_binary and (server_path := shutil.which(server_binary)):
        candidates.append(str(Path(server_path).resolve().with_name(name)))
    candidates.extend((name, f'/usr/local/bin/{name}', f'/usr/bin/{name}'))
    for candidate in candidates:
        if candidate and (executable := shutil.which(candidate)):
            return executable
    return None


def missing_core_client(data_dir, core_id, server_binary=None):
    return core_id in _CLIENTS and not core_client_binary(data_dir, core_id, server_binary)


_REPOS = {
    'xray-core': 'XTLS/Xray-core',
    'sing-box': 'SagerNet/sing-box',
    'mihomo': 'MetaCubeX/mihomo',
    'hysteria2': 'apernet/hysteria',
    'shadowsocks-rust': 'shadowsocks/shadowsocks-rust',
    'tuic-server': 'Siglud/tuic',
}


def _check_url(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname not in _ALLOWED_HOSTS:
        raise ValueError('下载地址不受信任')


class _Guard(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(request, timeout):
    return build_opener(_Guard()).open(request, timeout=timeout)


def _partial_paths(data_dir, binary_name):
    directory = Path(data_dir) / 'bin'
    return directory / (binary_name + '.partial'), directory / (binary_name + '.partial.json')


def _discard_files(*paths):
    for path in paths:
        if path.is_file() and not path.is_symlink():
            path.unlink()


def _discard_partial(data_dir, core_id):
    binary_name = _BINARIES.get(core_id)
    if binary_name:
        _discard_files(*_partial_paths(data_dir, binary_name))
    if core_id in _CLIENTS:
        _discard_files(*_partial_paths(data_dir, _CLIENTS[core_id][0]))


def _close_response(response):
    closer = getattr(response, 'close', None)
    if closer:
        try:
            closer()
        except OSError:
            pass


def pause_download(core_id):
    job = _JOBS.get(core_id)
    response = job.response if job else None
    if response is None:
        raise ValueError('没有正在下载的内核')
    job.pause.set()
    _close_response(response)


def cancel_download(data_dir, core_id):
    if core_id not in _BINARIES:
        raise ValueError('没有这个内核')
    job = _JOBS.get(core_id)
    if job:
        job.cancel.set()
        if job.response is not None:
            _close_response(job.response)
    _discard_partial(data_dir, core_id)


def _total_size(headers, status, offset):
    if not headers:
        return 0
    if status == 206:
        span = str(headers.get('Content-Range') or '')
        marker = span.rpartition('/')[2]
        if marker.isdigit():
            return int(marker)
    try:
        size = int(headers.get('Content-Length') or 0)
    except (TypeError, ValueError):
        size = 0
    return offset + max(0, size) if status == 206 else max(0, size)


def _download(opener, url, digest, data_path, meta_path, progress, job):
    data_path.parent.mkdir(parents=True, exist_ok=True)
    offset = 0
    if data_path.is_file() and not data_path.is_symlink() and meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            meta = {}
        if meta.get('url') == url and meta.get('digest') == digest:
            offset = data_path.stat().st_size
            saved_total = meta.get('total') or 0
            if saved_total and offset >= saved_total:
                return data_path.read_bytes()
        else:
            _discard_files(data_path, meta_path)
    headers = {'User-Agent': 'HopWorld-Router'}
    if offset:
        headers['Range'] = f'bytes={offset}-'
    try:
        response = opener(Request(url, headers=headers), 60)
    except OSError as exc:
        raise ValueError('无法下载安装包') from exc
    job.response = response
    try:
        status = getattr(response, 'status', None) or 200
        if offset and status != 206:
            offset = 0
        total = _total_size(getattr(response, 'headers', None), status, offset)
        meta_path.write_text(json.dumps({'url': url, 'digest': digest, 'total': total}))
        received = offset if status == 206 else 0
        mark = None
        with data_path.open('ab' if received else 'wb') as handle:
            if not received:
                handle.seek(0)
                handle.truncate()
            while True:
                if job.cancel.is_set():
                    raise _Cancelled()
                if job.pause.is_set():
                    handle.flush()
                    raise _Paused(received, total)
                try:
                    block = response.read(64 * 1024)
                except OSError:
                    if job.cancel.is_set():
                        raise _Cancelled()
                    if job.pause.is_set():
                        handle.flush()
                        raise _Paused(received, total)
                    raise
                if not block:
                    break
                handle.write(block)
                received += len(block)
                if received > MAX_BYTES:
                    _discard_files(data_path, meta_path)
                    raise ValueError('安装包过大')
                step = (received * 100 // total) if total else received // (256 * 1024)
                if step != mark:
                    progress(received, total)
                    mark = step
        if progress:
            progress(received, total)
        return data_path.read_bytes()
    except _Cancelled:
        _discard_files(data_path, meta_path)
        raise
    finally:
        job.response = None
        _close_response(response)


def _read_limited(response, progress=None):
    total_size = 0
    headers = getattr(response, 'headers', None)
    if headers is not None:
        try:
            total_size = max(0, int(headers.get('Content-Length') or 0))
        except (TypeError, ValueError):
            total_size = 0
    chunks = []
    total = 0
    mark = None
    while True:
        block = response.read(64 * 1024)
        if not block:
            break
        total += len(block)
        if total > MAX_BYTES:
            raise ValueError('安装包过大')
        chunks.append(block)
        if progress:
            step = (total * 100 // total_size) if total_size else total // (256 * 1024)
            if step != mark:
                progress(total, total_size)
                mark = step
    if progress:
        progress(total, total_size)
    return b''.join(chunks)


def _member_ok(name, wanted):
    parts = Path(name).parts
    return bool(parts) and '..' not in parts and not name.startswith('/') and parts[-1] == wanted


def _extract(blob, kind, wanted):
    if kind == 'raw':
        payload = blob
    elif kind == 'gz':
        try:
            payload = gzip.decompress(blob)
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            raise ValueError('安装包无法解压') from exc
    elif kind == 'zip':
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                chosen = [info for info in archive.infolist() if not info.is_dir() and _member_ok(info.filename, wanted)]
                if not chosen:
                    raise ValueError('安装包里没有要安装的程序')
                info = min(chosen, key=lambda item: len(Path(item.filename).parts))
                if info.file_size > MAX_BYTES:
                    raise ValueError('安装包过大')
                payload = archive.read(info)
        except (zipfile.BadZipFile, OSError) as exc:
            raise ValueError('安装包无法解压') from exc
    elif kind == 'tar':
        try:
            with tarfile.open(fileobj=io.BytesIO(blob), mode='r:*') as archive:
                chosen = [
                    member for member in archive.getmembers()
                    if member.isfile() and not member.issym() and not member.islnk() and _member_ok(member.name, wanted)
                ]
                if not chosen:
                    raise ValueError('安装包里没有要安装的程序')
                member = min(chosen, key=lambda item: len(Path(item.name).parts))
                if member.size > MAX_BYTES:
                    raise ValueError('安装包过大')
                extracted = archive.extractfile(member)
                payload = b'' if extracted is None else extracted.read(MAX_BYTES + 1)
        except (tarfile.TarError, OSError) as exc:
            raise ValueError('安装包无法解压') from exc
    else:
        raise ValueError('安装包格式不受支持')
    if not payload or len(payload) > MAX_BYTES:
        raise ValueError('安装包过大' if payload else '安装包是空的')
    return payload


def _version_tuple(text):
    match = re.search(r'(\d+)\.(\d+)\.(\d+)', text or '')
    return tuple(int(part) for part in match.groups()) if match else None


def _fetch_release(core_id, opener):
    repo = _REPOS.get(core_id)
    if not repo:
        raise ValueError('没有这个内核')
    release_ref = f'tags/{_TUIC_TAG}' if core_id == 'tuic-server' else 'latest'
    release_url = f'https://api.github.com/repos/{repo}/releases/{release_ref}'
    _check_url(release_url)
    request = Request(release_url, headers={'User-Agent': 'HopWorld-Router', 'Accept': 'application/vnd.github+json'})
    try:
        with opener(request, 30) as response:
            release = json.loads(_read_limited(response).decode('utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('无法读取官方发布信息') from exc
    tag = release.get('tag_name')
    if not isinstance(tag, str) or not _version_tuple(tag):
        raise ValueError('官方发布信息无效')
    return release


def check_update(core_id, current, opener=None):
    """Return whether the official release is newer than the running copy."""
    tag = _fetch_release(core_id, opener or _open)['tag_name']
    installed = _version_tuple(current) if current else None
    if current and not installed:
        raise ValueError('读不到当前版本，没有开始下载')
    return {'current': current, 'latest': tag, 'update': installed is None or _version_tuple(tag) > installed}


def install_core(data_dir, core_id, opener=None, system=None, machine=None, progress=None, current=None, *,
                 finalize_lock=None, check_finalize=None, check_binary=None):
    """Install one core under data_dir/bin. The bytes must match GitHub's asset digest."""
    def report(stage, received=0, total=0):
        if progress:
            progress({'stage': stage, 'received': received, 'total': total})

    if not _INSTALL_LOCK.acquire(blocking=False):
        raise ValueError('已有内核正在安装')
    try:
        opener = opener or _open
        report('release')
        release = _fetch_release(core_id, opener)
        tag = release['tag_name']
        installed = _version_tuple(current) if current else None
        if current and not installed:
            raise ValueError('读不到当前版本，没有开始下载')
        server_current = installed is not None and _version_tuple(tag) <= installed
        if server_current and not missing_core_client(data_dir, core_id):
            return {'id': core_id, 'version': tag, 'uptodate': True}
        asset_name, kind, binary_name = _asset(core_id, tag, system, machine)
        assets = release.get('assets')
        job = _JOBS.setdefault(core_id, _Job())
        job.pause.clear()
        job.cancel.clear()
        downloaded = []

        def verified_asset(name, partial_name):
            if job.cancel.is_set():
                raise _Cancelled()
            asset = next((item for item in assets if isinstance(item, dict) and item.get('name') == name), None) if isinstance(assets, list) else None
            digest = asset.get('digest') if asset else None
            download_url = asset.get('browser_download_url') if asset else None
            if not isinstance(digest, str) or not digest.startswith('sha256:') or not isinstance(download_url, str):
                raise ValueError('官方发布包没有可校验的 SHA256')
            if core_id == 'tuic-server' and digest != f'sha256:{_TUIC_DIGESTS[name]}':
                raise ValueError('TUIC 安装包与锁定的 SHA256 不一致')
            _check_url(download_url)
            report('download')
            data_path, meta_path = _partial_paths(data_dir, partial_name)
            try:
                blob = _download(opener, download_url, digest, data_path, meta_path,
                                 lambda received, total: report('download', received, total), job)
            except OSError as exc:
                raise ValueError('无法下载安装包') from exc
            report('check')
            if hashlib.sha256(blob).hexdigest() != digest.split(':', 1)[1].lower():
                _discard_files(data_path, meta_path)
                raise ValueError('安装包校验失败，已丢弃')
            downloaded.extend((data_path, meta_path))
            return blob

        payloads = {}
        client_spec = _CLIENTS.get(core_id)
        client_only = server_current
        if core_id == 'tuic-server':
            name = client_spec[0]
            client_blob = verified_asset(asset_name.replace('tuic-server-', 'tuic-client-', 1), name)
            payloads[name] = _extract(client_blob, 'raw', name)
        if not client_only or core_id == 'shadowsocks-rust':
            blob = verified_asset(asset_name, binary_name)
            report('extract')
            if core_id == 'shadowsocks-rust':
                name = client_spec[0]
                payloads[name] = _extract(blob, kind, name)
            if not client_only:
                payloads[binary_name] = _extract(blob, kind, binary_name)
        if job.cancel.is_set():
            raise _Cancelled()
        report('write')
        root = Path(data_dir).resolve()
        directory = Path(data_dir) / 'bin'
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not directory.resolve().is_relative_to(root):
            raise ValueError('安装目录无效')
        os.chmod(directory, 0o700)
        with tempfile.TemporaryDirectory(dir=directory, prefix='.update-') as folder:
            stage = Path(folder)
            for name, payload in payloads.items():
                candidate = stage / name
                candidate.write_bytes(payload)
                candidate.chmod(0o755)
                if check_binary:
                    check_binary(candidate)
            with (finalize_lock or nullcontext()):
                if check_finalize and (reason := check_finalize()):
                    raise ValueError(reason)
                previous = {}
                applied = []
                try:
                    for name in payloads:
                        destination = directory / name
                        if destination.exists() or destination.is_symlink():
                            previous[name] = stage / ('old-' + name)
                            os.link(destination, previous[name], follow_symlinks=False)
                        os.replace(stage / name, destination)
                        applied.append(name)
                except OSError:
                    for name in reversed(applied):
                        destination = directory / name
                        if name in previous:
                            os.replace(previous[name], destination)
                        else:
                            destination.unlink()
                    raise
        _discard_files(*downloaded)
        if client_only:
            return {'id': core_id, 'version': tag, 'client_only': True, 'path': str(directory / client_spec[0])}
        return {'id': core_id, 'version': tag, 'path': str(directory / binary_name)}
    finally:
        _INSTALL_LOCK.release()


def bundled_binary(data_dir, core_id):
    binary_name = _BINARIES.get(core_id)
    if not binary_name:
        raise ValueError('没有这个内核')
    return Path(data_dir) / 'bin' / binary_name


def uninstall_core(data_dir, core_id):
    """Remove only the copy this page installed under data_dir/bin."""
    if not _INSTALL_LOCK.acquire(blocking=False):
        raise ValueError('已有内核正在安装')
    try:
        target = bundled_binary(data_dir, core_id)
        root = Path(data_dir).resolve()
        if target.is_symlink() or not target.is_file():
            raise ValueError('没有从本页安装这个内核，不会删除系统里的程序')
        if not target.resolve().is_relative_to(root / 'bin'):
            raise ValueError('不能卸载这个程序')
        target.unlink()
        leftover = target.with_name(target.name + '.download')
        if leftover.is_file() and not leftover.is_symlink():
            leftover.unlink()
        if core_id in _CLIENTS:
            name = _CLIENTS[core_id][0]
            _discard_files(target.with_name(name), target.with_name(name + '.download'))
        _discard_partial(data_dir, core_id)
        return {'id': core_id, 'path': str(target)}
    finally:
        _INSTALL_LOCK.release()
