"""Published releases from the project's fixed GitHub repository."""

import re
from urllib.request import Request

from router.version import RELEASE_MAJOR


API = 'https://api.github.com/repos/ZaoHub/HopWorld-Router'
REPOSITORY = 'ZaoHub/HopWorld-Router'


def stable_version(value):
    match = re.fullmatch(r'v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value or '')
    return tuple(map(int, match.groups())) if match else None


def stable_release(fetch):
    releases = fetch(API + '/releases?per_page=100')
    if not isinstance(releases, list):
        raise ValueError('GitHub 发布列表无效')
    candidates = []
    for release in releases:
        if not isinstance(release, dict) or release.get('draft') or release.get('prerelease'):
            continue
        tag = release.get('tag_name')
        version = stable_version(tag) if isinstance(tag, str) and tag.startswith('v') else None
        if version and version[0] == RELEASE_MAJOR:
            candidates.append((version, release))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def deb_package(release):
    tag = release['tag_name']
    name = f'hopworld-router_{tag[1:]}_all.deb'
    expected = f'https://github.com/{REPOSITORY}/releases/download/{tag}/{name}'
    assets = release.get('assets')
    if not isinstance(assets, list):
        raise ValueError('GitHub 发布包列表无效')
    for asset in assets:
        if not isinstance(asset, dict) or asset.get('name') != name:
            continue
        digest = asset.get('digest')
        if not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
            raise ValueError('GitHub 发布包缺少可验证的 SHA-256')
        if asset.get('browser_download_url') != expected:
            raise ValueError('GitHub 下载地址与发布版本不一致')
        return tag[1:], name, expected, digest[7:]
    raise ValueError('这个 GitHub 版本未附带 Router Debian 软件包')
