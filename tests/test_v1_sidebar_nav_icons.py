import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'router' / 'static' / 'index.html').read_text(encoding='utf-8')
CSS = (ROOT / 'router' / 'static' / 'v1.css').read_text(encoding='utf-8')
LICENSE = ROOT / 'licenses' / 'lucide-LICENSE.txt'

LINKS = [
    ('overview', '首页', 'house', 'M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8'),
    ('cores', '内核管理', 'server', '<rect width="20" height="8" x="2" y="2" rx="2" ry="2"/>'),
    ('nodes', '节点管理', 'network', '<rect x="16" y="16" width="6" height="6" rx="1"/>'),
    ('subscriptions', '订阅管理', 'rss', '<path d="M4 11a9 9 0 0 1 9 9"/>'),
    ('certificates', '域名与证书', 'shield', 'M20 13c0 5-3.5 7.5-7.66 8.95'),
    ('public', '公网接入', 'globe', '<circle cx="12" cy="12" r="10"/>'),
    ('traffic', '流量监控', 'activity', 'M22 12h-2.48a2 2 0 0 0-1.93 1.46'),
    ('settings', '系统设置', 'settings', 'M9.671 4.136a2.34 2.34 0 0 1 4.659 0'),
    ('about', '关于', 'info', '<path d="M12 16v-4"/>'),
]

OLD_CHARS = '⌂▤☷↗◇⌁▥⚙ⓘ'


class SidebarNavIconTests(unittest.TestCase):
    def test_nine_lucide_icons_share_size_and_stroke(self):
        nav = re.search(r'<nav id="v1-navigation".*?</nav>', HTML, re.S).group(0)
        anchors = re.findall(r'<a href="#[^"]+" data-v1-link="[^"]+"[^>]*>.*?</a>', nav, re.S)
        self.assertEqual(len(anchors), 9)
        for (key, label, _name, snippet), anchor in zip(LINKS, anchors):
            with self.subTest(link=key):
                self.assertIn(f'data-v1-link="{key}"', anchor)
                self.assertIn(f'href="#{key}"', anchor)
                self.assertTrue(anchor.endswith(label + '</a>') or f'>{label}<' in anchor or label in anchor)
                svgs = re.findall(r'<svg\b[^>]*>.*?</svg>', anchor, re.S)
                self.assertEqual(len(svgs), 1)
                svg = svgs[0]
                self.assertIn('class="v1-nav-icon"', svg)
                self.assertIn('viewBox="0 0 24 24"', svg)
                self.assertIn('width="20"', svg)
                self.assertIn('height="20"', svg)
                self.assertIn('stroke-width="2"', svg)
                self.assertIn('stroke-linecap="round"', svg)
                self.assertIn('stroke-linejoin="round"', svg)
                self.assertIn('stroke="currentColor"', svg)
                self.assertIn('fill="none"', svg)
                self.assertIn('aria-hidden="true"', svg)
                self.assertIn('focusable="false"', svg)
                self.assertIn(snippet, svg)
                for char in OLD_CHARS:
                    self.assertNotIn(char, anchor)

    def test_core_badge_and_labels_remain(self):
        nav = re.search(r'<nav id="v1-navigation".*?</nav>', HTML, re.S).group(0)
        self.assertIn('id="v1-core-nav-count"', nav)
        self.assertIn('内核管理<em id="v1-core-nav-count"', nav)
        for _key, label, *_rest in LINKS:
            self.assertIn(label, nav)
        self.assertIn('Lucide Static v1.77.0', nav)

    def test_sidebar_icon_css_is_scoped_and_balanced(self):
        self.assertIn('.v1-sidebar a .v1-nav-icon { display: block; flex: none; width: 20px; height: 20px; color: #a8c8eb; }', CSS)
        self.assertIn('.v1-sidebar a[aria-current="page"] .v1-nav-icon { color: white; }', CSS)
        self.assertNotIn('.v1-sidebar a span { color: #a8c8eb; font-size: 18px; width: 21px; text-align: center; }', CSS)
        self.assertNotIn('.v1-sidebar a[aria-current="page"] span { color: white; }', CSS)
        # Brace balance for the file
        self.assertEqual(CSS.count('{'), CSS.count('}'))

    def test_license_notices_are_kept(self):
        text = LICENSE.read_text(encoding='utf-8')
        self.assertIn('ISC License', text)
        self.assertIn('Copyright (c) 2026 Lucide Icons and Contributors', text)
        self.assertIn('The MIT License (MIT)', text)
        self.assertIn('Copyright (c) 2013-present Cole Bemis', text)
        self.assertIn('info', text)
        self.assertIn('rss', text)
        self.assertIn('server', text)


if __name__ == '__main__':
    unittest.main()
