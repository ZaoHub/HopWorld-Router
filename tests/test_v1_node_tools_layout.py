"""Layout measurements for the node card header summary and compact toolbar (no scrollbar)."""

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


FIXTURE = '''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="STYLE_HREF">
  <link rel="stylesheet" href="V1_HREF">
  <style>body { margin: 0; }</style>
</head>
<body>
  <aside class="v1-sidebar" aria-hidden="true"><nav id="v1-navigation"><a href="#nodes">节点管理</a></nav></aside>
  <main class="page v1-page">
    <div class="v1-nodes-layout">
      <div class="v1-nodes-main">
        <section class="card v1-nodes-board" id="board">
          <div class="v1-core-toolbar" id="toolbar">
            <div class="v1-core-filters" id="core-filters" role="group" aria-label="节点快捷分类">
              <button type="button" data-node-filter="all" aria-pressed="true">全部节点</button>
              <button type="button" data-node-filter="local" aria-pressed="false">本地发布</button>
              <button type="button" data-node-filter="source" aria-pressed="false">订阅导入</button>
              <button type="button" data-node-filter="tcp" aria-pressed="false">TCP 节点</button>
              <button type="button" data-node-filter="udp" aria-pressed="false">UDP/QUIC 节点</button>
              <button type="button" data-node-filter="disabled" aria-pressed="false">已停用</button>
            </div>
            <div class="v1-node-probe-status" id="probe-status">
              <span id="v1-node-probe-progress" role="status">上次 Google · 通过 13/39</span>
            </div>
          </div>
          <div class="v1-node-tools" id="tools">
            <div class="v1-node-filters" id="filters">
              <label class="sr-only" for="v1-node-query">搜索节点</label>
              <input type="search" id="v1-node-query" placeholder="搜索节点名称、域名或上游…">
              <label class="sr-only" for="v1-node-protocol">协议</label>
              <select id="v1-node-protocol">
                <option value="all" selected>协议：全部</option>
                <option>Shadowsocks</option>
              </select>
              <label class="sr-only" for="v1-node-source">来源</label>
              <select id="v1-node-source"><option selected>来源：全部</option><option>本地发布</option></select>
              <label class="sr-only" for="v1-node-connectivity">连通性</label>
              <select id="v1-node-connectivity">
                <option value="all" selected>连通性：全部</option>
                <option value="ok">可用</option>
                <option value="unavailable">不可用</option>
                <option value="failed">失败</option>
                <option value="untested">未测试</option>
              </select>
              <label class="sr-only" for="v1-node-sub-options">订阅选项</label>
              <select id="v1-node-sub-options">
                <option value="all" selected>订阅：全部</option>
                <option>订阅：一条相当长的上游订阅名称用于检查最大宽度</option>
              </select>
            </div>
            <div class="v1-node-actions" id="actions">
              <button type="button" class="secondary" id="v1-node-probe-settings-open">测试设置</button>
              <div class="v1-menu">
                <button class="secondary" type="button" data-menu-toggle aria-controls="v1-node-batch-menu" aria-expanded="false" id="batch-toggle">批量操作</button>
                <div id="v1-node-batch-menu" class="v1-menu-panel" role="menu" hidden>
                  <button type="button" role="menuitem">全部测试</button>
                  <button type="button" role="menuitem">测试所选节点</button>
                  <button type="button" role="menuitem">测试筛选结果</button>
                  <button type="button" role="menuitem">复制所选名称</button>
                </div>
              </div>
              <button class="secondary" type="button" id="v1-node-probe-stop">停止测试</button>
            </div>
          </div>
        </section>
      </div>
      <div class="v1-nodes-side"><div class="card" style="min-height: 80px;"></div></div>
    </div>
  </main>
  <pre id="result"></pre>
  <script>
    const KEY = ['#v1-node-sub-options', '#v1-node-probe-settings-open', '#batch-toggle', '#v1-node-probe-progress', '#v1-node-probe-stop'];
    function box(el) {
      const r = el.getBoundingClientRect();
      const range = document.createRange();
      range.selectNodeContents(el);
      const lines = el.id === 'v1-node-probe-progress' ? [...range.getClientRects()] : [...el.getClientRects()];
      return {
        top: r.top, bottom: r.bottom, left: r.left, right: r.right,
        width: r.width, height: r.height, textLines: lines.length,
        visible: r.width > 0 && r.height > 0 && r.right > r.left
      };
    }
    function intersects(a, b, slop) {
      slop = slop || 0;
      return a.left + slop < b.right && a.right - slop > b.left && a.top + slop < b.bottom && a.bottom - slop > b.top;
    }
    function measureConnectivityFit() {
      const select = document.getElementById('v1-node-connectivity');
      const style = getComputedStyle(select);
      const labels = [...select.options].map(option => option.textContent);
      const canvas = document.createElement('canvas').getContext('2d');
      canvas.font = style.font;
      const texts = labels.map(label => ({label, width: canvas.measureText(label).width}));
      texts.sort((a, b) => b.width - a.width);
      const longest = texts[0];
      const probe = select.cloneNode(true);
      probe.removeAttribute('id');
      probe.style.cssText = [
        'position:absolute', 'left:-9999px', 'top:0', 'margin:0',
        'max-width:none', 'min-width:0', 'width:max-content', 'flex:none',
        'font:' + style.font,
        'padding:' + style.padding,
        'border:' + style.border,
        'box-sizing:' + style.boxSizing
      ].join(';');
      document.body.append(probe);
      const requiredWidth = probe.getBoundingClientRect().width;
      probe.remove();
      const actual = select.getBoundingClientRect();
      const padding = (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0);
      const contentWidth = actual.width - padding;
      return {
        labels,
        longest: longest.label,
        textWidth: longest.width,
        padding,
        requiredWidth,
        actualWidth: actual.width,
        contentWidth,
        maxWidth: style.maxWidth,
        fontSize: parseFloat(style.fontSize),
        textOverflow: style.textOverflow,
        fits: actual.width + 0.5 >= requiredWidth && contentWidth + 0.5 >= longest.width
      };
    }
    function overflow(el) {
      return {
        clientWidth: el.clientWidth, scrollWidth: el.scrollWidth,
        clientHeight: el.clientHeight, scrollHeight: el.scrollHeight,
        overflowX: getComputedStyle(el).overflowX,
        overflowY: getComputedStyle(el).overflowY
      };
    }
    function placeMenu(toggle, panel) {
      const rect = toggle.getBoundingClientRect();
      const viewport = document.documentElement.clientWidth;
      const width = Math.max(220, Math.round(rect.width));
      panel.style.position = 'fixed';
      panel.style.top = Math.round(rect.bottom + 6) + 'px';
      panel.style.bottom = 'auto';
      panel.style.minWidth = width + 'px';
      let left = Math.round(rect.right - width);
      if (left < 8) left = 8;
      if (left + width > viewport - 8) left = Math.max(8, viewport - width - 8);
      panel.style.left = left + 'px';
      panel.style.right = 'auto';
    }
    function rowSpread(el) {
      const kids = [...el.children].filter(child => !child.classList.contains('sr-only') && child.getClientRects().length);
      const tops = kids.map(child => child.getBoundingClientRect().top);
      return tops.length ? Math.max(...tops) - Math.min(...tops) : 0;
    }
    function measurePlacement() {
      const board = document.getElementById('board');
      const toolbar = document.getElementById('toolbar');
      const coreFilters = document.getElementById('core-filters');
      const tools = document.getElementById('tools');
      const status = document.getElementById('probe-status');
      const progress = document.getElementById('v1-node-probe-progress');
      const stop = document.getElementById('v1-node-probe-stop');
      const boardBox = board.getBoundingClientRect();
      const toolsBox = tools.getBoundingClientRect();
      const progressBox = progress.getBoundingClientRect();
      const style = getComputedStyle(board);
      const padRight = parseFloat(style.paddingRight) || 0;
      const padTop = parseFloat(style.paddingTop) || 0;
      const padLeft = parseFloat(style.paddingLeft) || 0;
      const contentRight = boardBox.right - padRight;
      const contentTop = boardBox.top + padTop;
      const contentLeft = boardBox.left + padLeft;
      const filterButtons = [...coreFilters.querySelectorAll('button')].map(el => {
        const b = box(el);
        return {text: el.textContent, overlap: intersects(b, progressBox, 0.5), top: b.top, bottom: b.bottom, left: b.left, right: b.right};
      });
      const firstFilter = filterButtons[0];
      return {
        inToolbar: toolbar.contains(progress),
        inTools: tools.contains(progress),
        inStatus: status.contains(progress),
        stopInTools: tools.contains(stop),
        stopInToolbar: toolbar.contains(stop),
        rightGap: contentRight - progressBox.right,
        topGap: progressBox.top - contentTop,
        insideCard: progressBox.left >= contentLeft - 1 && progressBox.right <= contentRight + 1 &&
          progressBox.top >= contentTop - 1 && progressBox.bottom <= boardBox.bottom + 1,
        alignedWithFirstFilterRow: !!(firstFilter && progressBox.bottom > firstFilter.top && progressBox.top < firstFilter.bottom),
        overlapFilters: filterButtons.filter(b => b.overlap).map(b => b.text),
        overlapTools: intersects(progressBox, toolsBox, 0.5),
        board: {padRight, padTop, padLeft, top: boardBox.top, right: boardBox.right, left: boardBox.left, bottom: boardBox.bottom},
        toolbar: box(toolbar),
        status: box(status),
        filterButtons
      };
    }
    function measure() {
      const tools = document.getElementById('tools');
      const filters = document.getElementById('filters');
      const actions = document.getElementById('actions');
      const toolbar = document.getElementById('toolbar');
      const board = document.getElementById('board');
      const progress = document.getElementById('v1-node-probe-progress');
      const stop = document.getElementById('v1-node-probe-stop');
      const toggle = document.getElementById('batch-toggle');
      const panel = document.getElementById('v1-node-batch-menu');
      panel.hidden = true;
      stop.hidden = false;
      void tools.offsetHeight;
      const viewport = {w: document.documentElement.clientWidth, h: document.documentElement.clientHeight};
      const key = Object.fromEntries(KEY.map(sel => [sel, box(document.querySelector(sel))]));
      const actionSpread = rowSpread(actions);
      const toolsSpread = rowSpread(tools);
      const progressStyle = getComputedStyle(progress);
      const placement = measurePlacement();
      panel.hidden = false;
      placeMenu(toggle, panel);
      void panel.offsetHeight;
      const panelBox = panel.getBoundingClientRect();
      const items = [...panel.querySelectorAll('button')].map(el => el.getBoundingClientRect());
      const panelInViewport = panelBox.top >= 0 && panelBox.left >= 0 &&
        panelBox.right <= viewport.w + 1 && panelBox.bottom <= viewport.h + 1;
      panel.hidden = true;
      const overflowed = [board, toolbar, tools, filters, actions].some(el => el.scrollWidth > el.clientWidth + 1);
      function controlMetrics(sel) {
        const el = document.querySelector(sel);
        const s = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return {
          height: r.height,
          minHeight: s.minHeight,
          fontSize: s.fontSize,
          boxSizing: s.boxSizing,
          paddingTop: s.paddingTop,
          paddingBottom: s.paddingBottom,
          paddingLeft: s.paddingLeft,
          paddingRight: s.paddingRight
        };
      }
      const queryEl = document.getElementById('v1-node-query');
      const queryBlurHeight = queryEl.getBoundingClientRect().height;
      queryEl.focus();
      void queryEl.offsetHeight;
      const queryFocusHeight = queryEl.getBoundingClientRect().height;
      const queryFocusBorder = getComputedStyle(queryEl).borderTopWidth;
      queryEl.blur();
      void queryEl.offsetHeight;
      const queryAfterBlurHeight = queryEl.getBoundingClientRect().height;
      const controlSels = ['#v1-node-query','#v1-node-protocol','#v1-node-source','#v1-node-connectivity','#v1-node-sub-options','#v1-node-probe-settings-open','#batch-toggle','#v1-node-probe-stop'];
      return {
        viewport,
        mediaPhone: matchMedia('(max-width: 710px)').matches,
        mediaCompactFont: matchMedia('(max-width: 640px)').matches,
        toolsOverflow: overflow(tools),
        filtersOverflow: overflow(filters),
        actionsOverflow: overflow(actions),
        toolbarOverflow: overflow(toolbar),
        boardOverflow: overflow(board),
        toolsSpread, actionSpread,
        key,
        stop: box(stop),
        placement,
        progressText: progress.textContent,
        progressWhiteSpace: progressStyle.whiteSpace,
        progressFontSize: parseFloat(progressStyle.fontSize),
        progressEllipsis: progressStyle.textOverflow,
        controls: Object.fromEntries(controlSels.map(sel => [sel, controlMetrics(sel)])),
        queryBlurHeight, queryFocusHeight, queryAfterBlurHeight, queryFocusBorder,
        menu: {
          width: panelBox.width, height: panelBox.height,
          itemCount: items.length,
          itemVisible: items.every(item => item.width > 0 && item.height > 0),
          panelInViewport
        },
        overflowed,
        connectivity: measureConnectivityFit()
      };
    }
    document.getElementById('result').textContent = JSON.stringify(measure());
    document.title = 'LAYOUT_READY';
  </script>
</body>
</html>
'''


def run_chrome(chrome, html, width, height=900):
    with tempfile.TemporaryDirectory(prefix='hopworld-toolbar-layout-') as tmp:
        tmp = pathlib.Path(tmp)
        page = tmp / 'toolbar.html'
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


def assert_summary_in_card_header(test, row):
    test.assertEqual(row['progressText'], '上次 Google · 通过 13/39')
    test.assertEqual(row['progressWhiteSpace'], 'nowrap')
    test.assertNotEqual(row['progressEllipsis'], 'ellipsis')
    test.assertGreaterEqual(row['progressFontSize'], 12)
    progress = row['key']['#v1-node-probe-progress']
    test.assertEqual(progress['textLines'], 1)
    test.assertTrue(progress['visible'])
    for sel in ('#v1-node-sub-options', '#v1-node-probe-settings-open', '#batch-toggle', '#v1-node-probe-progress', '#v1-node-probe-stop'):
        test.assertTrue(row['key'][sel]['visible'], f'{sel} 不可见')
    place = row['placement']
    test.assertTrue(place['inToolbar'], '摘要应在卡片头工具栏内')
    test.assertTrue(place['inStatus'])
    test.assertFalse(place['inTools'], '摘要不应再留在搜索/筛选工具栏')
    test.assertTrue(place['stopInTools'], '停止测试应保留在操作区')
    test.assertFalse(place['stopInToolbar'])
    test.assertTrue(place['insideCard'], f'摘要超出卡片内容区: {place}')
    test.assertTrue(place['alignedWithFirstFilterRow'], f'摘要未与分类按钮首行对齐: {place}')
    test.assertEqual(place['overlapFilters'], [], f'摘要遮挡分类按钮: {place}')
    test.assertFalse(place['overlapTools'], f'摘要遮挡下方工具栏: {place}')
    test.assertGreaterEqual(place['rightGap'], -1, f'摘要溢出卡片右边缘 gap={place["rightGap"]}')
    test.assertLessEqual(place['rightGap'], 8, f'摘要未靠卡片右上角 gap={place["rightGap"]}')
    test.assertGreaterEqual(place['topGap'], -1, f'摘要溢出卡片上边缘 gap={place["topGap"]}')
    keys = row['key']
    max_top = max(keys[sel]['top'] for sel in ('#v1-node-probe-settings-open', '#batch-toggle', '#v1-node-probe-stop'))
    min_bottom = min(keys[sel]['bottom'] for sel in ('#v1-node-probe-settings-open', '#batch-toggle', '#v1-node-probe-stop'))
    test.assertGreater(min_bottom - max_top, 8, f'测试设置/批量操作/停止测试不在同一行: {keys}')
    test.assertLess(row['actionSpread'], 24, f'操作区换行 spread={row["actionSpread"]}')
    test.assertFalse(row['actionsOverflow']['scrollWidth'] > row['actionsOverflow']['clientWidth'] + 1)
    test.assertFalse(row['toolbarOverflow']['scrollWidth'] > row['toolbarOverflow']['clientWidth'] + 1)
    test.assertFalse(row['boardOverflow']['scrollWidth'] > row['boardOverflow']['clientWidth'] + 1)
    for section, payload in (
        ('tools', row['toolsOverflow']),
        ('actions', row['actionsOverflow']),
        ('toolbar', row['toolbarOverflow']),
        ('board', row['boardOverflow']),
    ):
        test.assertNotIn(payload['overflowX'], {'auto', 'scroll', 'overlay'}, f'{section} 使用了横向滚动: {payload}')
    test.assertEqual(row['menu']['itemCount'], 4)
    test.assertTrue(row['menu']['itemVisible'])
    test.assertTrue(row['menu']['panelInViewport'])
    test.assertGreater(row['menu']['height'], 80)
    assert_restored_control_sizes(test, row)


def assert_restored_control_sizes(test, row):
    """Computed sizes should match the pre-edit node-page toolbar baseline."""
    controls = row['controls']
    compact = row['mediaCompactFont']
    query = controls['#v1-node-query']
    test.assertEqual(query['boxSizing'], 'border-box')
    test.assertEqual(query['minHeight'], '36px')
    test.assertEqual(query['paddingTop'], '0px')
    test.assertEqual(query['paddingBottom'], '0px')
    if compact:
        test.assertEqual(query['fontSize'], '16px')
    else:
        test.assertEqual(query['fontSize'], '13px')
    test.assertAlmostEqual(query['height'], 36.0, delta=0.5)
    test.assertAlmostEqual(row['queryBlurHeight'], query['height'], delta=0.5)
    test.assertAlmostEqual(row['queryFocusHeight'], query['height'], delta=0.5,
                           msg=f'聚焦后搜索框高度跳动 {row["queryFocusHeight"]} vs {query["height"]} border={row.get("queryFocusBorder")}')
    test.assertAlmostEqual(row['queryAfterBlurHeight'], query['height'], delta=0.5)
    for sel in ('#v1-node-protocol', '#v1-node-source', '#v1-node-connectivity', '#v1-node-sub-options'):
        item = controls[sel]
        test.assertEqual(item['minHeight'], '36px', sel)
        test.assertEqual(item['fontSize'], '16px', sel)
        test.assertEqual(item['paddingTop'], '0px', sel)
        test.assertEqual(item['paddingBottom'], '0px', sel)
        test.assertEqual(item['paddingLeft'], '10px', sel)
        test.assertEqual(item['paddingRight'], '10px', sel)
        test.assertAlmostEqual(item['height'], 36.0, delta=0.5, msg=sel)
        test.assertAlmostEqual(
            query['height'], item['height'], delta=0.5,
            msg=f'搜索框实际高度 {query["height"]:.2f}px 与 {sel} {item["height"]:.2f}px 不一致'
        )
    button_height = 44.0 if compact else 42.0
    button_min = '44px' if compact else '42px'
    for sel in ('#v1-node-probe-settings-open', '#batch-toggle', '#v1-node-probe-stop'):
        item = controls[sel]
        test.assertEqual(item['minHeight'], button_min, sel)
        test.assertEqual(item['fontSize'], '12px', sel)
        test.assertEqual(item['paddingTop'], '10px', sel)
        test.assertEqual(item['paddingBottom'], '10px', sel)
        test.assertAlmostEqual(item['height'], button_height, delta=0.5, msg=sel)


class NodeToolsLayoutTests(unittest.TestCase):
    CONNECTIVITY_LABELS = ('连通性：全部', '可用', '不可用', '失败', '未测试')

    def assert_connectivity_label_fits(self, row):
        fit = row['connectivity']
        self.assertEqual(tuple(fit['labels']), self.CONNECTIVITY_LABELS)
        self.assertEqual(fit['longest'], '连通性：全部')
        self.assertEqual(fit['maxWidth'], 'none')
        self.assertNotEqual(fit['textOverflow'], 'ellipsis')
        self.assertGreaterEqual(fit['fontSize'], 13)
        self.assertGreater(
            fit['actualWidth'] + 0.5, fit['requiredWidth'] - 0.5,
            f'连通性选择框 {fit["actualWidth"]:.2f}px 小于完整选项所需 {fit["requiredWidth"]:.2f}px '
            f'(最长「{fit["longest"]}」文字 {fit["textWidth"]:.2f}px + padding {fit["padding"]:.2f}px)'
        )
        self.assertGreater(
            fit['contentWidth'] + 0.5, fit['textWidth'],
            f'选择框内容区 {fit["contentWidth"]:.2f}px 放不下「{fit["longest"]}」({fit["textWidth"]:.2f}px)'
        )
        self.assertTrue(fit['fits'])

    @classmethod
    def setUpClass(cls):
        cls.chrome = chrome_path()
        if not cls.chrome:
            raise unittest.SkipTest('未找到可用于布局测量的独立 Chrome/Chromium')
        cls.html = (FIXTURE
                    .replace('STYLE_HREF', 'file://' + pathname2url(str(STYLE_CSS.resolve())))
                    .replace('V1_HREF', 'file://' + pathname2url(str(V1_CSS.resolve()))))

    def test_desktop_widths_keep_toolbar_on_one_row_without_overflow(self):
        # Common desktop widths, including a two-column nodes layout (>=1641).
        for width in (1280, 1366, 1440, 1680, 1920):
            with self.subTest(width=width):
                row = run_chrome(self.chrome, self.html, width)
                self.assertFalse(row['mediaPhone'])
                self.assertLess(
                    row['toolsSpread'], 24,
                    f'{width}px 桌面工具栏分成多行 spread={row["toolsSpread"]}; '
                    f'tools scroll {row["toolsOverflow"]}'
                )
                self.assertFalse(
                    row['overflowed'],
                    f'{width}px 出现横向溢出: tools={row["toolsOverflow"]} '
                    f'filters={row["filtersOverflow"]} actions={row["actionsOverflow"]} '
                    f'toolbar={row["toolbarOverflow"]} board={row["boardOverflow"]}'
                )
                assert_summary_in_card_header(self, row)
                # 订阅 select must stay capped so a long option cannot blow the row.
                self.assertLessEqual(row['key']['#v1-node-sub-options']['width'], 140)
                self.assert_connectivity_label_fits(row)

    def test_phone_widths_keep_probe_controls_together_without_scrollbar(self):
        for width in (390, 430, 710):
            with self.subTest(width=width):
                row = run_chrome(self.chrome, self.html, width)
                self.assertTrue(row['mediaPhone'])
                assert_summary_in_card_header(self, row)
                self.assert_connectivity_label_fits(row)
                self.assertFalse(
                    row['actionsOverflow']['scrollWidth'] > row['actionsOverflow']['clientWidth'] + 1,
                    f'{width}px 操作区溢出（含停止按钮）: {row["actionsOverflow"]} stop={row["stop"]}'
                )
                self.assertFalse(
                    row['toolbarOverflow']['scrollWidth'] > row['toolbarOverflow']['clientWidth'] + 1,
                    f'{width}px 卡片头溢出: {row["toolbarOverflow"]} placement={row["placement"]}'
                )
                self.assertNotIn(row['toolsOverflow']['overflowX'], {'auto', 'scroll', 'overlay'})


if __name__ == '__main__':
    unittest.main()
