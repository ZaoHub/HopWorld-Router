#!/usr/bin/env node
'use strict';

const path = require('path');
const assert = require('assert');

const qrcode = require(path.join(__dirname, '../router/static/qrcode.js'));
global.qrcode = qrcode;
const about = require(path.join(__dirname, '../router/static/v1-about.js'));

const EXPECTED = {
  wechat: 'wxp://f2f0GMSCwBWM6Esa1EBmJLXz3gQZkWKmQ0XdgXpmCiGnxxM',
  usdt: '0x67c110DCDBE2174AA32c84D26D8C337294086838',
  alipay: 'https://qr.alipay.com/fkx18579lgdnd6ogigc3qc1',
};

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log('ok', name);
}

test('configured channels keep exact authorized payloads', () => {
  const byId = Object.fromEntries(about.SUPPORT_CHANNELS.map(channel => [channel.id, channel]));
  assert.strictEqual(byId.wechat.payload, EXPECTED.wechat);
  assert.strictEqual(byId.usdt.payload, EXPECTED.usdt);
  assert.strictEqual(byId.alipay.payload, EXPECTED.alipay);
  assert.ok(byId.usdt.showPayload);
  assert.ok(byId.usdt.description.includes('ERC20'));
  assert.ok(byId.usdt.description.includes('Ethereum'));
});

test('payload helper does not wrap USDT or rewrite wxp/https schemes', () => {
  assert.strictEqual(about.configuredPayload(EXPECTED.usdt), EXPECTED.usdt);
  assert.strictEqual(about.configuredPayload(EXPECTED.wechat), EXPECTED.wechat);
  assert.strictEqual(about.configuredPayload(EXPECTED.alipay), EXPECTED.alipay);
  assert.strictEqual(about.configuredPayload(''), null);
  assert.strictEqual(about.configuredPayload(null), null);
  const usdt = about.encodeQr(EXPECTED.usdt);
  assert.strictEqual(usdt.payload, EXPECTED.usdt);
  assert.ok(!usdt.payload.startsWith('ethereum:'));
  assert.ok(!usdt.svg.includes('ethereum:'));
  assert.strictEqual(about.encodeQr(EXPECTED.wechat).payload, EXPECTED.wechat);
  assert.ok(EXPECTED.wechat.startsWith('wxp://'));
});

test('encodeQr uses the exact payload and empty values do not invent a QR', () => {
  for (const payload of Object.values(EXPECTED)) {
    const encoded = about.encodeQr(payload);
    assert.ok(encoded, payload);
    assert.strictEqual(encoded.payload, payload);
    assert.ok(encoded.moduleCount >= 21);
    assert.strictEqual(encoded.modules.length, encoded.moduleCount);
    assert.ok(encoded.svg.includes('<svg'));
  }
  assert.strictEqual(about.encodeQr(''), null);
  assert.strictEqual(about.encodeQr('   '.trim() && ''), null);
  const emptyView = about.channelView({id: 'empty', title: '空', payload: '', description: ''});
  assert.strictEqual(emptyView.configured, false);
  assert.strictEqual(emptyView.placeholder, about.UNCONFIGURED);
  assert.strictEqual(emptyView.svg, '');
});

test('channel views keep wechat wxp payloads and do not emit payment links', () => {
  const views = about.SUPPORT_CHANNELS.map(about.channelView);
  const wechat = views.find(item => item.id === 'wechat');
  const usdt = views.find(item => item.id === 'usdt');
  const alipay = views.find(item => item.id === 'alipay');
  assert.ok(wechat.configured);
  assert.strictEqual(wechat.payload, EXPECTED.wechat);
  assert.ok(wechat.svg.includes('<svg'));
  assert.ok(!wechat.svg.includes('href='));
  assert.strictEqual(usdt.payload, EXPECTED.usdt);
  assert.ok(usdt.showPayload);
  assert.strictEqual(alipay.payload, EXPECTED.alipay);
  assert.ok(!alipay.showPayload);
});

console.log(passed + ' tests passed');
