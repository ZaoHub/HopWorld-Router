'use strict';

const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const css = fs.readFileSync(path.join(root, 'router/static/v1.css'), 'utf8');
const style = fs.readFileSync(path.join(root, 'router/static/style.css'), 'utf8');
const html = fs.readFileSync(path.join(root, 'router/static/index.html'), 'utf8');
const secret = fs.readFileSync(path.join(root, 'router/static/v1-overview-secret.js'), 'utf8');

function fail(message) {
  throw new Error(message);
}

function assert(cond, message) {
  if (!cond) fail(message);
}

const rootLh = /:root\s*\{[^}]*\bline-height:\s*1\.5\b/.test(style);
assert(rootLh, ':root line-height should be 1.5');

const defs = css.match(/\.v1-definitions\s*\{[^}]*\}/);
assert(defs, 'missing .v1-definitions');
assert(/\bfont-size:\s*13px\b/.test(defs[0]), '.v1-definitions font-size should be 13px');

const block = css.match(
  /#v1-overview-cert\s+\.v1-overview-secret-toggle,\s*#v1-overview-access\s+\.v1-overview-secret-toggle\s*\{([\s\S]*?)\n\}/
);
assert(block, 'homepage secret-toggle CSS block missing or not scoped to cert/access');
const body = block[1];
assert(/\bfont-size:\s*13px\b/.test(body), 'toggle font-size should be 13px to match the definition label');
assert(/\bline-height:\s*0\b/.test(body), 'toggle line-height should be 0 so the frame is height-controlled');
assert(/\bwidth:\s*1\.5em\b/.test(body) && /\bheight:\s*1\.5em\b/.test(body), 'toggle box should be 1.5em');
assert(/\bmin-width:\s*1\.5em\b/.test(body) && /\bmax-width:\s*1\.5em\b/.test(body), 'toggle width should be locked to 1.5em');
assert(/\bmax-height:\s*1\.5em\b/.test(body), 'toggle max-height should be 1.5em');
assert(/\bmin-height:\s*0\b/.test(body), 'toggle must override global button min-height with min-height: 0');
assert(/\bpadding:\s*0\b/.test(body), 'toggle padding should be 0');
assert(!/\bmin-height:\s*(28px|42px|44px)\b/.test(body), 'toggle must not keep the old 28px or global 42/44 min-height');

const svg = css.match(
  /#v1-overview-cert\s+\.v1-overview-secret-toggle\s+svg,\s*#v1-overview-access\s+\.v1-overview-secret-toggle\s+svg\s*\{([^}]*)\}/
);
assert(svg, 'homepage secret-toggle svg size block missing');
assert(/\bwidth:\s*14px\b/.test(svg[1]) && /\bheight:\s*14px\b/.test(svg[1]), 'homepage eye icon should be 14px');

const otherToggle = css.match(/\.v1-icon-btn\.v1-overview-secret-toggle(?![\s\S]{0,80}#v1-overview)\s*\{[^}]*min-height:\s*28px/);
// other buttons stay untouched: global min-height 42 still present
assert(/\bbutton\s*\{[^}]*min-height:\s*42px/.test(style) || /button\s*\{[^}]*min-height:\s*42px/.test(css) ||
  /button\s*\{[^}]*?min-height:\s*42px/.test(style), 'global button min-height should remain');
assert(style.includes('button,.heading-link{min-height:44px}'),
  'narrow-screen button min-height 44px should remain');
assert(style.includes('@media(max-width:640px)'),
  '640px breakpoint should remain');

assert(html.includes('id="v1-overview-cert"') && html.includes('id="v1-overview-access"'),
  'homepage cert/access regions should exist');
assert(secret.includes('M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0') && secret.includes('m2 2 20 20') && secret.includes('viewBox="0 0 24 24"'), 'official Lucide eye / eye-off paths should remain inlined');

const labelPx = 13;
const lineHeight = 1.5;
const boxPx = labelPx * lineHeight;
const iconPx = 14;
assert(boxPx === 19.5, 'calculated label line-height should be 19.5px');
assert(boxPx <= 19.5, 'button frame must not exceed label line-height');
assert(iconPx <= boxPx, 'icon must fit inside the 19.5px button frame');

console.log(JSON.stringify({
  labelFontSizePx: labelPx,
  rootLineHeight: lineHeight,
  labelLineHeightPx: boxPx,
  buttonFrame: '1.5em at 13px = 19.5px',
  buttonFramePx: boxPx,
  iconPx,
  minHeightOverride: 0,
  padding: 0,
  scope: '#v1-overview-cert, #v1-overview-access .v1-overview-secret-toggle',
}, null, 2));
console.log('test_v1_overview_secret_eye_css.js ok');
