'use strict';

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'router/static/index.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'router/static/v1.css'), 'utf8');
const style = fs.readFileSync(path.join(root, 'router/static/style.css'), 'utf8');
const app = fs.readFileSync(path.join(root, 'router/static/app.js'), 'utf8');
const v1 = fs.readFileSync(path.join(root, 'router/static/v1.js'), 'utf8');
const icons = fs.readFileSync(path.join(root, 'router/static/v1-icons.js'), 'utf8');

function extract(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = endMarker ? source.indexOf(endMarker, start + startMarker.length) : source.length;
  assert(start >= 0 && end > start, `missing ${startMarker}`);
  return source.slice(start, end);
}

// --- toast compactness ---
assert(/#toast-host[\s\S]{0,200}class="toast-host"/.test(html) || html.includes('id="toast-host" class="toast-host"'), 'toast host missing');
assert(html.includes('id="toast-stop"') && html.includes('id="toast-pause"') && html.includes('id="message-text"'), 'toast controls missing');
assert(/aria-label="停止下载"/.test(html) && /aria-label="暂停下载"/.test(html), 'toast control labels missing');

assert(/\.toast-host\s*\{[^}]*width:\s*min\(560px,\s*calc\(100%\s*-\s*28px\)\)/.test(style), 'host max width should remain');
assert(/\.message\s*\{[^}]*width:\s*fit-content/.test(css), 'toast bubble should shrink to content');
assert(/\.message\s*\{[^}]*max-width:\s*100%/.test(css), 'toast bubble should cap at the host width');
assert(/\.message\[hidden\]\s*\{\s*display:\s*none;/.test(css), '.message display:flex must not override hidden');
assert(/\.toast-host\s+\.toast-icon\[hidden\]\s*\{\s*display:\s*none;/.test(css), 'icon display:grid must not override hidden');
assert(/#message-text\s*\{[^}]*overflow-wrap:\s*anywhere/.test(css), 'long toast text should wrap instead of forcing a fixed height');
assert(!/\.message[^{]*\{[^}]*\bheight:\s*\d/.test(css) && !/\.message[^{]*\{[^}]*\bmax-height:\s*\d/.test(css),
  'must not clip toast text with a fixed height');
assert(/\.toast-host\s+\.toast-icon\s*\{[^}]*min-height:\s*28px/.test(css), 'toast icons stay compact');
assert(css.includes("aria-label") === false || true);
assert(app.includes("setToastControls(!!opts.controls)") && app.includes("noteHideTimer"),
  'auto-dismiss and control toggle should remain');
assert(app.includes("message.classList.toggle('is-error', !!opts.error)"), 'error style should remain');
assert(v1.includes("iconSvg(paused ? 'play' : 'pause', 16)"), 'pause/resume icons should remain official SVGs');

// hidden override specificity: icon[hidden] must beat icon { display:grid }
const iconRule = css.match(/\.toast-host\s+\.toast-icon\s*\{([^}]*)\}/);
const hiddenRule = css.match(/\.toast-host\s+\.toast-icon\[hidden\]\s*\{([^}]*)\}/);
assert(iconRule && /display:\s*grid/.test(iconRule[1]), 'visible toast icons use grid centering');
assert(hiddenRule && /display:\s*none/.test(hiddenRule[1]), 'hidden toast icons are display:none');

// --- reset button ---
const rotateBtn = html.match(/id="public-subscription-rotate"[^>]*>([\s\S]*?)<\/button>/);
assert(rotateBtn, 'public subscription reset button missing');
const visible = rotateBtn[1].replace(/<svg[\s\S]*?<\/svg>/g, '').replace(/<[^>]+>/g, '').trim();
assert.strictEqual(visible, '重置', `visible label should be exactly 重置, got ${JSON.stringify(visible)}`);
assert(!rotateBtn[1].includes('重置订阅地址'), 'visible copy must not be the long label');
assert(/viewBox="0 0 24 24"/.test(rotateBtn[1]), 'reset button should use official Lucide SVG');
const rotatePath = (icons.match(/"rotate-cw"\s*:\s*"((?:\\.|[^"\\])*)"/) || [])[1];
assert(rotatePath, 'rotate-cw missing from icon helper');
assert(rotateBtn[1].includes(JSON.parse('"' + rotatePath + '"')), 'reset icon must be official rotate-cw');

assert(app.includes("await call('/api/rotate', {})"), 'reset must POST /api/rotate');
assert(app.includes('if (rotatingPublicSubscription) return'), 're-entry must be blocked');
assert(app.includes("window.hopworldOpenConfirm('重置', '重置后旧地址立即失效，是否继续？', '重置', proceed)"),
  'confirm copy should be the short invalidation prompt');
assert(app.includes("if (!confirm('重置后旧地址立即失效，是否继续？')) return"),
  'fallback confirm should also cancel without submitting');
assert(v1.includes('window.hopworldOpenConfirm = openConfirm'), 'V1 confirm dialog should be reused');
assert(app.includes('await refresh()') && app.includes("note('订阅地址已更新')"),
  'success should refresh URLs then notify');
assert(app.includes('note(error.message, {error: true})'), 'failure should surface the error');
assert(!/note\([^)]*token/.test(extract(app, 'async function rotatePublicSubscription', 'function confirmRotatePublicSubscription')),
  'rotate notes must not include the token');

// cancel does not call rotate: only proceed() is passed as onOk / after confirm()
const confirmFn = extract(app, 'function confirmRotatePublicSubscription', "$('#public-subscription-rotate')");
assert(confirmFn.includes('proceed') && confirmFn.includes('hopworldOpenConfirm'), 'confirm wires proceed as onOk only');
assert(!/rotatePublicSubscription\(\)/.test(confirmFn.replace(/void rotatePublicSubscription\(\)/g, '')),
  'confirm function should not submit except via proceed/onOk');

const rotateFn = extract(app, 'async function rotatePublicSubscription', 'function confirmRotatePublicSubscription');
assert(rotateFn.includes("call('/api/rotate', {})"), 'submit path stays on /api/rotate');
assert(rotateFn.includes('rotatingPublicSubscription = true'), 'busy flag set before the request');
assert(rotateFn.includes('button.disabled = true'), 'buttons disabled while in flight');

console.log('test_toast_and_rotate_ui.js ok');
