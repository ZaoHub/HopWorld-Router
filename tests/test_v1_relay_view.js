'use strict';

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'router/static/index.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'router/static/v1.css'), 'utf8');
const js = fs.readFileSync(path.join(root, 'router/static/v1.js'), 'utf8');
const server = fs.readFileSync(path.join(root, 'router/server.py'), 'utf8');

function extractFunction(source, name, nextName) {
  const start = source.indexOf(`function ${name}`);
  const end = source.indexOf(`function ${nextName}`, start);
  assert(start >= 0 && end > start, `cannot extract ${name}`);
  return source.slice(start, end);
}

const relaySummary = new Function(
  `${extractFunction(js, 'relaySummary', 'paintRelayView')}; return relaySummary;`
)();

const leaked = ['super-secret-token', '-----BEGIN CERTIFICATE-----', 'PRIVATE KEY'];
const configured = relaySummary({
  configured: true,
  domain: 'nodes.example.com',
  server: '203.0.113.8',
  port: 7000,
  cores: ['sing-box'],
  token: 'super-secret-token',
  ca: '-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----',
});
assert.strictEqual(configured.token, '已配置');
assert.strictEqual(configured.ca, '已配置');
assert.strictEqual(configured.domain, 'nodes.example.com');
assert.strictEqual(configured.server, '203.0.113.8');
assert.strictEqual(configured.port, '7000');
assert.deepStrictEqual(configured.cores, ['sing-box']);
for (const secret of leaked) {
  assert(!Object.values(configured).some(value => String(value).includes(secret)),
    `summary leaked ${secret}`);
}

const empty = relaySummary({configured: false, domain: '', server: '', port: '', cores: [], token: 'x', ca: 'y'});
assert.strictEqual(empty.configured, false);
assert.strictEqual(empty.domain, '未配置');
assert.strictEqual(empty.server, '未配置');
assert.strictEqual(empty.port, '未配置');
assert.strictEqual(empty.token, '未配置');
assert.strictEqual(empty.ca, '未配置');
assert.deepStrictEqual(empty.cores, []);
assert.notStrictEqual(empty.token, 'x');
assert.notStrictEqual(empty.ca, 'y');

const paint = extractFunction(js, 'paintRelayView', 'setRelayEditing');
assert(!/status\.token|status\.ca|elements\.token|elements\.ca/.test(paint),
  'paintRelayView must not read raw token/ca');
assert(paint.includes("['FRP 令牌', view.token]"), 'token row uses summary value');
assert(paint.includes("['VPS CA 证书', view.ca]"), 'CA row uses summary value');
assert(paint.includes('尚未配置 VPS 隧道'), 'unconfigured copy should be friendly');
assert(paint.includes("byId('v1-relay-setup')") && paint.includes('setup.hidden = false'),
  'unconfigured state should expose the setup entry');

assert(/<form id="v1-relay-form" class="v1-relay-form" hidden>/.test(html),
  'VPS form must be hidden by default');
assert(/id="v1-relay-view"/.test(html), 'read-only view container missing');
assert(/id="v1-relay-edit"[\s\S]*编辑/.test(html), 'Edit button missing');
assert(/id="v1-relay-setup"[\s\S]*配置 VPS 中转/.test(html), 'unconfigured setup entry missing');
assert(/id="v1-relay-cancel"[\s\S]*取消/.test(html), 'Cancel button missing');
assert(/id="v1-relay-save"[\s\S]*保存 VPS 配置/.test(html), 'Save button should remain on the form');
assert(!/<textarea[^>]*id="v1-relay-view/.test(html) && !html.includes('id="v1-relay-view-ca"'),
  'view must not include a CA textarea');
assert(/id="v1-relay-edit"[\s\S]{0,200}viewBox="0 0 24 24"/.test(html),
  'Edit button should use official SVG');

assert(/#v1-relay-form\[hidden\], #v1-relay-view\[hidden\]\s*\{\s*display:\s*none;/.test(css),
  'hidden form must override display:grid');

assert(js.includes('if (!(relayEditing && !initialize))'),
  'renderRelay must skip form writes while editing unless resetting');
assert(js.includes("byId('v1-relay-edit').addEventListener('click', () => {\n    setRelayEditing(true, {resetForm: true, focus: true});"),
  'Edit should only enter edit and restore saved values');
assert(js.includes("byId('v1-relay-setup').addEventListener('click', () => {\n    setRelayEditing(true, {resetForm: true, focus: true});"),
  'Setup entry should enter edit without saving');
assert(js.includes("byId('v1-relay-cancel').addEventListener('click', () => {\n    setRelayEditing(false);"),
  'Cancel should leave edit without saving');
assert(js.includes('if (document.hidden && relayEditing) setRelayEditing(false)'),
  'hiding the page should return to read-only without saving');
assert(js.includes("if (page !== next && page === 'public' && relayEditing) setRelayEditing(false)"),
  'leaving the public page should return to read-only without saving');
assert(js.includes("if (current === 'vps' && tab !== 'vps' && relayEditing) setRelayEditing(false)"),
  'leaving the VPS tab should return to read-only without saving');
assert(js.includes("if (!data.action && relayEditing) setRelayEditing(false, {notice: savedNote})"),
  'successful save should return to read-only');
assert(js.includes('relayFeedback.textContent = error.message'),
  'failed save should keep the existing feedback prompt');
assert(js.includes('catch (error)') && js.includes('relayFeedback.focus()'),
  'failed save should stay on the form');

const editClick = js.slice(js.indexOf("byId('v1-relay-edit').addEventListener"),
  js.indexOf("byId('v1-relay-setup').addEventListener"));
assert(!editClick.includes('changeRelay') && !editClick.includes("call('/api/v1/relay'"),
  'clicking Edit must not save or POST');

const leaveFn = extractFunction(js, 'setRelayEditing', 'renderPublicAccess');
assert(leaveFn.includes('if (leaving && relay) renderRelay(relay, true)'),
  'leaving edit should discard the draft and restore the saved config');

// Polling draft-protection simulation: same skip predicate as renderRelay.
function pollWouldWriteForm(relayEditing, hasRelay, resetForm) {
  const initialize = !hasRelay || resetForm;
  return !(relayEditing && !initialize);
}
assert.strictEqual(pollWouldWriteForm(true, true, false), false, 'poll while editing must not write the form');
assert.strictEqual(pollWouldWriteForm(true, true, true), true, 'enter-edit reset is allowed to refill the form');
assert.strictEqual(pollWouldWriteForm(false, true, false), true, 'view-mode render may update a hidden form');
assert.strictEqual(pollWouldWriteForm(false, false, false), true, 'first load may fill the form');

assert(js.includes('async function changeRelay(data)') && js.includes("await call('/api/v1/relay', data)"),
  'save must still reuse changeRelay / POST /api/v1/relay');
assert(!js.includes("call('/api/v1/relay'") || js.includes("await call('/api/v1/relay', data)") && js.includes("await call('/api/v1/relay')"),
  'only existing GET/POST relay endpoints should be used');

const posts = [...js.matchAll(/call\('\/api\/v1\/relay'([^)]*)\)/g)].map(item => item[0]);
assert(posts.includes("call('/api/v1/relay')"), 'GET refresh remains');
assert(posts.includes("call('/api/v1/relay', data)"), 'POST save remains');
assert(posts.length === 2, `unexpected extra /api/v1/relay calls: ${posts.join(', ')}`);

assert(server.includes("if path == '/api/v1/relay':") &&
  server.includes('return self.json(200, store.relay_status())') &&
  server.includes("elif path == '/api/v1/relay':") &&
  server.includes('return self.json(200, store.configure_relay(data))'),
  'GET/POST /api/v1/relay handlers should be unchanged');
const statusReturned = server.match(/def relay_status\(self\):[\s\S]*?return \{([\s\S]*?)\n            \}/);
assert(statusReturned, 'relay_status return payload not found');
assert.strictEqual(/['"]token['"]/.test(statusReturned[1]), false, 'relay_status must still omit the token');
assert.strictEqual(/['"]ca['"]/.test(statusReturned[1]), false, 'relay_status must still omit the CA');
assert(statusReturned[1].includes("'configured': bool(settings)"), 'configured flag should remain');

console.log('test_v1_relay_view.js ok');
