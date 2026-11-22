"""Sidebar should show the installed app version, never a leftover 开发版 label."""

import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from router.system_info import system_info
from router.version import VERSION


ROOT = pathlib.Path(__file__).resolve().parents[1]
HTML = (ROOT / 'router' / 'static' / 'index.html').read_text(encoding='utf-8')
JS = (ROOT / 'router' / 'static' / 'v1.js').read_text(encoding='utf-8')
CSS = (ROOT / 'router' / 'static' / 'v1.css').read_text(encoding='utf-8')


class SidebarVersionTests(unittest.TestCase):
    def test_markup_has_no_development_edition_placeholder(self):
        self.assertIn('id="v1-sidebar-version-label"', HTML)
        self.assertIn('class="v1-sidebar-version"', HTML)
        self.assertNotIn('开发版', HTML)
        self.assertNotIn('V1 开发版', HTML)
        self.assertIn('<small id="v1-sidebar-version-label"></small>', HTML)
        self.assertIn('function formatAppVersion(version)', JS)
        self.assertIn('function renderAppVersion(version)', JS)
        self.assertIn("byId('v1-sidebar-version-label')", JS)
        self.assertIn('renderAppVersion(value.version)', JS)
        self.assertIn("byId('v1-settings-version').textContent = value.version || dash", JS)
        self.assertIn('.v1-sidebar-version small:empty { display: none; }', CSS)
        self.assertNotIn('api.github.com', JS[JS.index('function formatAppVersion'):JS.index('function renderSystem')])

    def test_format_app_version_uses_local_metadata_and_is_honest_when_missing(self):
        script = r"""
const fs = require('fs');
const src = fs.readFileSync(process.env.V1_JS, 'utf8');
const start = src.indexOf('  function formatAppVersion(version) {');
const end = src.indexOf('  function renderAppVersion(version) {', start);
if (start < 0 || end < 0) throw new Error('cannot extract formatAppVersion');
eval(src.slice(start, end));
const cases = [
  ['1.0.2', 'v1.0.2'],
  ['v1.0.2', 'v1.0.2'],
  [' 1.0.0 ', 'v1.0.0'],
  ['9.9.9-test', 'v9.9.9-test'],
  ['', ''],
  ['   ', ''],
  [null, ''],
  [undefined, ''],
];
process.stdout.write(JSON.stringify(cases.map(([input, expected]) => ({
  input, expected, actual: formatAppVersion(input),
}))));
"""
        env = {**os.environ, 'V1_JS': str(ROOT / 'router' / 'static' / 'v1.js')}
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'format.js'
            path.write_text(script, encoding='utf-8')
            proc = subprocess.run(['node', path], capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for row in json.loads(proc.stdout):
            self.assertEqual(row['actual'], row['expected'], row)

    def test_system_version_uses_installed_package_or_project_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SimpleNamespace(data_dir=pathlib.Path(tmp))
            env = {key: value for key, value in os.environ.items() if key != 'HOPWORLD_PACKAGE_VERSION'}
            with patch('router.system_info.core_inventory', return_value=[]), patch.dict(os.environ, env, clear=True):
                self.assertEqual(system_info(store)['version'], VERSION)
            with patch('router.system_info.core_inventory', return_value=[]), patch.dict(os.environ, {**env, 'HOPWORLD_PACKAGE_VERSION': '9.9.9-test'}, clear=True):
                self.assertEqual(system_info(store)['version'], '9.9.9-test')
            with patch('router.system_info.core_inventory', return_value=[]):
                self.assertNotIn('开发版', system_info(store)['version'] or '')


if __name__ == '__main__':
    unittest.main()
