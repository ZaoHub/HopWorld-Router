const $ = s => document.querySelector(s);
/** 界面展示名：中文为主，英文名放括号内 */
const LE_LABEL = '免费 SSL 证书（Let\u2019s Encrypt）';
const message = $('#message');
let probeTimer;
let autoObservationTimer;
let autoObservationBusy = false;
let ikev2Timer;
const selectedGroups = new Set();
const seenGroups = new Set();
let groupSignature = '';
let probeGroupsEditing = false;
let probeGroupFilter = '';
let cachedProbeGroups = [];
function renderProbeGroupsList(groups) {
  cachedProbeGroups = groups;
  const container = $('#probe-groups');
  if (!container) return;
  container.replaceChildren();
  const q = probeGroupFilter.trim().toLowerCase();
  const filtered = groups.filter(g => !q || g.name.toLowerCase().includes(q));
  if (!groups.length) {
    const empty = document.createElement('p');
    empty.className = 'group-list-empty';
    empty.textContent = '暂无订阅组';
    container.append(empty);
    return;
  }
  if (!filtered.length) {
    const empty = document.createElement('p');
    empty.className = 'group-list-empty';
    empty.textContent = '无匹配的订阅组';
    container.append(empty);
    return;
  }
  filtered.forEach((group, index) => {
    const row = document.createElement('div');
    row.className = 'group-list-row';
    row.setAttribute('role', 'listitem');
    if (!selectedGroups.has(group.id)) row.classList.add('is-excluded');
    const num = document.createElement('span');
    num.className = 'group-list-index';
    num.textContent = String(index + 1);
    const name = document.createElement('span');
    name.className = 'group-list-name';
    name.textContent = group.name;
    row.append(num, name);
    if (probeGroupsEditing) {
      const label = document.createElement('label');
      label.className = 'group-list-check';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = selectedGroups.has(group.id);
      input.setAttribute('aria-label', `将「${group.name}」加入批量测试`);
      input.onchange = () => {
        if (input.checked) selectedGroups.add(group.id);
        else selectedGroups.delete(group.id);
        row.classList.toggle('is-excluded', !input.checked);
      };
      label.append(input);
      row.append(label);
    } else {
      const tag = document.createElement('span');
      tag.className = 'group-list-tag';
      tag.textContent = selectedGroups.has(group.id) ? '参与测试' : '跳过';
      row.append(tag);
    }
    container.append(row);
  });
}
function syncProbeGroupsEditUi() {
  const btn = $('#probe-groups-edit');
  if (btn) btn.textContent = probeGroupsEditing ? '完成' : '编辑';
  renderProbeGroupsList(cachedProbeGroups);
}
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
  const subscription = publicUrls.subscription_url
    || (token ? `${location.origin}/s/${token}` : '');
  const ikev2Profile = data.ikev2?.enabled
    ? (publicUrls.ikev2_profile_url || `${location.origin}/s/${token}/ikev2.mobileconfig`)
    : '';
  return { subscription, ikev2Profile };
}
let lastProbeTarget = 'google';
const PROBE_TARGET_GROUPS = {
  general: {
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
function populateProbeTargets(preserve = true) {
  const category = $('#test-target-category')?.value || 'general';
  const select = $('#test-target');
  if (!select) return;
  const group = PROBE_TARGET_GROUPS[category] || PROBE_TARGET_GROUPS.general;
  const prev = preserve ? (select.value || lastProbeTarget) : '';
  select.replaceChildren();
  for (const item of group.targets) {
    const option = document.createElement('option');
    option.value = item.value;
    option.textContent = item.label;
    select.append(option);
  }
  const pick = group.targets.some(t => t.value === prev) ? prev : group.targets[0]?.value;
  if (pick) {
    select.value = pick;
    lastProbeTarget = pick;
  }
}
function syncProbeTargetCategoryFromValue(value) {
  const categoryEl = $('#test-target-category');
  if (!categoryEl || !value) return;
  for (const [key, group] of Object.entries(PROBE_TARGET_GROUPS)) {
    if (group.targets.some(t => t.value === value)) {
      categoryEl.value = key;
      break;
    }
  }
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
  const ddnsOn = !!$('#local-ddns')?.checked;
  const input = $('#local-host');
  const hint = $('#local-host-ddns-hint');
  if (!input || !hint) return;
  const show = ddnsOn && isPublicHostIp(input.value);
  hint.hidden = !show;
  input.classList.toggle('has-inline-hint', show);
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
    auto_public_host: $('#local-auto-host')?.checked ? '1' : '0',
    ddns_enabled: $('#local-ddns')?.checked ? '1' : '0',
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
    auto_public_host: $('#local-auto-host')?.checked ? '1' : '0',
    ddns_enabled: $('#local-ddns')?.checked ? '1' : '0',
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
  if (!acme) return '';
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
  const acmeOn = !!$('#local-acme')?.checked || !!acme?.enabled;
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
  const summary = $('#local-acme-summary');
  const acmeLine = formatAcmeLine(acme);
  const readOnly = !showHostControls;
  if (summary) {
    if (readOnly && acmeLine) {
      summary.hidden = false;
      summary.textContent = acmeLine;
      summary.classList.toggle('is-acme-error', !!acme?.error);
    } else {
      summary.hidden = true;
      summary.textContent = '';
      summary.classList.remove('is-acme-error');
    }
  }
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
function syncLocalForm(local, acme) {
  acme = acme || {};
  if (local && !localEditing) {
    $('#local-name').value = local.name || '本地网络';
    if (local.public_host) $('#local-host').value = local.public_host;
    $('#local-port').value = local.port || 8443;
    if ($('#local-auto-host')) $('#local-auto-host').checked = !!local.auto_public_host;
    if ($('#local-ddns')) $('#local-ddns').checked = !!local.ddns_enabled;
    if ($('#local-host-prefer')) $('#local-host-prefer').value = local.public_host_prefer || 'ipv4';
    if (local.egress_mode) $('#local-egress-mode').value = local.egress_mode;
    if (local.auto_target) $('#local-auto-target').value = local.auto_target;
    if (local.local_proxy) {
      const select = $('#local-proxy-choice');
      if (![...select.options].some(o => o.value === local.local_proxy)) select.add(new Option(local.local_proxy, local.local_proxy));
      select.value = local.local_proxy;
    }
    if (local.egress_mode === 'auto') $('#local-auto-summary').textContent = `当前候选 ${local.auto_pool_count || 0} 个；无可用出口时阻断，不直连。`;
    if ($('#local-acme')) $('#local-acme').checked = !!acme.enabled;
    if ($('#local-acme-email')) $('#local-acme-email').value = acme.email || '';
    if ($('#local-acme-challenge')) {
      $('#local-acme-challenge').value = acme.challenge || 'auto';
    }
  }
  const localEditable = !local?.enabled || localEditing;
  $('#local-egress-mode').disabled = !localEditable;
  $('#local-auto-target').disabled = !localEditable;
  $('#local-proxy-port').disabled = !localEditable;
  $('#local-proxy-choice').disabled = !localEditable;
  $('#local-proxy-scan').disabled = !localEditable;
  $('#local-proxy-settings').hidden = $('#local-egress-mode').value !== 'local_proxy';
  $('#local-auto-settings').hidden = $('#local-egress-mode').value !== 'auto';
  const readOnlyView = !!local?.enabled && !localEditing;
  const showHostControls = !readOnlyView;
  const autoHost = !!$('#local-auto-host')?.checked;
  const ddnsOn = !!$('#local-ddns')?.checked;
  const hostSection = $('#local-host-section');
  if (hostSection) {
    hostSection.hidden = !showHostControls;
    hostSection.classList.toggle('is-on', autoHost || ddnsOn);
    hostSection.classList.toggle('is-off', !autoHost && !ddnsOn);
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
      };
      const prefer = preferLabels[local.public_host_prefer] || '优先 IPv4';
      if (local.ddns_enabled) summaryMode.textContent = '固定域名 · 自动 DNS';
      else if (local.auto_public_host) summaryMode.textContent = `自动探测 · ${prefer}`;
      else summaryMode.textContent = '固定公网';
    }
  }
  const localFormHint = $('#local-form-hint');
  if (localFormHint) localFormHint.hidden = readOnlyView;
  renderAcmeStatus(acme, showHostControls);
  syncAcmeFwHint(acme, showHostControls);
  for (const id of ['local-acme-email']) {
    const input = $('#' + id);
    if (!input) continue;
    const editable = showHostControls;
    input.readOnly = !editable;
    input.classList.toggle('is-readonly', !editable);
  }
  $('#local-acme')?.toggleAttribute('disabled', !showHostControls);
  const acmeChallenge = $('#local-acme-challenge');
  if (acmeChallenge) acmeChallenge.disabled = !showHostControls || !$('#local-acme')?.checked;
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
  $('#local-auto-host')?.toggleAttribute('disabled', !localEditable);
  $('#local-ddns')?.toggleAttribute('disabled', !localEditable);
  const prefer = $('#local-host-prefer');
  if (prefer) prefer.disabled = !localEditable || (!autoHost && !ddnsOn);
  $('#local-edit').hidden = !local?.enabled || localEditing;
  $('#local-save').hidden = !localEditing || !local?.enabled;
  $('#local-cancel').hidden = !localEditing;
  const on = !!local?.enabled;
  const localToggle = $('#local-toggle');
  localToggle.hidden = localEditing;
  localToggle.textContent = on ? '停用' : '启用';
  localToggle.classList.toggle('primary', !on);
  localToggle.classList.toggle('secondary', on);
  localToggle.disabled = localEditing;
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
async function call(path, data) {
  const options = data ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)} : {};
  const response = await fetch(path, options);
  if (!response.ok) {
    let result;
    try { result = await response.json(); } catch { result = {}; }
    const err = humanizeApiMessage(result.error) || `请求失败 (${response.status})`;
    throw new Error(err);
  }
  return response.json();
}
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
function clearNote() {
  clearTimeout(noteHideTimer);
  noteHideTimer = 0;
  if (!message) return;
  message.textContent = '';
  message.hidden = true;
  message.classList.remove('is-visible', 'is-error');
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
  message.textContent = text;
  message.hidden = false;
  message.classList.add('is-visible');
  message.classList.toggle('is-error', !!opts.error);
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
    hint.textContent = location.protocol === 'https:'
      ? '使用手机客户端扫描导入订阅'
      : '当前为 HTTP 订阅，若客户端拒绝扫码，请复制链接手动添加';
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
  renderMobileConnect(subscription);
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
    const info = document.createElement('div'); const name = document.createElement('strong'); name.textContent=source.name;
    const meta = document.createElement('p'); meta.textContent = `${source.count} 个节点 · ${source.updated ? '更新于 ' + new Date(source.updated * 1000).toLocaleString() : '尚未成功更新'}${source.error ? ' · ' + source.error : ''}`;
    info.append(name,meta); const actions=document.createElement('div'); actions.className='actions';
    for (const [text,path] of [['更新','/api/refresh'],['删除','/api/delete']]) {
      const button=document.createElement('button'); button.className='secondary'; button.textContent=text;
      button.onclick=async()=>{if(path==='/api/delete' && !confirm(`删除订阅「${source.name}」？`)) return; button.disabled=true; try {await call(path,{id:source.id});note('操作完成');} catch(e){note(e.message,{error:true});} finally {await refresh();}};
      actions.append(button);
    }
    row.append(info,actions); list.append(row);
  }
  const nodes = $('#nodes'); nodes.replaceChildren();
  $('#node-count').textContent = `${data.nodes.length} 个节点`;
  const local = data.local_node || {};
  syncLocalForm(local, data.acme || {});
  maybeShowLocalNodeNotice(local);
  const localStatus = $('#local-status');
  const localSummary = $('#local-node-summary');
  const localLine = local.enabled
    ? (local.running
      ? `已启用 · ${local.name || '本地网络'} · ${local.public_host}:${local.port}`
      : '已启用，但 Xray 未在运行')
    : '尚未启用';
  if (localStatus) localStatus.textContent = localLine;
  if (localSummary) {
    const acmeHint = local.enabled ? formatAcmeLine(data.acme) : '';
    localSummary.textContent = local.enabled
      ? (acmeHint ? `${localLine} · ${acmeHint}` : localLine)
      : '尚未启用 · 点击查看可启用或修改';
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
  const groups = data.sources.map(source=>({id:`source:${source.id}`,name:source.name}));
  if(data.nodes.some(node=>node.manual)) groups.push({id:'manual',name:'手动导入'});
  if(data.nodes.some(node=>node.local)) groups.push({id:'local',name:local.name || '本地网络'});
  const groupIDs = new Set(groups.map(group=>group.id));
  for(const id of selectedGroups) if(!groupIDs.has(id)) selectedGroups.delete(id);
  for(const id of seenGroups) if(!groupIDs.has(id)) seenGroups.delete(id);
  for(const group of groups) if(!seenGroups.has(group.id)){seenGroups.add(group.id);selectedGroups.add(group.id);}
  const signature = JSON.stringify(groups);
  if (signature !== groupSignature) groupSignature = signature;
  const filterInput = $('#probe-group-filter');
  if (filterInput && filterInput.value !== probeGroupFilter) filterInput.value = probeGroupFilter;
  renderProbeGroupsList(groups);
  for (const kind of ['update','probe']) {
    const job=data.jobs[kind], button=$(`#batch-${kind}`), progress=$(`#${kind}-progress`);
    button.textContent=job.running?'停止':(kind==='update'?'批量更新':'批量测试');
    progress.textContent=job.total ? `${job.target ? job.target+' · ' : ''}${job.running?'进行中':job.cancelled?'已停止':'已完成'} ${job.done}/${job.total}${job.failed ? ` · ${job.failed} 未通过或跳过` : ''}` : '';
  }
  if (!data.nodes.length) { const empty=document.createElement('p'); empty.className='empty'; empty.textContent='暂无节点'; nodes.append(empty); }
  for (const node of data.nodes) {
    const row=document.createElement('div'); row.className='row';
    const label=document.createElement('div'); label.className='node-label';
    const title=document.createElement('span'); title.textContent=`${node.title} · ${node.protocol.toUpperCase()}`;
    if(node.probe){const state=document.createElement('small'); state.className='probe-result'; state.textContent=node.probe.status==='ok' ? `${node.probe.target||'Google'} 可用 · ${node.probe.latency_ms} ms` : node.probe.detail; title.append(document.createElement('br'),state);}
    if(!node.enabled){const excluded=document.createElement('small');excluded.className='probe-result';excluded.textContent='此前已移出订阅';title.append(document.createElement('br'),excluded);}
    label.append(title); row.append(label);
    const test=document.createElement('button'); test.className='secondary'; test.textContent=node.probe?.status==='testing'?'测试中…':'测试连接'; test.disabled=node.probe?.status==='testing';
    test.onclick=async()=>{test.disabled=true;try{await call('/api/probe',{id:node.id,target:$('#test-target').value});await refresh();}catch(e){test.disabled=false;note(e.message);}};row.append(test);
    if(!node.enabled){const restore=document.createElement('button');restore.className='secondary';restore.textContent='加入订阅';restore.onclick=async()=>{try{await call('/api/node',{id:node.id,enabled:true});await refresh();note('节点已重新加入订阅');}catch(e){note(e.message,{error:true});}};row.append(restore);}
    if(node.manual){const button=document.createElement('button');button.className='secondary';button.textContent='移除手动节点';button.onclick=async()=>{if(!confirm('移除这个手动导入的节点？'))return;try{await call('/api/manual/delete',{id:node.id});await refresh();note('手动节点已移除');}catch(e){note(e.message,{error:true});}};row.append(button);}
    nodes.append(row);
  }
  if(data.nodes.some(node=>node.probe?.status==='testing') || Object.values(data.jobs).some(job=>job.running)) probeTimer=setTimeout(()=>refresh().catch(()=>note('无法获取任务进度')), 1800);
}
const MODAL_IDS = {
  subscription: 'subscription-modal',
  'add-source': 'add-source-modal',
  'local-node-notice': 'local-node-notice-modal',
  'local-node': 'local-node-modal',
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
  if (key !== 'ikev2-settings' && key !== 'local-node-notice') refresh().catch(() => {});
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
function openAddSourceModal() {
  const modal = $('#add-source-modal');
  if (!modal) return;
  modal.hidden = false;
  syncModalBodyScroll();
  $('#name')?.focus();
}
function closeAddSourceModal() { closeModal('add-source'); }
function openLocalNodeModal() {
  localEditing = false;
  const modal = $('#local-node-modal');
  if (!modal) return;
  modal.hidden = false;
  syncModalBodyScroll();
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
for (const id of ['add-source-open', 'add-source-open-top']) {
  const btn = $('#' + id);
  if (btn) btn.onclick = openAddSourceModal;
}
$('#subscription-open')?.addEventListener('click', openSubscriptionModal);
$('#local-node-open')?.addEventListener('click', openLocalNodeModal);
$('#local-node-notice-go')?.addEventListener('click', () => {
  localNodeNoticeSuppressed = true;
  closeModal('local-node-notice');
  openLocalNodeModal();
});
$('#ikev2-open')?.addEventListener('click', openIkev2Modal);
$('#ikev2-settings-open')?.addEventListener('click', openIkev2SettingsModal);
$('#manual-open')?.addEventListener('click', openManualModal);
document.querySelectorAll('[data-modal-close]').forEach(el => {
  const target = el.getAttribute('data-modal-close');
  if (target && MODAL_IDS[target]) el.onclick = () => closeModal(target);
});
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  for (const key of ['ikev2-settings', 'local-node-notice', 'subscription', 'manual', 'ikev2', 'local-node', 'add-source']) {
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
  const auto = !!$('#local-auto-host')?.checked;
  const ddns = !!$('#local-ddns')?.checked;
  return {
    enabled,
    name: $('#local-name').value,
    public_host: auto && !ddns ? '' : $('#local-host').value,
    port: Number($('#local-port').value),
    auto_public_host: auto,
    ddns_enabled: ddns,
    acme_enabled: !!$('#local-acme')?.checked,
    acme_email: $('#local-acme-email')?.value || '',
    acme_challenge: $('#local-acme-challenge')?.value || 'auto',
    public_host_prefer: $('#local-host-prefer')?.value || 'ipv4',
    egress_mode: $('#local-egress-mode').value,
    local_proxy: $('#local-proxy-choice').value,
    auto_target: $('#local-auto-target').value,
  };
}
$('#local-egress-mode').addEventListener('change', () => {
  $('#local-proxy-settings').hidden = $('#local-egress-mode').value !== 'local_proxy';
  $('#local-auto-settings').hidden = $('#local-egress-mode').value !== 'auto';
  void refreshAutoObservations();
});
$('#local-proxy-scan').addEventListener('click', async () => {
  const button = $('#local-proxy-scan');
  const raw = $('#local-proxy-port').value;
  button.disabled = true;
  note('正在扫描本机代理…');
  try {
    const data = await call('/api/local/discover', raw ? {ports: [Number(raw)]} : {});
    const select = $('#local-proxy-choice');
    select.replaceChildren(new Option(data.proxies.length ? '请选择代理' : '未发现可用代理', ''));
    data.proxies.forEach(item => select.add(new Option(`${item.protocol.toUpperCase()} · 127.0.0.1:${item.port}`, `${item.protocol}:${item.port}`)));
    note(data.proxies.length ? `发现 ${data.proxies.length} 个本机代理，请选择` : '未发现可用代理');
  } catch (error) { note(error.message, {error: true}); }
  finally { button.disabled = false; }
});
function syncLocalHostModeToggles(ev) {
  const autoEl = $('#local-auto-host');
  const ddnsEl = $('#local-ddns');
  if (autoEl && ddnsEl && autoEl.checked && ddnsEl.checked) {
    if (ev?.target === ddnsEl) autoEl.checked = false;
    else if (ev?.target === autoEl) ddnsEl.checked = false;
    else ddnsEl.checked = false;
  }
  syncLocalForm({
    enabled: true,
    name: $('#local-name').value,
    public_host: $('#local-host').value,
    port: Number($('#local-port').value),
    auto_public_host: $('#local-auto-host')?.checked,
    ddns_enabled: $('#local-ddns')?.checked,
  }, { enabled: $('#local-acme')?.checked, email: $('#local-acme-email')?.value });
}
$('#local-auto-host')?.addEventListener('change', syncLocalHostModeToggles);
$('#local-ddns')?.addEventListener('change', syncLocalHostModeToggles);
$('#local-acme')?.addEventListener('change', () => {
  const acmeChallenge = $('#local-acme-challenge');
  if (acmeChallenge) acmeChallenge.disabled = !$('#local-acme')?.checked;
  renderAcmeStatus({ enabled: $('#local-acme')?.checked });
  syncAcmeFwHint({ enabled: $('#local-acme')?.checked }, true);
});
$('#local-acme-challenge')?.addEventListener('change', () => syncAcmeFwHint({}, true));
$('#local-host')?.addEventListener('input', () => {
  syncLocalHostDdnsHint();
  scheduleLocalDnsHintsPreview();
});
$('#local-host-prefer')?.addEventListener('change', scheduleLocalDnsHintsPreview);
$('#local-dns-verify')?.addEventListener('click', () => { void runLocalDnsVerify(); });
$('#local-edit').onclick=()=>{localEditing=true;syncLocalForm({enabled:true,name:$('#local-name').value,public_host:$('#local-host').value,port:Number($('#local-port').value),auto_public_host:$('#local-auto-host')?.checked});$('#local-name').focus();};
$('#local-cancel').onclick=async()=>{localEditing=false;await refresh();note('已取消编辑');};
$('#local-form').onsubmit=e=>e.preventDefault();
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
  const acmeOn=!!$('#local-acme')?.checked;
  button.disabled=true;
  if (acmeOn) note('正在申请证书（约 1 分钟）…', { duration: 0 });
  try{
    const result=await call('/api/local-node',localPayload(true));
    localEditing=false;
    await refresh();
    const acmeInfo = result?.local_node?.acme || result?.acme;
    const acmeErr = acmeInfo?.error;
    clearNote();
    if (acmeOn && acmeErr) note('已保存，证书未签发', { error: true });
    else if (acmeOn && acmeInfo?.days_left != null) note(`证书已签发（约 ${acmeInfo.days_left} 天）`);
    else note('已保存，请更新订阅');
  }catch(error){note(error.message,{error:true});}
  finally{button.disabled=false;}
};
function ikev2Payload(extra={}){const body={enabled:true,public_host:$('#ikev2-host').value,remote_id:$('#ikev2-remote-id').value,...extra};const psk=$('#ikev2-psk').value.trim();if(psk)body.psk=psk;return body;}
$('#ikev2-edit').onclick=()=>{ikev2Editing=true;syncIkev2Form({enabled:true, psk:$('#ikev2-psk').value});$('#ikev2-host').focus();};
$('#ikev2-cancel').onclick=async()=>{endIkev2Edit();await refresh();note('已取消编辑');};
$('#ikev2-save').onclick=async()=>{const psk=$('#ikev2-psk').value.trim();if(psk&&!/^[\dA-Za-z]{8,32}$/.test(psk)){note('共享密钥须为 8–32 位字母或数字');return;}if(!confirm('保存后需在 iPhone 重装描述文件或同步 VPN 配置，继续吗？'))return;const button=$('#ikev2-save');button.disabled=true;try{const result=await call('/api/ikev2',ikev2Payload());endIkev2Edit();if(result.ikev2)applyIkev2Ui(result.ikev2);await refresh();note('IKEv2 配置已保存');}catch(error){note(error.message,{error:true});}finally{button.disabled=false;}};
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
$('#test-target-category')?.addEventListener('change', () => populateProbeTargets(false));
$('#test-target')?.addEventListener('change', () => {
  lastProbeTarget = $('#test-target')?.value || lastProbeTarget;
});
syncProbeTargetCategoryFromValue(lastProbeTarget);
populateProbeTargets(true);
$('#probe-group-filter')?.addEventListener('input', e => {
  probeGroupFilter = e.target.value;
  renderProbeGroupsList(cachedProbeGroups);
});
$('#probe-groups-edit')?.addEventListener('click', () => {
  probeGroupsEditing = !probeGroupsEditing;
  syncProbeGroupsEditUi();
  if (!probeGroupsEditing) note('批量测试范围已更新');
});
for(const kind of ['update','probe']) $(`#batch-${kind}`).onclick=async e=>{const button=e.currentTarget;button.disabled=true;try{const active=button.textContent==='停止';if(kind==='probe'&&!active&&!selectedGroups.size){note('请先在编辑中勾选至少一个订阅组');return;}await call(`/api/batch/${kind}${active?'/stop':''}`,{target:$('#test-target').value,groups:[...selectedGroups]});await refresh();note(active?'停止请求已发送，正在结束当前任务':'批量任务已启动');}catch(error){note(error.message,{error:true});}finally{button.disabled=false;}};
async function copySubscriptionLink() {
  const val = $('#subscription')?.value;
  if (!val) { note('尚无订阅地址'); return; }
  try {
    await navigator.clipboard.writeText(val);
    note('订阅地址已复制');
  } catch {
    $('#subscription')?.select();
    note('请手动复制订阅地址');
  }
}
$('#copy')?.addEventListener('click', copySubscriptionLink);
$('#copy-modal')?.addEventListener('click', copySubscriptionLink);
$('#rotate').onclick=async()=>{if(!confirm('更换后旧订阅地址立即失效，继续吗？'))return;try{await call('/api/rotate',{});await refresh();note('订阅地址已更新');}catch(e){note(e.message,{error:true});}};
refresh().catch(() => {
  setConnectionLive(false);
  note('当前未连接管理服务。请启动服务后通过本机地址访问。', { error: true, duration: 8000 });
});
