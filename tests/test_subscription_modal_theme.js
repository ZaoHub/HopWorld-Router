'use strict';

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'router/static/index.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'router/static/v1.css'), 'utf8');
const style = fs.readFileSync(path.join(root, 'router/static/style.css'), 'utf8');
const app = fs.readFileSync(path.join(root, 'router/static/app.js'), 'utf8');
const icons = fs.readFileSync(path.join(root, 'router/static/v1-icons.js'), 'utf8');

const modalStart = html.indexOf('id="subscription-modal"');
const modalEnd = html.indexOf('id="add-source-modal"');
assert(modalStart >= 0 && modalEnd > modalStart, 'subscription modal markup missing');
const modal = html.slice(modalStart, modalEnd);

function cssBlock(selector) {
  const re = new RegExp(selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*\\{([^}]*)\\}');
  const match = css.match(re);
  assert(match, `missing ${selector}`);
  return match[1];
}

assert(modal.includes('aria-labelledby="subscription-modal-title"'), 'title a11y kept');
assert(modal.includes('data-modal-close="subscription"') && modal.includes('aria-label="关闭"'), 'close control kept');
assert(modal.includes('id="subscription"') && modal.includes('id="copy-modal"') && modal.includes('id="rotate"'),
  'copy/reset input ids must stay for existing handlers');
assert(modal.includes('id="mobile-qr"') && modal.includes('id="mobile-qr-hint"'), 'QR targets kept');
assert(!/data-(token|subscription)/.test(modal) && !/token=/.test(modal), 'must not put the token in new attributes');

const rotate = modal.match(/id="rotate"[^>]*>([\s\S]*?)<\/button>/);
assert(rotate, 'reset button missing');
const visible = rotate[1].replace(/<svg[\s\S]*?<\/svg>/g, '').replace(/<[^>]+>/g, '').trim();
assert.strictEqual(visible, '重置', `visible label must be 重置, got ${JSON.stringify(visible)}`);
assert(!rotate[1].includes('重置订阅地址'), 'no long visible label');
const rotatePath = (icons.match(/"rotate-cw"\s*:\s*"((?:\\.|[^"\\])*)"/) || [])[1];
assert(rotatePath, 'rotate-cw missing');
assert(rotate[1].includes(JSON.parse('"' + rotatePath + '"')), 'modal reset must use official rotate-cw');
assert(!rotate[1].includes('M3 12a9 9 0 0 1 9-9 9.75'), 'legacy refresh-cw icon should not remain on the modal reset button');

// reset shares the input row with copy; hint is no longer a stacked footer button
assert(/<div class="input-row has-field">[\s\S]*id="copy-modal"[\s\S]*id="rotate"/.test(modal),
  'reset should sit on the same action row as copy');
assert(!/address-footer[\s\S]*id="rotate"/.test(modal), 'reset must not stay in the wrapping address-footer');
assert(modal.includes('地址含访问密钥，仅分享给信任设备。'), 'secret hint kept');

const btn = cssBlock('#subscription-modal .input-row.has-field > button');
assert(/display:\s*inline-flex/.test(btn) && /align-items:\s*center/.test(btn) && /white-space:\s*nowrap/.test(btn),
  'reset/copy must stay a single centered inline-flex row');
assert(/flex:\s*0 0 auto/.test(btn), 'action buttons should not shrink into two lines');
assert(/min-width:\s*0/.test(cssBlock('#subscription-modal .input-row.has-field > input')),
  'address input must allow shrinking');
assert(/flex-wrap:\s*wrap/.test(cssBlock('#subscription-modal .input-row.has-field')),
  'narrow screens may wrap the whole control group');

assert(/background:\s*#152336/.test(cssBlock('#subscription-modal .mobile-step')), 'step cards use a dark surface');
assert(/border:\s*1px solid var\(--v1-border\)/.test(cssBlock('#subscription-modal .mobile-step')), 'step cards use theme borders');
assert(/color:\s*#e8f1ff/.test(cssBlock('#subscription-modal .mobile-step strong')), 'step titles are high contrast');
assert(/color:\s*var\(--v1-muted\)/.test(cssBlock('#subscription-modal .mobile-step p')), 'step body uses muted theme text');
assert(/color:\s*#e8f1ff/.test(cssBlock('#subscription-modal .mobile-connect-head h3')), '导入方式 title is high contrast');
assert(/border-top:\s*1px solid var\(--v1-border\)/.test(cssBlock('#subscription-modal .mobile-connect')),
  'divider uses the theme border, not a light hairline');

const qr = cssBlock('#subscription-modal .mobile-qr');
assert(/background:\s*#fff/.test(qr), 'QR scanning background must stay pure white');
assert(!/#subscription-modal \.mobile-qr\s*\{[^}]*background:\s*var\(--v1-card\)/.test(css),
  'QR panel must not use the dark card fill');

assert(/max-height:\s*min\(88vh,calc\(100dvh - 32px\)\)/.test(style) && /overflow-y:\s*auto/.test(style),
  'existing modal max-height and body scroll should remain');
assert(/#subscription-modal \.modal-panel-wide\s*\{[^}]*width:\s*min\(640px/.test(css),
  'wide modal should have room for the QR + instructions columns');
assert(/@media\s*\(max-width:\s*800px\)\s*\{[^}]*#subscription-modal \.mobile-connect-body\s*\{[^}]*grid-template-columns:\s*1fr/.test(css)
  || /@media \(max-width: 800px\)[\s\S]*#subscription-modal \.mobile-connect-body \{ grid-template-columns: 1fr/.test(css),
  'narrow screens may stack QR and instructions');

assert(app.includes("panel.innerHTML = qr.createSvgTag(3, 2)"), 'QR payload generation must stay unchanged');
assert(app.includes("await call('/api/rotate', {})") && app.includes('rotatingPublicSubscription'),
  'reset confirm / re-entry path must stay unchanged');
assert(app.includes("copySubscriptionLink($('#subscription'))") || app.includes("id of ['copy', 'copy-modal']"),
  'copy handler should remain');

console.log('test_subscription_modal_theme.js ok');
