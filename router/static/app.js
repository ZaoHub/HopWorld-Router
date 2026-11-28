const $ = s => document.querySelector(s);
/** 界面展示名：中文为主，英文名放括号内 */
const LE_LABEL = '免费 SSL 证书（Let\u2019s Encrypt）';
const message = $('#message');
let probeTimer;
let autoObservationTimer;
let autoObservationBusy = false;
let ikev2Timer;
let localTrafficTimer;
let localTrafficEnabled = false;
let localTrafficGeneration = 0;
let localTrafficSample = null;
let ikev2Editing = false;
let refreshSeq = 0;
let ikev2LiveSeq = 0;
let localEditing = false;
let localNodeNoticeSuppressed = false;
let routerToken = '';
let publicUrls = {};
function applyPublicUrls(data) {
  publicUrls = data.public_urls || {};
  const token = data.token || routerToken;
  const subscription = publicUrls.public_subscription_url || '';
  const ikev2Profile = data.ikev2?.enabled
    ? (publicUrls.ikev2_profile_url || `${location.origin}/s/${token}/ikev2.mobileconfig`)
    : '';
  return { subscription, ikev2Profile };
}
const PROBE_TARGET_GROUPS = {
  general: {
    label: '通用',
    targets: [
      { value: 'google', label: 'Google' },
      { value: 'youtube', label: 'YouTube' },
      { value: 'cloudflare', label: 'Cloudflare' },
      { value: 'apple', label: 'Apple' },
      { value: 'microsoft', label: 'Microsoft' },
      { value: 'github', label: 'GitHub' },
      { value: 'wikipedia', label: 'Wikipedia' },
      { value: 'bing', label: 'Bing' },
      { value: 'baidu', label: '百度' },
    ],
  },
  ai: {
    label: 'AI 应用',
    targets: [
      { value: 'openai', label: 'ChatGPT' },
      { value: 'claude', label: 'Claude' },
      { value: 'gemini', label: 'Gemini' },
      { value: 'copilot', label: 'Copilot' },
      { value: 'perplexity', label: 'Perplexity' },
      { value: 'huggingface', label: 'Hugging Face' },
      { value: 'grok', label: 'Grok' },
    ],
  },
  social: {
    label: '社交媒体',
    targets: [
      { value: 'tiktok', label: 'TikTok' },
      { value: 'twitter', label: 'X' },
      { value: 'facebook', label: 'Facebook' },
      { value: 'instagram', label: 'Instagram' },
      { value: 'telegram', label: 'Telegram' },
      { value: 'whatsapp', label: 'WhatsApp' },
      { value: 'reddit', label: 'Reddit' },
      { value: 'discord', label: 'Discord' },
      { value: 'linkedin', label: 'LinkedIn' },
      { value: 'pinterest', label: 'Pinterest' },
    ],
  },
};
function populateProbeTargets(select) {
  for (const {label, targets} of Object.values(PROBE_TARGET_GROUPS)) {
    const group = document.createElement('optgroup');
    group.label = label;
    for (const item of targets) group.append(new Option(item.label, item.value));
    select.append(group);
  }
}
function renderDefaultProbeTarget(value) {
  const select = $('#test-target');
  const name = Array.from(select.options).find(option => option.value === value)?.text || 'Google';
  const label = '系统默认 · ' + name;
  if (select.options[0].text !== label) select.options[0].text = label;
}
function probeTargetPayload() {
  const target = $('#test-target').value;
  return target ? {target} : {};
}
function endIkev2Edit() { ikev2Editing = false; }
function syncIkev2GuideFields(ike) {
  const server = (ike && (ike.server || ike.public_host)) || $('#ikev2-host')?.value || '—';
  const remote = (ike && ike.remote_id) || $('#ikev2-remote-id')?.value || '—';
  const psk = (ike && ike.psk) || $('#ikev2-psk')?.value?.trim() || '';
  if ($('#ikev2-guide-server')) $('#ikev2-guide-server').textContent = server;
  if ($('#ikev2-guide-remote')) $('#ikev2-guide-remote').textContent = remote;
  if ($('#ikev2-guide-psk')) {
    $('#ikev2-guide-psk').textContent = psk || '启用后在「配置」复制';
  }
}
function syncIkev2Form(ike) {
  const enabled = !!ike?.enabled;
  if (!ikev2Editing) {
    if (ike?.public_host) $('#ikev2-host').value = ike.public_host;
    $('#ikev2-remote-id').value = ike?.remote_id || 'hopworld.ikev2';
    $('#ikev2-psk').value = ike.psk || '';
  }
  const editable = (!enabled && !ikev2Editing) || (enabled && ikev2Editing);
  for (const id of ['ikev2-host', 'ikev2-remote-id', 'ikev2-psk']) {
    const input = $('#' + id);
    input.readOnly = !editable;
    input.classList.toggle('is-readonly', !editable);
  }
  if (!enabled) {
    $('#ikev2-psk').placeholder = ike?.psk ? '' : '留空则自动生成';
  } else if (!ikev2Editing) {
    $('#ikev2-psk').placeholder = '启用后显示';
  }
  $('#ikev2-edit').hidden = !enabled || ikev2Editing;
  $('#ikev2-save').hidden = !enabled || !ikev2Editing;
  $('#ikev2-cancel').hidden = !enabled || !ikev2Editing;
  const ikeToggle = $('#ikev2-toggle');
  ikeToggle.hidden = ikev2Editing;
  ikeToggle.textContent = enabled ? '停用' : '启用';
  ikeToggle.classList.toggle('primary', !enabled);
  ikeToggle.classList.toggle('secondary', enabled);
  $('#ikev2-rotate').hidden = !enabled || ikev2Editing;
  $('#ikev2-copy-psk').hidden = enabled && ikev2Editing;
  $('#ikev2-copy-psk').disabled = !$('#ikev2-psk').value.trim();
  syncIkev2SettingsUi(ike, { editing: ikev2Editing });
}
function syncIkev2SettingsUi(ike, opts = {}) {
  const enabled = !!ike?.enabled;
  const profile = $('#ikev2-settings-profile');
  const disabledHint = $('#ikev2-settings-disabled-hint');
  if (profile) profile.hidden = !enabled;
  if (disabledHint) disabledHint.hidden = enabled;
  if (!enabled) return;
  const editing = !!opts.editing;
  const showMc = enabled && !editing;
  for (const id of ['ikev2-mobileconfig', 'ikev2-mobileconfig-copy']) {
    const el = $(`#${id}`);
    if (el) el.hidden = !showMc;
  }
}
function ikev2StatusLine(ike) {
  if (!ike?.enabled) return '尚未启用';
  return ike.running
    ? `已启用 · strongSwan 运行中 · ${ike.public_host}`
    : '已启用，但 strongSwan 未在运行';
}
function applyIkev2Ui(ike, opts = {}) {
  if (!ike) return;
  syncIkev2Form(ike);
  syncIkev2GuideFields(ike);
  const ikeLine = ikev2StatusLine(ike);
  if ($('#ikev2-status')) $('#ikev2-status').textContent = ikeLine;
  if ($('#ikev2-summary')) {
    $('#ikev2-summary').textContent = ike.enabled
      ? ikeLine
      : '尚未启用 · 「配置」启用，「设置」装描述文件';
  }
  const healthEl = $('#ikev2-health');
  if (healthEl) {
    if (ike.enabled && ike.health) {
      const parts = [];
      if (ike.health.ip_forward === false) parts.push('未开启 IP 转发');
      if (ike.health.nat_masquerade === false) parts.push('NAT 未配置（可能无法上网）');
      if (ike.health.status_ok === false) parts.push('无法读取连接状态');
      if (parts.length) {
        healthEl.hidden = false;
        healthEl.textContent = `网络检查：${parts.join(' · ')}（见主栏「IKEv2 连接监视」）`;
      } else {
        healthEl.hidden = true;
        healthEl.textContent = '';
      }
    } else {
      healthEl.hidden = true;
      healthEl.textContent = '';
    }
  }
  renderIkev2Monitor(ike);
  $('#ikev2-toggle').disabled = ikev2Editing;
  $('#ikev2-rotate').disabled = !ike.enabled || ikev2Editing;
  const mcUrl = $('#ikev2-mobileconfig-url');
  if (mcUrl) {
    const profile = opts.ikev2Profile ?? (routerToken && ike.enabled
      ? `${location.origin}/s/${routerToken}/ikev2.mobileconfig`
      : '');
    mcUrl.value = ike.enabled && profile && !ikev2Editing ? profile : '';
  }
  syncIkev2SettingsUi(ike, { editing: ikev2Editing });
  updateDeviceConnectionIndicator(ike);
}
function maybeShowLocalNodeNotice(local) {
  const modal = $('#local-node-notice-modal');
  if (!modal) return;
  if (local?.enabled) {
    if (!modal.hidden) closeModal('local-node-notice');
    return;
  }
  if (localNodeNoticeSuppressed || !modal.hidden) return;
  modal.hidden = false;
  syncModalBodyScroll();
}
let dnsHintsTimer = 0;
function isPublicHostIp(value) {
  const v = (value || '').trim().replace(/^\[|\]$/g, '');
  if (!v) return false;
  const m = v.match(/^(\d{1,3}\.){3}\d{1,3}$/);
  if (m) return v.split('.').every((n) => Number(n) <= 255);
  return v.includes(':') && /^[0-9a-f:.]+$/i.test(v);
}
function syncLocalHostDdnsHint() {
  const ddnsOn = $('#local-host-mode').value === 'ddns';
  const input = $('#local-host');
  const hint = $('#local-host-ddns-hint');
  if (!input || !hint) return;
  const show = ddnsOn && isPublicHostIp(input.value);
  hint.hidden = !show;
  input.classList.toggle('has-inline-hint', show);
  const auto = $('#local-host-mode').value === 'auto';
  const check = $('#local-check-address');
  check.hidden = !auto && !ddnsOn && isPublicHostIp(input.value);
  check.textContent = auto ? '检查公网 IP' : '验证域名绑定';
}
function renderLocalDnsVerify(verify) {
  const el = $('#local-dns-verify-status');
  const button = $('#local-dns-verify');
  if (!el) return;
  const showCheck = verify && !['skip', 'unset'].includes(verify.status);
  if (button) button.hidden = !showCheck;
  if (!verify?.detail) {
    el.hidden = true;
    el.classList.remove('is-ok', 'is-bad', 'is-muted');
    return;
  }
  el.hidden = false;
  el.textContent = verify.detail;
  el.classList.remove('is-ok', 'is-bad', 'is-muted');
  if (verify.status === 'ok') el.classList.add('is-ok');
  else if (verify.status === 'mismatch' || verify.status === 'error') el.classList.add('is-bad');
  else el.classList.add('is-muted');
}
function renderLocalDnsHints(hints, verify) {
  const section = $('#local-dns-hints');
  if (!section) return;
  const mode = hints?.mode;
  const records = hints?.records || [];
  const show = mode && mode !== 'unknown'
    && (mode !== 'unset' || hints?.summary || records.length)
    && !(mode === 'ddns' && !records.length && !hints?.summary);
  if (!show) {
    section.hidden = true;
    renderLocalDnsVerify(null);
    return;
  }
  section.hidden = false;
  const summary = $('#local-dns-hints-summary');
  if (summary) {
    const text = hints.summary || '';
    summary.hidden = !text;
    summary.textContent = text;
  }
  const body = $('#local-dns-hints-body');
  const empty = $('#local-dns-hints-empty');
  if (!body) return;
  body.replaceChildren();
  if (!records.length) {
    if (empty) empty.hidden = !['fixed_ip', 'auto_ip'].includes(hints.mode);
    renderLocalDnsVerify(verify);
    return;
  }
  if (empty) empty.hidden = true;
  for (const row of records) {
    const tr = document.createElement('tr');
    for (const text of [row.type, row.host, row.value, row.managed ? 'Router 自动' : '手动配置']) {
      const td = document.createElement('td');
      td.textContent = text;
      tr.append(td);
    }
    body.append(tr);
  }
  renderLocalDnsVerify(verify);
}
async function fetchLocalDnsHintsPreview() {
  const params = new URLSearchParams({
    public_host: $('#local-host')?.value?.trim() || '',
    auto_public_host: $('#local-host-mode').value === 'auto' ? '1' : '0',
    ddns_enabled: $('#local-host-mode').value === 'ddns' ? '1' : '0',
    public_host_prefer: $('#local-host-prefer')?.value || 'ipv4',
  });
  try {
    const hints = await call(`/api/local/dns-hints?${params}`);
    renderLocalDnsHints(hints, null);
  } catch {
    /* keep last hints */
  }
}
async function runLocalDnsVerify() {
  const button = $('#local-dns-verify');
  const params = new URLSearchParams({
    force: '1',
    public_host: $('#local-host')?.value?.trim() || '',
    auto_public_host: $('#local-host-mode').value === 'auto' ? '1' : '0',
    ddns_enabled: $('#local-host-mode').value === 'ddns' ? '1' : '0',
    public_host_prefer: $('#local-host-prefer')?.value || 'ipv4',
  });
  if (button) button.disabled = true;
  try {
    const verify = await call(`/api/local/dns-verify?${params}`);
    renderLocalDnsVerify(verify);
    note(verify.status === 'ok' ? '域名解析正确' : verify.detail, { error: verify.status === 'mismatch' || verify.status === 'error' });
  } catch (e) {
    note(e.message, { error: true });
  } finally {
    if (button) button.disabled = false;
  }
}
function humanizeAcmeError(text) {
  const msg = humanizeApiMessage(String(text || '').trim());
  if (!msg) return '证书申请失败';
  if (msg.length > 200) return `${msg.slice(0, 200)}…`;
  return msg;
}
function formatAcmeLine(acme) {
  if (acme?.source === 'manual') {
    if (acme.state === 'expired') return '手动证书已过期，请重新导入';
    return acme.certificate_valid ? `手动证书 · 剩余约 ${acme.days_left} 天` : '手动证书不可用，请重新导入';
  }
  if (acme?.state === 'expired') return `${LE_LABEL}：证书已过期${acme.error ? ' · ' + humanizeAcmeError(acme.error) : ''}`;
  if (!acme) return '';
  if (acme.certificate_valid && acme.error) return `${LE_LABEL}：原证书仍有效 · 续签异常（${humanizeAcmeError(acme.error)}）`;
  if (acme.error) return `${LE_LABEL}：未签发（${humanizeAcmeError(acme.error)}）`;
  if (acme.days_left != null && acme.state === 'ok') {
    const via = acme.challenge_effective ? ` · ${acme.challenge_effective}` : '';
    const renew = acme.renew_days != null ? ` · ≤${acme.renew_days} 天自动续期` : '';
    return `${LE_LABEL}：约 ${acme.days_left} 天${via}${renew}`;
  }
  if (acme.enabled) return `${LE_LABEL}：已开启，等待签发或保存后重试`;
  return '';
}
function renderPublicAccessHints(acme, urls) {
  const tls = $('#subscription-tls-status');
  const line = formatAcmeLine(acme);
  if (!tls) return;
  if (line) {
    tls.hidden = false;
    tls.textContent = line;
    tls.classList.toggle('is-acme-error', !!acme?.error);
  } else if (urls?.subscription_https && urls.cert_days_left != null) {
    tls.hidden = false;
    tls.textContent = `HTTPS 订阅 · 证书约 ${urls.cert_days_left} 天`;
    tls.classList.remove('is-acme-error');
  } else {
    tls.hidden = true;
    tls.textContent = '';
  }
}
function syncAcmeFwHint(acme, showHostControls) {
  const wrap = $('#local-acme-fw-wrap');
  if (!wrap) return;
  const acmeOn = !!$('#local-acme')?.checked || !!acme?.automatic;
  wrap.hidden = !acmeOn;
  if (!acmeOn) return;
  let mode = ($('#local-acme-challenge')?.value || 'auto').trim();
  if (!showHostControls && acme?.challenge) mode = acme.challenge;
  for (const row of wrap.querySelectorAll('tr[data-challenge]')) {
    row.classList.toggle('is-active', row.dataset.challenge === mode);
  }
}
function renderAcmeStatus(acme, showHostControls) {
  const el = $('#local-acme-status');
  const acmeLine = formatAcmeLine(acme);
  const readOnly = !showHostControls;
  if (!el) return;
  const boxOn = !!$('#local-acme')?.checked;
  if (readOnly) {
    el.hidden = true;
    el.textContent = '';
    el.classList.remove('is-acme-error', 'is-acme-pending');
    return;
  }
  const show = !!(acme?.error || acme?.days_left != null || acme?.enabled || boxOn);
  if (!show) {
    el.hidden = true;
    el.classList.remove('is-acme-error', 'is-acme-pending');
    return;
  }
  el.hidden = false;
  el.classList.toggle('is-acme-error', !!acme?.error);
  el.classList.toggle('is-acme-pending', !acme?.error && acme?.state === 'pending');
  if (acmeLine) el.textContent = acmeLine;
  else if (acme?.state === 'pending') el.textContent = '签发中或未成功，保存后可重试';
  else if (acme?.enabled) el.textContent = `${LE_LABEL}：已开启`;
  else if (boxOn) el.textContent = '保存后将申请证书';
  else el.textContent = '';
  el.hidden = !el.textContent;
}
function scheduleLocalDnsHintsPreview() {
  clearTimeout(dnsHintsTimer);
  dnsHintsTimer = setTimeout(() => { void fetchLocalDnsHintsPreview(); }, 280);
}
function showLocalProxyProtocol() {
  const select = $('#local-proxy-choice');
  const option = select.selectedOptions[0];
  const text = $('#local-proxy-protocol');
  if (!select.value) {
    text.textContent = '请扫描并选择代理，以查看 TCP 和 UDP 状态。';
    return;
  }
  const tcp = option?.dataset.scanned ? '已验证' : '待扫描';
  const udp = option?.dataset.udp || (select.value.startsWith('http:') ? 'unsupported' : 'unknown');
  const labels = {supported: '已验证', unsupported: '不支持', unknown: '待验证'};
  const detail = udp === 'supported' ? '扫描验证了本机回环 UDP 转发，公网访问仍需实际测试。' :
    udp === 'unsupported' ? 'HTTP 代理不支持 UDP 转发。' : '请重新扫描并实际测试 UDP。';
  text.textContent = `TCP：${tcp} · UDP：${labels[udp] || labels.unknown}。${option?.dataset.scanned ? 'TCP 扫描验证了本机回环连接。' : ''}${detail}`;
}
function syncFixedUpstream(local, nodes) {
  const select = $('#local-upstream');
  if (nodes) {
    const current = localEditing ? select.value : local.upstream_id || '';
    select.replaceChildren(new Option('请选择已启用的上游节点', ''));
    for (const node of nodes) {
      if (!node.local && node.enabled) select.add(new Option(node.title + ' · ' + node.protocol.toUpperCase(), node.id));
    }
    if (current && ![...select.options].some(option => option.value === current)) {
      select.add(new Option('已绑定的上游当前不可用', current));
    }
    select.value = current;
  }
  $('#local-fixed-settings').hidden = $('#local-egress-mode').value !== 'fixed';
  select.disabled = !!local?.enabled && !localEditing;
}
function syncLocalForm(local, acme, preserveFields = false) {
  acme = acme || {};
  if (local && !localEditing && !preserveFields) {
    $('#local-name').value = local.name || '本地网络';
    if (local.public_host) $('#local-host').value = local.public_host;
    $('#local-port').value = local.port || 8443;
    $('#local-host-mode').value = local.ddns_enabled ? 'ddns' : local.auto_public_host ? 'auto' : 'manual';
    if ($('#local-host-prefer')) $('#local-host-prefer').value = local.public_host_prefer || 'ipv4';
    if (local.egress_mode) $('#local-egress-mode').value = local.egress_mode;
    if (local.auto_target) $('#local-auto-target').value = local.auto_target;
    if (local.auto_interval) $('#local-auto-interval').value = String(local.auto_interval);
    if (local.local_proxy) {
      const select = $('#local-proxy-choice');
      if (![...select.options].some(o => o.value === local.local_proxy)) select.add(new Option(local.local_proxy, local.local_proxy));
      select.value = local.local_proxy;
    }
    if (local.egress_mode === 'auto') $('#local-auto-summary').textContent = `当前候选 ${local.auto_pool_count || 0} 个；无可用出口时阻断，不直连。`;
  }
  const localEditable = !local?.enabled || localEditing;
  $('#local-egress-mode').disabled = !localEditable;
  syncFixedUpstream(local);
  $('#local-auto-target').disabled = !localEditable;
  $('#local-auto-interval').disabled = !localEditable;
  $('#local-proxy-port').disabled = !localEditable;
  $('#local-proxy-choice').disabled = !localEditable;
  $('#local-proxy-scan').disabled = !localEditable;
  $('#local-proxy-settings').hidden = $('#local-egress-mode').value !== 'local_proxy';
  showLocalProxyProtocol();
  $('#local-auto-settings').hidden = $('#local-egress-mode').value !== 'auto';
  const readOnlyView = !!local?.enabled && !localEditing;
  const showHostControls = !readOnlyView;
  const autoHost = $('#local-host-mode').value === 'auto';
  const ddnsOn = $('#local-host-mode').value === 'ddns';
  const hostSection = $('#local-host-section');
  if (hostSection) {
    hostSection.hidden = !showHostControls;
    hostSection.classList.toggle('is-on', autoHost || ddnsOn);
  }
  const hostSummary = $('#local-host-summary');
  const summaryMode = $('#local-host-summary-mode');
  if (hostSummary && summaryMode) {
    hostSummary.hidden = !readOnlyView;
    if (readOnlyView) {
      const preferLabels = {
        ipv4: '优先 IPv4',
        ipv6: 'IPv6',
        ipv4_first: 'IPv4，失败再用 IPv6',
        dual: 'IPv4 + IPv6',
      };
      const prefer = preferLabels[local.public_host_prefer] || '优先 IPv4';
      if (local.ddns_enabled) summaryMode.textContent = '固定域名 · 自动 DNS';
      else if (local.auto_public_host) summaryMode.textContent = `自动探测 · ${prefer}`;
      else summaryMode.textContent = '固定公网';
    }
  }
  const localFormHint = $('#local-form-hint');
  if (localFormHint) localFormHint.hidden = readOnlyView;
  for (const id of ['local-name', 'local-port']) {
    const input = $('#' + id);
    input.readOnly = !localEditable;
    input.classList.toggle('is-readonly', !localEditable);
  }
  const hostInput = $('#local-host');
  const hostEditable = localEditable && !autoHost;
  hostInput.readOnly = !hostEditable;
  hostInput.classList.toggle('is-readonly', !hostEditable);
  hostInput.required = !autoHost || ddnsOn;
  $('#local-host-mode').disabled = !localEditable;
  const prefer = $('#local-host-prefer');
  prefer.options[3].disabled = autoHost;
  if (autoHost && prefer.value === 'dual') prefer.value = 'ipv4';
  prefer.disabled = !localEditable;
  const on = !!local?.enabled;
  $('#local-edit').hidden = !local?.enabled || localEditing;
  $('#local-save').hidden = !localEditing;
  $('#local-cancel').hidden = !localEditing;
  const localToggle = $('#local-toggle');
  localToggle.hidden = on && localEditing;
  localToggle.textContent = on ? '停用' : '启用';
  localToggle.classList.toggle('primary', !on);
  localToggle.classList.toggle('secondary', on);
  localToggle.disabled = on && localEditing;
  syncLocalHostDdnsHint();
  const dnsPreview = showHostControls || (readOnlyView && !!local?.enabled);
  if (dnsPreview) {
    if (readOnlyView && local?.dns_hints) renderLocalDnsHints(local.dns_hints, local.dns_verify);
    else scheduleLocalDnsHintsPreview();
  } else if ($('#local-dns-hints')) $('#local-dns-hints').hidden = true;
}
function fmtTrafficBytes(n) {
  const v = Number(n) || 0;
  if (v < 1024) return `${v} B`;
  if (v < 1048576) return `${(v / 1024).toFixed(1)} KB`;
  if (v < 1073741824) return `${(v / 1048576).toFixed(2)} MB`;
  return `${(v / 1073741824).toFixed(2)} GB`;
}
function setLocalTrafficEnabled(enabled) {
  if (enabled === localTrafficEnabled) return;
  localTrafficEnabled = enabled;
  const generation = ++localTrafficGeneration;
  clearTimeout(localTrafficTimer);
  localTrafficSample = null;
  $('#local-traffic-monitor').hidden = !enabled;
  if (enabled) {
    $('#local-traffic-status').textContent = '正在读取流量…';
    $('#local-traffic-values').hidden = true;
    void refreshLocalTraffic(generation);
  }
}
async function refreshLocalTraffic(generation) {
  try {
    const data = await call('/api/local/traffic');
    if (generation !== localTrafficGeneration) return;
    const ok = data.state === 'ok' && Number.isFinite(data.up_bytes) && Number.isFinite(data.down_bytes)
      && data.up_bytes >= 0 && data.down_bytes >= 0;
    $('#local-traffic-values').hidden = !ok;
    if (!ok) {
      const messages = {inactive: '本地网络节点尚未启用', stopped: '本地网络节点未运行', unavailable: '暂时无法读取流量'};
      $('#local-traffic-status').textContent = messages[data.state] || '暂时无法读取流量';
      localTrafficSample = null;
    } else {
      const now = performance.now();
      const previous = localTrafficSample;
      const seconds = previous ? (now - previous.time) / 1000 : 0;
      const canCalculate = data.instance && previous?.instance === data.instance && seconds > 0
        && data.up_bytes >= previous.up && data.down_bytes >= previous.down;
      $('#local-traffic-up-rate').textContent = canCalculate ? `${fmtTrafficBytes(Math.round((data.up_bytes - previous.up) / seconds))}/s` : '—';
      $('#local-traffic-down-rate').textContent = canCalculate ? `${fmtTrafficBytes(Math.round((data.down_bytes - previous.down) / seconds))}/s` : '—';
      $('#local-traffic-up').textContent = fmtTrafficBytes(data.up_bytes);
      $('#local-traffic-down').textContent = fmtTrafficBytes(data.down_bytes);
      $('#local-traffic-total').textContent = fmtTrafficBytes(data.up_bytes + data.down_bytes);
      $('#local-traffic-status').textContent = '上行：客户端发往节点 · 下行：节点发往客户端';
      localTrafficSample = {instance: data.instance, up: data.up_bytes, down: data.down_bytes, time: now};
    }
  } catch {
    if (generation !== localTrafficGeneration) return;
    localTrafficSample = null;
    $('#local-traffic-values').hidden = true;
    $('#local-traffic-status').textContent = '暂时无法读取流量';
  }
  if (generation === localTrafficGeneration && localTrafficEnabled) {
    localTrafficTimer = setTimeout(() => refreshLocalTraffic(generation), 2000);
  }
}
async function call(path, data, method = 'POST') {
  const headers = {'Accept':'application/json'};
  const options = {headers};
  if (data) {
    headers['Content-Type'] = 'application/json';
    Object.assign(options, {method, body:JSON.stringify(data)});
  }
  const response = await fetch(path, options);
  if (!response.ok) {
    if (response.status === 401) location.replace('/login' + location.hash);
    let result;
    try { result = await response.json(); } catch { result = {}; }
    const err = humanizeApiMessage(result.error) || `请求失败 (${response.status})`;
    throw new Error(err);
  }
  return response.json();
}
let systemUpdateTimer;
let systemUpdatePolling = false;
let systemUpdateStarted = 0;
let systemUpdateTarget = '';
let systemUpdateSawRunning = false;
let systemUpdateFailure = '';
let manualUpdateCheck = false;
let managedUpdate = true;
let updateInstallation = 'project';
const updateWaitLimit = 12 * 60 * 1000;
function renderRuntimeUpdate(runtime) {
  if (!runtime) return;
  const apt = runtime.apt_version || '—';
  const running = runtime.running_version || apt;
  const commit = runtime.running_commit ? String(runtime.running_commit).slice(0, 7) : '—';
  const health = $('#v1-update-health');
  if ($('#v1-update-apt-version')) $('#v1-update-apt-version').textContent = apt.startsWith('V') || apt === '—' ? apt : `V${apt}`;
  if ($('#v1-update-running-version')) {
    $('#v1-update-running-version').textContent = running.startsWith('V') || running === '—' ? running : `V${running}`;
  }
  if ($('#v1-update-running-commit')) $('#v1-update-running-commit').textContent = `Commit: ${commit}`;
  if ($('#v1-update-install-kind')) {
    $('#v1-update-install-kind').textContent = runtime.installation === 'package' ? 'APT 安装' : 'Git / 源码安装';
  }
  if (health) {
    const ok = runtime.health === 'ok';
    health.dataset.state = ok ? 'ok' : (runtime.migrated ? 'pending' : 'pending');
    health.textContent = ok ? '运行正常' : (runtime.migrated ? '运行时待校验' : '待迁移独立运行时');
  }
  const migrate = $('#v1-update-migrate');
  if (migrate) migrate.hidden = !!runtime.migrated;
  const channel = $('#v1-update-channel');
  if (channel && (runtime.channel === 'stable' || runtime.channel === 'development')) channel.value = runtime.channel;
  const box = $('#v1-update-releases');
  if (box) {
    const releases = Array.isArray(runtime.releases) ? runtime.releases.filter(item => !item.active) : [];
    box.hidden = !releases.length || !$('#v1-update-releases').dataset.open;
    box.replaceChildren(...releases.slice(0, 8).map(item => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'secondary';
      button.dataset.rollbackId = item.id;
      button.textContent = `回滚到 ${item.id}${item.commit ? ' · ' + String(item.commit).slice(0, 7) : ''}`;
      return button;
    }));
  }
}

function renderSystemUpdate(status) {
  const banner = $('#system-update-banner');
  const title = $('#system-update-title');
  const description = $('#system-update-description');
  const details = $('#system-update-details');
  const list = $('#system-update-files');
  const button = $('#system-update-apply');
  const files = Array.isArray(status.changed_files) ? status.changed_files : [];
  if (status.state === 'failed' && status.error) systemUpdateFailure = status.error;
  const statusText = {
    checking: '正在检查 GitHub…', available: `发现新版本 ${status.release || status.target || ''}`,
    no_release: '本主版本通道暂无正式发布', current: '程序已是最新版本', updating: '正在更新程序…',
    unavailable: status.error || '暂时无法检查更新', failed: systemUpdateFailure || '更新失败',
  }[status.state] || '等待检查';
  $('#v1-settings-update-status').textContent = statusText;
  $('#v1-settings-notify-update').textContent = statusText;
  const aboutStatus = $('#v1-about-update-status');
  if (aboutStatus) aboutStatus.textContent = statusText;
  if (typeof status.managed === 'boolean') managedUpdate = status.managed;
  if (status.installation) updateInstallation = status.installation;
  renderRuntimeUpdate(status.runtime);
  const check = $('#v1-settings-check-update');
  const restart = $('#v1-settings-restart');
  $('#v1-settings-update-channel').textContent = status.channel || 'GitHub Releases';
  for (const field of document.querySelectorAll('[data-managed-update]')) field.hidden = !managedUpdate || updateInstallation === 'package';
  check.classList.toggle('primary', managedUpdate);
  check.classList.toggle('secondary', !managedUpdate);
  restart.classList.toggle('primary', !managedUpdate);
  restart.classList.toggle('secondary', managedUpdate);
  if (status.state !== 'checking') manualUpdateCheck = false;
  check.disabled = !managedUpdate || manualUpdateCheck || status.state === 'updating';
  const aboutCheck = $('#v1-about-check-update');
  if (aboutCheck) {
    aboutCheck.disabled = check.disabled;
    aboutCheck.classList.toggle('primary', managedUpdate);
    aboutCheck.classList.toggle('secondary', !managedUpdate);
  }
  const available = status.state === 'available';
  if (status.state === 'failed' && systemUpdateStarted) systemUpdateFailure = status.error || '请检查服务状态后重试。';
  const failed = !!systemUpdateFailure;
  if (available && !systemUpdateStarted && !failed) systemUpdateTarget = status.target || '';
  if (status.state === 'updating') {
    systemUpdateSawRunning = true;
    if (!systemUpdateStarted) systemUpdateStarted = Date.now();
  }
  const completed = systemUpdateStarted && status.state === 'current'
    && (systemUpdateSawRunning || (systemUpdateTarget && status.current === systemUpdateTarget)
      || status.finished_at * 1000 >= systemUpdateStarted - 1000);
  const pending = !!systemUpdateStarted && !completed && !failed;
  if (!available && !pending && !failed) {
    banner.hidden = true;
    if (completed) {
      systemUpdateStarted = 0;
      systemUpdateSawRunning = false;
      note('项目文件已更新，请刷新页面查看最新界面');
    }
    return;
  }
  banner.hidden = false;
  list.replaceChildren();
  for (const file of files) {
    const item = document.createElement('li');
    item.textContent = String(file);
    list.append(item);
  }
  details.hidden = !files.length || pending;
  button.hidden = (!available || pending) && !failed;
  button.disabled = pending;
  button.dataset.action = failed ? 'check' : 'apply';
  button.textContent = failed ? '重新检查' : '更新程序';
  title.textContent = failed ? '程序更新失败' : pending ? '正在更新程序' : '发现程序更新';
  description.textContent = failed ? systemUpdateFailure : pending
    ? '正在等待更新和服务重启，已有连接可能短暂中断。'
    : `GitHub 已发布 ${status.release || status.target || '新版本'}。更新会重启服务，已有连接可能短暂中断。`;
  if (failed) systemUpdateStarted = 0;
}
let updatePrefs = {auto_check: true, auto_apply: true};
let updatePrefSequence = 0;
function rememberUpdatePrefs(status) {
  if (typeof status.auto_check !== 'boolean' || typeof status.auto_apply !== 'boolean') return;
  updatePrefs = {auto_check: status.auto_check, auto_apply: status.auto_apply};
}
async function persistManagementSettings(data) {
  const saved = await call('/api/v1/settings', data);
  updatePrefSequence++;
  rememberUpdatePrefs(saved);
  clearTimeout(systemUpdateTimer);
  if (managedUpdate && (updatePrefs.auto_check || systemUpdateStarted)) void checkSystemUpdate();
  return saved;
}
async function applySystemUpdate() {
  if (!confirm('更新将重启服务，现有连接可能短暂中断。继续吗？')) return;
  systemUpdateStarted = Date.now();
  systemUpdateSawRunning = false;
  systemUpdateFailure = '';
  renderSystemUpdate({state: 'updating'});
  try {
    await call('/api/system/update', {});
  } catch (error) {
    if (!(error instanceof TypeError)) {
      renderSystemUpdate({state: 'failed', error: error.message});
      return;
    }
  }
  if (!systemUpdatePolling) void checkSystemUpdate();
}
async function checkSystemUpdate() {
  if (systemUpdatePolling) return;
  systemUpdatePolling = true;
  clearTimeout(systemUpdateTimer);
  let delay = 600000;
  const prefSequence = updatePrefSequence;
  try {
    const status = await call('/api/system/update');
    if (prefSequence === updatePrefSequence) rememberUpdatePrefs(status);
    if (status.state === 'checking') delay = 3000;
    if (systemUpdateStarted && Date.now() - systemUpdateStarted > updateWaitLimit && status.state !== 'current') {
      renderSystemUpdate({state: 'failed', error: '等待更新超时，请检查服务状态。'});
    } else {
      renderSystemUpdate(status);
    }
  } catch (error) {
    if (systemUpdateStarted && Date.now() - systemUpdateStarted > updateWaitLimit) {
      renderSystemUpdate({state: 'failed', error: '暂时无法连接服务，请检查更新状态。'});
    } else if (manualUpdateCheck) {
      renderSystemUpdate({state: 'unavailable', error: error.message});
    }
  } finally {
    systemUpdatePolling = false;
    if (managedUpdate && (updatePrefs.auto_check || systemUpdateStarted || manualUpdateCheck || delay === 3000)) {
      systemUpdateTimer = setTimeout(checkSystemUpdate, systemUpdateStarted ? 5000 : delay);
    }
  }
}
async function requestSystemUpdateCheck() {
  manualUpdateCheck = true;
  systemUpdateFailure = '';
  $('#v1-settings-check-update').disabled = true;
  const aboutCheck = $('#v1-about-check-update');
  if (aboutCheck) aboutCheck.disabled = true;
  $('#v1-settings-update-status').textContent = '正在检查 GitHub…';
  const aboutStatus = $('#v1-about-update-status');
  if (aboutStatus) aboutStatus.textContent = '正在检查 GitHub…';
  try {
    const status = await call('/api/system/update', {check: true});
    renderSystemUpdate(status);
    if (status.state === 'checking') {
      clearTimeout(systemUpdateTimer);
      systemUpdateTimer = setTimeout(checkSystemUpdate, 1500);
    }
  } catch (error) {
    renderSystemUpdate({state: 'unavailable', error: error.message});
  }
}
$('#system-update-apply').addEventListener('click', () => {
  if ($('#system-update-apply').dataset.action === 'check') void requestSystemUpdateCheck();
  else void applySystemUpdate();
});
$('#v1-settings-check-update').addEventListener('click', requestSystemUpdateCheck);
async function requestFileUpdateCheck() {
  const status = $('#v1-update-files-status');
  const diff = $('#v1-update-diff');
  const list = $('#v1-update-diff-list');
  const button = $('#v1-update-check-files');
  const apply = $('#v1-update-apply-files');
  button.disabled = true;
  if (apply) apply.hidden = true;
  status.textContent = '正在检查 GitHub main 文件差异…';
  diff.hidden = false;
  try {
    const result = await call('/api/v1/updates/runtime', {action: 'check-files'});
    const incremental = result.incremental || {};
    const files = Array.isArray(incremental.changed_files) ? incremental.changed_files : [];
    list.replaceChildren(...files.map(name => {
      const item = document.createElement('li');
      item.textContent = name;
      return item;
    }));
    status.textContent = incremental.error || incremental.note
      || (files.length ? `发现 ${files.length} 个可增量文件` : '没有可增量的文件变更');
    if (apply) apply.hidden = !incremental.apply_ready;
  } catch (error) {
    list.replaceChildren();
    status.textContent = error.message;
    if (apply) apply.hidden = true;
  } finally {
    button.disabled = false;
  }
}
async function requestFileUpdateApply() {
  if (!confirm('将应用已签名的 main 运行时产物并重启服务，现有连接可能短暂中断。继续吗？')) return;
  const status = $('#v1-update-files-status');
  const apply = $('#v1-update-apply-files');
  apply.disabled = true;
  status.textContent = '正在排队应用增量更新…';
  try {
    const result = await call('/api/v1/updates/runtime', {action: 'apply-files'});
    status.textContent = result.started
      ? `已开始应用 ${String(result.queued || '').slice(0, 7)}，请稍后刷新页面`
      : `已排队 ${String(result.queued || '').slice(0, 7)}`;
    note('增量更新已开始');
  } catch (error) {
    status.textContent = error.message;
    note(error.message, {error: true});
  } finally {
    apply.disabled = false;
  }
}
$('#v1-update-check-files')?.addEventListener('click', () => void requestFileUpdateCheck());
$('#v1-update-apply-files')?.addEventListener('click', () => void requestFileUpdateApply());
$('#v1-update-settings-open')?.addEventListener('click', () => {
  const panel = $('#v1-update-settings-panel');
  panel.hidden = !panel.hidden;
});
$('#v1-update-history-open')?.addEventListener('click', () => {
  location.hash = '#settings';
  $('#v1-updates-open')?.click();
});
$('#v1-update-rollback-open')?.addEventListener('click', () => {
  const box = $('#v1-update-releases');
  box.dataset.open = box.dataset.open === '1' ? '' : '1';
  box.hidden = box.dataset.open !== '1' || !box.children.length;
  if (box.hidden) note(box.children.length ? '已收起回滚列表' : '暂无可以回滚的运行版本');
});
$('#v1-update-releases')?.addEventListener('click', async event => {
  const id = event.target.closest('[data-rollback-id]')?.dataset.rollbackId;
  if (!id || !confirm(`确认回滚到 ${id}？服务将重启。`)) return;
  try {
    const result = await call('/api/v1/updates/runtime', {action: 'rollback', id});
    renderRuntimeUpdate(result.runtime);
    note('已切换运行版本，请按需重启 Router');
  } catch (error) {
    note(error.message, {error: true});
  }
});
$('#v1-update-channel')?.addEventListener('change', async event => {
  try {
    const result = await call('/api/v1/updates/runtime', {action: 'channel', channel: event.target.value});
    renderRuntimeUpdate(result.runtime);
    note(event.target.value === 'development' ? '已切换到开发通道（不会自动应用未签名产物）' : '已切换到正式通道');
  } catch (error) {
    note(error.message, {error: true});
  }
});
$('#v1-update-migrate')?.addEventListener('click', async () => {
  try {
    await call('/api/v1/updates/runtime', {action: 'migrate'});
    note('迁移已开始，后台可能短暂断开。恢复后刷新页面检查运行版本。');
  } catch (error) {
    note(error.message, {error: true});
  }
});
$('#v1-about-check-update')?.addEventListener('click', requestSystemUpdateCheck);
void checkSystemUpdate();
let noteHideTimer = 0;
function humanizeApiMessage(text) {
  if (text == null) return '';
  const msg = String(text).replace(/\s+/g, ' ').trim();
  if (!msg) return '';
  if (/^ok$/i.test(msg)) return '';
  if (/loaded (pool|ike secret|connection)|successfully loaded \d+ (pools|connections)/i.test(msg)) {
    return 'VPN 服务已在服务器加载完成，可在手机尝试连接';
  }
  if (/(swanctl|strongswan|charon)/i.test(msg) && msg.length > 60) {
    return 'VPN 配置已应用；若仍无法连接，请查看主栏「IKEv2 连接监视」';
  }
  return msg;
}
function setToastControls(on) {
  if (!message) return;
  message.classList.toggle('has-controls', on);
  const stop = $('#toast-stop');
  const pause = $('#toast-pause');
  if (stop) stop.hidden = !on;
  if (pause) pause.hidden = !on;
}
function clearNote() {
  clearTimeout(noteHideTimer);
  noteHideTimer = 0;
  if (!message) return;
  const label = $('#message-text');
  if (label) label.textContent = '';
  message.hidden = true;
  message.classList.remove('is-visible', 'is-error');
  setToastControls(false);
  $('#toast-host')?.setAttribute('aria-hidden', 'true');
}
function note(text, opts = {}) {
  if (!message) return;
  text = humanizeApiMessage(text);
  if (!text) {
    clearNote();
    return;
  }
  clearTimeout(noteHideTimer);
  const label = $('#message-text');
  if (label) label.textContent = text;
  message.hidden = false;
  message.classList.add('is-visible');
  message.classList.toggle('is-error', !!opts.error);
  setToastControls(!!opts.controls);
  $('#toast-host')?.setAttribute('aria-hidden', 'false');
  const inProgress = /^正在/.test(text);
  const duration = opts.duration ?? (inProgress ? 0 : (opts.error ? 6500 : 4200));
  if (duration > 0) {
    noteHideTimer = setTimeout(clearNote, duration);
  }
}
function setConnectionLive(live) {
  $('.avatar')?.classList.toggle('is-live', live);
  const env = $('.environment');
  if (env) {
    env.classList.toggle('is-live', live);
    env.title = live ? '有设备处于正常 VPN 连接' : '暂无正常连接的 VPN 设备';
  }
}
function updateDeviceConnectionIndicator(ike) {
  const clients = ike?.clients || [];
  const normal = !!ike?.enabled
    && !!ike?.running
    && clients.some((c) => c.net_status === 'ok' || c.net_status === 'low_traffic');
  setConnectionLive(normal);
}
function renderMobileConnect(url) {
  const panel = $('#mobile-qr');
  const hint = $('#mobile-qr-hint');
  if (!panel || !hint) return;
  panel.replaceChildren();
  panel.classList.remove('is-empty');
  if (!url || typeof qrcode !== 'function') {
    panel.classList.add('is-empty');
    panel.textContent = '暂无订阅二维码';
    hint.textContent = '连接服务并生成订阅地址后可扫码导入';
    return;
  }
  try {
    const qr = qrcode(0, 'M');
    qr.addData(url);
    qr.make();
    panel.innerHTML = qr.createSvgTag(3, 2);
    const svg = panel.querySelector('svg');
    if (svg) {
      svg.setAttribute('width', '132');
      svg.setAttribute('height', '132');
      svg.setAttribute('role', 'img');
      svg.setAttribute('aria-label', '订阅二维码');
    }
    hint.textContent = '使用手机客户端扫描导入订阅';
  } catch (error) {
    panel.classList.add('is-empty');
    panel.textContent = '二维码生成失败';
    hint.textContent = '请复制上方订阅链接，在手机客户端手动添加';
  }
}
function renderIkev2Monitor(ike) {
  const panel = $('#ikev2-monitor');
  const body = $('#ikev2-monitor-body');
  const diagWrap = $('#ikev2-diagnosis');
  const diagList = $('#ikev2-diagnosis-list');
  const summary = $('#ikev2-monitor-summary');
  const countEl = $('#ikev2-monitor-count');
  if (!panel || !body) return;
  clearTimeout(ikev2Timer);
  const syncBtn = $('#ikev2-sync-nat');
  if (!ike.enabled) {
    panel.hidden = true;
    if (syncBtn) syncBtn.hidden = true;
    updateDeviceConnectionIndicator(ike);
    return;
  }
  panel.hidden = false;
  if (syncBtn) syncBtn.hidden = !ike.running;
  const clients = ike.clients || [];
  const connecting = clients.filter((client) => client.net_status === 'connecting');
  countEl.textContent = connecting.length
    ? `${clients.length} 台 · ${connecting.length} 正在连接`
    : `${ike.client_count || clients.length} 台在线`;
  if (!ike.running) {
    summary.textContent = 'IKEv2 已启用，但 strongSwan 未运行';
  } else if (connecting.length) {
    summary.textContent = `正在连接 ${connecting.map((client) => client.public_ip || '未知地址').join('、')}`;
  } else if (!clients.length) {
    summary.textContent = 'strongSwan 运行中 · 正在监视 UDP 500';
  } else {
    summary.textContent = `strongSwan 运行中 · 地址池 ${ike.pool || '10.66.0.0/24'}`;
  }
  body.replaceChildren();
  if (!clients.length) {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 8;
    cell.className = 'ikev2-client-empty';
    cell.textContent = '暂无连接。手机向 UDP 500 发出第一包后，这里会显示正在连接。';
    row.append(cell);
    body.append(row);
  } else {
    for (const client of clients) {
      const row = document.createElement('tr');
      const status = document.createElement('td');
      const badge = document.createElement('span');
      badge.className = `ikev2-badge ikev2-badge-${client.net_status || 'no_traffic'}`;
      badge.textContent = client.net_label || '—';
      status.append(badge);
      const cells = [
        ['VPN IP', client.virtual_ip || '—'],
        ['公网 IP', client.public_ip || '—'],
        ['本机 ID', client.remote_id || '—'],
        ['时长', client.uptime || '—'],
        ['上行', fmtTrafficBytes(client.up_bytes)],
        ['下行', fmtTrafficBytes(client.down_bytes)],
        ['合计', fmtTrafficBytes(client.total_bytes)],
      ];
      for (const [label, text] of cells) {
        const td = document.createElement('td');
        td.dataset.label = label;
        td.textContent = text;
        row.append(td);
      }
      status.dataset.label = '状态';
      row.append(status);
      body.append(row);
    }
  }
  const diagnosis = ike.diagnosis || [];
  diagList.replaceChildren();
  if (diagnosis.length) {
    diagWrap.hidden = false;
    for (const item of diagnosis) {
      const li = document.createElement('li');
      li.className = `ikev2-diag-${item.level || 'info'}`;
      if (item.title) {
        const title = document.createElement('strong');
        title.textContent = item.title;
        li.append(title);
      }
      li.append(document.createTextNode(item.text));
      diagList.append(li);
    }
  } else {
    diagWrap.hidden = true;
  }
  if (ike.running) {
    ikev2Timer = setTimeout(() => refreshIkev2Only().catch(() => refresh().catch(() => {})), 1500);
  }
  updateDeviceConnectionIndicator(ike);
}
async function refreshIkev2Only() {
  const seq = ++ikev2LiveSeq;
  const data = await call('/api/ikev2/live');
  if (seq !== ikev2LiveSeq || !data.ikev2) return;
  applyIkev2Ui(data.ikev2);
}
function scheduleProbeRefresh(interval) {
  probeTimer = setTimeout(async () => {
    const seq = refreshSeq + 1;
    try {
      await refresh();
    } catch {
      if (seq === refreshSeq) scheduleProbeRefresh(interval);
    }
  }, interval);
}
async function refresh() {
  const seq = ++refreshSeq;
  ++ikev2LiveSeq;
  clearTimeout(probeTimer);
  clearTimeout(ikev2Timer);
  const data = await call('/api/status');
  if (seq !== refreshSeq) return;
  routerToken = data.token || '';
  const { subscription, ikev2Profile } = applyPublicUrls(data);
  const subInput = $('#subscription');
  if (subInput) subInput.value = subscription;
  const mobileconfig = $('#ikev2-mobileconfig-url');
  if (mobileconfig) mobileconfig.value = ikev2Profile || '';
  renderMobileConnect(subscription);
  renderPublicSubscription(data);
  const subSummary = $('#subscription-summary');
  if (subSummary) {
    if (!data.token) {
      subSummary.textContent = '连接服务后生成';
    } else if (publicUrls.subscription_https) {
      subSummary.textContent = `HTTPS 订阅 · ${publicUrls.host}`;
    } else if (publicUrls.host) {
      subSummary.textContent = `对外 ${publicUrls.host} · 管理 ${publicUrls.admin_url || '—'}`;
    } else {
      subSummary.textContent = `已生成 · ${data.nodes.length} 个节点`;
    }
  }
  renderPublicAccessHints(data.acme || {}, publicUrls);
  $('#acme-card-summary').textContent = data.acme?.enabled ? (formatAcmeLine(data.acme) || '已开启') : '尚未启用证书';
  $('#count').textContent = `${data.sources.length} 个订阅`;
  const addSummary = $('#add-source-summary');
  if (addSummary) {
    addSummary.textContent = data.sources.length
      ? `已有 ${data.sources.length} 个上游订阅 · 点击可再添加`
      : '尚无上游订阅 · 点击添加';
  }
  const list = $('#sources'); list.replaceChildren();
  if (!data.sources.length) { const empty = document.createElement('p'); empty.className='empty'; empty.textContent='还没有订阅，添加后可在这里查看。'; list.append(empty); }
  for (const source of data.sources) {
    const row = document.createElement('div'); row.className='row';
    row.dataset.sourceName = source.name;
    row.dataset.sourceEnabled = source.enabled !== false;
    const info = document.createElement('div'); const name = document.createElement('strong'); name.textContent=source.name;
    const meta = document.createElement('p');
    const schedule = source.enabled !== false && source.auto_update ? ` · 每 ${Math.round(source.update_interval / 60)} 分钟检查` : '';
    meta.textContent = `${source.enabled === false ? '已停用' : '已启用'} · ${source.count} 个节点${schedule} · ${source.updated ? '更新于 ' + new Date(source.updated * 1000).toLocaleString() : '尚未成功更新'}${source.error ? ' · ' + source.error : ''}`;
    info.append(name,meta); const actions=document.createElement('div'); actions.className='actions';
    for (const [text,path] of [['更新','/api/refresh'],['删除','/api/delete']]) {
      const button=document.createElement('button'); button.className='secondary'; button.textContent=text;
      button.disabled = path === '/api/refresh' && source.enabled === false;
      button.onclick=async()=>{if(path==='/api/delete' && !confirm(`删除订阅「${source.name}」？`)) return; button.disabled=true; try {await call(path,{id:source.id});note('操作完成');} catch(e){note(e.message,{error:true});} finally {await refresh();}};
      actions.append(button);
    }
    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'secondary';
    edit.textContent = '设置';
    edit.onclick = () => openSourceSettings(source);
    actions.prepend(edit);
    row.append(info,actions); list.append(row);
  }
  const nodes = $('#nodes'); nodes.replaceChildren();
  $('#node-count').textContent = `${data.nodes.length} 个节点`;
  const local = data.local_node || {};
  setLocalTrafficEnabled(!!local.enabled);
  syncFixedUpstream(local, data.nodes);
  syncLocalForm(local, data.acme || {});

  const localStatus = $('#local-status');
  const localSummary = $('#local-node-summary');
  const localLine = local.enabled
    ? (local.running
      ? `已启用 · ${local.name || '本地网络'} · ${local.public_host}:${local.port}`
      : '已启用，但 Xray 未在运行')
    : '尚未启用';
  if (localStatus) localStatus.textContent = localLine;
  const protocol = local.egress_protocol_info;
  const protocolLine = protocol ? `TCP：${protocol.tcp} · UDP：${protocol.udp}` : 'TCP / UDP 配置状态暂不可用';
  const quic = local.quic || {};
  const quicLine = !local.enabled ? '' : (quic.available ? `Hysteria 2 UDP ${quic.hy2_port}、TUIC UDP ${quic.tuic_port} 已加入订阅` : (quic.error || ''));
  $('#local-protocol-status').textContent = protocolLine;
  $('#local-protocol-detail').textContent = [protocol?.detail || '', quicLine].filter(Boolean).join(' ');
  const singboxInstall = $('#local-singbox-install');
  if (singboxInstall) singboxInstall.hidden = !!quic.available;
  $('#local-node-protocol-summary').textContent = quicLine || protocolLine;
  if (localSummary) {
    localSummary.textContent = local.enabled ? localLine : '尚未启用 · 点击查看可启用或修改';
  }
  if (seq !== refreshSeq) return;
  applyIkev2Ui(data.ikev2 || {}, { ikev2Profile: ikev2Profile });
  const manualCount = data.nodes.filter(n => n.manual).length;
  const manualSummary = $('#manual-summary');
  if (manualSummary) {
    manualSummary.textContent = manualCount
      ? `已导入 ${manualCount} 个手动节点`
      : '暂无手动节点 · 点击粘贴链接导入';
  }
  renderDefaultProbeTarget(data.probe_target);
  for (const kind of ['update', 'probe']) {
    const job = data.jobs[kind];
    const button = $(`#batch-${kind}`);
    button.dataset.running = String(job.running);
    if (kind === 'update') {
      button.textContent = job.running ? '停止' : '批量更新';
      $('#update-progress').textContent = job.total ? `${job.running ? '进行中' : job.cancelled ? '已停止' : '已完成'} ${job.done}/${job.total}${job.failed ? ` · ${job.failed} 未通过或跳过` : ''}` : '';
    }
  }
  if (!data.nodes.length) { const empty=document.createElement('p'); empty.className='empty'; empty.textContent='暂无节点'; nodes.append(empty); }
  for (const node of data.nodes) {
    const row=document.createElement('div'); row.className='row';
    row.dataset.nodeTitle = node.title;
    row.dataset.nodeKind = node.local ? 'local' : node.manual ? 'manual' : 'source';
    row.dataset.nodeEnabled = node.enabled;
    const label=document.createElement('div'); label.className='node-label';
    const title=document.createElement('span'); title.textContent=`${node.title} · ${node.protocol.toUpperCase()}`;
    if(node.probe){const state=document.createElement('small'); state.className='probe-result'; state.textContent=node.probe.status==='ok' ? `${node.probe.target||'Google'} 可用 · ${node.probe.latency_ms} ms` : node.probe.detail; title.append(document.createElement('br'),state);}
    if(!node.enabled){const excluded=document.createElement('small');excluded.className='probe-result';excluded.textContent='此前已移出订阅';title.append(document.createElement('br'),excluded);}
    const sourceLabel = node.local ? '本地发布' : node.manual ? '手动导入' : '订阅导入';
    const source = document.createElement('span'); source.className = 'v1-row-meta'; source.textContent = sourceLabel;
    label.append(title, source); row.append(label);
    const test=document.createElement('button'); test.className='secondary'; test.textContent=node.probe?.status==='testing'?'测试中…':'测试连接'; test.disabled=node.probe?.status==='testing';
    test.onclick=async()=>{test.disabled=true;try{await call('/api/probe',{id:node.id,...probeTargetPayload()});await refresh();}catch(e){test.disabled=false;note(e.message);}};row.append(test);
    const toggle = document.createElement('button');
    toggle.className = 'secondary';
    toggle.textContent = node.enabled ? '从订阅移除' : '加入订阅';
    toggle.onclick = async () => {
      toggle.disabled = true;
      try {
        await call('/api/node', {id: node.id, enabled: !node.enabled});
        await refresh();
        note(node.enabled ? '已从订阅移除' : '已加入订阅');
      } catch (error) { toggle.disabled = false; note(error.message, {error: true}); }
    };
    row.append(toggle);
    if(node.manual){const button=document.createElement('button');button.className='secondary';button.textContent='移除手动节点';button.onclick=async()=>{if(!confirm('移除这个手动导入的节点？'))return;try{await call('/api/manual/delete',{id:node.id});await refresh();note('手动节点已移除');}catch(e){note(e.message,{error:true});}};row.append(button);}
    nodes.append(row);
  }
  window.hopworldStatus = data;
  window.dispatchEvent(new CustomEvent('hopworld:status', {detail: data}));
  const probing = data.jobs.probe.running || data.nodes.some(node => node.probe?.status === 'testing');
  if (probing || data.jobs.update.running) scheduleProbeRefresh(probing ? 800 : 1800);
}
const MODAL_IDS = {
  subscription: 'subscription-modal',
  'add-source': 'add-source-modal',
  'source-settings': 'source-settings-modal',
  'local-node-notice': 'local-node-notice-modal',
  'local-node': 'local-node-modal',
  acme: 'acme-modal',
  ikev2: 'ikev2-modal',
  'ikev2-settings': 'ikev2-settings-modal',
  manual: 'manual-modal',
};
function syncModalBodyScroll() {
  const open = Object.values(MODAL_IDS).some(id => {
    const m = $('#' + id);
    return m && !m.hidden;
  });
  document.body.classList.toggle('modal-open', open);
}
function closeModal(key) {
  const modal = $(`#${MODAL_IDS[key]}`);
  if (!modal || modal.hidden) return;
  if (key === 'local-node') { localEditing = false; clearInterval(autoObservationTimer); }
  if (key === 'ikev2') {
    endIkev2Edit();
    const settings = $('#ikev2-settings-modal');
    if (settings) settings.hidden = true;
  }
  modal.hidden = true;
  syncModalBodyScroll();
  if (key === 'local-node-notice') localNodeNoticeSuppressed = true;
  if (key !== 'ikev2-settings' && key !== 'local-node-notice' && key !== 'update-settings') refresh().catch(() => {});
}
async function openIkev2SettingsModal() {
  try {
    const data = await call('/api/status');
    routerToken = data.token || routerToken;
    const ike = data.ikev2 || {};
    syncIkev2GuideFields(ike);
    const { ikev2Profile } = applyPublicUrls({ ...data, ikev2: ike });
    const mcUrl = $('#ikev2-mobileconfig-url');
    if (mcUrl) mcUrl.value = ikev2Profile || '';
    syncIkev2SettingsUi(ike);
  } catch {
    syncIkev2GuideFields();
    syncIkev2SettingsUi({});
  }
  const modal = $('#ikev2-settings-modal');
  if (!modal) return;
  modal.hidden = false;
  syncModalBodyScroll();
}
function openSubscriptionModal() {
  const modal = $('#subscription-modal');
  if (!modal) return;
  modal.hidden = false;
  syncModalBodyScroll();
  const url = $('#subscription')?.value;
  if (url) renderMobileConnect(url);
}
function openSourceSettings(source) {
  const form = $('#v1-source-form');
  form.dataset.sourceId = source.id;
  $('#v1-source-name').value = source.name;
  $('#v1-source-url').value = source.url || '';
  $('#v1-source-enabled').checked = source.enabled !== false;
  $('#v1-source-auto').checked = source.auto_update !== false;
  $('#v1-source-interval').value = (source.update_interval || 3600) / 60;
  $('#source-settings-modal').hidden = false;
  syncModalBodyScroll();
  $('#v1-source-name').focus();
}
$('#v1-source-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('[type="submit"]');
  button.disabled = true;
  try {
    await call('/api/sources/settings', {
      id: Number(form.dataset.sourceId), name: $('#v1-source-name').value, url: $('#v1-source-url').value,
      enabled: $('#v1-source-enabled').checked, auto_update: $('#v1-source-auto').checked,
      update_interval: Math.round(Number($('#v1-source-interval').value) * 60),
    });
    closeModal('source-settings');
    note('订阅设置已保存');
  } catch (error) { note(error.message, {error: true}); }
  finally { button.disabled = false; }
});
function openAddSourceModal() {
  const modal = $('#add-source-modal');
  if (!modal) return;
  modal.hidden = false;
  syncModalBodyScroll();
  $('#name')?.focus();
}
function closeAddSourceModal() { closeModal('add-source'); }
function openLocalNodeModal(egressMode) {
  localEditing = false;
  const modal = $('#local-node-modal');
  if (!modal) return;
  modal.hidden = false;
  syncModalBodyScroll();
  const status = window.hopworldStatus;
  if (typeof egressMode === 'string' && status?.local_node) {
    syncLocalForm(status.local_node, status.acme);
    localEditing = true;
    const mode = $('#local-egress-mode');
    mode.value = egressMode;
    syncLocalForm(status.local_node, status.acme);
    mode.focus();
  }
  refresh().then(refreshAutoObservations).catch(() => {});
  clearInterval(autoObservationTimer);
  autoObservationTimer = setInterval(refreshAutoObservations, 10000);
}
async function refreshAutoObservations() {
  if ($('#local-node-modal').hidden || $('#local-egress-mode').value !== 'auto' || autoObservationBusy) return;
  autoObservationBusy = true;
  try {
    const data = await call('/api/local/auto-status');
    const messages = {inactive: '启用自动换线后显示', stopped: '本地节点未运行', empty: '没有符合条件的候选节点', unavailable: '暂时无法读取 Xray 探测状态', ok: '最近探测结果'};
    $('#local-auto-observation-status').textContent = messages[data.state] || '暂时无法读取状态';
    const list = $('#local-auto-observations');
    list.replaceChildren();
    for (const node of data.nodes || []) {
      const item = document.createElement('li');
      const labels = {pending: '等待探测', ok: '可用', failed: '不可用', stale: '结果已过期'};
      const delay = node.delay_ms == null ? '' : ` · ${node.delay_ms} ms`;
      const checked = node.last_checked ? ` · ${new Date(node.last_checked * 1000).toLocaleTimeString()}` : '';
      item.textContent = `${node.title} · ${labels[node.state] || '未知'}${delay}${checked}`;
      list.append(item);
    }
  } catch {
    $('#local-auto-observation-status').textContent = '暂时无法读取 Xray 探测状态';
    $('#local-auto-observations').replaceChildren();
  } finally { autoObservationBusy = false; }
}
function openIkev2Modal() {
  endIkev2Edit();
  const modal = $('#ikev2-modal');
  if (!modal) return;
  modal.hidden = false;
  syncModalBodyScroll();
  refresh().catch(() => {});
}
function openManualModal() {
  const modal = $('#manual-modal');
  if (!modal) return;
  modal.hidden = false;
  syncModalBodyScroll();
  $('#links')?.focus();
}
const addSourceOpen = $('#add-source-open');
if (addSourceOpen) addSourceOpen.onclick = openAddSourceModal;
$('#public-subscription-qr').addEventListener('click', openSubscriptionModal);
$('#local-node-open')?.addEventListener('click', openLocalNodeModal);
let acmeFormState;
let acmeIpCheck = 0;
let acmeVerified;
let acmeBusy = false;

function acmeRequest() {
  const family = $('#acme-subject').value;
  return {family, target: family === 'domain' ? $('#acme-domain').value : $('#acme-ip-address').value.trim(),
    email: $('#local-acme-email').value.trim(), challenge: $('#local-acme-challenge').value};
}

function syncAcmeActions() {
  const request = acmeRequest();
  const enabled = $('#local-acme').checked;
  const available = !!request.email && (request.family !== 'domain' || !!request.target);
  const verified = acmeVerified?.request === JSON.stringify(request);
  $('#acme-preflight').disabled = acmeBusy || !enabled || !available;
  $('#acme-save').disabled = acmeBusy || (enabled && !verified);
  $('#acme-save').textContent = enabled ? '申请证书' : '关闭自动续期';
}

function invalidateAcmePreflight() {
  acmeVerified = null;
  $('#acme-preflight-status').textContent = '';
  syncAcmeActions();
}

function ipCertificateCheckText(result) {
  const cert = result.certificate;
  const reusable = cert?.address === result.address && cert?.expires > Date.now() / 1000;
  return [
    result.address ? `检测到 ${result.address}` : result.address_error,
    reusable && `证书有效至 ${new Date(cert.expires * 1000).toLocaleString()}`,
    $('#local-acme-challenge').value === 'http' && result.certbot_status,
    $('#local-acme-challenge').value === 'http' && result.port_status,
  ].filter(Boolean).join(' · ');
}

function syncAcmeSubject() {
  const isIP = $('#acme-subject').value !== 'domain';
  const enabled = $('#local-acme').checked;
  $('#acme-domain-fields').hidden = isIP;
  $('#acme-ip-fields').hidden = !isIP;
  for (const option of $('#local-acme-challenge').options) {
    option.hidden = option.disabled = isIP && ['auto', 'dns'].includes(option.value);
  }
  $('#acme-challenge-fields').hidden = !enabled;
  $('#acme-domain').required = !isIP && enabled;
  $('#local-acme-challenge').disabled = !enabled;
  $('#local-acme-email').required = enabled;
  $('#acme-ip-hint').hidden = !isIP;
  $('#acme-ip-status').hidden = !isIP;
  $('#local-acme').nextElementSibling.textContent = isIP
    ? '申请证书并自动续期（Let’s Encrypt）' : "免费 SSL 证书（Let's Encrypt）";
  if (isIP) {
    $('#local-acme-fw-wrap').hidden = true;
    $('#local-acme-status').hidden = true;
  } else {
    syncAcmeFwHint({enabled}, true);
  }
  syncAcmeActions();
}

async function selectAcmeSubject() {
  const family = $('#acme-subject').value;
  const request = ++acmeIpCheck;
  const domain = acmeFormState?.domain || {};
  const record = acmeFormState?.ip?.[family];
  $('#local-acme').checked = family === 'domain' ? domain.source !== 'manual' : record?.enabled ?? true;
  $('#local-acme-email').value = record?.email || domain.email || '';
  $('#acme-ip-address').value = record?.manual ? record.address : '';
  $('#local-acme-challenge').value = family === 'domain' ? domain.challenge || 'auto' : record?.challenge || 'http';
  invalidateAcmePreflight();
  syncAcmeSubject();
  if (family === 'domain') {
    renderAcmeStatus(domain, true);
    return;
  }
  const status = $('#acme-ip-status');
  status.textContent = '正在检测公网地址和签发环境…';
  try {
    const result = await call('/api/acme/ip?family=' + encodeURIComponent(family));
    if (request === acmeIpCheck) status.textContent = ipCertificateCheckText(result);
  } catch (error) {
    if (request === acmeIpCheck) status.textContent = error.message;
  }
}

$('#acme-subject').addEventListener('change', selectAcmeSubject);
for (const id of ['acme-ip-address', 'acme-domain', 'local-acme-email', 'local-acme-challenge']) {
  $('#' + id).addEventListener('input', invalidateAcmePreflight);
}
$('#local-acme').addEventListener('change', invalidateAcmePreflight);
$('#acme-preflight').addEventListener('click', async () => {
  const request = acmeRequest();
  const snapshot = JSON.stringify(request);
  const status = $('#acme-preflight-status');
  acmeBusy = true;
  acmeVerified = null;
  syncAcmeActions();
  status.textContent = '正在由 Let’s Encrypt 测试环境验证…';
  try {
    const result = await call('/api/acme/preflight', request);
    if (snapshot === JSON.stringify(acmeRequest())) {
      acmeVerified = {request: snapshot, target: result.target};
      status.textContent = `${result.target} 已通过外部 ACME 验证，可以申请正式证书。`;
    }
  } catch (error) {
    if (snapshot === JSON.stringify(acmeRequest())) status.textContent = error.message;
  } finally {
    acmeBusy = false;
    syncAcmeActions();
  }
});
$('#acme-open')?.addEventListener('click', async () => {
  try {
    const [data, {domains}] = await Promise.all([call('/api/status'), call('/api/v1/domains')]);
    const acme = data.acme || {};
    const select = $('#acme-domain');
    select.replaceChildren(new Option(domains.length ? '请选择已绑定域名' : '请先绑定域名', ''),
      ...domains.map(item => new Option(item.host, item.host)));
    if (domains.some(item => item.host === acme.domain)) select.value = acme.domain;
    $('#local-acme-challenge').value = acme.challenge || 'auto';
    acmeFormState = {domain: acme, ip: data.ip_certificates || {}};
    $('#acme-subject').value = 'domain';
    void selectAcmeSubject();
    $('#acme-modal').hidden = false;
    syncModalBodyScroll();
  } catch (error) { note(error.message, {error:true}); }
});
$('#acme-form').addEventListener('submit', async e => {
  e.preventDefault();
  const enabled = $('#local-acme').checked;
  const request = acmeRequest();
  const {family, target, email, challenge} = request;
  if (acmeBusy || enabled && acmeVerified?.request !== JSON.stringify(request)) return;
  acmeBusy = true;
  syncAcmeActions();
  if (enabled) note('正在申请证书…', {duration: 0});
  try {
    if (family !== 'domain') {
      await call('/api/acme/ip', {family, enabled, email, challenge, address: target, verified_target: acmeVerified?.target});
      clearNote();
      await refresh();
      closeModal('acme');
      note(enabled ? '公网 IP 证书已签发或复用' : '此入口的公网 IP 证书续期已关闭');
      return;
    }
    const result = await call('/api/acme', {enabled, email, challenge, domain: target});
    clearNote();
    await refresh();
    if (enabled && result.acme?.error) {
      renderAcmeStatus(result.acme, true);
      note('已保存，证书未签发', {error:true});
    } else {
      closeModal('acme');
      note(enabled ? '证书设置已保存' : 'ACME 自动续签已关闭');
    }
  } catch (error) {
    clearNote();
    $('#acme-preflight-status').textContent = error.message;
  }
  finally { acmeBusy = false; syncAcmeActions(); }
});
$('#local-node-notice-go')?.addEventListener('click', () => {
  localNodeNoticeSuppressed = true;
  closeModal('local-node-notice');
  openLocalNodeModal();
});
$('#ikev2-open')?.addEventListener('click', openIkev2Modal);
$('#ikev2-settings-open')?.addEventListener('click', openIkev2SettingsModal);
$('#manual-open')?.addEventListener('click', openManualModal);
$('#v1-node-file').addEventListener('change', async event => {
  const input = event.currentTarget;
  const file = input.files[0];
  if (!file) return;
  try {
    if (file.size > 16384) throw new Error('文件内容超过 16 KB 请求限制');
    const content = await file.text();
    if (new TextEncoder().encode(JSON.stringify({content})).length > 16384) throw new Error('文件内容超过 16 KB 请求限制');
    $('#links').value = content;
  } catch (error) { note(error.message, {error: true}); }
  finally { input.value = ''; }
});
document.querySelectorAll('[data-modal-close]').forEach(el => {
  const target = el.getAttribute('data-modal-close');
  if (target && MODAL_IDS[target]) el.onclick = () => closeModal(target);
});
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  for (const key of ['source-settings', 'update-settings', 'ikev2-settings', 'local-node-notice', 'subscription', 'manual', 'ikev2', 'local-node', 'acme', 'add-source']) {
    const m = $(`#${MODAL_IDS[key]}`);
    if (m && !m.hidden) {
      closeModal(key);
      break;
    }
  }
});
$('#add').onsubmit=async e=>{e.preventDefault(); const button=e.submitter; button.disabled=true; note('正在更新订阅…'); try {await call('/api/sources',{name:$('#name').value,url:$('#url').value}); e.target.reset(); closeAddSourceModal(); await refresh(); note('订阅已添加，请查看更新状态');} catch(error){note(error.message,{error:true});} finally {button.disabled=false;}};
$('#manual').onsubmit=async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{await call('/api/manual',{content:$('#links').value});e.target.reset();closeModal('manual');await refresh();note('节点已导入');}catch(error){note(error.message,{error:true});}finally{button.disabled=false;}};
function localPayload(enabled){
  const auto = $('#local-host-mode').value === 'auto';
  const ddns = $('#local-host-mode').value === 'ddns';
  return {
    enabled,
    name: $('#local-name').value,
    public_host: auto && !ddns ? '' : $('#local-host').value,
    port: Number($('#local-port').value),
    auto_public_host: auto,
    ddns_enabled: ddns,
    public_host_prefer: $('#local-host-prefer')?.value || 'ipv4',
    egress_mode: $('#local-egress-mode').value,
    upstream_id: $('#local-upstream').value,
    local_proxy: $('#local-proxy-choice').value,
    auto_target: $('#local-auto-target').value,
    auto_interval: Number($('#local-auto-interval').value),
  };
}
$('#local-egress-mode').addEventListener('change', () => {
  syncFixedUpstream(window.hopworldStatus?.local_node);
  $('#local-proxy-settings').hidden = $('#local-egress-mode').value !== 'local_proxy';
  $('#local-auto-settings').hidden = $('#local-egress-mode').value !== 'auto';
  showLocalProxyProtocol();
  void refreshAutoObservations();
});
$('#local-proxy-choice').addEventListener('change', showLocalProxyProtocol);
$('#local-proxy-scan').addEventListener('click', async () => {
  const button = $('#local-proxy-scan');
  const raw = $('#local-proxy-port').value;
  button.disabled = true;
  note('正在扫描本机代理…');
  try {
    const data = await call('/api/local/discover', raw ? {ports: [Number(raw)]} : {});
    const select = $('#local-proxy-choice');
    select.replaceChildren(new Option(data.proxies.length ? '请选择代理' : '未发现可用代理', ''));
    data.proxies.forEach(item => {
      const udp = {supported: '已验证', unsupported: '不支持', unknown: '待验证'}[item.udp] || '待验证';
      const option = new Option(`${item.protocol.toUpperCase()} · 127.0.0.1:${item.port} · TCP 已验证 / UDP ${udp}`, `${item.protocol}:${item.port}`);
      option.dataset.scanned = 'true';
      option.dataset.udp = item.udp || 'unknown';
      select.add(option);
    });
    showLocalProxyProtocol();
    note(data.proxies.length ? `发现 ${data.proxies.length} 个本机代理，请选择` : '未发现可用代理');
  } catch (error) { note(error.message, {error: true}); }
  finally { button.disabled = false; }
});
$('#local-host-mode').addEventListener('change', () => {
  $('#local-check-address-result').textContent = '';
  syncLocalForm(window.hopworldStatus?.local_node, window.hopworldStatus?.acme, true);
});
$('#local-check-address').addEventListener('click', async event => {
  const button = event.currentTarget;
  const result = $('#local-check-address-result');
  const family = $('#local-host-prefer').value;
  const auto = $('#local-host-mode').value === 'auto';
  const host = $('#local-host').value.trim();
  if (!auto && !host) {
    result.textContent = '请先填写已设置 DNS 解析的域名。';
    $('#local-host').focus();
    return;
  }
  button.disabled = true;
  result.textContent = auto ? '正在检测公网地址…' : '正在验证域名指向…';
  const check = family => call('/api/v1/cores/address-check', {mode: 'auto_ip', family});
  try {
    const response = auto
      ? await (family === 'ipv4_first' ? check('ipv4').catch(() => check('ipv6')) : check(family))
      : await call('/api/v1/cores/address-check', {mode: 'domain', family, host});
    if (($('#local-host-mode').value === 'auto') === auto &&
        $('#local-host-prefer').value === family && $('#local-host').value.trim() === host) {
      result.textContent = auto ? `已检测到 ${response.address} · 公网入站待验证` : response.detail;
    }
  } catch (error) {
    result.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
$('#local-acme')?.addEventListener('change', () => {
  syncAcmeSubject();
  if ($('#acme-subject').value === 'domain') renderAcmeStatus({enabled: $('#local-acme').checked}, true);
});
$('#local-acme-challenge')?.addEventListener('change', () => syncAcmeFwHint({}, true));
$('#local-host')?.addEventListener('input', () => {
  $('#local-check-address-result').textContent = '';
  syncLocalHostDdnsHint();
  scheduleLocalDnsHintsPreview();
});
$('#local-host-prefer')?.addEventListener('change', () => {
  $('#local-check-address-result').textContent = '';
  scheduleLocalDnsHintsPreview();
});
$('#local-dns-verify')?.addEventListener('click', () => { void runLocalDnsVerify(); });
$('#local-edit').onclick=()=>{localEditing=true;syncLocalForm({enabled:true,name:$('#local-name').value,public_host:$('#local-host').value,port:Number($('#local-port').value),auto_public_host:$('#local-host-mode').value === 'auto'});$('#local-name').focus();};
$('#local-cancel').onclick=async()=>{localEditing=false;await refresh();note('已取消编辑');};
$('#local-form').onsubmit=e=>e.preventDefault();
$('#local-singbox-install')?.addEventListener('click', async () => {
  const button = $('#local-singbox-install');
  button.disabled = true;
  note('正在检测 sing-box…');
  try {
    await call('/api/local/singbox', {});
    await refresh();
    note('已检测到 sing-box');
  } catch (error) {
    note(error.message, {error: true});
  } finally {
    button.disabled = false;
  }
});
$('#local-toggle').onclick=async()=>{
  const button=$('#local-toggle');
  const on=button.textContent==='停用';
  if(on&&!confirm('停用后订阅中将移除本地网络节点，继续吗？'))return;
  button.disabled=true;
  note(on?'正在停用…':'正在启用本地网络节点…');
  try{
    await call('/api/local-node',localPayload(!on));
    localEditing=false;
    await refresh();
    note(on?'本地网络节点已停用':'本地网络节点已启用，并已加入订阅');
  }catch(error){note(error.message,{error:true});}finally{button.disabled=false;}
};
$('#local-save').onclick=async()=>{
  const button=$('#local-save');
  const enabled=!!window.hopworldStatus?.local_node?.enabled;
  button.disabled=true;
  note('正在保存…');
  try{
    await call('/api/local-node',localPayload(enabled));
    localEditing=false;
    await refresh();
    note(enabled ? '已保存，请更新订阅' : '本地节点配置已保存');
  }catch(error){note(error.message,{error:true});}
  finally{button.disabled=false;}
};
function ikev2Payload(extra={}){const body={enabled:true,public_host:$('#ikev2-host').value,remote_id:$('#ikev2-remote-id').value,...extra};const psk=$('#ikev2-psk').value.trim();if(psk)body.psk=psk;return body;}
$('#ikev2-edit').onclick=()=>{ikev2Editing=true;syncIkev2Form({enabled:true, psk:$('#ikev2-psk').value});$('#ikev2-host').focus();};
$('#ikev2-cancel').onclick=async()=>{endIkev2Edit();await refresh();note('已取消编辑');};
$('#ikev2-save').onclick=async()=>{const psk=$('#ikev2-psk').value.trim();if(psk&&!/^[\dA-Za-z]{8,32}$/.test(psk)){note('共享密钥须为 8–32 位字母或数字');return;}if(!confirm('保存后需在 iPhone 重装描述文件或同步 VPN 配置，继续吗？'))return;const button=$('#ikev2-save');button.disabled=true;note('正在保存…');try{const result=await call('/api/ikev2',ikev2Payload());endIkev2Edit();if(result.ikev2)applyIkev2Ui(result.ikev2);await refresh();note('IKEv2 配置已保存');}catch(error){note(error.message,{error:true});}finally{button.disabled=false;}};
$('#ikev2-form').onsubmit=e=>e.preventDefault();
$('#ikev2-toggle').onclick=async()=>{
  const button=$('#ikev2-toggle');
  const on=button.textContent==='停用';
  if(on&&!confirm('停用后系统 VPN 将无法连接，继续吗？'))return;
  if(!on){
    const psk=$('#ikev2-psk').value.trim();
    if(psk&&!/^[\dA-Za-z]{8,32}$/.test(psk)){note('共享密钥须为 8–32 位字母或数字');return;}
  }
  button.disabled=true;
  note(on?'正在停用…':'正在启用系统 VPN…');
  try{
    if(on){
      const result=await call('/api/ikev2',{enabled:false,public_host:$('#ikev2-host').value,remote_id:$('#ikev2-remote-id').value});
      endIkev2Edit();
      if(result.ikev2)applyIkev2Ui(result.ikev2);
      await refresh();
      note('IKEv2 已停用');
    }else{
      const result=await call('/api/ikev2',ikev2Payload());
      endIkev2Edit();
      if(result.ikev2)applyIkev2Ui(result.ikev2);
      await refresh();
      note('IKEv2 已启用，请安装描述文件');
    }
  }catch(error){note(error.message,{error:true});}finally{button.disabled=false;}
};
$('#ikev2-rotate').onclick=async()=>{if(!confirm('将生成新的随机密钥，手机需更新 VPN 配置，继续吗？'))return;const button=$('#ikev2-rotate');button.disabled=true;try{const result=await call('/api/ikev2',{enabled:true,public_host:$('#ikev2-host').value,remote_id:$('#ikev2-remote-id').value,rotate_psk:true});endIkev2Edit();if(result.ikev2)applyIkev2Ui(result.ikev2);await refresh();note('已生成随机共享密钥');}catch(error){note(error.message,{error:true});}finally{button.disabled=false;}};
$('#ikev2-copy-psk').onclick=async()=>{try{await navigator.clipboard.writeText($('#ikev2-psk').value);note('共享密钥已复制');}catch{$('#ikev2-psk').select();note('请手动复制共享密钥');}};
$('#ikev2-sync-nat').onclick=async()=>{const button=$('#ikev2-sync-nat');button.disabled=true;note('正在修复 VPN 转发/NAT…');try{await call('/api/ikev2/sync',{});await refreshIkev2Only();note('已同步在线设备的 NAT 与路由');}catch(error){note(error.message,{error:true});}finally{button.disabled=false;}};
$('#ikev2-mobileconfig').onclick=()=>{
  if (!routerToken) { note('请先刷新页面'); return; }
  window.location.href = `/s/${routerToken}/ikev2.mobileconfig`;
};
$('#ikev2-mobileconfig-copy').onclick=async()=>{
  const url = $('#ikev2-mobileconfig-url').value;
  if (!url) { note('IKEv2 未启用'); return; }
  try { await navigator.clipboard.writeText(url); note('已复制，请在手机 Safari 粘贴打开'); }
  catch { $('#ikev2-mobileconfig-url').select(); note('请手动复制上方链接'); }
};
populateProbeTargets($('#test-target'));
populateProbeTargets($('#v1-settings-probe-target'));
for (const kind of ['update', 'probe']) {
  $(`#batch-${kind}`).onclick = async event => {
    const button = event.currentTarget;
    const active = button.dataset.running === 'true';
    button.disabled = true;
    try {
      await call(`/api/batch/${kind}${active ? '/stop' : ''}`, kind === 'probe' && !active ? probeTargetPayload() : {});
      await refresh();
      note(active ? '停止请求已发送，正在结束当前任务' : '批量任务已启动');
    } catch (error) {
      note(error.message, {error: true});
    } finally {
      button.disabled = false;
    }
  };
}
async function copySubscriptionLink(input) {
  const val = input.value;
  if (!val) { note('尚无订阅地址'); return; }
  try {
    await navigator.clipboard.writeText(val);
    note('订阅地址已复制');
  } catch {
    input.select();
    note('请手动复制订阅地址');
  }
}
for (const id of ['copy', 'copy-modal']) {
  $('#' + id)?.addEventListener('click', () => copySubscriptionLink($('#subscription')));
}
const publicSubscriptionDialog = $('#public-subscription-dialog');
const publicSubscriptionForm = $('#public-subscription-form');
const publicSubscriptionEnable = $('#public-subscription-enable');
const publicSubscriptionDisable = $('#public-subscription-disable');
const publicSubscriptionFeedback = $('#public-subscription-feedback');
const publicSubscriptionHost = $('#public-subscription-host');
let publicSubscriptionBusy = false;

function renderPublicSubscriptionPortHint() {
  const port = publicSubscriptionForm.elements.port;
  const rule = port.validity.valid ? `TCP ${port.value}` : '所填 TCP 端口';
  $('#public-subscription-port-hint').textContent =
    `在云安全组（如 RAKsmart）和主机防火墙放行入站 ${rule}。有 NAT 时，将该端口转发到 Router 主机。`;
}

function renderPublicSubscription(data) {
  const publication = data?.public_subscription;
  const url = data?.public_urls?.public_subscription_url || '';
  $('#public-subscription-address').hidden = !url;
  $('#public-subscription-url').value = url;
  $('#public-subscription-status').textContent = publication?.base
    ? `正在监听 TCP ${publication.port} · 外网可达性待验证`
    : url ? '已配置 HTTPS 反代 · 外网可达性待验证'
    : publication?.error || '尚未发布。启用后可在外网更新订阅。';
  if (!publicSubscriptionDialog.open) return;
  renderPublicSubscriptionPortHint();
  const tls = data?.public_subscription_tls;
  const legacy = data?.acme;
  const choices = tls?.certificates || (legacy?.certificate_valid ? [{host: legacy.domain, type: 'domain'}] : []);
  const options = choices.map(({host, type}) => new Option(`${type === 'ip' ? '公网 IP 证书' : '域名证书'} · ${host}`, host));
  const previous = publicSubscriptionHost.value || publication?.domain;
  if (choices.length !== publicSubscriptionHost.options.length || options.some((option, i) =>
    option.text !== publicSubscriptionHost.options[i]?.text || option.value !== publicSubscriptionHost.options[i]?.value)) {
    publicSubscriptionHost.replaceChildren(...options);
  }
  publicSubscriptionHost.value = choices.some(choice => choice.host === previous) ? previous : choices[0]?.host || '';
  $('#public-subscription-certificate').textContent = choices.length > 1 ? '订阅地址使用所选证书覆盖的地址。'
    : choices.length ? `将复用${options[0].text}`
    : tls?.hysteria_local
      ? '已生成 Hysteria 2 专用自签证书，节点客户端可用指纹验证。公网订阅需要与访问地址匹配的受信任证书。'
      : '请为订阅域名或公网 IP 配置受信任的 HTTPS 证书。';
  $('#public-subscription-cert-options').hidden = choices.length < 2;
  $('#public-subscription-cert-link').hidden = !!choices.length;
  publicSubscriptionEnable.textContent = publication?.base ? '保存发布设置' : '启用公网订阅';
  publicSubscriptionEnable.disabled = publicSubscriptionBusy || !publication || !choices.length;
  publicSubscriptionDisable.hidden = !publication?.enabled;
  publicSubscriptionDisable.disabled = publicSubscriptionBusy;
}

async function setPublicSubscription(enabled) {
  if (publicSubscriptionBusy) return;
  publicSubscriptionBusy = true;
  renderPublicSubscription(window.hopworldStatus);
  publicSubscriptionFeedback.textContent = enabled ? '正在启动 HTTPS 监听…' : '正在停用 HTTPS 监听…';
  try {
    const {port} = publicSubscriptionForm.elements;
    await call('/api/v1/public-subscription', {enabled, port: Number(port.value), host: publicSubscriptionHost.value});
    publicSubscriptionDialog.close();
    note(enabled ? '公网订阅监听已启动' : '公网订阅监听已停用');
    void refresh().catch(() => {});
  } catch (error) {
    publicSubscriptionFeedback.textContent = error.message;
  } finally {
    publicSubscriptionBusy = false;
    renderPublicSubscription(window.hopworldStatus);
  }
}

$('#public-subscription-open').addEventListener('click', () => {
  const status = window.hopworldStatus;
  publicSubscriptionForm.elements.port.value = status?.public_subscription?.port || 9443;
  publicSubscriptionHost.value = '';
  publicSubscriptionFeedback.textContent = status?.public_subscription
    ? status.public_subscription.error
    : '请重启 Router 以加载公网订阅功能。';
  publicSubscriptionDialog.showModal();
  renderPublicSubscription(status);
});
for (const id of ['public-subscription-close', 'public-subscription-cert-link']) {
  $('#' + id).addEventListener('click', () => publicSubscriptionDialog.close());
}
publicSubscriptionForm.addEventListener('submit', event => {
  event.preventDefault();
  void setPublicSubscription(true);
});
publicSubscriptionForm.elements.port.addEventListener('input', renderPublicSubscriptionPortHint);
publicSubscriptionDisable.addEventListener('click', () => setPublicSubscription(false));
$('#public-subscription-copy').addEventListener('click', () => copySubscriptionLink($('#public-subscription-url')));
let rotatingPublicSubscription = false;
async function rotatePublicSubscription() {
  if (rotatingPublicSubscription) return;
  rotatingPublicSubscription = true;
  const buttons = ['public-subscription-rotate', 'rotate'].map(id => $('#' + id)).filter(Boolean);
  for (const button of buttons) button.disabled = true;
  try {
    await call('/api/rotate', {});
    await refresh();
    note('订阅地址已更新');
  } catch (error) {
    note(error.message, {error: true});
  } finally {
    rotatingPublicSubscription = false;
    for (const button of buttons) button.disabled = false;
  }
}
function confirmRotatePublicSubscription() {
  if (rotatingPublicSubscription) return;
  const proceed = () => { void rotatePublicSubscription(); };
  if (typeof window.hopworldOpenConfirm === 'function') {
    window.hopworldOpenConfirm('重置', '重置后旧地址立即失效，是否继续？', '重置', proceed);
    return;
  }
  if (!confirm('重置后旧地址立即失效，是否继续？')) return;
  proceed();
}
$('#public-subscription-rotate')?.addEventListener('click', confirmRotatePublicSubscription);
const legacyRotate = $('#rotate');
if (legacyRotate) legacyRotate.onclick = confirmRotatePublicSubscription;
refresh().catch(() => {
  setConnectionLive(false);
  note('当前未连接管理服务。请启动服务后通过本机地址访问。', { error: true, duration: 8000 });
});

const updateNotices = $('#v1-update-notices');
const updateBell = $('#v1-updates-open');
const updateBadge = $('#v1-updates-count');
const updateHistory = $('#v1-updates-history');
const updateFeedback = $('#v1-updates-feedback');
const markUpdateRead = $('#v1-updates-read');
const updateSettings = $('#v1-updates-settings');
const updatePolicies = $('#v1-updates-policies');
let updateSnapshot;
let updateRequest;
let updatePreferencesRendered = false;

function renderUpdateHistory() {
  if (!updateSnapshot) return;
  const {events, unread, read_through: readThrough, components} = updateSnapshot;
  const names = {project: 'Router 程序', package: 'Router Debian 软件包', ...Object.fromEntries(components)};
  updateBadge.hidden = !unread;
  updateBadge.textContent = unread > 99 ? '99+' : String(unread);
  updateBell.setAttribute('aria-label', unread ? `更新提醒，${unread} 条未读` : '更新提醒');
  if (!updateNotices.open) return;
  const states = {available: '发现更新', started: '已启动', completed: '已完成', failed: '失败', unavailable: '检查异常'};
  updateHistory.replaceChildren(...events.map(event => {
    const item = document.createElement('li');
    const title = document.createElement('strong');
    const when = document.createElement('time');
    const message = document.createElement('p');
    item.dataset.unread = String(event.id > readThrough);
    title.textContent = [names[event.target] || event.target, states[event.state] || event.state,
      event.target_version].filter(Boolean).join(' · ');
    when.dateTime = new Date(event.timestamp * 1000).toISOString();
    when.textContent = new Date(event.timestamp * 1000).toLocaleString();
    message.textContent = [event.detail, event.current_version && '开始前版本 ' + event.current_version].filter(Boolean).join(' · ');
    item.append(title, when, message);
    return item;
  }));
  updateHistory.hidden = !events.length;
  if (!unread && document.activeElement === markUpdateRead) updateSettings.focus();
  markUpdateRead.hidden = !unread;
  markUpdateRead.disabled = !unread;
  updateFeedback.textContent = events.length ? '' : '暂无更新记录';
}

function renderUpdatePreferences() {
  if (!updateSnapshot || updatePreferencesRendered) return;
  updatePreferencesRendered = true;
  const {components, installation, preferences} = updateSnapshot;
  const rows = installation === 'package' ? [['package', 'Router Debian 软件包'], ...components] : components;
  updatePolicies.replaceChildren();
  for (const [target, name] of rows) {
    const label = document.createElement('label');
    const select = document.createElement('select');
    select.id = 'update-policy-' + target;
    label.htmlFor = select.id;
    label.textContent = name;
    select.replaceChildren(new Option('手动更新', 'manual'), new Option('自动更新', 'automatic'));
    select.value = preferences[target] ? 'automatic' : 'manual';
    select.addEventListener('change', async () => {
      const feedback = $('#v1-update-policy-feedback');
      select.disabled = true;
      try {
        const saved = await call('/api/v1/updates/preferences', {target, automatic: select.value === 'automatic'});
        updateSnapshot.preferences[target] = saved.automatic;
        feedback.textContent = name + '的更新设置已保存。';
        if (target === 'package') void checkSystemUpdate();
      } catch (error) {
        select.value = updateSnapshot.preferences[target] ? 'automatic' : 'manual';
        feedback.textContent = error.message;
      } finally {
        select.disabled = false;
      }
    });
    updatePolicies.append(label, select);
  }
}

function loadUpdateNotices() {
  if (updateRequest) return updateRequest;
  updateRequest = (async () => {
    try {
      updateSnapshot = await call('/api/v1/updates');
      renderUpdateHistory();
      renderUpdatePreferences();
    } catch (error) {
      if (updateNotices.open) {
        updateFeedback.textContent = error.message;
        if (!markUpdateRead.hidden) markUpdateRead.disabled = false;
      }
      if (!updatePreferencesRendered) $('#v1-update-policy-feedback').textContent = error.message;
    } finally {
      updateRequest = null;
    }
  })();
  return updateRequest;
}

updateBell.addEventListener('click', () => {
  updateFeedback.textContent = '正在读取更新记录…';
  updateHistory.hidden = markUpdateRead.hidden = true;
  updateNotices.showModal();
  void loadUpdateNotices();
});
markUpdateRead.addEventListener('click', async () => {
  const seen = updateSnapshot?.events[0]?.id;
  if (!seen) return;
  markUpdateRead.disabled = true;
  try {
    await call('/api/v1/updates/read', {seen});
    if (updateRequest) await updateRequest;
    await loadUpdateNotices();
  } catch (error) {
    updateFeedback.textContent = error.message;
    markUpdateRead.disabled = false;
  }
});
updateSettings.addEventListener('click', () => updateNotices.close());
void loadUpdateNotices();
setInterval(loadUpdateNotices, 45000);
