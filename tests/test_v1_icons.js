const path = require('path');
const assert = require('assert');
const icons = require(path.join(__dirname, '../router/static/v1-icons.js'));

const required = [
  'house', 'server', 'network', 'rss', 'shield', 'globe', 'activity', 'settings', 'info',
  'menu', 'calendar', 'plus', 'import', 'download', 'refresh-cw', 'gauge', 'list-filter',
  'shield-plus', 'chevron-down', 'arrow-right', 'scroll-text', 'blocks', 'circle-dot',
  'search', 'smartphone', 'arrow-left-right', 'arrow-up-down', 'earth', 'pie-chart',
  'layers', 'list-ordered', 'map-pin', 'chart-column', 'chart-line', 'radio',
  'shield-check', 'lock', 'layout-grid', 'bell', 'wrench', 'archive', 'cpu', 'check',
  'share-2', 'pencil', 'copy', 'x', 'square', 'play', 'rotate-cw', 'pause', 'arrow-down',
  'arrow-up', 'zap', 'unplug', 'eye', 'eye-off', 'circle-user', 'circle', 'file-text',
  'plus-circle', 'triangle-alert',
];

for (const name of required) {
  assert.ok(icons.PATHS[name], `missing official path: ${name}`);
  const svg = icons.svg(name, 20);
  assert.ok(svg.includes('viewBox="0 0 24 24"'), name);
  assert.ok(svg.includes('width="20"'), name);
  assert.ok(svg.includes('height="20"'), name);
  assert.ok(svg.includes('stroke-width="2"'), name);
  assert.ok(svg.includes('stroke-linecap="round"'), name);
  assert.ok(svg.includes('stroke-linejoin="round"'), name);
  assert.ok(svg.includes('stroke="currentColor"'), name);
  assert.ok(svg.includes('fill="none"'), name);
  assert.ok(svg.includes('aria-hidden="true"'), name);
  assert.ok(svg.includes('focusable="false"'), name);
  assert.ok(svg.includes(icons.PATHS[name]), name);
}

assert.strictEqual(icons.svg('not-a-real-icon', 20), '');
assert.ok(icons.svg('pencil', 24).includes('width="24"'));
assert.ok(icons.svg('pencil').includes('width="20"'), 'default size 20');
console.log(`ok ${required.length} lucide icons`);
