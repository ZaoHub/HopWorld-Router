#!/usr/bin/env node
'use strict';

const path = require('path');
const assert = require('assert');
const secret = require(path.join(__dirname, '../router/static/v1-overview-secret.js'));

function createClock() {
  let now = 0;
  const timers = [];
  let nextId = 1;
  return {
    now: () => now,
    setTimeout(fn, delay) {
      const id = nextId++;
      timers.push({id, at: now + delay, fn});
      return id;
    },
    clearTimeout(id) {
      const index = timers.findIndex(item => item.id === id);
      if (index >= 0) timers.splice(index, 1);
    },
    get pending() { return timers.length; },
    advance(ms) {
      const target = now + ms;
      while (true) {
        timers.sort((a, b) => a.at - b.at || a.id - b.id);
        const next = timers.find(item => item.at <= target);
        if (!next) { now = target; return; }
        now = next.at;
        const index = timers.indexOf(next);
        if (index >= 0) timers.splice(index, 1);
        next.fn();
      }
    },
  };
}

function createController() {
  const clock = createClock();
  const changes = [];
  const api = secret.createOverviewSecrets({
    now: clock.now,
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
    onChange: id => changes.push(id),
  });
  return {clock, changes, api};
}

function leaked(haystack, value) {
  return String(haystack).includes(String(value));
}

const CERT = 'overview-cert-domain';
const ENTRY = 'overview-access-entry';
const SAMPLE_DOMAIN = 'hidden.example.test';
const SAMPLE_ENTRY = '192.0.2.10';
const OTHER_DOMAIN = 'mask-me.example.test';
let passed = 0;

function test(name, fn) {
  fn();
  passed += 1;
  console.log('ok', name);
}

test('placeholders are not masked', () => {
  for (const value of [null, undefined, '', '—', '未配置', '待配置', '未获取']) {
    assert.strictEqual(secret.isSecretValue(value), false, String(value));
    assert.strictEqual(secret.displayValue(value, false), value == null || value === '' ? '—' : String(value).trim());
  }
  assert.strictEqual(secret.isSecretValue(SAMPLE_DOMAIN), true);
});

test('initial mask hides sample values and does not leak in view metadata', () => {
  const {api} = createController();
  const snap = api.setValue(CERT, SAMPLE_DOMAIN);
  assert.strictEqual(snap.revealed, false);
  assert.strictEqual(snap.display, secret.MASK);
  assert.ok(!leaked(snap.display, SAMPLE_DOMAIN));
  const view = secret.viewState('证书域名', snap);
  assert.strictEqual(view.text, secret.MASK);
  assert.strictEqual(view.button.ariaLabel, '显示证书域名');
  assert.strictEqual(view.button.ariaPressed, 'false');
  assert.ok(!leaked(JSON.stringify(view), SAMPLE_DOMAIN));
});

test('placeholder fields stay visible and have no toggle', () => {
  const {api} = createController();
  const snap = api.setValue(CERT, '未配置');
  assert.strictEqual(snap.maskable, false);
  assert.strictEqual(snap.display, '未配置');
  const view = secret.viewState('证书域名', snap);
  assert.strictEqual(view.button, null);
  assert.strictEqual(api.toggle(CERT).display, '未配置');
});

test('click reveals the value and starts a fresh window', () => {
  const {api, clock} = createController();
  api.setValue(CERT, SAMPLE_DOMAIN);
  const snap = api.toggle(CERT);
  assert.strictEqual(snap.revealed, true);
  assert.strictEqual(snap.display, SAMPLE_DOMAIN);
  const view = secret.viewState('证书域名', snap);
  assert.strictEqual(view.button.ariaLabel, '隐藏证书域名');
  assert.strictEqual(view.button.ariaPressed, 'true');
  assert.ok(!leaked(view.button.ariaLabel, SAMPLE_DOMAIN));
  clock.advance(1);
  assert.strictEqual(api.snapshot(CERT).revealed, true);
});

test('4999ms still revealed, 5000ms hides by deadline', () => {
  const {api, clock} = createController();
  api.setValue(CERT, SAMPLE_DOMAIN);
  api.toggle(CERT);
  clock.advance(4999);
  assert.strictEqual(api.snapshot(CERT).revealed, true);
  assert.strictEqual(api.snapshot(CERT).display, SAMPLE_DOMAIN);
  clock.advance(1);
  const snap = api.snapshot(CERT);
  assert.strictEqual(snap.revealed, false);
  assert.strictEqual(snap.display, secret.MASK);
  assert.strictEqual(snap.until, null);
});

test('clicking the eye hides early; a later reveal starts a new 5s window', () => {
  const {api, clock} = createController();
  api.setValue(CERT, SAMPLE_DOMAIN);
  api.toggle(CERT);
  clock.advance(1200);
  assert.strictEqual(api.toggle(CERT).revealed, false);
  clock.advance(4000);
  assert.strictEqual(api.snapshot(CERT).revealed, false);
  api.toggle(CERT);
  clock.advance(4999);
  assert.strictEqual(api.snapshot(CERT).revealed, true);
  clock.advance(1);
  assert.strictEqual(api.snapshot(CERT).revealed, false);
});

test('fields have independent state and timers', () => {
  const {api, clock} = createController();
  api.setValue(CERT, SAMPLE_DOMAIN);
  api.setValue(ENTRY, SAMPLE_ENTRY);
  api.toggle(CERT);
  clock.advance(2000);
  api.toggle(ENTRY);
  clock.advance(3000);
  assert.strictEqual(api.snapshot(CERT).revealed, false);
  assert.strictEqual(api.snapshot(CERT).display, secret.MASK);
  assert.strictEqual(api.snapshot(ENTRY).revealed, true);
  assert.strictEqual(api.snapshot(ENTRY).display, SAMPLE_ENTRY);
  clock.advance(2000);
  assert.strictEqual(api.snapshot(ENTRY).revealed, false);
  assert.strictEqual(api.snapshot(ENTRY).display, secret.MASK);
});

test('refresh while hidden does not leak or start a reveal', () => {
  const {api, clock} = createController();
  api.setValue(CERT, SAMPLE_DOMAIN);
  const before = api.snapshot(CERT);
  const refreshed = api.setValue(CERT, OTHER_DOMAIN);
  assert.strictEqual(refreshed.revealed, false);
  assert.strictEqual(refreshed.display, secret.MASK);
  assert.ok(!leaked(refreshed.display, SAMPLE_DOMAIN));
  assert.ok(!leaked(refreshed.display, OTHER_DOMAIN));
  assert.strictEqual(before.until, null);
  assert.strictEqual(refreshed.until, null);
  clock.advance(5000);
  assert.strictEqual(api.snapshot(CERT).revealed, false);
  assert.strictEqual(api.snapshot(CERT).display, secret.MASK);
});

test('refresh while revealed does not renew the deadline; expiry still hides', () => {
  const {api, clock} = createController();
  api.setValue(CERT, SAMPLE_DOMAIN);
  api.toggle(CERT);
  const until = api.snapshot(CERT).until;
  clock.advance(2000);
  const refreshed = api.setValue(CERT, OTHER_DOMAIN);
  assert.strictEqual(refreshed.until, until);
  assert.strictEqual(refreshed.revealed, true);
  assert.strictEqual(refreshed.display, OTHER_DOMAIN);
  clock.advance(2999);
  assert.strictEqual(api.snapshot(CERT).revealed, true);
  clock.advance(1);
  const ended = api.snapshot(CERT);
  assert.strictEqual(ended.revealed, false);
  assert.strictEqual(ended.display, secret.MASK);
  assert.ok(!leaked(ended.display, OTHER_DOMAIN));
});

test('refresh at the deadline hides instead of continuing to show', () => {
  const {api, clock} = createController();
  api.setValue(CERT, SAMPLE_DOMAIN);
  api.toggle(CERT);
  clock.advance(5000);
  const refreshed = api.setValue(CERT, SAMPLE_DOMAIN);
  assert.strictEqual(refreshed.revealed, false);
  assert.strictEqual(refreshed.display, secret.MASK);
});

test('leave or hide page conceals immediately, clears timers, and return stays hidden', () => {
  const {api, clock} = createController();
  api.setValue(CERT, SAMPLE_DOMAIN);
  api.setValue(ENTRY, SAMPLE_ENTRY);
  api.toggle(CERT);
  api.toggle(ENTRY);
  clock.advance(1000);
  api.hideAll();
  assert.strictEqual(api.snapshot(CERT).revealed, false);
  assert.strictEqual(api.snapshot(ENTRY).revealed, false);
  assert.strictEqual(api.snapshot(CERT).display, secret.MASK);
  assert.strictEqual(clock.pending, 0);
  clock.advance(5000);
  assert.strictEqual(api.snapshot(CERT).revealed, false);
  const again = api.toggle(CERT);
  assert.strictEqual(again.revealed, true);
  clock.advance(5000);
  assert.strictEqual(api.snapshot(CERT).revealed, false);
});

console.log(`\n${passed} tests passed`);
