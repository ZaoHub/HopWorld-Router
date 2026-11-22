'use strict';

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const root = path.resolve(__dirname, '..');
const css = fs.readFileSync(path.join(root, 'router/static/v1.css'), 'utf8');
const js = fs.readFileSync(path.join(root, 'router/static/v1.js'), 'utf8');
const style = fs.readFileSync(path.join(root, 'router/static/style.css'), 'utf8');

function rule(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = css.match(new RegExp('(?:^|\\n)' + escaped + '\\s*\\{([^}]*)\\}'));
  assert(match, `missing ${selector}`);
  return match[1];
}

const head = rule('.v1-table th');
assert(/background:\s*#152437/.test(head), 'thead/generic th header fill must remain');
assert(/background:\s*transparent/.test(rule('.v1-table tbody th')),
  'body row-header names should have no independent fill, matching cores');
assert(/background:\s*transparent/.test(rule('.v1-core-table tbody th')),
  'core management name cells stay transparent');

assert(/background:\s*#13283f/.test(rule('.v1-node-table tr.is-selected th, .v1-node-table tr.is-selected td, .v1-sub-table tr.is-selected th, .v1-sub-table tr.is-selected td')),
  'selected row fill must remain');
assert(/background:\s*#13283f/.test(rule('.v1-core-table tr.is-selected th, .v1-core-table tr.is-selected td')),
  'selected core row fill must remain');
assert(/background:\s*#142c47/.test(rule('.v1-cert-list .v1-cert-current')),
  'current certificate row fill must remain');
assert(/background:\s*#142c47/.test(rule('.v1-public-management .v1-table tr.v1-public-chosen')),
  'chosen public-port row fill must remain');
assert(/background:\s*#1b3049/.test(rule('.v1-node-table .v1-icon-btn')),
  'node action button fill must remain');
assert(/\.icon-box\.blue\s*\{[^}]*background:\s*#173657/.test(css), 'identity icon-box fills must remain');

for (const source of [css, style]) {
  const rules = source.split('}');
  for (const block of rules) {
    const sel = block.split('{')[0];
    if (/(\bth\b|\btd\b|\.v1-table)/.test(sel) && /position\s*:\s*sticky/.test(block)) {
      assert.fail(`unexpected sticky table cell rule: ${sel.trim()}`);
    }
  }
}

assert(js.includes("const name = element('th');\n      name.scope = 'row';")
  || /name(?:Cell)?\.scope = 'row'/.test(js), 'name cells remain row headers');
assert(js.includes("const domainCell = element('th', host);\n      domainCell.scope = 'row';"),
  'certificate domain cells remain row headers');
assert(js.includes("const name = element('th', port.name);\n      name.scope = 'row';"),
  'public mapping name cells remain row headers');
assert(js.includes("const nameCell = element('th');\n      nameCell.scope = 'row';"),
  'core name cells remain row headers');

console.log('test_v1_table_name_background.js ok');
