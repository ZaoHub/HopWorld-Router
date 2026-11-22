"""Node list action icons must be consistent SVGs, sized and centered like one set."""

import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest
from urllib.request import pathname2url


ROOT = pathlib.Path(__file__).resolve().parents[1]
V1_CSS = ROOT / 'router' / 'static' / 'v1.css'
V1_JS = ROOT / 'router' / 'static' / 'v1.js'
STYLE_CSS = ROOT / 'router' / 'static' / 'style.css'
CHROME_CANDIDATES = (
    pathlib.Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
    pathlib.Path('/Applications/Chromium.app/Contents/MacOS/Chromium'),
)


def chrome_path():
    for path in CHROME_CANDIDATES:
        if path.is_file():
            return path
    return shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')


def parse_node_action_icons(js):
    match = re.search(r'const nodeActionIcons = \{(\s*edit:[\s\S]*?)\n  \};', js)
    if not match:
        raise AssertionError('v1.js 中未找到 nodeActionIcons')
    icons = dict(re.findall(r"(edit|probe|logs): '([^']+)'", match.group(1)))
    if set(icons) != {'edit', 'probe', 'logs'}:
        raise AssertionError(f'nodeActionIcons 不完整: {sorted(icons)}')
    return icons


FIXTURE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="STYLE_HREF">
  <link rel="stylesheet" href="V1_HREF">
  <style>body { margin: 0; }</style>
</head>
<body>
  <main class="page v1-page">
    <table class="v1-core-table" id="core-table">
      <tbody>
        <tr>
          <td>
            <div class="v1-core-ops" id="core-ops">
              <button type="button" class="v1-icon-btn" data-core-act="start" aria-label="启动 Xray-core"><svg class="v1-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M5 5a2 2 0 0 1 3.008-1.728l11.997 6.998a2 2 0 0 1 .003 3.458l-12 7A2 2 0 0 1 5 19z" /></svg></button>
              <button type="button" class="v1-icon-btn" data-core-act="install" aria-label="更新 Xray-core"><svg class="v1-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" /><path d="M21 3v5h-5" /><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" /><path d="M8 16H3v5" /></svg></button>
              <button type="button" class="v1-icon-btn" data-core-act="edit" aria-label="设置 Xray-core">
                <svg class="v1-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915" /><circle cx="12" cy="12" r="3" /></svg>
              </button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
    <table class="v1-node-table" id="node-table">
      <tbody>
        <tr>
          <td>
            <div class="v1-core-ops" id="node-ops">
              <button type="button" class="v1-icon-btn" data-node-act="edit" aria-label="编辑 demo">EDIT_SVG</button>
              <button type="button" class="v1-icon-btn" data-node-act="probe" aria-label="测试 demo">PROBE_SVG</button>
              <button type="button" class="v1-icon-btn" data-node-act="logs" aria-label="日志 demo">LOGS_SVG</button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
    <table class="v1-sub-table" id="sub-table">
      <tbody>
        <tr>
          <td>
            <div class="v1-core-ops" id="sub-ops">
              <button type="button" class="v1-icon-btn" data-sub-act="edit" aria-label="设置 订阅">&#9998;</button>
              <button type="button" class="v1-icon-btn" data-sub-act="refresh" aria-label="更新 订阅">&#8635;</button>
              <button type="button" class="v1-icon-btn" data-sub-act="delete" aria-label="删除 订阅">&#215;</button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </main>
  <pre id="result"></pre>
  <script>
    function box(el) {
      const r = el.getBoundingClientRect();
      return {top: r.top, bottom: r.bottom, left: r.left, right: r.right, width: r.width, height: r.height, cx: r.left + r.width / 2, cy: r.top + r.height / 2};
    }
    function overlap(a, b) {
      return a.left < b.right - 0.5 && a.right > b.left + 0.5 && a.top < b.bottom - 0.5 && a.bottom > b.top + 0.5;
    }
    function inkBox(svg) {
      const raw = svg.getBBox();
      const stroke = Number.parseFloat(getComputedStyle(svg).strokeWidth || svg.getAttribute('stroke-width') || '0') || 0;
      const pad = stroke / 2;
      const ctm = svg.getScreenCTM();
      const pts = [
        [raw.x - pad, raw.y - pad],
        [raw.x + raw.width + pad, raw.y - pad],
        [raw.x - pad, raw.y + raw.height + pad],
        [raw.x + raw.width + pad, raw.y + raw.height + pad]
      ].map(([x, y]) => {
        const point = svg.createSVGPoint();
        point.x = x;
        point.y = y;
        return point.matrixTransform(ctm);
      });
      const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
      const left = Math.min(...xs), right = Math.max(...xs);
      const top = Math.min(...ys), bottom = Math.max(...ys);
      return {
        kind: 'svg-ink',
        left: left, right: right, top: top, bottom: bottom,
        width: right - left, height: bottom - top,
        cx: (left + right) / 2, cy: (top + bottom) / 2,
        user: {x: raw.x, y: raw.y, width: raw.width, height: raw.height}
      };
    }
    function buttonMetrics(button) {
      const style = getComputedStyle(button);
      const svg = button.querySelector('svg');
      return {
        act: button.dataset.coreAct || button.dataset.nodeAct || button.dataset.subAct || '',
        label: button.getAttribute('aria-label') || '',
        button: box(button),
        icon: svg ? inkBox(svg) : null,
        svgSize: svg ? box(svg) : null,
        svgAriaHidden: svg ? svg.getAttribute('aria-hidden') : null,
        fontSize: parseFloat(style.fontSize),
        lineHeight: style.lineHeight,
        paddingTop: parseFloat(style.paddingTop),
        paddingRight: parseFloat(style.paddingRight),
        paddingBottom: parseFloat(style.paddingBottom),
        paddingLeft: parseFloat(style.paddingLeft),
        minWidth: parseFloat(style.minWidth),
        minHeight: parseFloat(style.minHeight),
        display: style.display
      };
    }
    function group(id) {
      const buttons = [...document.querySelectorAll('#' + id + ' .v1-icon-btn')].map(buttonMetrics);
      const overlaps = [];
      for (let i = 0; i < buttons.length; i++) {
        for (let j = i + 1; j < buttons.length; j++) {
          if (overlap(buttons[i].button, buttons[j].button) ||
              (buttons[i].icon && buttons[j].icon && overlap(buttons[i].icon, buttons[j].icon))) {
            overlaps.push([buttons[i].act, buttons[j].act]);
          }
        }
      }
      return {buttons: buttons, overlaps: overlaps};
    }
    function snapshot() {
      return {core: group('core-ops'), node: group('node-ops'), sub: group('sub-ops')};
    }
    function measure() {
      const rest = snapshot();
      const nodeButtons = [...document.querySelectorAll('#node-ops .v1-icon-btn')];
      const focused = [];
      for (const button of nodeButtons) {
        button.focus();
        focused.push(buttonMetrics(button));
      }
      if (document.activeElement) document.activeElement.blur();
      return {
        viewport: {w: document.documentElement.clientWidth, h: document.documentElement.clientHeight},
        media710: matchMedia('(max-width: 710px)').matches,
        core: rest.core,
        node: rest.node,
        sub: rest.sub,
        nodeFocused: focused
      };
    }
    document.getElementById('result').textContent = JSON.stringify(measure());
    document.title = 'LAYOUT_READY';
  </script>
</body>
</html>
"""


def run_chrome(chrome, html, width, height=900):
    with tempfile.TemporaryDirectory(prefix='hopworld-ops-icons-') as tmp:
        tmp = pathlib.Path(tmp)
        page = tmp / 'ops.html'
        page.write_text(html, encoding='utf-8')
        profile = tmp / 'chrome-profile'
        profile.mkdir()
        cmd = [
            str(chrome),
            '--headless=new',
            f'--user-data-dir={profile}',
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-extensions',
            '--disable-sync',
            '--disable-gpu',
            '--disable-background-networking',
            '--force-device-scale-factor=1',
            f'--window-size={width},{height}',
            '--virtual-time-budget=4000',
            '--dump-dom',
            page.as_uri(),
        ]
        proc = subprocess.run(
            cmd, check=False, capture_output=True, text=True, timeout=40,
            env={**os.environ, 'HOME': str(tmp)},
        )
        dump = proc.stdout or ''
        match = re.search(r'<pre id="result">(.+?)</pre>', dump, re.S)
        if not match:
            raise AssertionError(
                f'无头 Chrome 未输出布局结果 width={width} exit={proc.returncode} '
                f'stderr={proc.stderr[-2000:]!r}'
            )
        raw = match.group(1).replace('&quot;', '"').replace('&lt;', '<').replace('&gt;', '>')
        return json.loads(raw)


def assert_node_icon_geometry(test, measured, width):
    expected = 44 if measured['media710'] else 36
    if width <= 710:
        test.assertTrue(measured['media710'], f'{width}px 应命中 710 断点')
    if width > 710:
        test.assertFalse(measured['media710'], f'{width}px 不应命中 710 断点')

    test.assertEqual(len(measured['node']['buttons']), 3)
    test.assertEqual(measured['node']['overlaps'], [], f'{width}px 节点操作按钮重叠')
    test.assertEqual(measured['core']['overlaps'], [], f'{width}px 内核操作按钮重叠')
    test.assertEqual(measured['sub']['overlaps'], [], f'{width}px 订阅操作按钮重叠')

    for item in measured['sub']['buttons']:
        test.assertLessEqual(abs(item['button']['width'] - 32), 1, f'{width}px 订阅按钮被连带改动: {item}')
        test.assertLessEqual(abs(item['button']['height'] - 32), 1, item)

    for item in measured['core']['buttons']:
        test.assertLessEqual(abs(item['button']['width'] - expected), 1, f'{width}px 内核按钮宽 {item["button"]["width"]} != {expected}')
        test.assertLessEqual(abs(item['button']['height'] - expected), 1, f'{width}px 内核按钮高 {item["button"]["height"]} != {expected}')

    inks = {}
    for item in measured['node']['buttons']:
        test.assertEqual(item['svgAriaHidden'], 'true', item)
        test.assertIsNotNone(item['icon'], f'{width}px {item["act"]} 缺少 SVG 墨迹')
        test.assertEqual(item['icon']['kind'], 'svg-ink')
        test.assertLessEqual(abs(item['button']['width'] - expected), 1, f'{width}px 节点按钮宽 {item["button"]["width"]} != {expected}')
        test.assertLessEqual(abs(item['button']['height'] - expected), 1, f'{width}px 节点按钮高 {item["button"]["height"]} != {expected}')
        test.assertEqual((item['paddingTop'], item['paddingRight'], item['paddingBottom'], item['paddingLeft']), (0, 0, 0, 0))
        test.assertLessEqual(abs(item['svgSize']['width'] - 20), 1, f'{width}px {item["act"]} SVG 外框宽 {item["svgSize"]["width"]}')
        test.assertLessEqual(abs(item['svgSize']['height'] - 20), 1, f'{width}px {item["act"]} SVG 外框高 {item["svgSize"]["height"]}')
        test.assertLessEqual(
            abs(item['icon']['cx'] - item['button']['cx']), 1.5,
            f'{width}px {item["act"]} 水平未居中: ink_cx={item["icon"]["cx"]} btn_cx={item["button"]["cx"]} ink={item["icon"]}'
        )
        test.assertLessEqual(
            abs(item['icon']['cy'] - item['button']['cy']), 1.5,
            f'{width}px {item["act"]} 垂直未居中: ink_cy={item["icon"]["cy"]} btn_cy={item["button"]["cy"]} ink={item["icon"]}'
        )
        test.assertGreaterEqual(item['icon']['left'], item['button']['left'] - 0.5)
        test.assertLessEqual(item['icon']['right'], item['button']['right'] + 0.5)
        test.assertGreaterEqual(item['icon']['top'], item['button']['top'] - 0.5)
        test.assertLessEqual(item['icon']['bottom'], item['button']['bottom'] + 0.5)
        inks[item['act']] = item['icon']

    test.assertEqual(set(inks), {'edit', 'probe', 'logs'})
    sizes = {act: (ink['width'], ink['height'], (ink['width'] * ink['height']) ** 0.5, max(ink['width'], ink['height'])) for act, ink in inks.items()}
    eq = [sizes[act][2] for act in sizes]
    longest = [sizes[act][3] for act in sizes]
    test.assertLessEqual(max(longest) - min(longest), 4, f'{width}px 三图标最长边不一致: {sizes}')
    test.assertLessEqual(max(eq) - min(eq), 4, f'{width}px 三图标等效边长不一致（中间可能偏小）: {sizes}')
    test.assertGreaterEqual(
        sizes['probe'][2] / max(sizes['edit'][2], sizes['logs'][2]), 0.85,
        f'{width}px 测试图标明显偏小: {sizes}'
    )

    test.assertEqual(len(measured['nodeFocused']), 3)
    rest_by_act = {item['act']: item for item in measured['node']['buttons']}
    for item in measured['nodeFocused']:
        rest = rest_by_act[item['act']]
        test.assertLessEqual(abs(item['button']['width'] - rest['button']['width']), 0.5, f'{width}px {item["act"]} focus 宽度跳动')
        test.assertLessEqual(abs(item['button']['height'] - rest['button']['height']), 0.5, f'{width}px {item["act"]} focus 高度跳动')
        test.assertLessEqual(abs(item['icon']['width'] - rest['icon']['width']), 0.5, f'{width}px {item["act"]} focus 图标宽度跳动')
        test.assertLessEqual(abs(item['icon']['height'] - rest['icon']['height']), 0.5, f'{width}px {item["act"]} focus 图标高度跳动')
        test.assertLessEqual(abs(item['icon']['cx'] - item['button']['cx']), 1.5)
        test.assertLessEqual(abs(item['icon']['cy'] - item['button']['cy']), 1.5)


class NodeCoreOpsIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chrome = chrome_path()
        if not cls.chrome:
            raise unittest.SkipTest('未找到可用于布局测量的独立 Chrome/Chromium')
        cls.css = V1_CSS.read_text(encoding='utf-8')
        cls.js = V1_JS.read_text(encoding='utf-8')
        cls.icons = parse_node_action_icons(cls.js)
        cls.html = (FIXTURE
                    .replace('STYLE_HREF', 'file://' + pathname2url(str(STYLE_CSS.resolve())))
                    .replace('V1_HREF', 'file://' + pathname2url(str(V1_CSS.resolve())))
                    .replace('EDIT_SVG', cls.icons['edit'])
                    .replace('PROBE_SVG', cls.icons['probe'])
                    .replace('LOGS_SVG', cls.icons['logs']))

    def test_js_uses_shared_svg_icons_for_node_list_actions(self):
        self.assertIn('function nodeIconButton(act, label, id)', self.js)
        self.assertIn("nodeIconButton('probe', '测试 ' + node.title, node.id)", self.js)
        self.assertIn("nodeIconButton('edit', '编辑 ' + node.title, node.id)", self.js)
        self.assertIn("nodeIconButton('logs', '日志 ' + node.title, node.id)", self.js)
        self.assertNotIn("iconButton('⌁', '测试 ' + node.title, 'probe', node.id)", self.js)
        self.assertNotIn("iconButton('✎', '编辑 ' + node.title, 'edit', node.id)", self.js)
        self.assertNotIn("iconButton('☰', '日志 ' + node.title, 'logs', node.id)", self.js)
        self.assertIn("iconButton(canStop ? 'square' : 'play'", self.js)
        self.assertIn("tile('pencil', '编辑节点', 'edit', true)", self.js)
        for act, svg in self.icons.items():
            self.assertIn('viewBox="0 0 24 24"', svg, act)
            self.assertIn('width="20"', svg, act)
            self.assertIn('height="20"', svg, act)
            self.assertIn('aria-hidden="true"', svg, act)
            self.assertIn('stroke="currentColor"', svg, act)
            self.assertIn('stroke-width="2"', svg, act)

    def test_css_scopes_node_table_to_core_table_metrics_only(self):
        self.assertIn(
            '.v1-core-table .v1-icon-btn { min-width: 36px; min-height: 36px; padding: 0; background: #1b3049; color: #dcecff; display: grid; place-items: center; }',
            self.css,
        )
        self.assertIn(
            '.v1-node-table .v1-icon-btn { min-width: 36px; min-height: 36px; padding: 0; background: #1b3049; color: #dcecff; display: grid; place-items: center; }',
            self.css,
        )
        self.assertIn(
            '.v1-node-table .v1-icon-btn svg { width: 20px; height: 20px; display: block; pointer-events: none; }',
            self.css,
        )
        self.assertIn(
            '.v1-core-table .v1-icon-btn svg { width: 20px; height: 20px; display: block; pointer-events: none; }',
            self.css,
        )
        self.assertIn(
            '.v1-sub-table .v1-icon-btn svg { width: 18px; height: 18px; display: block; pointer-events: none; }',
            self.css,
        )
        self.assertIn(
            '.v1-sub-table .v1-icon-btn { width: 32px; min-width: 32px; height: 32px; min-height: 32px; padding: 0; background: #1b3049; color: #dcecff; display: grid; place-items: center; }',
            self.css,
        )
        self.assertIn('.v1-core-table .v1-icon-btn { min-width: 44px; min-height: 44px; }', self.css)
        self.assertIn('.v1-node-table .v1-icon-btn { min-width: 44px; min-height: 44px; }', self.css)
        for kind in (':hover', ':focus', ':focus-visible', ':active'):
            pattern = re.compile(rf'\.v1-node-table[^{{]*v1-icon-btn[^{{]*{re.escape(kind)}[^{{]*\{{[^}}]*\}}')
            for rule in pattern.findall(self.css):
                self.assertNotRegex(rule, r'(^|[^a-z-])(width|height|min-width|min-height|padding|font-size|line-height)\s*:')
        self.assertIn('.v1-sidebar-version small:empty { display: none; }', self.css)
        self.assertIn('grid-template-columns: 14px minmax(0,1fr) 7ch 9ch', self.css)
        self.assertIn('height: 36px; min-height: 36px; margin-block: 0; padding-block: 0;', self.css)

    def test_rendered_node_ops_match_core_ops_across_breakpoints(self):
        for width in (1440, 1280, 900, 710, 430, 390):
            with self.subTest(width=width):
                measured = run_chrome(self.chrome, self.html, width)
                assert_node_icon_geometry(self, measured, width)


if __name__ == '__main__':
    unittest.main()
