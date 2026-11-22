const fs = require('fs');
const path = require('path');
const assert = require('assert');

const root = path.join(__dirname, '..');
const icons = require(path.join(root, 'router/static/v1-icons.js'));
const v1 = fs.readFileSync(path.join(root, 'router/static/v1.js'), 'utf8');

function extractMetricsCalls(source) {
  const found = [];
  const re = /metrics\(\s*'([^']+)'\s*,\s*\[/g;
  let match;
  while ((match = re.exec(source))) {
    const box = match[1];
    const start = match.end ? match.end - 1 : match.index + match[0].length - 1;
    let depth = 0;
    let end = -1;
    for (let i = start; i < source.length; i++) {
      const ch = source[i];
      if (ch === "'" || ch === '"') {
        const q = ch;
        i++;
        while (i < source.length) {
          if (source[i] === '\\') { i += 2; continue; }
          if (source[i] === q) break;
          i++;
        }
        continue;
      }
      if (source[i] === '`') {
        i++;
        while (i < source.length) {
          if (source[i] === '\\') { i += 2; continue; }
          if (source[i] === '`') break;
          i++;
        }
        continue;
      }
      if (source[i] === '[') depth++;
      else if (source[i] === ']') {
        depth--;
        if (depth === 0) { end = i; break; }
      }
    }
    if (end < 0) throw new Error('unclosed metrics array for ' + box);
    const block = source.slice(start, end + 1);
    found.push({ box, block, entries: parseEntries(block, box) });
  }
  return found;
}

function parseEntries(block, box) {
  const entries = [];
  let i = 1;
  const n = block.length;
  while (i < n) {
    while (i < n && /[\s,]/.test(block[i])) i++;
    if (i >= n || block[i] === ']') break;
    if (block[i] !== '[') { i++; continue; }
    let depth = 0;
    let inS = false, q = '', esc = false;
    const s = i;
    for (let k = i; k < n; k++) {
      const ch = block[k];
      if (inS) {
        if (esc) { esc = false; continue; }
        if (ch === '\\') { esc = true; continue; }
        if (ch === q) inS = false;
        continue;
      }
      if (ch === "'" || ch === '"') { inS = true; q = ch; continue; }
      if (ch === '`') {
        k++;
        while (k < n) {
          if (block[k] === '\\') { k += 2; continue; }
          if (block[k] === '`') break;
          k++;
        }
        continue;
      }
      if (ch === '[' || ch === '(') depth++;
      else if (ch === ']' || ch === ')') {
        depth--;
        if (ch === ']' && depth === 0) {
          entries.push(splitEntry(block.slice(s, k + 1), box));
          i = k + 1;
          break;
        }
      }
    }
  }
  return entries;
}

function splitEntry(entry, box) {
  const parts = [];
  let depth = 0, inS = false, q = '', esc = false, start = 1;
  const body = entry;
  for (let k = 1; k < body.length - 1; k++) {
    const ch = body[k];
    if (inS) {
      if (esc) { esc = false; continue; }
      if (ch === '\\') { esc = true; continue; }
      if (ch === q) inS = false;
      continue;
    }
    if (ch === "'" || ch === '"') { inS = true; q = ch; continue; }
    if (ch === '`') {
      k++;
      while (k < body.length - 1) {
        if (body[k] === '\\') { k += 2; continue; }
        if (body[k] === '`') break;
        k++;
      }
      continue;
    }
    if (ch === '[' || ch === '(') depth++;
    else if (ch === ']' || ch === ')') depth--;
    else if (ch === ',' && depth === 0) {
      parts.push(body.slice(start, k).trim());
      start = k + 1;
    }
  }
  parts.push(body.slice(start, body.length - 1).trim());
  const labelRaw = parts[0] || '';
  const label = (labelRaw.match(/^'((?:\\'|[^'])*)'$/) || [])[1] || labelRaw;
  const iconRaw = parts[2];
  if (iconRaw == null) {
    throw new Error(`${box} entry ${label} is missing the icon slot in [label, value, icon, tone?]`);
  }
  const iconMatch = iconRaw.match(/^'((?:\\'|[^'])*)'$/);
  return {
    box,
    label,
    value: parts[1],
    iconKey: iconMatch ? iconMatch[1] : iconRaw,
    iconLiteral: !!iconMatch,
    tone: parts[3] || null,
    raw: entry,
  };
}

function extractCallKeys(source, fn) {
  const keys = [];
  const re = new RegExp(fn + "\\(\\s*'([^']+)'", 'g');
  let m;
  while ((m = re.exec(source))) keys.push(m[1]);
  const tern = new RegExp(fn + "\\(\\s*[^'?)\\n]+\\?\\s*'([^']+)'\\s*:\\s*'([^']+)'", 'g');
  while ((m = tern.exec(source))) keys.push(m[1], m[2]);
  return keys;
}

function assertIconRenders(key, where) {
  const resolved = icons.resolve(key);
  assert.ok(resolved, `${where}: resolve(${JSON.stringify(key)}) was empty`);
  const svg = icons.svg(key, 24);
  assert.ok(svg && svg.includes('<svg'), `${where}: key ${JSON.stringify(key)} resolved to ${JSON.stringify(resolved)} but produced empty SVG`);
  assert.ok(svg.includes('viewBox="0 0 24 24"'), `${where}: ${key} missing viewBox`);
  assert.ok(svg.includes(icons.PATHS[resolved]), `${where}: ${key} SVG missing official path`);
  assert.notStrictEqual(svg, '');
  return svg;
}

const calls = extractMetricsCalls(v1);
assert.ok(calls.length >= 8, 'expected multiple metrics() call sites, got ' + calls.length);

const required = [
  { box: 'v1-sub-metrics', label: '异常节点' },
  { box: 'v1-cert-metrics', label: '即将到期' },
  { box: 'v1-public-metrics', label: '通道延迟（VPS）' },
  { box: 'v1-public-metrics', label: '中转流量（今日）' },
  { box: 'v1-traffic-metrics', label: '今日流量' },
];

let total = 0;
const seen = [];
for (const call of calls) {
  assert.ok(call.entries.length, `${call.box} has no parsed metric entries`);
  for (const entry of call.entries) {
    total++;
    assert.ok(entry.iconLiteral, `${call.box} ${entry.label} icon is not a string literal: ${entry.iconKey}`);
    const svg = assertIconRenders(entry.iconKey, `${call.box} ${entry.label}`);
    seen.push({ box: call.box, label: entry.label, icon: entry.iconKey, resolved: icons.resolve(entry.iconKey), length: svg.length });
  }
}

for (const item of required) {
  const hit = seen.find((row) => row.box === item.box && row.label === item.label);
  assert.ok(hit, `missing required metrics case ${item.box} ${item.label}`);
  assert.ok(hit.length > 0, `${item.label} SVG was empty`);
}

// Also consume tile / detailHeading / iconButton keys the same way
const otherKeys = [
  ...extractCallKeys(v1, 'tile'),
  ...extractCallKeys(v1, 'detailHeading'),
  ...extractCallKeys(v1, 'iconButton'),
];
for (const key of otherKeys) {
  assertIconRenders(key, `${key} from tile/detailHeading/iconButton`);
}

// loops in subscription tools
assert.ok(v1.includes("['pencil', '设置 ', 'edit']"));
assert.ok(v1.includes("['refresh-cw', '更新 ', 'refresh']"));
assert.ok(v1.includes("['x', '删除 ', 'delete']"));
assert.ok(v1.includes("['refresh-cw', '立即更新'"));
assert.ok(v1.includes("['gauge', '测试节点'"));
assert.ok(v1.includes("['arrow-right', '查看节点'"));
for (const key of ['pencil', 'refresh-cw', 'x', 'gauge', 'arrow-right']) {
  assertIconRenders(key, 'subscription loop ' + key);
}

assert.strictEqual(icons.resolve('updown'), 'arrow-up-down');
assert.ok(icons.svg('updown', 24).includes('viewBox="0 0 24 24"'));
assert.ok(icons.PATHS['triangle-alert']);

console.log(`ok ${calls.length} metrics() sites, ${total} entries, ${otherKeys.length} tile/heading/button keys`);
for (const item of required) {
  const hit = seen.find((row) => row.box === item.box && row.label === item.label);
  console.log(`ok ${item.box} ${item.label} -> ${hit.icon} (${hit.length} bytes)`);
}


// Settings category buttons and leftover static decorative spans must be official SVGs.
const html = fs.readFileSync(path.join(root, 'router/static/index.html'), 'utf8');
const tabRe = /<button type="button" data-settings-tab="([^"]+)"[^>]*>([\s\S]*?)<\/button>/g;
const tabs = [];
let tm;
while ((tm = tabRe.exec(html))) tabs.push({ key: tm[1], body: tm[2], text: tm[2].replace(/<[^>]+>/g, '').trim() });
assert.strictEqual(tabs.length, 7, 'expected 7 settings category buttons, got ' + tabs.length);
const expectedTabs = {
  base: '基础设置',
  network: '网络设置',
  'network-security': '网络安全',
  security: '安全与权限',
  services: '服务与功能',
  alerts: '通知告警',
  maintenance: '系统维护',
};
for (const tab of tabs) {
  assert.strictEqual(tab.text, expectedTabs[tab.key], tab.key);
  assert.ok(tab.body.includes('<svg class="v1-icon"'), `${tab.key} ${tab.text} is missing an official Lucide SVG`);
  assert.ok(!/[▱♧♙▥⧉ϟ]/.test(tab.body), `${tab.key} still has leftover unicode icon`);
  const svgMatch = tab.body.match(/<svg class="v1-icon"[^>]*>([\s\S]*?)<\/svg>/);
  assert.ok(svgMatch, tab.key);
  const inner = svgMatch[1];
  const official = Object.values(icons.PATHS).some((path) => path === inner);
  assert.ok(official, `${tab.key} SVG inner is not an official Lucide path`);
}
assert.ok(html.includes('data-settings-tab="services"') && html.includes('服务与功能'));
assert.ok(html.includes('>通知告警</button>') || html.includes('通知告警</button>'));
for (const [label, name] of [['服务与功能', 'layers'], ['通知告警', 'bell'], ['通知与告警', 'bell'], ['管理员与安全', 'user-round-cog'], ['资源与限额', 'gauge']]) {
  assert.ok(icons.PATHS[name], name);
  const idx = html.indexOf(label);
  assert.ok(idx > 0, label);
  // nearest preceding svg inner should be the official icon
}
assert.ok(!html.includes('aria-hidden="true">▱<') && !html.includes('aria-hidden="true">♧<') && !html.includes('aria-hidden="true">♙<'));
console.log('ok 7 settings tabs use official Lucide SVGs, including 服务与功能=layers and 通知告警=bell');
