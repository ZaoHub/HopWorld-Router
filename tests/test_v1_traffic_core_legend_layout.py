"""Layout measurements for the traffic-page collected-share legend (percent stays on one line)."""

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
  <aside class="v1-sidebar" aria-hidden="true"><nav id="v1-navigation"><a href="#traffic">流量监控</a></nav></aside>
  <main class="page v1-page">
    <div data-v1-view="traffic">
      <div class="v1-traffic-split v1-traffic-primary">
        <section class="card" id="chart-card">
          <div class="v1-panel-head"><h2>实时流量曲线</h2></div>
          <div class="v1-traffic-plot" style="min-height: 160px;"></div>
        </section>
        <section class="card" id="share-card">
          <div class="v1-panel-head"><h2><span aria-hidden="true">◎</span> 已采集流量（今日）</h2></div>
          <div class="v1-traffic-share" id="share">
            <div class="v1-traffic-donut has-traffic" id="v1-traffic-donut"><div><strong id="v1-traffic-donut-total">1.23 GB</strong><small>已采集合计</small></div></div>
            <ul id="v1-traffic-cores" class="v1-traffic-core-list" aria-label="今日已采集流量占比">
              <li data-core="xray-core"><i class="v1-traffic-core-dot" aria-hidden="true"></i><span>Xray-core</span><span>0.0%</span><span>0 B</span></li>
              <li data-core="sing-box" id="sing-box-row"><i class="v1-traffic-core-dot" aria-hidden="true"></i><span>sing-box</span><span>100.0%</span><span>10.88 MB</span></li>
              <li data-core="mihomo"><i class="v1-traffic-core-dot" aria-hidden="true"></i><span>mihomo</span><span>99.9%</span><span>12.34 MB</span></li>
              <li data-core="hysteria2"><i class="v1-traffic-core-dot" aria-hidden="true"></i><span>Hysteria 2</span><span>0.1%</span><span>1.02 KB</span></li>
              <li data-core="shadowsocks-rust"><i class="v1-traffic-core-dot" aria-hidden="true"></i><span>shadowsocks-rust 超长名称用于检测换行后是否与数值列重叠</span><span>0.0%</span><span>—</span></li>
              <li data-core="tuic-server"><i class="v1-traffic-core-dot" aria-hidden="true"></i><span>TUIC Server</span><span>—</span><span>102.39 GB</span></li>
            </ul>
          </div>
        </section>
      </div>
    </div>
  </main>
  <pre id="result"></pre>
  <script>
    function box(el) {
      const r = el.getBoundingClientRect();
      return {top: r.top, bottom: r.bottom, left: r.left, right: r.right, width: r.width, height: r.height};
    }
    function overlap(a, b) {
      return a.left < b.right - 0.5 && a.right > b.left + 0.5 && a.top < b.bottom - 0.5 && a.bottom > b.top + 0.5;
    }
    function textLines(el) {
      const range = document.createRange();
      range.selectNodeContents(el);
      return [...range.getClientRects()].filter(r => r.width > 0 && r.height > 0).length;
    }
    function overflow(el) {
      const s = getComputedStyle(el);
      return {
        clientWidth: el.clientWidth, scrollWidth: el.scrollWidth,
        overflowX: s.overflowX, overflowY: s.overflowY
      };
    }
    function measureRow(li) {
      const name = li.children[1];
      const pct = li.children[2];
      const amount = li.children[3];
      const nameBox = box(name), pctBox = box(pct), amountBox = box(amount);
      const pctStyle = getComputedStyle(pct);
      const amountStyle = getComputedStyle(amount);
      return {
        core: li.dataset.core,
        name: name.textContent,
        percent: pct.textContent,
        amount: amount.textContent,
        percentLines: textLines(pct),
        amountLines: textLines(amount),
        percentWhiteSpace: pctStyle.whiteSpace,
        percentFontSize: parseFloat(pctStyle.fontSize),
        percentEllipsis: pctStyle.textOverflow,
        percentOverflow: pctStyle.overflow,
        nameBox, pctBox, amountBox,
        overlapNamePercent: overlap(nameBox, pctBox),
        overlapPercentAmount: overlap(pctBox, amountBox),
        overlapNameAmount: overlap(nameBox, amountBox),
        percentClipped: pct.scrollWidth > pct.clientWidth + 1,
        amountClipped: amount.scrollWidth > amount.clientWidth + 1
      };
    }
    function measure() {
      const card = document.getElementById('share-card');
      const list = document.getElementById('v1-traffic-cores');
      const share = document.getElementById('share');
      const rows = [...list.querySelectorAll('li')].map(measureRow);
      const listBox = box(list);
      const cardBox = box(card);
      for (const row of rows) {
        row.percentInside = row.pctBox.left >= listBox.left - 1 && row.pctBox.right <= listBox.right + 1;
        row.amountInside = row.amountBox.left >= listBox.left - 1 && row.amountBox.right <= listBox.right + 1;
        row.nameInside = row.nameBox.left >= listBox.left - 1 && row.nameBox.right <= listBox.right + 1;
      }
      return {
        viewport: {w: document.documentElement.clientWidth, h: document.documentElement.clientHeight},
        media690: matchMedia('(max-width: 690px)').matches,
        media1080: matchMedia('(max-width: 1080px)').matches,
        cardWidth: cardBox.width,
        listWidth: listBox.width,
        rows,
        listOverflow: overflow(list),
        shareOverflow: overflow(share),
        cardOverflow: overflow(card)
      };
    }
    document.getElementById('result').textContent = JSON.stringify(measure());
    document.title = 'LAYOUT_READY';
  </script>
</body>
</html>
'''


def run_chrome(chrome, html, width, height=900):
    with tempfile.TemporaryDirectory(prefix='hopworld-traffic-legend-') as tmp:
        tmp = pathlib.Path(tmp)
        page = tmp / 'legend.html'
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


def assert_legend_layout(test, row, width):
    test.assertGreater(row['cardWidth'], 0, f'{width}px 卡片宽度为 0')
    test.assertGreater(row['listWidth'], 0)
    for section, payload in (
        ('list', row['listOverflow']),
        ('share', row['shareOverflow']),
        ('card', row['cardOverflow']),
    ):
        test.assertFalse(
            payload['scrollWidth'] > payload['clientWidth'] + 1,
            f'{width}px {section} 横向溢出: {payload} card={row["cardWidth"]} list={row["listWidth"]}'
        )
        test.assertNotIn(payload['overflowX'], {'auto', 'scroll', 'overlay'}, f'{width}px {section} 使用了横向滚动')

    percent_rows = [item for item in row['rows'] if item['percent'].endswith('%')]
    test.assertTrue(any(item['percent'] == '100.0%' and item['core'] == 'sing-box' for item in percent_rows))
    test.assertGreaterEqual(len(percent_rows), 4)

    min_font = 12 if row['media690'] else 13
    for item in row['rows']:
        if item['percent'].endswith('%'):
            test.assertEqual(item['percentLines'], 1, f'{width}px {item["core"]} 百分比换行: {item["percent"]!r} lines={item["percentLines"]}')
            test.assertEqual(item['percentWhiteSpace'], 'nowrap', item['core'])
            test.assertNotEqual(item['percentEllipsis'], 'ellipsis', item['core'])
            test.assertGreaterEqual(item['percentFontSize'], min_font, f'{width}px {item["core"]} 字号过小 {item["percentFontSize"]}')
            test.assertFalse(item['percentClipped'], f'{width}px {item["core"]} 百分比被裁切')
        test.assertEqual(item['amountLines'], 1, f'{width}px {item["core"]} 流量值换行: {item["amount"]!r}')
        test.assertFalse(item['amountClipped'], f'{width}px {item["core"]} 流量值被裁切')
        test.assertFalse(item['overlapNamePercent'], f'{width}px {item["core"]} 名称与百分比重叠: {item}')
        test.assertFalse(item['overlapPercentAmount'], f'{width}px {item["core"]} 百分比与流量重叠: {item}')
        test.assertFalse(item['overlapNameAmount'], f'{width}px {item["core"]} 名称与流量重叠: {item}')
        test.assertTrue(item['percentInside'], f'{width}px {item["core"]} 百分比超出列表: {item}')
        test.assertTrue(item['amountInside'], f'{width}px {item["core"]} 流量值超出列表: {item}')
        test.assertTrue(item['nameInside'], f'{width}px {item["core"]} 名称超出列表: {item}')

    test.assertGreaterEqual(len(row['rows']), 2, f'{width}px 需要跨行比较列边界')
    percent_rights = [item['pctBox']['right'] for item in row['rows']]
    amount_rights = [item['amountBox']['right'] for item in row['rows']]
    percent_lefts = [item['pctBox']['left'] for item in row['rows']]
    amount_lefts = [item['amountBox']['left'] for item in row['rows']]
    test.assertLessEqual(
        max(percent_rights) - min(percent_rights), 1,
        f'{width}px 百分比列右边界不一致: {list(zip((item["core"] for item in row["rows"]), (item["percent"] for item in row["rows"]), percent_rights))}'
    )
    test.assertLessEqual(
        max(percent_lefts) - min(percent_lefts), 1,
        f'{width}px 百分比列左边界不一致: {list(zip((item["core"] for item in row["rows"]), (item["percent"] for item in row["rows"]), percent_lefts))}'
    )
    test.assertLessEqual(
        max(amount_rights) - min(amount_rights), 1,
        f'{width}px 流量列右边界不一致: {list(zip((item["core"] for item in row["rows"]), (item["amount"] for item in row["rows"]), amount_rights))}'
    )
    test.assertLessEqual(
        max(amount_lefts) - min(amount_lefts), 1,
        f'{width}px 流量列左边界不一致: {list(zip((item["core"] for item in row["rows"]), (item["amount"] for item in row["rows"]), amount_lefts))}'
    )
    # 0.0% vs 100.0% and 0 B vs 10.88 MB must share the same column edges, not shrink-wrap per row.
    by_core = {item['core']: item for item in row['rows']}
    test.assertIn('xray-core', by_core)
    test.assertIn('sing-box', by_core)
    test.assertEqual(by_core['xray-core']['percent'], '0.0%')
    test.assertEqual(by_core['xray-core']['amount'], '0 B')
    test.assertEqual(by_core['sing-box']['percent'], '100.0%')
    test.assertEqual(by_core['sing-box']['amount'], '10.88 MB')
    test.assertLessEqual(abs(by_core['sing-box']['pctBox']['right'] - by_core['xray-core']['pctBox']['right']), 1)
    test.assertLessEqual(abs(by_core['sing-box']['amountBox']['right'] - by_core['xray-core']['amountBox']['right']), 1)


class TrafficCoreLegendLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chrome = chrome_path()
        if not cls.chrome:
            raise unittest.SkipTest('未找到可用于布局测量的独立 Chrome/Chromium')
        css = V1_CSS.read_text(encoding='utf-8')
        cls.css = css
        cls.html = (FIXTURE
                    .replace('STYLE_HREF', 'file://' + pathname2url(str(STYLE_CSS.resolve())))
                    .replace('V1_HREF', 'file://' + pathname2url(str(V1_CSS.resolve()))))

    def test_css_gives_percent_column_room_and_nowrap(self):
        self.assertIn('grid-template-columns: 14px minmax(0,1fr) 7ch 9ch', self.css)
        self.assertIn('.v1-traffic-core-list li > :nth-child(n+3) { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }', self.css)
        self.assertNotIn('grid-template-columns: 14px minmax(0,1fr) 5ch 8ch', self.css)
        self.assertNotIn('grid-template-columns: 14px minmax(0,1fr) max-content max-content', self.css)
        self.assertNotIn('grid-template-columns: 10px minmax(0,1fr) max-content max-content', self.css)
        self.assertNotIn('.v1-traffic-core-list span { overflow-wrap: anywhere; }', self.css)
        self.assertIn('.v1-traffic-core-list li > :nth-child(2) { min-width: 0; overflow-wrap: anywhere; }', self.css)
        self.assertIn('grid-template-columns: 10px minmax(0,1fr) 7ch 9ch', self.css)
        self.assertIn('minmax(min-content,1fr)', self.css)
        self.assertNotIn('grid-template-columns: minmax(110px,182px) minmax(0,1fr)', self.css)

    def test_desktop_and_narrow_keep_percent_on_one_line_without_overlap(self):
        # Desktop two-column share cards, the 1080 stacking breakpoint, and common phone widths.
        for width in (1920, 1440, 1280, 1080, 690, 430, 390):
            with self.subTest(width=width):
                row = run_chrome(self.chrome, self.html, width)
                if width > 1080:
                    self.assertFalse(row['media1080'])
                if width <= 690:
                    self.assertTrue(row['media690'])
                assert_legend_layout(self, row, width)


if __name__ == '__main__':
    unittest.main()
