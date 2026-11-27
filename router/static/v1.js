(() => {
  const byId = id => document.getElementById(id);
  const pages = {
    overview: ['首页', '运行概览'],
    cores: ['内核管理', '管理代理内核的安装、配置、运行与日志'],
    nodes: ['节点管理', '管理本地发布节点、订阅导入节点与统一域名映射',
      [['导入节点', 'manual-open'], ['添加订阅', 'add-source-open'], ['+ 新建节点', 'publish-core-open']]],
    subscriptions: ['订阅管理', '管理远程订阅源、自动获取、更新并导入代理节点', [['全部更新', 'batch-update'], ['测试全部节点', 'batch-probe'], ['添加订阅', 'add-source-open']]],
    certificates: ['域名与证书', '公网域名、DNS 和 HTTPS 证书'],
    public: ['公网接入', '管理公网订阅、节点端口与 VPS 反向隧道。',
      [['接入向导', 'v1-public-guide'], ['配置 VPS 中转', 'v1-relay-configure']]],
    traffic: ['流量监控', '实时流量、历史统计、按内核/协议/节点/端口多维度分析'],
    settings: ['系统设置', '平台运行、网络、权限、安全与维护等全局配置'],
    about: ['关于', '产品信息、版本与支持开发'],
  };
  const statuses = {running: '运行中', stopped: '已停止', unmanaged: '已安装 · 未托管', not_installed: '未安装'};
  let page = 'overview';
  let system;
  let traffic;
  let coreFilter = 'all';
  let subFilter = 'all';
  let selectedSourceId = null;
  let nodeFilter = 'all';
  let nodePageIndex = 0;
  let selectedNodeId = null;
  let nodeDomainSecretNodeId = null;
  const nodeChecks = new Set();
  let nodeProbePending = false;
  let probeProgressHideTimer;
  const nodePageSize = 7;
  const udpProtocols = new Set(['hysteria2', 'hy2', 'tuic']);
  let selectedCoreId = null;
  let coreInstallBusy = false;
  const coreChecks = new Set();
  const coreIcons = {
    generic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="5" y="4" width="14" height="16" rx="2"/><path d="M5 12h14M8 8h2m-2 8h2"/></svg>',
    xray: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M4.5 4h5.1L12 8.8 14.4 4h5.1l-4.6 8 4.6 8h-5.1L12 15.2 9.6 20H4.5l4.6-8z"/></svg>',
    sing: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M3 8.2 12.2 12 3 15.8v-2.6L8.2 12 3 10.8zm10.2-.6L21 12l-7.8 4.4V14l4.2-2-4.2-2z"/></svg>',
    meta: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M3.5 19V5h3.4l5.1 8.6L17.1 5h3.4v14h-3.1V10.4L13.6 17h-3.2l-3.8-6.6V19z"/></svg>',
    hysteria: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M3 4.5h2.8v5.4h6.2V4.5H15v13h-2.8v-5.2H5.8v5.2H3zm13.2 11.1c.5-.6 1.4-1 2.2-1 1 0 1.6.5 1.6 1.2 0 .9-.8 1.3-1.7 1.6l-.8.3v1.1h2.9V20h-4.2v-1.6l1.4-.5c.7-.3 1.1-.5 1.1-.8s-.2-.5-.5-.5c-.5 0-.9.3-1.1.7z"/></svg>',
    ss: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M2.4 10.7 21.2 2.2c.6-.3 1.2.3.9.9l-8.5 18.7c-.3.7-1.3.6-1.5-.1l-1.8-6.6-6.6-1.8c-.7-.2-.8-1.2-.3-1.6z"/><path fill="currentColor" opacity=".45" d="m10.3 13.4 4.8-4.6 1.1 6.1z"/></svg>',
    tuic: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M3.5 4.5h17v3.4h-6.8V19.5h-3.4V7.9H3.5z"/></svg>',
  };
  const coreCatalog = {
    'xray-core': {name: 'Xray-core', binary: 'xray', logo: 'xray'},
    'sing-box': {name: 'sing-box', binary: 'sing-box', logo: 'sing'},
    mihomo: {name: 'mihomo', binary: 'mihomo', logo: 'meta'},
    hysteria2: {name: 'Hysteria 2', binary: 'hysteria', logo: 'hysteria'},
    'shadowsocks-rust': {name: 'shadowsocks-rust', binary: 'ssserver', logo: 'ss'},
    'tuic-server': {name: 'TUIC Server', binary: 'tuic-server', logo: 'tuic'},
  };
  const coreOrder = Object.keys(coreCatalog);
  let overview;
  let homeRange = 'live';
  const liveSamples = [];
  const dash = '—';
  const bytes = value => !Number.isFinite(value) ? dash :
    value >= 1024 ** 4 ? `${(value / 1024 ** 4).toFixed(2)} TB` : fmtTrafficBytes(value);
  const rate = value => !Number.isFinite(value) ? dash : value < 1000 ? `${value} bps` :
    value >= 1e6 ? `${(value / 1e6).toFixed(2)} Mbps` : `${(value / 1000).toFixed(1)} kbps`;
  const endpoint = (host, port) => host ? `${host.includes(':') ? '[' + host + ']' : host}${port ? ':' + port : ''}` : dash;

  function coreMark(id) {
    return serviceMark(coreCatalog[id]?.logo);
  }
  function serviceMark(logo = 'generic') {
    const mark = element('span', null, 'icon-box v1-core-logo ' + logo);
    mark.setAttribute('aria-hidden', 'true');
    mark.innerHTML = coreIcons[logo];
    return mark;
  }
  function element(tag, content, className) {
    const node = document.createElement(tag);
    if (content != null) node.textContent = content;
    if (className) node.className = className;
    return node;
  }
  function iconsApi() {
    return (typeof window !== 'undefined' && window.hopworldIcons) || null;
  }
  function iconSvg(name, size) {
    const api = iconsApi();
    return (api && typeof api.svg === 'function' && api.svg(name, size)) || '';
  }
  function setIcon(node, name, size) {
    if (!node) return;
    const html = iconSvg(name, size);
    if (html) node.innerHTML = html;
    else node.replaceChildren();
  }
  function saveBlob(blob, name) {
    const url = URL.createObjectURL(blob);
    const link = element('a');
    link.href = url;
    link.download = name;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function metrics(id, entries) {
    const box = byId(id);
    const cards = entries.map(([label, value, icon, tone]) => {
      const card = element('div', null, 'v1-metric');
      const api = iconsApi();
      const iconName = (api && typeof api.resolve === 'function' && api.resolve(icon)) || (icon === 'updown' ? 'arrow-up-down' : icon);
      const mark = element('span', null, 'icon-box ' + (tone || 'blue') + (iconName === 'arrow-up-down' ? ' v1-metric-updown' : ''));
      mark.setAttribute('aria-hidden', 'true');
      setIcon(mark, iconName, 24);
      const details = element('div');
      details.append(element('span', label), element('strong', value ?? dash));
      card.append(mark, details);
      return card;
    });
    if (window.hopworldTheme?.motion?.applyMetricCards?.(box, cards, entries)) return;
    if (id === 'v1-overview-metrics' && cards.length) {
      const lead = element('div', null, 'v1-overview-metrics-lead');
      const last = cards[cards.length - 1];
      last.classList.add('v1-overview-metrics-rate');
      lead.append(...cards.slice(0, -1));
      box.replaceChildren(lead, last);
      return;
    }
    box.replaceChildren(...cards);
  }
  const overviewSecretApi = window.hopworldOverviewSecret;
  const overviewSecretIcons = overviewSecretApi?.ICONS || {};
  function fallbackOverviewSecrets() {
    const mask = '********';
    const placeholders = new Set(['', '—', '–', '-', '未配置', '待配置', '未获取', '待检测', '待签发', '无', '未设置', '原始地址']);
    const values = new Map();
    const snapshot = id => {
      const value = values.get(id) || '';
      const text = value.trim();
      const maskable = !placeholders.has(text);
      return {value, maskable, revealed: false, until: null, display: maskable ? mask : (text || '—')};
    };
    return {
      setValue(id, value) { values.set(id, value == null ? '' : String(value)); return snapshot(id); },
      toggle(id) { return snapshot(id); },
      conceal(id) { return snapshot(id); },
      hideAll() {},
      snapshot,
    };
  }
  const overviewSecrets = overviewSecretApi?.createOverviewSecrets({
    onChange() { paintOverviewSecrets(); },
  }) || fallbackOverviewSecrets();
  function secretViewState(label, snap) {
    if (overviewSecretApi?.viewState) return overviewSecretApi.viewState(label, snap);
    return {text: snap.display, maskable: snap.maskable, revealed: false, button: null};
  }
  function paintOverviewSecret(dd, id) {
    const label = dd.dataset.overviewSecretName || '';
    const snap = overviewSecrets.snapshot(id);
    const view = secretViewState(label, snap);
    const valueNode = element('span', view.text, 'v1-overview-secret-value');
    dd.removeAttribute('title');
    if (!view.button) {
      dd.classList.remove('v1-overview-secret');
      dd.replaceChildren(valueNode);
      return;
    }
    dd.classList.add('v1-overview-secret');
    const button = element('button', null, 'v1-icon-btn v1-overview-secret-toggle');
    button.type = 'button';
    button.setAttribute('aria-label', view.button.ariaLabel);
    button.setAttribute('aria-pressed', view.button.ariaPressed);
    button.innerHTML = overviewSecretIcons[view.button.icon] || '';
    button.addEventListener('click', () => overviewSecrets.toggle(id));
    if (dd.dataset.overviewSecretBefore === 'true') dd.replaceChildren(button, valueNode);
    else dd.replaceChildren(valueNode, button);
  }
  function paintOverviewSecrets() {
    for (const dd of document.querySelectorAll('[data-overview-secret]')) {
      paintOverviewSecret(dd, dd.dataset.overviewSecret);
    }
  }
  function hideOverviewSecrets() {
    overviewSecrets.hideAll();
    paintOverviewSecrets();
  }
  function definitions(id, entries) {
    byId(id).replaceChildren(...entries.map(([name, value, secret]) => {
      const item = element('div');
      const dd = element('dd');
      item.append(element('dt', name), dd);
      if (secret?.id) {
        dd.dataset.overviewSecret = secret.id;
        dd.dataset.overviewSecretName = secret.label || name;
        overviewSecrets.setValue(secret.id, value ?? '');
        paintOverviewSecret(dd, secret.id);
      } else {
        dd.textContent = value ?? dash;
      }
      return item;
    }));
  }
  function badge(core) {
    const item = element('span', statuses[core.status] || '未能确认', 'v1-status');
    item.dataset.state = core.status;
    return item;
  }
  function renderOverview() {
    const nodes = overview?.nodes || [];
    metrics('v1-overview-metrics', [
      ['管理服务', overview ? '在线' : '等待连接', 'zap'],
      ['运行内核', system ? `${system.cores.filter(c => c.status === 'running').length} / ${system.cores.length}` : null, 'cpu', 'cyan'],
      ['订阅中启用节点', overview ? nodes.filter(n => n.enabled).length : null, 'network', 'violet'],
      ['今日节点流量', bytes(traffic?.today?.total_bytes), 'arrow-left-right'],
      ['实时速率', `↓ ${rate(traffic?.down_bps)} / ↑ ${rate(traffic?.up_bps)}`, 'updown', 'violet'],
    ]);
  }
  function renderGlobalProbe({jobs, nodes, sources}, wasRunning) {
    const job = jobs?.probe;
    const running = !!job?.running;
    const active = job?.active || {};
    const single = nodes.filter(node => node.probe?.status === 'testing' && !Object.hasOwn(active, node.id));
    const panel = byId('v1-global-probe');
    const finished = wasRunning && !running && !single.length;
    if (!running && !single.length && !finished) {
      if (!probeProgressHideTimer) {
        panel.open = false;
        panel.hidden = true;
      }
      return;
    }
    clearTimeout(probeProgressHideTimer);
    probeProgressHideTimer = undefined;
    panel.hidden = false;

    const batch = running || finished;
    const targets = batch ? job.target : [...new Set(single.map(node => node.probe.target))].join('、');
    const state = finished ? (job.cancelled ? '已停止' : '已完成') : job?.stopping ? '正在停止' : '正在测试';
    byId('v1-global-probe-state').textContent = [state, targets].filter(Boolean).join(' · ');
    const count = byId('v1-global-probe-count');
    const summary = finished ? `通过 ${job.done - job.failed} · 已测 ${job.done}/${job.total}`
      : running ? `已完成 ${job.done} / ${job.total}` : `正在测 ${single.length} 个节点`;
    if (count.textContent !== summary) count.textContent = summary;
    const meter = byId('v1-global-probe-meter');
    meter.hidden = !batch;
    if (batch) {
      meter.max = job.total;
      meter.value = job.done;
    }

    const groupNames = new Map(sources.map(source => [`source:${source.id}`, `订阅「${source.name}」`]));
    groupNames.set('local', '本地发布');
    groupNames.set('manual', '手动导入');
    const nodeById = new Map(nodes.map(node => [node.id, node]));
    const inFlight = Object.entries(active).sort((a, b) => a[1] - b[1])
      .map(([id, position]) => [`第 ${position}/${job.total} 个`, nodeById.get(id), job.groups]);
    for (const node of single) inFlight.push(['单节点', node, null]);
    const rows = inFlight.map(([position, node, scope]) => {
      const groups = node?.groups || scope || [];
      const labels = (scope ? groups.filter(group => scope.includes(group)) : groups).map(group => groupNames.get(group)).filter(Boolean);
      return [position, [...labels, node?.title || '节点已移除'].join(' · ')];
    });
    const preview = byId('v1-global-probe-preview');
    preview.textContent = rows[0]?.join(' · ') || '';
    preview.title = preview.textContent;
    byId('v1-global-probe-nodes').replaceChildren(...rows.map(([position, label]) => {
      const row = element('li');
      row.append(element('span', position), element('span', label));
      return row;
    }));
    if (finished) {
      probeProgressHideTimer = setTimeout(() => {
        panel.open = false;
        panel.hidden = true;
        probeProgressHideTimer = undefined;
      }, 6000);
    }
  }
  const subscriptionLabelDefaults = {running: '订阅开', error: '订阅异常', stopped: '订阅关', unknown: '订阅待查'};
  function renderSubscriptionIndicator(publication) {
    const status = byId('v1-subscription-health');
    const label = byId('v1-subscription-health-label');
    const accessible = byId('v1-subscription-health-a11y');
    const state = publication?.base ? 'running' : publication?.error ? 'error' :
      publication ? 'stopped' : 'unknown';
    const text = publication?.topbar_labels?.[state] ?? subscriptionLabelDefaults[state];
    const description = {running: '公网订阅已启动', error: '公网订阅启动异常',
      stopped: '公网订阅未启动', unknown: '公网订阅状态未知'}[state];
    status.dataset.state = state;
    window.hopworldTheme?.motion?.noteStatus?.(status, state);
    status.dataset.iconOnly = String(!text);
    label.hidden = !text;
    if (label.textContent !== text) label.textContent = text;
    if (accessible.textContent !== description) accessible.textContent = description;
    status.title = publication?.error || description;
  }
  function renderManagement(data) {
    const wasProbing = overview?.jobs?.probe?.running;
    overview = data;
    renderGlobalProbe(data, wasProbing);
    byId('v1-health').textContent = '服务在线';
    byId('v1-health').parentElement.dataset.ready = 'true';
    window.hopworldTheme?.motion?.noteStatus?.(byId('v1-health').parentElement, 'online');
    renderSubscriptionIndicator(data.public_subscription);
    const {nodes, sources} = data;
    const local = data.local_node || {};
    const acme = data.acme || {};
    const shownNodes = nodes.filter(n => n.enabled);
    const subNodes = nodes.filter(n => n.groups?.some(g => g.startsWith('source:')));
    const host = local.public_host;
    const certLabel = !acme.enabled ? '未启用'
      : acme.state === 'expired' ? '已过期'
      : acme.certificate_valid ? (acme.error ? '有效 · 续签异常' : '有效')
      : acme.source === 'manual' ? '不可用'
      : acme.error ? '签发异常' : '待签发';
    metrics('v1-node-metrics', [
      ['全部节点', nodes.length, 'network'], ['已启用', shownNodes.length, 'check', 'cyan'],
      ['本地发布', nodes.filter(n => n.local).length, 'radio', 'violet'], ['订阅导入', subNodes.length, 'rss'],
      ['今日流量', traffic ? bytes(traffic.today?.total_bytes) : null, 'arrow-left-right', 'cyan'],
    ]);
    renderNodes();
    const probed = subNodes.filter(node => node.probe && node.probe.status !== 'testing');
    const passed = probed.filter(node => node.probe.status === 'ok').length;
    const failed = probed.filter(node => node.probe.status !== 'ok').length;
    const nextUpdate = sources.map(source => source.next_update).filter(Boolean).sort((a, b) => a - b)[0];
    metrics('v1-sub-metrics', [
      ['订阅源总数', sources.length, 'rss'],
      ['解析节点总数', sources.reduce((sum, source) => sum + (source.count || 0), 0), 'layers', 'violet'],
      ['可用节点', probed.length ? passed : null, 'check', 'cyan'],
      ['异常节点', probed.length ? failed : null, 'triangle-alert', 'violet'],
      ['下次自动更新', nextUpdate ? remainText(nextUpdate) : null, 'refresh-cw'],
    ]);
    renderSubscriptions();
    renderPublicAccess(local);
    if (local.public_host && local.dns_verify && !certificateDNS.has(local.public_host)) {
      certificateDNS.set(local.public_host, local.dns_verify);
    }
    renderCertificates();
    renderSettingsNetwork(acme);
    definitions('v1-overview-cert', [
      ['证书域名', acme.domain || host, {id: 'overview-cert-domain', label: '证书域名'}],
      ['证书状态', certLabel],
      ['剩余有效期', Number.isFinite(acme.days_left) ? `${acme.days_left} 天` : dash],
      ['自动续签', acme.automatic ? '已开启' : '未开启'],
    ]);
    metrics('v1-overview-nodes', [
      ['全部节点', nodes.length, 'layout-grid'],
      ['Reality', nodes.filter(n => n.local && n.protocol === 'vless').length, 'shield', 'violet'],
      ['UDP / QUIC', nodes.filter(n => n.local && ['hysteria2','hy2','tuic'].includes(n.protocol)).length, 'zap', 'cyan'],
      ['已启用', shownNodes.length, 'check'],
    ]);
    const recent = sources.filter(source => source.updated).sort((a,b) => b.updated - a.updated).slice(0,5);
    byId('v1-overview-events').replaceChildren(...(recent.length ? recent.map(source => {
      const item = element('li');
      item.append(element('span', `${source.name} · 获取 ${source.count} 个节点`),
        element('small', new Date(source.updated * 1000).toLocaleString()));
      return item;
    }) : [element('li', '暂无成功更新记录')]));
    applyFilters();
    renderOverview();
    if (page === 'cores') renderCores();
  }
  function knownCores() {
    if (system?.cores?.length) return system.cores;
    return coreOrder.map(id => ({id, ...coreCatalog[id], status: 'not_installed', installed: false, path: null}));
  }
  function coreName(id) {
    return system?.cores?.find(core => core.id === id)?.name || coreCatalog[id]?.name || id;
  }
  function publishedCount(core) {
    const nodes = (overview?.nodes || []).filter(node => node.local && node.enabled !== false);
    const count = nodes.filter(node => node.core_id === core.id).length;
    if (core.independent) return count;
    const legacy = nodes.filter(node => !node.core_id);
    const nativePort = overview?.local_node?.native_hysteria?.port;
    if (core.id === 'xray-core') return count + legacy.filter(node => ['vless', 'vmess', 'trojan'].includes(node.protocol)).length;
    if (core.id === 'hysteria2') return count + legacy.filter(node => node.port === nativePort && node.protocol === 'hysteria2').length;
    if (core.id === 'sing-box') return count + legacy.filter(node => node.port !== nativePort && ['hysteria2', 'hy2', 'tuic'].includes(node.protocol)).length;
    return null;
  }
  function autostart(core) {
    if (!core.installed) return {on: false, text: core.managed ? '未安装' : '未托管'};
    if (!core.managed) return {on: false, text: '未托管'};
    if (core.independent) return {on: core.autostart, text: core.autostart ? '随 Router 恢复' : '未开启'};
    if (!overview) return {on: false, text: '读取中'};
    if (core.id === 'hysteria2') {
      return overview.local_node?.native_hysteria?.enabled
        ? {on: true, text: '随本地节点恢复'} : {on: false, text: '未开启'};
    }
    if (core.id === 'sing-box' && !overview.local_node?.legacy_singbox_enabled) return {on: false, text: '未开启'};
    return overview.local_node?.enabled ? {on: true, text: '跟随本地节点'} : {on: false, text: '未开启'};
  }
  function explain(core) {
    if (core.error) return core.error;
    if (core.installed && !core.independent && core.status === 'running') {
      return '此进程沿用旧版发布配置，可通过「独立配置」切换。';
    }
    if (core.installed) return '可单独配置监听地址、端口和出口，启动后加入统一订阅。';
    return '请先安装此内核。';
  }
  function openCoreSettings(core) {
    if (!core) return;
    byId('v1-core-settings-title').textContent = '设置 ' + core.name;
    const facts = element('dl', null, 'v1-definitions');
    facts.append(
      detailLine('版本', core.version || '未读取'),
      detailLine('程序', core.path || '未找到'),
      detailLine('配置文件', core.config_path ? core.config_path + (core.config_ready ? '' : '（尚未生成）') : '无'),
      detailLine('自动启动', autostart(core).text),
    );
    const actions = element('div', null, 'v1-core-quick');
    const copyProgram = element('button', '复制程序', 'secondary');
    copyProgram.type = 'button';
    copyProgram.disabled = !core.path;
    copyProgram.addEventListener('click', () => copyText(core.path, '已复制程序路径'));
    const copyConfig = element('button', '复制配置', 'secondary');
    copyConfig.type = 'button';
    copyConfig.disabled = !core.config_path;
    copyConfig.addEventListener('click', () => copyText(core.config_path, core.config_ready ? '已复制配置文件路径' : '已复制配置路径，文件尚未生成'));
    const install = element('button', core.installed ? '重新安装' : '安装内核', 'primary');
    install.type = 'button';
    install.addEventListener('click', () => {
      byId('v1-core-settings').close();
      askUpdate(core);
    });
    const remove = element('button', '卸载', 'secondary');
    remove.type = 'button';
    remove.disabled = !core.installed;
    remove.addEventListener('click', () => {
      byId('v1-core-settings').close();
      void uninstallCore(core.id);
    });
    actions.append(copyProgram, copyConfig, install, remove);
    const body = byId('v1-core-settings-body');
    body.replaceChildren(facts, element('p', explain(core), 'field-hint'),
      detailHeading('shield', '可发布协议'), coreProtocolChips(core));
    if (core.instances?.length) {
      const instanceActions = element('div', null, 'v1-core-protocol-switches');
      for (const instance of core.instances) {
        const row = element('div', null, 'v1-actions');
        const config = element('button', `配置端口 ${instance.port}`, 'secondary');
        config.type = 'button';
        config.addEventListener('click', () => openCoreProfile(core, null, instance.id));
        row.append(config);
        if (instance.running) {
          const stop = element('button', `停止端口 ${instance.port}`, 'text-button');
          stop.type = 'button';
          stop.addEventListener('click', () => askStop({...core, name: `${core.name} · ${instance.port}`}, instance.id));
          row.append(stop);
        }
        instanceActions.append(row);
      }
      body.append(detailHeading('radio', '其他发布端口'), instanceActions);
    }
    body.append(actions);
    const dialog = byId('v1-core-settings');
    if (!dialog.open) dialog.showModal();
  }
  function syncCheckAll() {
    const all = byId('v1-core-check-all');
    const boxes = [...byId('v1-core-rows').querySelectorAll('[data-core-check]')];
    const checked = boxes.filter(box => box.checked).length;
    all.disabled = !boxes.length;
    all.checked = boxes.length > 0 && checked === boxes.length;
    all.indeterminate = checked > 0 && checked < boxes.length;
  }
  function iconButton(name, label, act, id) {
    const button = element('button', null, 'v1-icon-btn');
    button.type = 'button';
    button.setAttribute('aria-label', label);
    button.dataset.coreAct = act;
    button.dataset.coreId = id;
    setIcon(button, name, 20);
    return button;
  }
  const nodeActionIcons = {
    edit: '<svg class="v1-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z" /><path d="m15 5 4 4" /></svg>',
    probe: '<svg class="v1-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m12 14 4-4" /><path d="M3.34 19a10 10 0 1 1 17.32 0" /></svg>',
    logs: '<svg class="v1-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M15 12h-5" /><path d="M15 8h-5" /><path d="M19 17V5a2 2 0 0 0-2-2H4" /><path d="M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11a1 1 0 0 0-1 1v1a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v2a1 1 0 0 0 1 1h3" /></svg>',
  };
  function nodeIconButton(act, label, id) {
    const button = element('button', null, 'v1-icon-btn');
    button.type = 'button';
    button.setAttribute('aria-label', label);
    button.dataset.nodeAct = act;
    button.dataset.nodeId = id;
    button.innerHTML = nodeActionIcons[act];
    return button;
  }
  function tile(name, label, act, enabled) {
    const button = element('button', null, 'secondary');
    button.type = 'button';
    button.dataset.coreAct = act;
    const mark = element('span', null, 'v1-tile-icon');
    mark.setAttribute('aria-hidden', 'true');
    setIcon(mark, name, 20);
    button.append(mark, element('span', label));
    button.disabled = !enabled;
    return button;
  }
  function detailHeading(name, text) {
    const heading = element('h3');
    const mark = element('span', null, 'v1-section-mark');
    mark.setAttribute('aria-hidden', 'true');
    setIcon(mark, name, 14);
    heading.append(mark, document.createTextNode(text));
    return heading;
  }
  function duration(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return null;
    const total = Math.floor(seconds);
    const days = Math.floor(total / 86400);
    const hours = Math.floor(total % 86400 / 3600);
    const minutes = Math.floor(total % 3600 / 60);
    return [days ? days + ' 天' : '', hours || days ? hours + ' 小时' : '', minutes + ' 分钟'].filter(Boolean).join(' ');
  }
  function meter(percent) {
    const bar = element('span', null, 'v1-meter');
    const fill = element('span');
    if (Number.isFinite(percent)) fill.style.width = Math.max(0, Math.min(100, percent)) + '%';
    bar.append(fill);
    return bar;
  }
  function detailLine(name, value, secret) {
    const item = element('div');
    const dd = element('dd');
    if (secret?.id) {
      dd.dataset.overviewSecret = secret.id;
      dd.dataset.overviewSecretName = secret.label || name;
      if (secret.before) dd.dataset.overviewSecretBefore = 'true';
      overviewSecrets.setValue(secret.id, value ?? '');
      paintOverviewSecret(dd, secret.id);
    } else if (value instanceof Node) dd.append(value);
    else dd.textContent = value ?? dash;
    item.append(element('dt', name), dd);
    return item;
  }
  function currentProtocolPublisher(protocol, coreId) {
    const publishers = publicPortRows.filter(port => port.nodeProtocol === protocol);
    return publishers.find(port => port.core_id === coreId)
      || publishers.find(port => port.running) || publishers[0];
  }
  function preferredProtocolCore(coreId, protocol) {
    const current = currentProtocolPublisher(protocol, coreId);
    const sharedQUIC = current?.core_id === 'sing-box' && publicPortRows.some(port =>
      port.core_id === 'sing-box' && port.nodeProtocol === (protocol === 'hysteria2' ? 'tuic' : 'hysteria2'));
    if (protocol === 'hysteria2') return sharedQUIC ? 'sing-box' : 'hysteria2';
    if (protocol === 'tuic') return sharedQUIC || current?.egress === 'fixed' ? 'sing-box' : 'tuic-server';
    if (protocol === 'ss') return current?.egress === 'fixed' ? 'mihomo' : 'shadowsocks-rust';
    return 'xray-core';
  }
  function protocolChip(protocol, coreId) {
    const button = element('button', coreSwitchGuide[protocol].name, 'v1-chip v1-protocol-chip');
    button.type = 'button';
    button.setAttribute('aria-haspopup', 'dialog');
    button.setAttribute('aria-controls', 'v1-core-protocol-dialog');
    if (preferredProtocolCore(coreId, protocol) === coreId) {
      button.dataset.preferred = 'true';
      button.append(element('small', '优先'));
    }
    button.addEventListener('click', () => openCoreProtocol(coreId, protocol));
    return button;
  }
  function coreProtocolChips(core) {
    const chips = element('div', null, 'v1-chips');
    for (const [protocol, spec] of Object.entries(coreSwitchGuide)) {
      if (!spec.cores.includes(core.id) || !core.protocols?.includes(spec.name)) continue;
      chips.append(protocolChip(protocol, core.id));
    }
    return chips;
  }
  function openCoreProtocol(selectedCore, protocol) {
    const spec = coreSwitchGuide[protocol];
    const currentCore = selectedCore || currentProtocolPublisher(protocol)?.core_id;
    const preferred = preferredProtocolCore(currentCore, protocol);
    const choices = element('dl', null, 'v1-core-protocol-options');
    for (const [index, coreId] of spec.cores.entries()) {
      const candidate = system?.cores.find(row => row.id === coreId);
      const label = element('dt', coreId === 'hysteria2' ? '原生 Hysteria 2' : coreName(coreId));
      if (coreId === currentCore) label.append(element('small', '当前内核'));
      if (candidate && !candidate.installed) label.append(element('small', '未安装'));
      if (coreId === preferred) label.append(element('small', '当前场景优先', 'v1-core-protocol-preference'));
      const entry = element('div');
      if (coreId === preferred) entry.dataset.preferred = 'true';
      entry.append(label, element('dd', spec.uses[index]));
      choices.append(entry);
    }
    const body = byId('v1-core-protocol-body');
    byId('v1-core-protocol-title').textContent = spec.name + (spec.cores.length > 1 ? ' 内核对比' : ' 发布内核');
    body.replaceChildren(choices);
    if (spec.cores.length > 1) {
      const shown = new Set();
      const actions = element('div', null, 'v1-core-protocol-switches');
      for (const port of publicPortRows) {
        const entry = {source: port.core_id, protocol, port: port.port};
        if (port.nodeProtocol !== protocol || !canSwitchPublisher(entry)) continue;
        const key = `${entry.source}:${entry.port}`;
        if (shown.has(key)) continue;
        shown.add(key);
        const target = spec.cores.find(id => id !== entry.source);
        const button = element('button', `将 ${port.publicPort ?? port.port} 切换到 ${coreName(target)}`, 'secondary');
        button.type = 'button';
        button.addEventListener('click', () => openPublisherSwitch(entry));
        actions.append(button);
      }
      body.append(shown.size ? actions : element('p', '暂无可切换的发布端口。', 'field-hint'));
    }
    coreProtocolDialog.showModal();
  }
  function renderDetail(core) {
    const panel = byId('v1-core-detail');
    if (!core) {
      panel.replaceChildren(element('p', '在左侧选择一个内核。'));
      return;
    }
    const head = element('div', null, 'v1-core-head');
    const mark = coreMark(core.id);
    const title = element('div');
    const meta = [core.version || '版本未读取'];
    if (core.pid) meta.push('PID: ' + core.pid);
    title.append(element('h2', core.name), element('p', meta.join(' | ')));
    head.append(mark, title, badge(core));
    const actions = element('div', null, 'v1-core-actions');
    const running = core.status === 'running';
    actions.append(
      running ? tile('square', '停止', 'stop', true) : tile('play', '启动', 'start', true),
      tile('rotate-cw', '重启内核', 'restart', running), tile('scroll-text', '查看日志', 'logs', true),
    );
    const hint = element('p', explain(core), 'field-hint');
    hint.id = 'v1-core-limit';
    actions.querySelectorAll('button').forEach(button => button.setAttribute('aria-describedby', hint.id));
    const facts = element('dl', null, 'v1-definitions');
    const cpu = Number.isFinite(core.cpu_percent) ? meter(core.cpu_percent) : null;
    const cpuText = document.createTextNode(Number.isFinite(core.cpu_percent) ? core.cpu_percent.toFixed(1) + '%' : '未采集');
    const cpuValue = element('span', null, 'v1-meter-line');
    if (cpu) cpuValue.append(cpu);
    cpuValue.append(cpuText);
    const memoryMax = system?.memory?.total_bytes;
    const memoryValue = element('span', null, 'v1-meter-line');
    if (Number.isFinite(core.memory_bytes)) {
      if (memoryMax) memoryValue.append(meter(100 * core.memory_bytes / memoryMax));
      memoryValue.append(document.createTextNode(bytes(core.memory_bytes)));
    } else memoryValue.textContent = '未采集';
    const listen = element('span', `TCP: ${core.tcp_ports?.length ? core.tcp_ports.join(', ') : '-'} | UDP: ${core.udp_ports?.length ? core.udp_ports.join(', ') : '-'}`);
    const config = element('span', null, 'v1-config-line');
    const configText = element('span', core.config_path ? core.config_path + (core.config_ready ? '' : '（尚未生成）') : '无');
    if (core.config_path) configText.title = core.config_path;
    config.append(configText);
    if (core.config_path) config.append(iconButton('copy', '复制配置路径', 'copy-config', core.id));
    facts.append(
      detailLine('运行时长', duration(core.uptime_seconds) || '未采集'),
      detailLine('CPU 使用率', cpuValue),
      detailLine('内存占用', memoryValue),
      detailLine('监听端口', listen),
      detailLine('配置文件', config),
    );
    const quick = element('div', null, 'v1-core-quick');
    quick.append(
      tile('pencil', '独立配置', 'profile', core.installed),
      tile('download', '导出配置', 'export', !!core.config_ready),
      tile('archive', '备份配置', 'backup', !!core.config_ready),
      coreNeedsDownload(core) ? tile('download', '下载内核', 'download', true) : tile('refresh-cw', '更新内核', 'install', true),
    );
    panel.replaceChildren(head, actions, hint, detailHeading('shield', '可发布协议'), coreProtocolChips(core),
      detailHeading('file-text', '运行信息'), facts, detailHeading('zap', '快捷操作'), quick);
  }
  function selectCore(id, scroll) {
    selectedCoreId = id;
    for (const row of byId('v1-core-rows').children) {
      const on = row.dataset.corePick === id;
      row.classList.toggle('is-selected', on);
      row.querySelector('.v1-core-pick')?.setAttribute('aria-pressed', String(on));
    }
    renderDetail((system?.cores || []).find(core => core.id === id));
    if (scroll && window.matchMedia('(max-width: 1100px)').matches) byId('v1-core-detail').scrollIntoView({block: 'nearest'});
  }
  function installMenuItems() {
    return knownCores().map(core => {
      const button = element('button', `${core.installed ? '重新安装' : '安装'} ${core.name}`);
      button.type = 'button';
      button.setAttribute('role', 'menuitem');
      button.dataset.coreInstall = core.id;
      return button;
    });
  }
  function coreHeadingButtons() {
    const wizard = element('button', '内核安装向导', 'secondary');
    wizard.type = 'button';
    wizard.id = 'v1-core-wizard-open';
    const wrap = element('div', null, 'v1-menu');
    const install = element('button', '+ 安装内核', 'primary');
    install.type = 'button';
    install.id = 'v1-core-install';
    install.dataset.menuToggle = '';
    install.setAttribute('aria-controls', 'v1-core-install-menu');
    install.setAttribute('aria-haspopup', 'menu');
    install.setAttribute('aria-expanded', 'false');
    const menu = element('div', null, 'v1-menu-panel');
    menu.id = 'v1-core-install-menu';
    menu.setAttribute('role', 'menu');
    menu.hidden = true;
    menu.append(...installMenuItems());
    wrap.append(install, menu);
    return [wizard, wrap];
  }
  function fillWizard(focusId) {
    const intro = element('p', '安装包经 SHA256 校验后写入本机数据目录。独立 TUIC Server 使用锁定的 Siglud/tuic v1.0.0。');
    intro.className = 'field-hint';
    const list = element('ol', null, 'v1-wizard-list');
    for (const core of knownCores()) {
      const item = element('li');
      item.id = 'v1-wizard-' + core.id;
      if (core.id === focusId) item.className = 'is-focus';
      const binary = core.binary || coreCatalog[core.id]?.binary || dash;
      item.append(element('strong', core.name), element('span', `${statuses[core.status] || '尚未检测'} · 程序名 ${binary}`), element('span', core.path || '未找到'));
      list.append(item);
    }
    byId('v1-core-wizard-body').replaceChildren(intro, list);
  }
  function openWizard(focusId) {
    closeMenus();
    fillWizard(focusId);
    const dialog = byId('v1-core-wizard');
    if (!dialog.open) dialog.showModal();
    if (focusId) byId('v1-wizard-' + focusId)?.scrollIntoView({block: 'nearest'});
  }
  function clearMenuPosition(panel) {
    panel.style.position = '';
    panel.style.top = '';
    panel.style.right = '';
    panel.style.left = '';
    panel.style.bottom = '';
    panel.style.minWidth = '';
  }
  function placeMenu(toggle, panel) {
    if (!toggle.closest('.v1-node-tools')) return;
    // Keep absolute under .v1-menu. Fixed + viewport coords break when a
    // theme ancestor (backdrop-filter / transform) becomes the fixed containing block.
    clearMenuPosition(panel);
    const rect = toggle.getBoundingClientRect();
    const menu = toggle.closest('.v1-menu');
    const menuRect = menu?.getBoundingClientRect() || rect;
    const viewport = document.documentElement.clientWidth;
    const width = Math.max(220, Math.round(rect.width));
    panel.style.minWidth = `${width}px`;
    const rightAlignedLeft = menuRect.right - width;
    if (rightAlignedLeft >= 8 && menuRect.right <= viewport - 8) {
      panel.style.left = 'auto';
      panel.style.right = '0';
      return;
    }
    panel.style.right = 'auto';
    panel.style.left = '0';
  }
  function closeMenus() {
    for (const panel of document.querySelectorAll('.v1-menu-panel')) {
      panel.hidden = true;
      clearMenuPosition(panel);
      panel.parentElement?.querySelector('[data-menu-toggle]')?.setAttribute('aria-expanded', 'false');
    }
    const filterMenu = byId('v1-filter-select-menu');
    if (filterMenu) {
      filterMenu.hidden = true;
      filterMenu.replaceChildren();
      delete filterMenu.dataset.selectId;
    }
    for (const select of document.querySelectorAll('.v1-node-filters select[aria-expanded="true"]')) {
      select.setAttribute('aria-expanded', 'false');
    }
  }
  function filterSelectMenuHost() {
    return document.querySelector('.v1-node-filters');
  }
  function ensureFilterSelectMenu() {
    let panel = byId('v1-filter-select-menu');
    if (panel) return panel;
    panel = element('div', null, 'v1-menu-panel');
    panel.id = 'v1-filter-select-menu';
    panel.setAttribute('role', 'listbox');
    panel.hidden = true;
    return panel;
  }
  function placeFilterSelectMenu(select, panel) {
    const host = filterSelectMenuHost();
    if (!host || !select || !panel) return;
    if (panel.parentElement !== host) host.append(panel);
    clearMenuPosition(panel);
    const sr = select.getBoundingClientRect();
    const hr = host.getBoundingClientRect();
    const width = Math.max(Math.round(sr.width), 160);
    panel.style.position = 'absolute';
    panel.style.top = `${Math.round(sr.bottom - hr.top + 6)}px`;
    panel.style.left = `${Math.round(sr.left - hr.left)}px`;
    panel.style.right = 'auto';
    panel.style.minWidth = `${width}px`;
    panel.style.zIndex = '50';
  }
  function openFilterSelectMenu(select) {
    if (document.documentElement.dataset.theme !== 'neon-fusion') return false;
    if (!select || !select.closest('.v1-node-filters')) return false;
    const panel = ensureFilterSelectMenu();
    const already = !panel.hidden && panel.dataset.selectId === select.id;
    closeMenus();
    if (already) return true;
    panel.replaceChildren(...[...select.options].map(option => {
      const item = element('button', option.textContent, null);
      item.type = 'button';
      item.setAttribute('role', 'option');
      item.dataset.value = option.value;
      if (option.disabled) item.disabled = true;
      if (option.selected) item.setAttribute('aria-selected', 'true');
      return item;
    }));
    panel.dataset.selectId = select.id;
    panel.hidden = false;
    select.setAttribute('aria-expanded', 'true');
    placeFilterSelectMenu(select, panel);
    return true;
  }
  function syncOpenMenus() {
    for (const toggle of document.querySelectorAll('[data-menu-toggle][aria-expanded="true"]')) {
      const panel = byId(toggle.getAttribute('aria-controls'));
      if (panel && !panel.hidden) placeMenu(toggle, panel);
    }
    const filterMenu = byId('v1-filter-select-menu');
    if (filterMenu && !filterMenu.hidden && filterMenu.dataset.selectId) {
      const select = byId(filterMenu.dataset.selectId);
      if (select) placeFilterSelectMenu(select, filterMenu);
    }
  }
  window.addEventListener('resize', syncOpenMenus);
  document.addEventListener('scroll', syncOpenMenus, true);
  async function copyText(text, success) {
    try {
      await navigator.clipboard.writeText(text);
      note(success || '已复制');
    } catch {
      note('复制失败，请手动选择路径', {error: true});
    }
  }
  function installProgress(name, event) {
    if (event.stage === 'release') return `正在读取 ${name} 的发布信息…`;
    if (event.stage === 'check') return `正在校验 ${name} 的安装包…`;
    if (event.stage === 'extract') return `正在解压 ${name}…`;
    if (event.stage === 'write') return `正在写入 ${name}…`;
    const got = bytes(event.received || 0);
    if (event.total > 0) {
      const pct = Math.min(100, Math.round(100 * event.received / event.total));
      return `正在下载 ${name} ${pct}%（${got} / ${bytes(event.total)}）`;
    }
    return event.received ? `正在下载 ${name} ${got}` : `正在下载 ${name}…`;
  }
  async function readInstallEvents(response, name) {
    if (!response.ok || !response.body) {
      let result = {};
      try { result = await response.json(); } catch { result = {}; }
      throw new Error(result.error || `请求失败 (${response.status})`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let result = null;
    const take = line => {
      if (!line.trim()) return;
      const event = JSON.parse(line);
      if (event.error) throw new Error(event.error);
      if (event.ok) result = event;
      else if (event.paused) {
        const error = new Error('已暂停下载');
        error.paused = true;
        error.received = event.received || 0;
        error.total = event.total || 0;
        throw error;
      } else if (event.cancelled) {
        const error = new Error('已停止下载');
        error.cancelled = true;
        throw error;
      } else if (event.stage === 'download') downloadNote(installProgress(name, event), false);
      else note(installProgress(name, event));
    };
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      const lines = buffer.split('\n');
      buffer = lines.pop();
      lines.forEach(take);
    }
    buffer += decoder.decode();
    if (buffer.trim()) take(buffer);
    if (!result) throw new Error('安装没有完成');
    return result;
  }
  async function uninstallCore(id) {
    if (!id) return;
    if (coreInstallBusy) { note('正在安装，请稍候'); return; }
    const core = (system?.cores || []).find(item => item.id === id) || {name: coreCatalog[id]?.name || '内核'};
    if (!window.confirm(`只删除本页安装到数据目录的 ${core.name}。系统里的同名程序不会删除。`)) return;
    coreInstallBusy = true;
    note('正在卸载 ' + core.name + '…');
    try {
      const result = await call('/api/v1/cores/uninstall', {id});
      if (result.system) renderSystem(result.system);
      else await loadSystem(true);
      note(core.name + ' 已从本机数据目录卸载');
    } catch (error) {
      note(error.message, {error: true});
    } finally {
      coreInstallBusy = false;
    }
  }
  function openConfirm(title, content, okLabel, onOk) {
    byId('v1-core-confirm-title').textContent = title;
    const actions = element('div', null, 'v1-core-quick');
    const dismiss = element('button', okLabel ? '取消' : '知道了', 'secondary');
    dismiss.type = 'button';
    dismiss.addEventListener('click', () => byId('v1-core-confirm').close());
    actions.append(dismiss);
    if (okLabel) {
      const ok = element('button', okLabel, 'primary');
      ok.type = 'button';
      ok.addEventListener('click', () => {
        byId('v1-core-confirm').close();
        onOk();
      });
      actions.append(ok);
    }
    byId('v1-core-confirm-body').replaceChildren(content instanceof Node ? content : element('p', content), actions);
    const dialog = byId('v1-core-confirm');
    if (!dialog.open) dialog.showModal();
    else if (!dialog.contains(document.activeElement)) dismiss.focus();
  }
  window.hopworldOpenConfirm = openConfirm;
  function askRestart(core) {
    if (!core.managed) {
      openConfirm('无法重启 ' + core.name, explain(core));
      return;
    }
    if (!core.installed) {
      openConfirm('还不能重启 ' + core.name, '本机还没有这个程序。可以先安装，再启动。', '去安装', () => askUpdate(core));
      return;
    }
    if (core.status !== 'running') {
      openConfirm(core.name + ' 没有在运行', '请先启动。没有运行的内核不能重启。');
      return;
    }
    openConfirm('重启 ' + core.name, '将重新启动这个内核。正在使用它的连接会短暂中断。', '重启', () => { void restartCore(core); });
  }
  async function restartCore(core) {
    note('正在重启 ' + core.name + '…');
    try {
      const result = await call('/api/v1/cores/restart', {id: core.id});
      if (result.system) renderSystem(result.system);
      note(core.name + ' 已重启');
    } catch (error) {
      note(error.message, {error: true});
    }
  }
  async function askLogs(core) {
    if (!core.managed) {
      openConfirm('没有 ' + core.name + ' 的日志', explain(core));
      return;
    }
    note('正在读取 ' + core.name + ' 的日志…');
    try {
      const result = await call('/api/v1/cores/logs', {id: core.id});
      clearNote();
      openLog(core.name + ' 的日志', result.text || '还没有输出。这个内核下次启动后，新的日志会出现在这里。');
    } catch (error) {
      note(error.message, {error: true});
    }
  }
  function openLog(title, text) {
    byId('v1-core-confirm-title').textContent = title;
    const pre = element('pre', text, 'v1-core-log');
    const actions = element('div', null, 'v1-core-quick');
    const dismiss = element('button', '知道了', 'secondary');
    dismiss.type = 'button';
    dismiss.addEventListener('click', () => byId('v1-core-confirm').close());
    actions.append(dismiss);
    byId('v1-core-confirm-body').replaceChildren(pre, actions);
    const dialog = byId('v1-core-confirm');
    if (!dialog.open) dialog.showModal();
  }
  function askStart(core) {
    if (!core.installed) {
      openConfirm('启动 ' + core.name, '请先安装此内核。', '去安装', () => { void askUpdate(core); });
      return;
    }
    void openCoreProfile(core);
  }
  function askStop(core, instance = '') {
    openConfirm('停止 ' + core.name, '会中断该内核的连接、移除它的订阅条目并关闭其自动恢复。', '停止', async () => {
      note('正在停止 ' + core.name + '…');
      try {
        const result = await call('/api/v1/cores/stop', {id: core.id, instance});
        if (result.system) renderSystem(result.system);
        await refresh().catch(() => {});
        if (instance && byId('v1-core-settings').open) openCoreSettings(system?.cores.find(item => item.id === core.id));
        note(core.name + ' 已停止');
      } catch (error) {
        note(error.message, {error: true});
      }
    });
  }

  const publishCoreDialog = byId('v1-publish-core-dialog');
  const publishCoreList = byId('v1-publish-core-list');
  const publishCoreStatus = byId('v1-publish-core-status');
  let publishCoreRequest = 0;

  byId('v1-publish-core-manage').addEventListener('click', () => {
    publishCoreDialog.close();
    location.hash = 'cores';
  });

  async function openPublishedCorePicker() {
    if (publishCoreDialog.open) return;
    const request = ++publishCoreRequest;
    publishCoreList.hidden = true;
    publishCoreList.replaceChildren();
    publishCoreStatus.textContent = '正在读取已安装的内核…';
    publishCoreDialog.showModal();
    const current = () => publishCoreDialog.open && request === publishCoreRequest;
    try {
      const {cores} = await call('/api/v1/system');
      if (!current()) return;
      const installed = cores.filter(core => core.installed);
      publishCoreStatus.textContent = installed.length
        ? '选择已安装的内核，配置节点地址和出口。'
        : '当前没有已安装的内核，可前往「内核管理」安装。';
      publishCoreList.replaceChildren(...installed.map(core => {
        const row = element('li', null, 'v1-core-row');
        const name = element('span', core.name);
        const detail = [core.version, statuses[core.status]].filter(Boolean).join(' · ');
        if (detail) name.append(element('small', detail));
        const button = element('button', '配置', 'secondary');
        button.type = 'button';
        button.setAttribute('aria-label', '配置 ' + core.name);
        button.addEventListener('click', () => {
          publishCoreDialog.close();
          void openCoreProfile(core);
        });
        row.append(coreMark(core.id), name, button);
        return row;
      }));
      publishCoreList.hidden = !installed.length;
    } catch (error) {
      if (current()) publishCoreStatus.textContent = error.message || '无法读取内核列表，请关闭后重试。';
    }
  }

  const profileDialog = byId('v1-core-profile');
  const profileForm = byId('v1-core-profile-form');
  const profileBody = profileForm.querySelector('.v1-core-profile-body');
  const profileStatus = byId('v1-core-profile-status');
  const profileUpstream = profileForm.elements.upstream_id;
  const profileMode = profileForm.elements.egress;
  const profileSubmit = byId('v1-core-profile-submit');
  const addressResult = byId('v1-core-address-result');
  const addressCheck = byId('v1-core-address-check');
  let profileCore;
  let profileCertificate;
  let profileRequest = 0;
  let profileIpCheck = 0;
  let profileDomains = [];
  let profilePublisher;
  let profileInstance = '';
  const addressFamilies = {ipv4: 'IPv4', ipv6: 'IPv6', dual: 'IPv4 + IPv6'};
  const coreProtocolDialog = byId('v1-core-protocol-dialog');
  const coreSwitchGuide = {
    vless: {name: 'VLESS Reality', cores: ['xray-core'], uses: ['Router 的 VLESS Reality 发布由 Xray-core 提供。']},
    hysteria2: {name: 'Hysteria 2', cores: ['sing-box', 'hysteria2'],
      uses: ['同时发布 Hysteria 2 和 TUIC 时优先，支持固定 SOCKS5 上游。', '只发布 Hysteria 2 或需要公网 IP 证书时优先，也支持固定 SOCKS5 上游。']},
    tuic: {name: 'TUIC v5', cores: ['sing-box', 'tuic-server'],
      uses: ['需要固定 SOCKS5 上游，或同时发布 Hysteria 2 时优先。', '只发布 TUIC 且由本机直连时优先。']},
    ss: {name: 'Shadowsocks', cores: ['mihomo', 'shadowsocks-rust'],
      uses: ['需要固定 SOCKS5 上游时优先。', '只发布 Shadowsocks 且由本机直连时优先。']},
  };
  function canSwitchPublisher(entry) {
    const cores = coreSwitchGuide[entry?.protocol]?.cores;
    return cores?.length > 1 && cores.includes(entry.source);
  }
  const switchDialog = byId('v1-core-switch');
  const switchForm = byId('v1-core-switch-form');
  const switchStatus = byId('v1-core-switch-status');
  const switchSubmit = byId('v1-core-switch-submit');
  const switchClose = byId('v1-core-switch-close');
  const switchManage = byId('v1-core-switch-manage');
  const switchHint = byId('v1-core-switch-hint');
  let switchRequest = 0;
  let switchPublisher;

  async function openPublisherSwitch(entry) {
    if (!canSwitchPublisher(entry)) return;
    const request = ++switchRequest;
    switchPublisher = null;
    const {target, egress} = switchForm.elements;
    target.replaceChildren(new Option('正在读取…', ''));
    egress.replaceChildren(new Option('请选择出口方式', ''));
    switchSubmit.disabled = true;
    switchManage.hidden = true;
    switchHint.hidden = true;
    switchStatus.textContent = '';
    byId('v1-core-switch-node').textContent =
      `${protocolName(entry.protocol)} · 端口 ${entry.port} · 当前内核 ${coreCatalog[entry.source].name}`;
    if (!switchDialog.open) switchDialog.showModal();
    try {
      const view = await call('/api/v1/cores/switch?' + new URLSearchParams(entry));
      if (request !== switchRequest || !switchDialog.open) return;
      target.replaceChildren(new Option(coreCatalog[view.target].name, view.target));
      if (view.keep_egress) egress.add(new Option('沿用当前出口', 'keep'));
      egress.add(new Option('由本机直连', 'direct'));
      egress.value = view.keep_egress ? 'keep' : '';
      switchStatus.textContent = view.reason || (view.keep_egress ? '' : '请明确选择新内核的出口。');
      switchManage.hidden = !view.manage_target;
      if (!view.reason) {
        switchHint.hidden = false;
        switchHint.textContent = '切换期间连接会短暂中断。新内核将接管原端口和认证凭据。' +
          (view.replace_saved ? '目标内核已停止的配置会被本次切换替换。' : '');
        switchPublisher = {...entry, target: view.target};
        switchSubmit.disabled = false;
      }
    } catch (error) {
      if (request === switchRequest && switchDialog.open) switchStatus.textContent = error.message;
    }
  }

  switchClose.addEventListener('click', () => switchDialog.close());
  function closePublisherSwitch() {
    switchDialog.close();
    coreProtocolDialog.close();
    byId('v1-core-settings').close();
    profileDialog.close();
  }
  switchManage.addEventListener('click', closePublisherSwitch);
  switchDialog.addEventListener('cancel', event => { if (switchClose.disabled) event.preventDefault(); });
  switchForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (!switchPublisher || switchSubmit.disabled) return;
    const data = {...switchPublisher, egress: switchForm.elements.egress.value};
    switchSubmit.disabled = switchClose.disabled = true;
    switchStatus.textContent = '正在切换内核…';
    try {
      const result = await call('/api/v1/cores/switch', data);
      if (result.system) renderSystem(result.system);
      closePublisherSwitch();
      await refresh().catch(() => {});
      note(`${protocolName(data.protocol)} 已切换到 ${coreCatalog[data.target].name}`);
    } catch (error) {
      switchStatus.textContent = error.message;
    } finally {
      switchSubmit.disabled = switchClose.disabled = false;
    }
  });
  byId('v1-core-profile-switch-open').addEventListener('click', () => openPublisherSwitch(profilePublisher));

  function syncProfileSubmit() {
    const fields = profileForm.elements;
    profileSubmit.disabled = !profileCore || (fields.address_mode.value === 'domain' && !fields.bound_domain.value);
  }

  function syncProfileEgress() {
    const fixed = profileMode.value === 'fixed';
    byId('v1-core-profile-upstream').hidden = !fixed;
    profileUpstream.required = fixed;
    byId('v1-core-profile-direct').hidden = profileMode.value !== 'direct';
  }

  async function showProfileIpReadiness(family, enabled) {
    const check = ++profileIpCheck;
    const request = profileRequest;
    const status = byId('v1-core-ip-tls-status');
    status.hidden = !enabled;
    if (!enabled) return;
    status.textContent = '正在检测签发环境…';
    try {
      const result = await call('/api/acme/ip?family=' + encodeURIComponent(family));
      if (check !== profileIpCheck || request !== profileRequest || !profileDialog.open) return;
      status.textContent = ipCertificateCheckText(result);
      const email = profileForm.elements.ip_certificate_email;
      if (!email.value && result.certificate?.email) email.value = result.certificate.email;
    } catch (error) {
      if (check === profileIpCheck && request === profileRequest) status.textContent = error.message;
    }
  }

  function syncProfileAddress() {
    const fields = profileForm.elements;
    const mode = fields.address_mode.value;
    const domain = mode === 'domain';
    const hysteriaAutoIP = mode === 'auto_ip' && profileCore?.id === 'hysteria2';
    const publicIPCertificate = hysteriaAutoIP && fields.tls_mode.value === 'ip_acme';
    byId('v1-core-ip-tls').hidden = !hysteriaAutoIP;
    byId('v1-core-ip-tls-email').hidden = !publicIPCertificate;
    fields.ip_certificate_email.disabled = !publicIPCertificate;
    fields.ip_certificate_email.required = publicIPCertificate;
    if (profileCertificate?.required) {
      const localHysteriaCertificate = hysteriaAutoIP && !profileCertificate.ready;
      const expires = fields.address_family.value === profileCertificate.family && profileCertificate.expires;
      byId('v1-core-profile-cert-note').textContent = publicIPCertificate
        ? (expires > Date.now() / 1000 ? `公网 IP 证书有效至 ${formatWhen(expires)}。Router 会自动续期。` :
          '签发需从外网访问所选公网 IPv4 或 IPv6 的 TCP 80 端口。证书有效期约 6 天，Router 会自动续期。')
        : mode === 'auto_ip'
        ? (profileCertificate.ready ? `连接地址为公网 IP。TLS 校验使用证书名称 ${profileCertificate.domain}。` :
          localHysteriaCertificate ? 'Router 会自动生成 TLS 证书。请使用支持证书指纹校验的 Hysteria 2 客户端。' :
            '连接地址为公网 IP。此模式仍需 TLS 证书。')
        : domain ? 'TLS 证书须与所选域名匹配。' : 'TLS 证书须与所填 IP 匹配。';
      byId('v1-core-profile-cert-link').hidden = mode === 'auto_ip' && (publicIPCertificate || profileCertificate.ready || localHysteriaCertificate);
    }
    fields.address_family.options[2].disabled = !domain;
    if (!domain && fields.address_family.value === 'dual') fields.address_family.value = 'ipv4';
    byId('v1-core-address-host').hidden = mode !== 'fixed_ip';
    fields.public_host.required = mode === 'fixed_ip';
    byId('v1-core-bound-domain').hidden = !domain;
    fields.bound_domain.required = domain;
    fields.address_family.disabled = domain;
    if (domain) {
      const selected = profileDomains.find(item => item.host === fields.bound_domain.value);
      fields.address_family.value = selected?.family || 'ipv4';
    }
    byId('v1-core-address-ddns').hidden = !domain;
    addressCheck.hidden = mode !== 'auto_ip';
    addressCheck.textContent = '检查公网 IP';
    byId('v1-core-address-hint').textContent = domain
      ? (profileDomains.length ? '地址族沿用域名绑定设置。' : '暂无已绑定域名，请先完成绑定。')
      : mode === 'auto_ip' ? 'Router 会定期检测公网 IP。地址变化后客户端需刷新订阅。'
      : '现有手动 IP 可继续使用，也可改为自动获取。';
    void showProfileIpReadiness(fields.address_family.value, publicIPCertificate);
  }

  async function openCoreProfile(core, publisher, instance = '') {
    const request = ++profileRequest;
    profileCore = null;
    profileCertificate = null;
    profileDomains = [];
    profilePublisher = publisher;
    profileInstance = instance;
    byId('v1-core-profile-switch').hidden = !canSwitchPublisher(publisher);
    profileForm.reset();
    profileBody.inert = true;
    byId('v1-core-profile-title').textContent = core.name + ' 独立配置';
    profileSubmit.disabled = true;
    profileSubmit.textContent = '保存并启动';
    byId('v1-core-profile-legacy').hidden = true;
    profileStatus.textContent = '正在读取独立配置…';
    if (!profileDialog.open) profileDialog.showModal();
    try {
      const saved = await call('/api/v1/cores/profile?' + new URLSearchParams({id: core.id, instance}));
      if (!overview) await refresh();
      if (!profileDialog.open || request !== profileRequest) return;
      profileCore = core;
      const fields = profileForm.elements;
      profileCertificate = {required: saved.certificate_required, domain: saved.certificate_domain,
        ready: saved.certificate_ready, family: saved.address_family, expires: saved.ip_certificate_expires};
      fields.address_mode.options[2].hidden = saved.address_mode !== 'fixed_ip';
      fields.address_mode.value = saved.address_mode;
      fields.address_family.value = saved.address_family;
      fields.tls_mode.value = saved.tls_mode || 'automatic';
      fields.ip_certificate_email.value = saved.ip_certificate_email || '';
      fields.ddns.checked = saved.ddns;
      fields.public_host.value = saved.address_mode === 'fixed_ip' ? saved.public_host : '';
      profileDomains = saved.bound_domains || [];
      fields.bound_domain.replaceChildren(new Option('请选择已绑定域名', ''),
        ...profileDomains.map(item => new Option(`${item.host} · ${addressFamilies[item.family]}`, item.host)));
      fields.bound_domain.value = profileDomains.some(item => item.host === saved.public_host) ? saved.public_host : '';
      addressResult.textContent = [
        saved.detected_host && `当前订阅地址 ${saved.detected_host}`,
        saved.ddns_ipv4 && `上次更新 A ${saved.ddns_ipv4}`,
        saved.ddns_ipv6 && `上次更新 AAAA ${saved.ddns_ipv6}`,
        saved.address_error,
      ].filter(Boolean).join(' · ');
      syncProfileAddress();
      profileForm.elements.port.value = saved.port;
      profileForm.elements.public_listener.checked = saved.public_listener;
      profileMode.replaceChildren(new Option('请选择出口方式', ''), new Option('由本机直连', 'direct'));
      if (saved.fixed_upstream) {
        profileMode.add(new Option(saved.fixed_upstream === 'socks' ? '绑定 SOCKS5 上游' : '绑定指定上游', 'fixed'));
      }
      profileMode.value = saved.egress || '';
      byId('v1-core-profile-security').textContent = saved.security?.dns === 'ACTIVE_UNVERIFIED'
        ? '已应用加密 DNS，出口防泄漏待验证。'
        : '固定上游由内核配置约束。严格出口尚未开放，客户端 DNS 和 WebRTC 需要单独实测。';
      profileUpstream.replaceChildren(new Option('请选择已启用的上游', ''));
      const compatible = saved.fixed_upstream === 'socks' ? ['socks', 'socks5'] :
        ['socks', 'socks5', 'http', 'https', 'vless', 'vmess', 'trojan', 'ss', 'shadowsocks'];
      for (const node of overview?.nodes || []) {
        if (!node.local && node.enabled && compatible.includes(node.protocol)) {
          profileUpstream.add(new Option(node.title + ' · ' + node.protocol.toUpperCase(), node.id));
        }
      }
      profileUpstream.value = saved.upstream_id || '';
      byId('v1-core-profile-certificate').hidden = !saved.certificate_required;
      const portHint = byId('v1-core-profile-port-hint');
      portHint.hidden = core.id !== 'sing-box' || saved.quic_protocols?.length === 1;
      portHint.textContent = saved.tuic_port
        ? `当前 TUIC 端口为 ${saved.tuic_port}。修改 Hysteria 2 端口后，TUIC 使用相邻端口。`
        : '此端口用于 Hysteria 2，TUIC 使用相邻端口。';
      const legacyHint = byId('v1-core-profile-legacy');
      legacyHint.hidden = !saved.legacy;
      legacyHint.textContent = core.id === 'sing-box' && saved.quic_protocols?.length !== 1
        ? '保存后，Hysteria 2 和 TUIC 将一起切换到独立 sing-box，使用这里选择的出口。'
        : '保存后将切换到独立发布，切换期间连接会短暂中断。';
      if (saved.legacy) profileSubmit.textContent = '切换并启动';
      profileStatus.textContent = '';
      profileBody.inert = false;
      syncProfileEgress();
      syncProfileSubmit();
    } catch (error) {
      if (profileDialog.open && request === profileRequest) {
        profileStatus.textContent = '';
        openConfirm('无法读取独立配置', error.message);
      }
    }
  }

  profileMode.addEventListener('change', syncProfileEgress);
  profileForm.elements.tls_mode.addEventListener('change', syncProfileAddress);
  for (const name of ['address_mode', 'address_family', 'ddns', 'bound_domain']) {
    profileForm.elements[name].addEventListener('change', () => {
      addressResult.textContent = '';
      syncProfileAddress();
      syncProfileSubmit();
    });
  }
  addressCheck.addEventListener('click', async () => {
    const fields = profileForm.elements;
    const family = fields.address_family.value;
    const request = profileRequest;
    const current = () => request === profileRequest && fields.address_mode.value === 'auto_ip' &&
      fields.address_family.value === family;
    addressCheck.disabled = true;
    addressResult.textContent = '正在检查地址…';
    try {
      const result = await call('/api/v1/cores/address-check', {mode: 'auto_ip', family});
      if (current()) addressResult.textContent = `检测到公网地址 ${result.address} · 入站待验证`;
    } catch (error) {
      if (current()) addressResult.textContent = error.message;
    } finally {
      addressCheck.disabled = false;
    }
  });
  byId('v1-core-profile-close').addEventListener('click', () => profileDialog.close());
  byId('v1-core-profile-cert-link').addEventListener('click', () => profileDialog.close());
  const domainDialog = byId('v1-bind-domain');
  const domainForm = byId('v1-bind-domain-form');
  const domainStatus = byId('v1-bind-domain-status');
  const recordList = byId('v1-bind-records');
  const recordStatus = byId('v1-bind-records-status');
  let recordRequest = 0;
  let recordTimer;
  function refreshDomainRecords(delay = 0) {
    clearTimeout(recordTimer);
    const request = ++recordRequest;
    const data = Object.fromEntries(new FormData(domainForm));
    data.host = data.host.trim();
    byId('v1-bind-vps-link').hidden = data.ingress !== 'vps';
    byId('v1-bind-direct-hint').hidden = data.ingress !== 'direct';
    recordList.replaceChildren();
    recordStatus.textContent = '正在获取 DNS 记录值…';
    recordTimer = setTimeout(async () => {
      try {
        const {target, records} = await call('/api/v1/domains/records', data);
        if (request !== recordRequest || !domainDialog.open) return;
        recordStatus.textContent = data.ingress === 'direct' ? '探测到的本机公网出口地址' : `记录指向${target}`;
        recordList.replaceChildren(...records.map(record => {
          const row = element('div', null, 'v1-bind-record');
          row.append(element('strong', record.type),
            element('span', record.name ? `记录名 ${record.name}` : '填写域名后显示记录名'));
          if (record.name) {
            row.append(element('p', `当前 DNS 解析 ${record.current_values?.join('、') || '未查到'}`, 'field-hint'));
          }
          for (const value of record.values) {
            const field = element('div', null, 'v1-bind-record-value');
            const copy = element('button', '复制应填写的值', 'text-button');
            copy.type = 'button';
            copy.setAttribute('aria-label', `复制应填写的 ${record.type} 记录值 ${value}`);
            copy.addEventListener('click', () => copyText(value, '已复制 DNS 记录值'));
            field.append(element('span', '应填写的记录值'), element('code', value), copy);
            row.append(field);
          }
          if (!record.values.length) row.append(element('p', `暂未获取 ${record.type} 记录值`, 'field-hint'));
          return row;
        }));
      } catch (error) {
        if (request === recordRequest && domainDialog.open) recordStatus.textContent = error.message;
      }
    }, delay);
  }
  const renderBoundDomains = domains => {
    byId('v1-bound-domain-list').replaceChildren(...(domains.length
      ? domains.map(item => element('li', `${item.host} · ${addressFamilies[item.family]}`))
      : [element('li', '暂无已绑定域名')]));
  };
  byId('v1-bind-domain-close').addEventListener('click', () => domainDialog.close());
  domainDialog.addEventListener('close', () => {
    clearTimeout(recordTimer);
    ++recordRequest;
  });
  domainForm.elements.host.addEventListener('input', () => refreshDomainRecords(350));
  domainForm.elements.family.addEventListener('change', () => refreshDomainRecords());
  domainForm.elements.ingress.addEventListener('change', () => refreshDomainRecords());
  byId('v1-bind-vps-link').addEventListener('click', () => {
    domainDialog.close();
    openRelayPorts();
  });
  byId('v1-bind-records-refresh').addEventListener('click', () => refreshDomainRecords());
  byId('v1-core-bind-link').addEventListener('click', () => profileDialog.close());
  byId('v1-bind-domain-open').addEventListener('click', async () => {
    domainStatus.textContent = '';
    domainDialog.showModal();
    refreshDomainRecords();
    try { renderBoundDomains((await call('/api/v1/domains')).domains); }
    catch (error) { domainStatus.textContent = error.message; }
  });
  domainForm.addEventListener('submit', async event => {
    event.preventDefault();
    const button = event.submitter;
    button.disabled = true;
    domainStatus.textContent = '正在核对 DNS 指向…';
    try {
      const {domains} = await call('/api/v1/domains', Object.fromEntries(new FormData(domainForm)));
      renderBoundDomains(domains);
      domainStatus.textContent = '域名已绑定，可在独立配置中选择。';
      domainForm.reset();
      refreshDomainRecords();
    } catch (error) {
      domainStatus.textContent = error.message;
      domainStatus.focus();
      refreshDomainRecords();
    } finally {
      button.disabled = false;
    }
  });
  const importCertDialog = byId('v1-import-cert');
  const importCertForm = byId('v1-import-cert-form');
  const importCertStatus = byId('v1-import-cert-status');
  const importCertButton = importCertForm.querySelector('button[type="submit"]');
  const openImportCert = byId('v1-import-cert-open');
  const closeImportCert = byId('v1-import-cert-close');
  let importingCert = false;
  closeImportCert.addEventListener('click', () => importCertDialog.close());
  importCertDialog.addEventListener('cancel', event => {
    if (importingCert) event.preventDefault();
  });
  importCertDialog.addEventListener('close', () => {
    if (!importCertDialog.open) importCertForm.reset();
  });
  openImportCert.addEventListener('click', async () => {
    importCertForm.reset();
    const domain = importCertForm.elements.domain;
    domain.replaceChildren(new Option('请选择已绑定域名', ''));
    importCertStatus.textContent = '正在读取已绑定域名…';
    importCertButton.disabled = true;
    importCertDialog.showModal();
    openImportCert.disabled = true;
    try {
      const {domains} = await call('/api/v1/domains');
      if (!importCertDialog.open) return;
      for (const entry of domains) domain.add(new Option(entry.host, entry.host));
      if (domains.length === 1) domain.value = domains[0].host;
      importCertStatus.textContent = domains.length ? '' : '请先绑定域名，再导入证书。';
      importCertButton.disabled = !domains.length;
    } catch (error) { importCertStatus.textContent = error.message; }
    finally { openImportCert.disabled = false; }
  });
  importCertForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (importCertButton.disabled) return;
    const {domain, certificate, private_key} = importCertForm.elements;
    const chain = certificate.files[0];
    const key = private_key.files[0];
    if (!chain || !key || chain.size > 128 * 1024 || key.size > 32 * 1024) {
      importCertStatus.textContent = '请上传不超过 128 KB 的证书链和不超过 32 KB 的私钥。';
      return;
    }
    importingCert = importCertButton.disabled = closeImportCert.disabled = true;
    importCertStatus.textContent = '正在校验证书与私钥…';
    try {
      await call('/api/acme/import', {domain: domain.value, certificate: await chain.text(), private_key: await key.text()});
      importCertDialog.close();
      note('证书已导入');
      await refresh();
    } catch (error) {
      if (importCertDialog.open) importCertStatus.textContent = error.message;
      else note(error.message, {error: true});
    } finally {
      importingCert = importCertButton.disabled = closeImportCert.disabled = false;
    }
  });
  profileForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (!profileCore || profileSubmit.disabled) return;
    const {id, name} = profileCore;
    const fields = profileForm.elements;
    if (fields.address_mode.value === 'auto_ip' && profileCertificate.required && !profileCertificate.ready && id !== 'hysteria2') {
      openConfirm('需要有效的 TLS 证书',
        '订阅会使用检测到的公网 IP 连接。当前自动公网 IP 模式使用域名证书校验 TLS，请先配置有效证书。',
        '配置证书', () => {
          profileDialog.close();
          location.hash = '#certificates';
        });
      return;
    }
    const request = profileRequest;
    const profile = {
      public_host: fields.address_mode.value === 'domain' ? fields.bound_domain.value : fields.public_host.value.trim(),
      port: Number(fields.port.value),
      address_mode: fields.address_mode.value, address_family: fields.address_family.value,
      ddns: fields.address_mode.value === 'domain' && fields.ddns.checked,
      public_listener: fields.public_listener.checked, egress: profileMode.value,
      upstream_id: profileMode.value === 'fixed' ? profileUpstream.value : '',
    };
    if (id === 'hysteria2' && profile.address_mode === 'auto_ip' && fields.tls_mode.value === 'ip_acme') {
      profile.tls_mode = 'ip_acme';
      profile.ip_certificate_email = fields.ip_certificate_email.value.trim();
    }
    profileSubmit.disabled = true;
    profileStatus.textContent = profile.tls_mode === 'ip_acme' ? '正在检查或申请公网 IP 证书…' : '正在配置并启动内核…';
    try {
      const result = await call('/api/v1/cores/start', {id, profile, instance: profileInstance});
      if (result.system) renderSystem(result.system);
      if (request === profileRequest) profileDialog.close();
      await refresh().catch(() => {});
      note(name + ' 已独立启动');
    } catch (error) {
      if (profileDialog.open && request === profileRequest) openConfirm('无法启动 ' + name, error.message);
      else note(error.message, {error: true});
    } finally {
      if (request === profileRequest) {
        profileStatus.textContent = '';
        syncProfileSubmit();
      }
    }
  });

  function coreNeedsDownload(core) {
    return !core.installed || (!core.version && core.status !== 'running');
  }
  function askDownload(core) {
    openConfirm('下载 ' + core.name, '将下载官方安装包，校验后安装到 Router 数据目录。', '下载', () => { void installCore(core.id); });
  }
  async function askUpdate(core) {
    if (coreNeedsDownload(core)) {
      askDownload(core);
      return;
    }
    note('正在检查 ' + core.name + ' 的新版本…');
    try {
      const result = await call('/api/v1/cores/check', {id: core.id});
      if (!result.update && result.client_missing) {
        clearNote();
        openConfirm('安装连通性测试客户端', `将为 ${core.name} 安装同项目的测试客户端。`, '安装', () => { void installCore(core.id); });
        return;
      }
      if (!result.update) {
        note(core.name + ' 已是最新版本 ' + result.latest);
        return;
      }
      clearNote();
      openConfirm(
        '更新 ' + core.name,
        `发现新版本 ${result.latest}，当前版本 ${result.current || '未读取'}。将下载并替换数据目录里的这一份。系统里的同名程序不会改动。`,
        '更新',
        () => { void installCore(core.id); },
      );
    } catch (error) {
      note(error.message, {error: true});
    }
  }
  let installJob = null;
  function downloadNote(text, paused) {
    note(text, {duration: 0, controls: true});
    const pause = byId('toast-pause');
    if (!pause) return;
    const html = iconSvg(paused ? 'play' : 'pause', 16);
    if (html) pause.innerHTML = html;
    else pause.replaceChildren();
    pause.setAttribute('aria-label', paused ? '继续下载' : '暂停下载');
    pause.setAttribute('aria-pressed', String(paused));
  }
  function pausedLabel(job) {
    return installProgress(job.name, {stage: 'download', received: job.received || 0, total: job.total || 0}).replace('正在下载', '已暂停');
  }
  async function stopInstall() {
    const job = installJob;
    if (!job) { clearNote(); return; }
    job.stopping = true;
    try {
      await fetch('/api/v1/cores/cancel', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: job.id})});
    } catch (error) {
      note(error.message, {error: true});
      return;
    }
    if (job.paused) {
      installJob = null;
      note('已停止下载');
    }
  }
  async function toggleInstallPause() {
    const job = installJob;
    if (!job) return;
    if (job.paused) { void installCore(job.id); return; }
    try {
      await fetch('/api/v1/cores/pause', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: job.id})});
    } catch (error) {
      note(error.message, {error: true});
    }
  }
  async function installCore(id) {
    if (!id) return;
    if (coreInstallBusy) { note('正在安装，请稍候'); return; }
    coreInstallBusy = true;
    const core = (system?.cores || []).find(item => item.id === id) || {name: coreCatalog[id]?.name || '内核'};
    installJob = {id, name: core.name, paused: false, stopping: false, received: installJob?.id === id ? installJob.received : 0, total: installJob?.id === id ? installJob.total : 0};
    note('正在准备安装 ' + core.name + '…');
    try {
      const response = await fetch('/api/v1/cores/install', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id}),
      });
      const result = await readInstallEvents(response, core.name);
      installJob = null;
      if (result.uptodate) {
        if (result.system) renderSystem(result.system);
        note(core.name + ' 已是最新版本 ' + (result.version || ''));
        return;
      }
      if (result.system) renderSystem(result.system);
      else await loadSystem(true);
      note(core.name + (result.installed?.client_only ? ' 的测试客户端已安装' : ' 已安装到本机数据目录'));
    } catch (error) {
      if (error.paused && installJob) {
        installJob.paused = true;
        installJob.received = error.received;
        installJob.total = error.total;
        downloadNote(pausedLabel(installJob), true);
        return;
      }
      if (error.cancelled || installJob?.stopping) {
        installJob = null;
        note('已停止下载');
        return;
      }
      installJob = null;
      note(error.message, {error: true});
    } finally {
      coreInstallBusy = false;
    }
  }
  function runCoreAct(action, id) {
    const coreId = id || selectedCoreId;
    const core = (system?.cores || []).find(item => item.id === coreId);
    if (action === 'select' && core) { selectCore(core.id, true); return; }
    if (action === 'wizard') { openWizard(coreId); return; }
    if (action === 'download') { if (core) askDownload(core); return; }
    if (action === 'install') { if (core) askUpdate(core); return; }
    if (action === 'start') { if (core) askStart(core); return; }
    if (action === 'stop') { if (core) askStop(core); return; }
    if (action === 'profile') {
      if (coreCatalog[coreId]) void openCoreProfile(core || {id: coreId, ...coreCatalog[coreId]});
      return;
    }
    if (action === 'restart') { if (core) askRestart(core); return; }
    if (action === 'logs') { if (core) void askLogs(core); return; }
    if (action === 'autostart') {
      if (core) {
        if (autostart(core).on) askStop(core);
        else if (core.status !== 'running') askStart(core);
      }
      return;
    }
    if (action === 'uninstall') { void uninstallCore(coreId); return; }
    if (!core) return;
    if (action === 'explain') note(explain(core));
    if (action === 'edit') openCoreSettings(core);
    if (action === 'copy-config') void copyText(core.config_path, core.config_ready ? '已复制配置文件路径' : '已复制配置路径，文件尚未生成');
    if (action === 'export' || action === 'backup') void saveCoreConfiguration(core, action);
    if (action === 'copy-bin') void copyText(core.path, '已复制程序路径');
  }
  async function saveCoreConfiguration(core, action) {
    try {
      if (action === 'backup') {
        const result = await call('/api/v1/cores/backup', {id: core.id});
        note(`已备份到数据目录的 core-config-backups/${result.filename}`);
      } else {
        const response = await fetch('/api/v1/cores/config?id=' + encodeURIComponent(core.id));
        if (!response.ok) throw new Error((await response.json()).error || '配置导出失败');
        saveBlob(await response.blob(), core.id + '.json');
      }
    } catch (error) {
      note(error.message, {error: true});
    }
  }
  function runBatch(action) {
    if (action === 'refresh') { void loadSystem(true); return; }
    if (action === 'clear') { coreChecks.clear(); renderCores(); return; }
    const picked = (system?.cores || []).filter(core => coreChecks.has(core.id) && core.path);
    if (!picked.length) { note('先勾选已经安装的内核'); return; }
    void copyText(picked.map(core => `${core.name} ${core.path}`).join('\n'), `已复制 ${picked.length} 个程序路径`);
  }
  function renderCores() {
    const cores = system?.cores || [];
    const installed = cores.filter(core => core.installed).length;
    const navCount = byId('v1-core-nav-count');
    navCount.hidden = !system;
    if (system) {
      navCount.textContent = String(installed);
      navCount.setAttribute('aria-label', `已安装 ${installed} 个`);
    }
    const trafficValue = traffic ? bytes(traffic.today?.total_bytes) : null;
    metrics('v1-core-metrics', [
      ['已安装内核', system ? `${installed} / ${cores.length}` : null, 'cpu'],
      ['运行中', system ? cores.filter(core => core.status === 'running').length : null, 'play', 'cyan'],
      ['已停止', system ? cores.filter(core => core.status === 'stopped').length : null, 'pause', 'violet'],
      ['总节点数', overview ? (overview.nodes || []).length : null, 'network'],
    ]);
    metrics('v1-core-traffic', [['总流量（今日）', trafficValue, 'arrow-left-right', 'cyan']]);
    const counts = {
      all: cores.length,
      running: cores.filter(core => core.status === 'running').length,
      stopped: cores.filter(core => core.status === 'stopped').length,
      not_installed: cores.filter(core => core.status === 'not_installed').length,
    };
    const filterLabels = {all: '全部', running: '运行中', stopped: '已停止', not_installed: '未安装'};
    for (const button of document.querySelectorAll('[data-core-filter]')) {
      button.textContent = `${filterLabels[button.dataset.coreFilter]} (${counts[button.dataset.coreFilter] || 0})`;
      button.setAttribute('aria-pressed', String(button.dataset.coreFilter === coreFilter));
    }
    const installMenu = byId('v1-core-install-menu');
    if (installMenu) installMenu.replaceChildren(...installMenuItems());
    if (!system) {
      const waiting = element('tr');
      const cell = element('td', '正在读取本机内核…');
      cell.colSpan = 9;
      waiting.append(cell);
      byId('v1-core-rows').replaceChildren(waiting);
      renderDetail(null);
      syncCheckAll();
      return;
    }
    if (!cores.some(core => core.id === selectedCoreId)) selectedCoreId = (cores.find(core => core.status === 'running') || cores[0])?.id || null;
    const visible = cores.filter(core => coreFilter === 'all' || core.status === coreFilter);
    byId('v1-core-rows').replaceChildren(...(visible.length ? visible.map(core => {
      const row = element('tr');
      row.dataset.corePick = core.id;
      row.classList.toggle('is-selected', core.id === selectedCoreId);
      const checkCell = element('td');
      const label = element('label', null, 'v1-check');
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = coreChecks.has(core.id);
      input.dataset.coreCheck = core.id;
      input.setAttribute('aria-label', '选择 ' + core.name);
      label.append(input);
      checkCell.append(label);
      const nameCell = element('th');
      nameCell.scope = 'row';
      const nameBox = element('div', null, 'v1-core-name');
      const mark = coreMark(core.id);
      const pick = element('button', null, 'v1-core-pick');
      pick.type = 'button';
      pick.dataset.coreAct = 'select';
      pick.dataset.coreId = core.id;
      pick.setAttribute('aria-pressed', String(core.id === selectedCoreId));
      pick.append(element('strong', core.name), element('small', core.protocols?.join(' · ') || dash));
      nameBox.append(mark, pick);
      nameCell.append(nameBox);
      const state = element('td');
      state.append(badge(core));
      const count = publishedCount(core);
      const ports = element('div', null, 'v1-port-line');
      ports.append(element('span', 'TCP: ' + (core.tcp_ports?.length ? core.tcp_ports.join(', ') : dash)), element('small', 'UDP: ' + (core.udp_ports?.length ? core.udp_ports.join(', ') : dash)));
      const start = autostart(core);
      const auto = element('div', null, 'v1-autostart');
      const knob = element('button', null, 'v1-switch');
      knob.type = 'button';
      knob.dataset.coreAct = 'autostart';
      knob.dataset.coreId = core.id;
      knob.dataset.on = String(start.on);
      knob.setAttribute('aria-pressed', String(start.on));
      knob.setAttribute('aria-label', (start.on ? '关闭 ' : '开启 ') + core.name + ' 的自动启动');
      auto.append(knob, element('small', start.text));
      const ops = element('td');
      const opsBox = element('div', null, 'v1-core-ops');
      const canStop = core.status === 'running';
      const settingsButton = iconButton('settings', '设置 ' + core.name, 'edit', core.id);
      const packageButton = coreNeedsDownload(core)
        ? iconButton('download', '下载 ' + core.name, 'download', core.id)
        : iconButton('refresh-cw', '更新 ' + core.name, 'install', core.id);
      opsBox.append(iconButton(canStop ? 'square' : 'play', `${canStop ? '停止' : '启动'} ${core.name}`, canStop ? 'stop' : 'start', core.id),
        packageButton, settingsButton);
      ops.append(opsBox);
      const trafficCell = element('td', bytes(traffic?.by_core?.[core.id]?.total_bytes));
      trafficCell.title = '独立发布实例的已采集流量';
      row.append(checkCell, nameCell, element('td', core.version || dash), state, element('td', count == null ? dash : String(count)), trafficCell, element('td', null), element('td', null), ops);
      row.children[6].append(ports);
      row.children[7].append(auto);
      return row;
    }) : [emptyCoreRow('没有符合当前筛选的内核')]));
    syncCheckAll();
    renderDetail(cores.find(core => core.id === selectedCoreId));
  }
  function emptyCoreRow(text) {
    const row = element('tr');
    const cell = element('td', text);
    cell.colSpan = 9;
    row.append(cell);
    return row;
  }
  const certificateDNS = new Map();
  let certificatePanel = 'basic';
  let certificateFilter = 'all';
  let certificateDomain = '';
  let selectedCertificate = '';
  let visibleCertificates = [];
  let certificateBusy = '';
  const certBatch = byId('v1-cert-batch');
  const certActions = ['renew', 'verify', 'download'];

  function syncCertificateActions() {
    const acme = overview?.acme || {};
    const selected = !!selectedCertificate;
    const hasCertificate = certificateDomain === acme.domain && acme.download_available;
    const available = {renew: acme.automatic && hasCertificate, verify: !!certificateDomain, download: hasCertificate};
    byId('v1-cert-selection').textContent = `已选 ${Number(selected)} 个域名`;
    byId('v1-cert-count').textContent = `共 ${visibleCertificates.length} 条记录${selected ? ' · 已选 1 项' : ''}`;
    for (const action of certActions) {
      byId(`v1-cert-${action}`).disabled = !!certificateBusy || !available[action];
      certBatch.querySelector(`[data-cert-batch="${action}"]`).disabled = !!certificateBusy || !selected || !available[action];
    }
  }

  function renderCertificates() {
    if (!overview) return;
    const now = Date.now() / 1000;
    const ipRecords = Object.entries(overview.ip_certificates || {}).map(([family, cert]) => {
      const bound = overview.public_subscription;
      return [family === 'ipv6' ? '公网 IPv6' : '公网 IPv4', [
        cert.address, cert.challenge === 'tls-alpn' ? 'TLS-ALPN-01' : 'HTTP-01',
        cert.expires > now ? `有效至 ${formatWhen(cert.expires)}` : '已到期',
        cert.expires > now && cert.expires - now <= 48 * 3600 && '不足 48 小时，请检查续签状态',
        cert.enabled ? '自动续签' : '自动续签已关闭',
        cert.last_check && `最近检查 ${formatWhen(cert.last_check)}`,
        cert.retry_at > now && `下次重试 ${formatWhen(cert.retry_at)}`,
        !!bound?.base && bound.domain === cert.address && '已绑定公网订阅',
        cert.error,
      ].filter(Boolean).join(' · ')];
    });
    byId('v1-ip-certificates').hidden = !ipRecords.length;
    definitions('v1-ip-certificate-records', ipRecords);
    const local = overview.local_node || {};
    const acme = overview.acme || {};
    const rows = new Map();
    for (const binding of overview.bound_domains || []) {
      const families = rows.get(binding.host) || new Set();
      for (const record of binding.family === 'dual' ? ['A', 'AAAA'] : [binding.family === 'ipv6' ? 'AAAA' : 'A']) families.add(record);
      rows.set(binding.host, families);
    }
    if (acme.domain && !rows.has(acme.domain)) rows.set(acme.domain, new Set());
    const activeDomain = acme.domain || '';
    const matches = (host, filter) => filter === 'all' || host === activeDomain && (
      filter === 'valid' && acme.certificate_valid || filter === 'expiring' && acme.certificate_valid && acme.days_left <= 14 ||
      filter === 'renew' && acme.automatic);
    const query = byId('v1-cert-search').value.trim().toLowerCase();
    visibleCertificates = [...rows.keys()].filter(host => host.toLowerCase().includes(query) && matches(host, certificateFilter));
    if (!visibleCertificates.includes(selectedCertificate)) selectedCertificate = '';
    const domain = selectedCertificate || (rows.has(activeDomain) ? activeDomain : rows.keys().next().value) || '';
    const hasDomain = !!domain;
    const dns = certificateDNS.get(domain);
    certificateDomain = hasDomain ? domain : '';
    const domainKey = value => (value || '').toLowerCase().replace(/\.$/, '');
    const subscription = overview.public_subscription;
    const servicesFor = host => {
      const services = new Set(publicPortRows.filter(row => host && domainKey(row.address) === domainKey(host))
        .map(row => `${row.core} / ${protocolName(row.nodeProtocol)}`));
      if (host && subscription?.base && domainKey(subscription.domain) === domainKey(host)) services.add('公网 HTTPS 订阅');
      return [...services].join('、') || '无关联服务';
    };
    const linkedServices = servicesFor(domain);
    const matched = hasDomain && acme.domain?.toLowerCase() === domain.toLowerCase();
    const valid = matched && acme.certificate_valid;
    const expiring = valid && acme.days_left <= 14;
    const renew = matched && acme.automatic;
    const certState = valid ? (acme.error ? '有效 · 续签异常' : '有效') : matched && acme.state === 'expired' ? '已到期' :
      matched && acme.error ? '签发异常' : matched && acme.enabled ? '待签发' : '未签发';
    const manual = acme.source === 'manual';
    const certService = !matched ? dash : manual ? '手动导入' : 'Let’s Encrypt';
    const method = !matched || manual ? dash : acme.challenge_effective || ({dns:'DNS-01', http:'HTTP-01', 'tls-alpn':'TLS-ALPN-01', auto:'自动'}[acme.challenge]) || dash;
    const remaining = matched && Number.isFinite(acme.days_left) ? `${acme.days_left} 天` : dash;
    const recordTypes = host => [...(rows.get(host) || [])].sort().join(' / ') || dash;
    const records = recordTypes(domain);
    const dnsLabel = result => ({ok: '已验证', mismatch: '记录不匹配', error: '查询失败'}[result?.status] || '待验证');
    const resolved = dnsLabel(dns);
    metrics('v1-cert-metrics', [
      ['已绑定域名', new Set((overview.bound_domains || []).map(binding => binding.host)).size, 'circle-dot'],
      ['有效证书', Number(!!acme.certificate_valid), 'shield', 'cyan'],
      ['即将到期', Number(!!acme.certificate_valid && acme.days_left <= 14), 'triangle-alert', 'violet'],
      ['自动续签', Number(!!acme.automatic), 'refresh-cw', 'violet'],
      ['DNS 解析', resolved === '已验证' ? records : dash, 'blocks'],
    ]);
    byId('v1-cert-domain').textContent = hasDomain ? domain : '域名详情';
    const status = byId('v1-cert-badge');
    status.textContent = hasDomain ? certState : '未配置';
    status.dataset.state = expiring ? 'stopped' : valid ? 'running' : '';
    const detailPanels = {
      basic: [['证书来源', certService], ['验证方式', method], ['证书状态', certState],
        ['剩余有效期', remaining], ['自动续签', renew ? '已开启' : '未开启'],
        ['公网域名', hasDomain ? domain : dash], ['域名关联服务', linkedServices], ['DNS 解析', hasDomain ? resolved : dash]],
      dns: [['DNS 解析', hasDomain ? resolved : dash], ['记录类型', records],
        ['IPv4', dns?.resolved_ipv4?.join('、') || dash], ['IPv6', dns?.resolved_ipv6?.join('、') || dash]],
      acme: [['证书来源', certService], ['验证方式', method], ['申请状态', certState],
        ['续签阈值', renew && Number.isFinite(acme.renew_days) ? `剩余 ${acme.renew_days} 天时` : dash]],
      certificate: [['证书域名', matched ? acme.domain : dash], ['证书状态', certState], ['剩余有效期', remaining],
        ['自动续签', renew ? '已开启' : '未开启']],
    };
    definitions('v1-cert-details', detailPanels[certificatePanel]);
    definitions('v1-cert-integrations', [['证书来源', certService], ['验证方式', method],
      ['固定域名自动 DNS', local.ddns_enabled ? '已开启' : '未开启']]);
    const history = matched ? acme.history || [] : [];
    const diagnostic = byId('v1-cert-diagnostic');
    diagnostic.textContent = certificateBusy || (matched && acme.error) || (!history.length && dns?.detail) ||
      (hasDomain ? '暂无操作日志' : '请先配置公网域名。');
    diagnostic.hidden = !certificateBusy && !(matched && acme.error) && !!history.length;
    byId('v1-cert-history').replaceChildren(...history.map(entry => {
      const item = element('li');
      const timestamp = new Date(entry.timestamp * 1000);
      const when = element('time', timestamp.toLocaleString());
      when.dateTime = timestamp.toISOString();
      item.append(when, element('span', `${entry.action} · ${entry.detail}`));
      return item;
    }));
    const filterLabels = {all: '全部域名', valid: '有效证书', expiring: '即将到期', renew: '自动续签'};
    for (const item of document.querySelectorAll('[data-cert-filter]')) {
      const key = item.dataset.certFilter;
      item.textContent = `${filterLabels[key]} (${[...rows.keys()].filter(host => matches(host, key)).length})`;
      item.setAttribute('aria-pressed', String(key === certificateFilter));
    }
    const tableRows = visibleCertificates.map(host => {
      const row = element('tr');
      row.classList.toggle('v1-cert-current', selectedCertificate === host);
      const selectCell = element('td');
      const checkbox = element('input');
      checkbox.type = 'checkbox';
      checkbox.id = 'v1-cert-select-' + host;
      checkbox.checked = selectedCertificate === host;
      checkbox.setAttribute('aria-label', `选择 ${host}`);
      checkbox.addEventListener('change', () => {
        selectedCertificate = checkbox.checked ? host : '';
        renderCertificates();
        byId(checkbox.id)?.focus();
      });
      selectCell.append(checkbox);
      const domainCell = element('th', host);
      domainCell.scope = 'row';
      const hasCert = host === activeDomain;
      const lease = hasCert && Number.isFinite(acme.days_left) ? `${acme.days_left} 天` : dash;
      const currentMethod = hasCert && !manual ? acme.challenge_effective || dash : dash;
      const state = !hasCert ? '未签发' : acme.certificate_valid ? (acme.error ? '有效 · 续签异常' : '有效') :
        acme.state === 'expired' ? '已到期' : acme.error ? '签发异常' : acme.enabled ? '待签发' : '未启用';
      const values = [recordTypes(host), dnsLabel(certificateDNS.get(host)), currentMethod, state, lease, servicesFor(host),
        hasCert && acme.automatic ? '已开启' : '未开启'];
      row.append(selectCell, domainCell, ...values.map((value, index) => {
        const cell = element('td');
        if ([0, 1, 2, 3, 5, 6].includes(index)) {
          const tag = element('span', value, 'v1-status');
          if ((index === 1 && certificateDNS.get(host)?.status === 'ok') ||
              (index === 3 && hasCert && acme.certificate_valid) || (index === 6 && hasCert && acme.automatic)) tag.dataset.state = 'running';
          if (index === 3 && hasCert && acme.certificate_valid && acme.days_left <= 14) tag.dataset.state = 'stopped';
          cell.append(tag);
        } else cell.textContent = value;
        return cell;
      }));
      return row;
    });
    if (!tableRows.length) {
      const row = element('tr');
      const empty = element('td', hasDomain ? '没有匹配的域名' : '尚未配置公网域名');
      empty.className = 'v1-cert-empty';
      empty.colSpan = 9;
      row.append(empty);
      tableRows.push(row);
    }
    byId('v1-cert-rows').replaceChildren(...tableRows);
    syncCertificateActions();
  }
  for (const button of document.querySelectorAll('[data-cert-panel]')) {
    button.addEventListener('click', () => {
      certificatePanel = button.dataset.certPanel;
      for (const item of document.querySelectorAll('[data-cert-panel]')) item.setAttribute('aria-pressed', String(item === button));
      renderCertificates();
    });
  }
  for (const button of document.querySelectorAll('[data-cert-filter]')) {
    button.addEventListener('click', () => {
      certificateFilter = button.dataset.certFilter;
      renderCertificates();
    });
  }
  byId('v1-cert-show-diagnostics').addEventListener('click', () => {
    byId('v1-cert-diagnostics').focus({preventScroll: true});
    byId('v1-cert-diagnostics').scrollIntoView({block: 'nearest'});
  });
  byId('v1-cert-search').addEventListener('input', () => renderCertificates());
  async function runCertificateAction(action, domain) {
    if (!domain || certificateBusy) return;
    if (action === 'renew' && !window.confirm(`立即续签 ${domain} 的证书？`)) return;
    certificateBusy = {renew: '正在续签证书…', verify: '正在检查 DNS…', download: '正在下载证书…'}[action];
    renderCertificates();
    try {
      if (action === 'download') {
        const response = await fetch(`/api/acme/download?domain=${encodeURIComponent(domain)}`);
        if (!response.ok) throw new Error((await response.json()).error || '证书下载失败');
        saveBlob(await response.blob(), `${domain}.pem`);
      } else if (action === 'renew') {
        await call('/api/acme/renew', {domain});
        await refresh();
        note('证书续签成功');
      } else {
        const result = await call('/api/acme/dns', {domain});
        overview.acme = result.acme;
        certificateDNS.set(domain, result.dns);
        note(result.dns.detail, {error: result.dns.status !== 'ok'});
      }
    } catch (error) {
      if (action === 'renew') await refresh().catch(() => {});
      note(error.message, {error: true});
    } finally {
      certificateBusy = '';
      renderCertificates();
    }
  }
  for (const action of certActions) {
    byId(`v1-cert-${action}`).addEventListener('click', () => runCertificateAction(action, certificateDomain));
    certBatch.querySelector(`[data-cert-batch="${action}"]`).addEventListener('click', () => {
      certBatch.open = false;
      certBatch.querySelector('summary').focus();
      void runCertificateAction(action, selectedCertificate);
    });
  }
  document.addEventListener('click', event => {
    if (certBatch.open && !certBatch.contains(event.target)) certBatch.open = false;
  });
  certBatch.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !certBatch.open) return;
    certBatch.open = false;
    certBatch.querySelector('summary').focus();
  });

  let publicPortRows = [];
  const publicPortSelection = new Set();
  const publicSelectAll = byId('v1-public-check-all');
  const publicPortsBody = byId('v1-public-rows');
  const publicCheckNote = byId('v1-public-check-note');
  const relayForm = byId('v1-relay-form');
  const relaySave = byId('v1-relay-save');
  const relayFeedback = byId('v1-relay-feedback');
  const relayCores = byId('v1-relay-cores');
  const relayPortFields = byId('v1-relay-port-fields');
  const relayPortDraft = new Map();
  const portDialog = byId('v1-port-dialog');
  const portTarget = byId('v1-port-target');
  const relayPortKey = (coreId, port) => `${coreId}:${port}`;
  let relay;
  let relayBusy = false;
  let relayLoading = false;
  let relayCandidatesKey = '';
  let relayEditing = false;

  function relaySummary(status) {
    const configured = Boolean(status && status.configured);
    return {
      configured,
      domain: ((status && status.domain) || '').trim() || '未配置',
      server: ((status && status.server) || '').trim() || '未配置',
      port: status && status.port ? String(status.port) : '未配置',
      token: configured ? '已配置' : '未配置',
      ca: configured ? '已配置' : '未配置',
      cores: Array.isArray(status && status.cores) ? status.cores.slice() : [],
    };
  }

  function paintRelayView(status) {
    const statusNode = byId('v1-relay-view-status');
    const feedback = byId('v1-relay-view-feedback');
    const fields = byId('v1-relay-view-fields');
    const edit = byId('v1-relay-edit');
    const setup = byId('v1-relay-setup');
    if (!statusNode || !fields || !edit || !setup) return;
    if (!status) {
      statusNode.textContent = '正在读取 VPS 配置…';
      if (feedback) {
        feedback.hidden = true;
        feedback.textContent = '';
      }
      fields.hidden = true;
      fields.replaceChildren();
      edit.hidden = true;
      setup.hidden = true;
      return;
    }
    const view = relaySummary(status);
    if (view.configured) {
      const registered = (status.mappings || []).filter(item => item.registered).length;
      statusNode.textContent = !status.enabled
        ? status.mappings.length ? '隧道已停止。' : 'VPS 入口已登记，等待配置映射。'
        : status.error || (status.running
          ? `${registered} / ${status.mappings.length} 个端口已注册 · 公网待异地验证`
          : '等待本机监听器或 frpc 进程恢复。');
      definitions('v1-relay-view-fields', [
        ['节点统一域名', view.domain],
        ['VPS 控制地址', view.server],
        ['VPS 控制端口', view.port],
        ['FRP 令牌', view.token],
        ['VPS CA 证书', view.ca],
        ['要发布的内核', view.cores.length ? view.cores.map(id => coreName(id)).join('、') : '未选择'],
      ]);
      fields.hidden = false;
      edit.hidden = false;
      setup.hidden = true;
    } else {
      statusNode.textContent = '尚未配置 VPS 隧道。可先登记 VPS 入口；待域名指向 VPS、独立节点启动后，再勾选要发布的内核并保存。';
      fields.hidden = true;
      fields.replaceChildren();
      edit.hidden = true;
      setup.hidden = false;
    }
  }

  function setRelayEditing(editing, options = {}) {
    const {resetForm = false, focus = false, notice = ''} = options;
    const view = byId('v1-relay-view');
    const viewFeedback = byId('v1-relay-view-feedback');
    const leaving = relayEditing && !editing;
    relayEditing = Boolean(editing);
    if (relayForm) relayForm.hidden = !relayEditing;
    if (view) view.hidden = relayEditing;
    if (leaving && relay) renderRelay(relay, true);
    else if (relayEditing && resetForm && relay) renderRelay(relay, true);
    else if (!relayEditing) paintRelayView(relay);
    if (leaving && relayFeedback) relayFeedback.textContent = '';
    if (relayEditing) {
      if (viewFeedback) {
        viewFeedback.hidden = true;
        viewFeedback.textContent = '';
      }
      if (focus && relayForm) relayForm.elements.domain.focus();
    } else if (viewFeedback) {
      viewFeedback.hidden = !notice;
      viewFeedback.textContent = notice;
    }
  }

  let directAddresses;
  let directProbeCommand = '';
  const subscriptionLabelsDialog = byId('public-subscription-labels');
  const subscriptionLabelsForm = byId('public-subscription-labels-form');
  const subscriptionLabelsSave = byId('public-subscription-labels-save');
  const subscriptionLabelsClose = byId('public-subscription-labels-close');
  const subscriptionLabelsFeedback = byId('public-subscription-labels-feedback');
  byId('public-subscription-labels-open').addEventListener('click', () => {
    const labels = overview?.public_subscription?.topbar_labels || subscriptionLabelDefaults;
    for (const field of subscriptionLabelsForm.querySelectorAll('input')) field.value = labels[field.name] ?? '';
    subscriptionLabelsFeedback.hidden = true;
    subscriptionLabelsDialog.showModal();
  });
  subscriptionLabelsClose.addEventListener('click', () => subscriptionLabelsDialog.close());
  subscriptionLabelsDialog.addEventListener('cancel', event => {
    if (subscriptionLabelsSave.disabled) event.preventDefault();
  });
  subscriptionLabelsForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (subscriptionLabelsSave.disabled) return;
    subscriptionLabelsSave.disabled = subscriptionLabelsClose.disabled = true;
    subscriptionLabelsFeedback.hidden = true;
    try {
      const publication = await call('/api/v1/public-subscription/topbar', Object.fromEntries(new FormData(subscriptionLabelsForm)));
      if (overview) overview.public_subscription = publication;
      renderSubscriptionIndicator(publication);
      subscriptionLabelsDialog.close();
      note('状态文字已保存');
    } catch (error) {
      subscriptionLabelsFeedback.textContent = error.message;
      subscriptionLabelsFeedback.hidden = false;
    } finally {
      subscriptionLabelsSave.disabled = subscriptionLabelsClose.disabled = false;
    }
  });
  function renderPublicAccess(local, hints = local.dns_hints, dns = local.dns_verify) {
    const host = local.public_host || '';
    const relayOn = relay?.enabled;
    const relayRegistered = relayOn ? relay.mappings.filter(mapping => mapping.registered).length : 0;
    const relayState = relayOn ? `${relayRegistered} / ${relay.mappings.length} 个端口已注册` :
      relay?.configured ? '已停止' : '未配置';
    const quic = local.quic || {};
    const native = local.native_hysteria || {};
    const independent = local.independent_endpoints || [];
    const running = local.running || quic.available || native.running || independent.length > 0;
    const state = running ? '本机运行中' : local.enabled ? '本机未运行' : '未启用';
    const mode = {fixed_domain: '固定域名', ddns: '固定域名 / DDNS', fixed_ip: '固定 IP', auto_ip: '自动出口 IP'}[hints?.mode];
    const dnsState = relayOn ? '请核对 A / AAAA 指向 VPS' : {ok: '解析与预期地址匹配', mismatch: '解析与预期地址不匹配', error: '解析查询失败'}[dns?.status]
      || dns?.detail || '待检测';
    const egress = !local.enabled && independent.length
      ? {tcp: '按独立内核配置', udp: '按独立内核配置', detail: '各内核使用所选的出口。'}
      : local.egress_protocol_info || {};
    publicPortRows = local.enabled ? [
      {name: 'VLESS / Reality', protocol: 'TCP', nodeProtocol: 'vless', port: local.port, core_id: 'xray-core', running: !!local.running},
      {name: 'Hysteria 2', protocol: 'UDP', nodeProtocol: 'hysteria2', port: quic.enabled ? quic.hy2_port : null, core_id: 'sing-box', running: !!quic.available},
      {name: 'TUIC', protocol: 'UDP', nodeProtocol: 'tuic', port: quic.enabled ? quic.tuic_port : null, core_id: 'sing-box', running: !!quic.available},
      {name: '原生 Hysteria 2', protocol: 'UDP', nodeProtocol: 'hysteria2', port: native.port, core_id: 'hysteria2', running: !!native.running},
    ].filter(row => Number.isInteger(row.port) && row.port > 0).map(row => ({
      ...row, core: coreCatalog[row.core_id].name, legacy: true, egress: local.egress_mode,
      bind: ['ipv6', 'dual', 'ipv4_first'].includes(local.public_host_prefer) ? '::' : '0.0.0.0',
      address: host, entrance: endpoint(host, row.port),
    })) : [];
    for (const item of independent) {
      const core = coreName(item.core_id);
      const transports = item.protocol === 'ss' ? ['TCP', 'UDP'] : [item.protocol === 'vless' ? 'TCP' : 'UDP'];
      for (const transport of transports) {
        const mapping = relay?.mappings.find(row => row.core_id === item.core_id && row.port === item.port &&
          row.transport.toUpperCase() === transport && relay.domain === item.public_host && item.bind === '127.0.0.1');
        const publicPort = mapping?.public_port ?? item.public_port ?? item.port;
        publicPortRows.push({name: `${core} · ${protocolName(item.protocol)} ${transport}`, protocol: transport,
          nodeProtocol: item.protocol, port: item.port, publicPort, core, core_id: item.core_id, running: true, bind: item.bind,
          profile_id: item.profile_id,
          egress: item.egress,
          relay: mapping ? (mapping.registered ? 'VPS 已注册' : relayOn ? 'VPS 未注册' : '隧道已停止') : '',
          address: item.public_host, entrance: endpoint(item.public_host, publicPort)});
      }
    }
    const publication = overview?.public_subscription;
    const directHost = (publication?.base ? publication.domain : '') ||
      publicPortRows.find(row => row.running && !row.relay && row.address)?.address || host;
    const entryHost = relayOn ? relay.domain : directHost;
    const entryAddresses = [...new Set(publicPortRows.map(row => row.address).filter(Boolean))];
    const portsFor = transport => [...new Set(publicPortRows.filter(row => row.protocol === transport)
      .map(row => row.publicPort ?? row.port))].join(' / ') || dash;
    definitions('v1-overview-access', [
      ['公网入口', entryAddresses.join('、') || directHost, {id: 'overview-access-entry', label: '公网入口'}],
      ['本地节点', state], ['VPS 隧道', relayState],
      ['TCP 端口', portsFor('TCP')], ['UDP 端口', portsFor('UDP')],
    ]);
    directProbeCommand = /^[a-z0-9:.\-]+$/i.test(directHost)
      ? `python3 -m router.direct_diagnostics ${directHost}${publication?.base ? ' --https-port ' + publication.port : ''}`
      : '';
    byId('v1-public-probe-command').disabled = !directProbeCommand;
    metrics('v1-public-metrics', [
      ['接入状态', relayOn ? relayState : state, 'unplug', running ? 'cyan' : 'blue'],
      ['接入模式', relayOn ? 'VPS 中转' : entryHost ? '本机入口' : '未配置', 'layout-grid'],
      ['隧道类型', relay?.configured ? 'FRP · TCP / UDP' : '未配置', 'arrow-left-right', 'violet'],
      ['通道延迟（VPS）', dash, 'timer', 'cyan'],
      ['中转流量（今日）', dash, 'activity', 'violet'],
    ]);
    byId('v1-public-entry').textContent = entryHost || '未配置';
    byId('v1-public-path').textContent = relayOn ? 'VPS → FRP → 本机' : entryHost ? '直接入口 · 待外网验证' : '待配置';
    byId('v1-public-host-state').textContent = state;
    definitions('v1-public-details', [
      ['入口地址', entryHost], ['地址模式', relayOn ? 'VPS 域名' : mode], ['隧道状态', relayState],
      ['本地服务', state], ['TCP 出口', relayOn ? '按独立节点设置' : egress.tcp],
      ['UDP 出口', relayOn ? '按独立节点设置' : egress.udp],
    ]);
    definitions('v1-public-network', [
      ...['ipv4', 'ipv6'].flatMap((family, index) => {
        const result = directAddresses?.[family];
        const label = index === 0 ? 'IPv4' : 'IPv6';
        const kind = family === 'ipv4' && result?.kind === '本机公网地址' ? '' : result?.kind;
        return [
          [`公网出口 ${label}`, result?.public || hints?.['egress_' + family] || '未获取'],
          [`本机路由 ${label}`, result ? [result.local, kind].filter(Boolean).join(' · ') : '待检测'],
          ...(result?.last_change ? [[`${label} 最近记录变更`, formatWhen(result.last_change)]] : []),
        ];
      }),
      ['公网入站', '待异地验证'],
    ]);
    const steps = [
      ['域名解析', dnsState, !relayOn && dns?.status === 'ok'],
      ['接入链路', relayOn ? `${relayState} · 公网待验证` : entryHost ? '已配置入口 · 公网待验证' : '等待配置公网地址', false],
      ...['TCP', 'UDP'].map(protocol => {
        const ports = publicPortRows.filter(row => row.protocol === protocol);
        const active = ports.filter(row => row.running).length;
        return [`${protocol} 端口`, `配置 ${ports.length} 个 · 本机运行 ${active} 个`, active > 0];
      }),
    ];
    byId('v1-public-steps').replaceChildren(...steps.map(([label, detail, ok]) => {
      const item = element('li');
      const icon = element('span', null, ok ? 'running' : '');
      icon.setAttribute('aria-hidden', 'true');
      setIcon(icon, ok ? 'check' : 'circle', 16);
      item.append(icon, element('span', `${label} · ${detail}`));
      return item;
    }));
    definitions('v1-public-diagnosis', relayOn ? [
      ['DNS', dnsState], ['FRP', relay.error || relayState], ['代理握手和业务转发', '待异地验证'],
    ] : [
      ['DNS', dnsState], ['TCP / UDP 出口', egress.detail],
      ['UDP / QUIC 入站', publicPortRows.some(row => row.protocol === 'UDP' && row.running) ? '本机运行中' : quic.error || '未发布'],
    ]);
    byId('v1-public-disconnect').disabled = !running && !local.enabled;
    byId('v1-public-ports-label').textContent = String(publicPortRows.length);
    renderPublicPorts();
    renderPublicLogs();
  }

  function syncPublicSelection() {
    const checks = [...publicPortsBody.querySelectorAll('input[type="checkbox"]')];
    for (const box of checks) {
      box.checked = publicPortSelection.has(box.value);
      box.closest('tr').classList.toggle('v1-public-chosen', box.checked);
    }
    publicSelectAll.disabled = !checks.length;
    publicSelectAll.checked = checks.length > 0 && publicPortSelection.size === checks.length;
    publicSelectAll.indeterminate = publicPortSelection.size > 0 && !publicSelectAll.checked;
    byId('v1-public-copy-selected').disabled = !publicPortRows.some(row => row.address && publicPortSelection.has(row.name));
    byId('v1-public-export-selected').disabled = !publicPortSelection.size;
    byId('v1-public-export').disabled = !publicPortRows.length;
    byId('v1-public-port-note').textContent = publicPortRows.length
      ? `已选 ${publicPortSelection.size} 项 · 公网可达性待异地验证`
      : '启用本地发布节点后，这里会列出 TCP 和 UDP 端口。';
  }

  function renderPublicPorts() {
    for (const name of publicPortSelection) {
      if (!publicPortRows.some(row => row.name === name)) publicPortSelection.delete(name);
    }
    const rows = publicPortRows.map(port => {
      const row = element('tr');
      const check = element('input');
      check.type = 'checkbox';
      check.value = port.name;
      check.setAttribute('aria-label', `选择 ${port.name}`);
      const selectCell = element('td');
      selectCell.append(check);
      const name = element('th', port.name);
      name.scope = 'row';
      const protocol = element('td');
      const transport = element('span', port.protocol, 'v1-public-transport');
      transport.dataset.transport = port.protocol;
      protocol.append(transport);
      const status = element('td');
      const badge = element('span', port.relay || (port.running ? '本机运行中' : '未运行'), 'v1-status');
      badge.dataset.state = (port.relay ? port.relay === 'VPS 已注册' : port.running) ? 'running' : 'stopped';
      status.append(badge);
      const copy = element('button', '复制', 'text-button');
      copy.type = 'button';
      copy.disabled = !port.address;
      copy.setAttribute('aria-label', `复制 ${port.name} 入口`);
      copy.addEventListener('click', () => copyText(port.entrance, '已复制公网入口'));
      const actions = element('td', null, 'v1-public-row-actions');
      const settings = element('button', port.legacy ? '独立配置' : '配置', 'text-button');
      settings.type = 'button';
      const publisher = {source: port.core_id, port: port.port, protocol: port.nodeProtocol};
      if (port.relay) {
        settings.textContent = 'VPS 端口';
        settings.addEventListener('click', () => {
          openRelayPorts();
          const key = relayPortKey(port.core_id, port.port);
          const input = [...relayPortFields.querySelectorAll('input')].find(field => field.dataset.portKey === key);
          input?.focus();
        });
      } else {
        settings.addEventListener('click', () =>
          openCoreProfile({id: port.core_id, ...coreCatalog[port.core_id]}, publisher, port.profile_id));
      }
      settings.setAttribute('aria-label', `配置 ${port.name} 端口`);
      actions.append(settings, copy);
      if (canSwitchPublisher(publisher)) {
        const switchCore = element('button', '切换内核', 'text-button');
        switchCore.type = 'button';
        switchCore.setAttribute('aria-label', `切换内核 · ${port.name}`);
        switchCore.addEventListener('click', () => openPublisherSwitch(publisher));
        actions.append(switchCore);
      }
      const legacyTraffic = port.protocol === 'TCP' && port.legacy;
      const flow = element('td', bytes(legacyTraffic ? traffic?.legacy?.today?.total_bytes :
        port.legacy || (port.nodeProtocol === 'ss' && port.protocol === 'UDP') ? null : publisherBytes(port.profile_id || port.core_id, port.port)));
      if (port.nodeProtocol === 'ss' && !port.legacy) flow.title = '按发布端点合计，包含 TCP 和 UDP';
      row.append(selectCell, name, protocol, element('td', port.entrance),
        element('td', `${endpoint(port.bind || '0.0.0.0', port.port)}（${port.core}）`), status, flow, actions);
      return row;
    });
    if (!rows.length) {
      const row = element('tr');
      const empty = element('td', '暂无发布端口', 'v1-public-empty');
      empty.colSpan = 8;
      row.append(empty);
      rows.push(row);
    }
    publicPortsBody.replaceChildren(...rows);
    syncPublicSelection();
  }

  function showPublicTab(tab) {
    const current = document.querySelector('[data-public-tab][aria-pressed="true"]')?.dataset.publicTab;
    if (current === 'vps' && tab !== 'vps' && relayEditing) setRelayEditing(false);
    for (const button of document.querySelectorAll('[data-public-tab]')) {
      button.setAttribute('aria-pressed', String(button.dataset.publicTab === tab));
    }
    for (const panel of document.querySelectorAll('[data-public-panel]')) {
      panel.hidden = panel.dataset.publicPanel !== tab;
    }
    byId('v1-public-port-actions').hidden = tab !== 'ports';
  }

  function renderRelayPorts() {
    const domain = relayForm.elements.domain.value.trim().toLowerCase().replace(/\.$/, '');
    const selected = new Set(new FormData(relayForm).getAll('cores'));
    const entries = (relay?.candidates || []).filter(item => selected.has(item.core_id) && item.public_host === domain);
    const inputs = [...relayPortFields.querySelectorAll('input')];
    const keys = entries.map(({core_id, port}) => relayPortKey(core_id, port));
    byId('v1-relay-ports').hidden = !entries.length;
    if (inputs.length === keys.length && inputs.every((input, i) =>
      input.dataset.portKey === keys[i] && input.dataset.domain === domain)) return;
    relayPortFields.replaceChildren(...entries.map((item, index) => {
      const key = keys[index];
      const label = element('label', `${coreName(item.core_id)} · ${protocolName(item.protocol)}（本机 ${item.port}）`);
      const input = element('input');
      input.type = 'number';
      input.min = '1';
      input.max = '65535';
      input.required = true;
      input.dataset.portKey = key;
      input.dataset.domain = domain;
      const draftKey = `${domain}/${key}`;
      input.value = relayPortDraft.get(draftKey) ?? item.public_port ?? item.port;
      input.addEventListener('input', () => relayPortDraft.set(draftKey, input.value));
      label.append(input);
      return label;
    }));
  }

  function renderRelay(status, resetForm = false) {
    const initialize = !relay || resetForm;
    relay = status;
    if (!(relayEditing && !initialize)) {
      const {elements} = relayForm;
      const selected = new Set(initialize ? status.cores : new FormData(relayForm).getAll('cores'));
      if (initialize) {
        relayPortDraft.clear();
        relayPortFields.replaceChildren();
        for (const key of ['domain', 'server', 'port']) elements[key].value = status[key];
        for (const key of ['token', 'ca']) {
          elements[key].value = '';
          elements[key].required = !status.configured;
          elements[key].placeholder = status.configured ? '留空沿用已保存的值' : '';
        }
      }
      const choices = [...new Set([...status.candidates.map(item => item.core_id), ...selected])];
      const optionsKey = JSON.stringify([choices, status.candidates]);
      if (initialize || optionsKey !== relayCandidatesKey) {
        const options = choices.map(id => {
          const label = element('label');
          const input = element('input');
          input.type = 'checkbox';
          input.name = 'cores';
          input.value = id;
          input.checked = selected.has(id);
          const hosts = [...new Set(status.candidates.filter(item => item.core_id === id).map(item => item.public_host))];
          label.append(input, element('span', `${coreName(id)} · ${hosts.join('、') || '等待本机发布'}`));
          return label;
        });
        relayCores.replaceChildren(...(options.length ? options : [element('p', '请先启动至少一个仅监听本机的独立节点。', 'field-hint')]));
        relayCandidatesKey = optionsKey;
      }
      renderRelayPorts();
    }
    byId('v1-relay-install').textContent = status.installed ? '已检测到家庭端 frpc。VPS 端使用启用 TLS 的 frps。' :
      '请在家庭 Router 主机安装 frpc v0.72.0，或为服务设置 HOPWORLD_FRPC，再刷新接入状态。';
    const registered = status.mappings.filter(mapping => mapping.registered).length;
    const summary = !status.enabled ? (status.configured
      ? (status.mappings.length ? '隧道已停止。' : 'VPS 入口已登记，等待配置映射。') : '尚未配置 VPS 隧道。') :
      status.error || (status.running ? `${registered} / ${status.mappings.length} 个端口已注册 · 公网待异地验证` :
        '等待本机监听器或 frpc 进程恢复。');
    const summaryNode = byId('v1-relay-summary');
    if (summaryNode.textContent !== summary) summaryNode.textContent = summary;
    definitions('v1-relay-mappings', status.mappings.map(mapping => [
      `${coreName(mapping.core_id)} · ${mapping.transport.toUpperCase()} ${mapping.public_port} → ${mapping.port}`,
      !status.enabled ? '隧道已停止' : !mapping.local_ready ? '本机节点未就绪，端口或域名变更后需重新保存' :
        mapping.error || (mapping.registered ? 'VPS 已注册' : '等待 VPS 注册，详情见 frpc 日志'),
    ]));
    if (!relayEditing) paintRelayView(status);
    relaySave.disabled = relayBusy;
    byId('v1-relay-stop').disabled = relayBusy || !status.enabled;
    if (overview?.local_node) renderPublicAccess(overview.local_node);
  }

  async function refreshRelay(showError = false) {
    if (relayBusy || relayLoading) return;
    relayLoading = true;
    try {
      const status = await call('/api/v1/relay');
      if (!relayBusy) renderRelay(status);
    } catch (error) {
      if (showError || !relay) {
        byId('v1-relay-summary').textContent = error.message;
        const viewStatus = byId('v1-relay-view-status');
        if (viewStatus && !relayEditing) viewStatus.textContent = error.message;
      }
    } finally {
      relayLoading = false;
    }
  }

  async function changeRelay(data) {
    if (relayBusy) return;
    relayBusy = true;
    relaySave.disabled = byId('v1-relay-stop').disabled = true;
    relayFeedback.textContent = data.action ? '正在停止隧道…' : '正在校验并启动 frpc…';
    try {
      const status = await call('/api/v1/relay', data);
      renderRelay(status, true);
      await refresh().catch(() => {});
      const savedNote = data.action ? '' : status.mappings.length
        ? '已保存。请在「隧道配置」查看注册状态。' : 'VPS 入口已登记。请先验证域名解析，再启动独立节点。';
      if (!data.action && relayEditing) setRelayEditing(false, {notice: savedNote});
      else {
        relayFeedback.textContent = savedNote;
        if (data.action && relayEditing) setRelayEditing(false);
      }
      if (data.action) note('隧道已停止');
    } catch (error) {
      relayFeedback.textContent = error.message;
      if (data.action) note(error.message, {error: true});
      else relayFeedback.focus();
    } finally {
      relayBusy = false;
      if (relay) renderRelay(relay);
    }
  }

  relayForm.addEventListener('submit', event => {
    event.preventDefault();
    const data = new FormData(relayForm);
    const public_ports = Object.fromEntries([...relayPortFields.querySelectorAll('input')]
      .map(input => [input.dataset.portKey, Number(input.value)]));
    void changeRelay({...Object.fromEntries(data), port: Number(data.get('port')), cores: data.getAll('cores'), public_ports});
  });
  byId('v1-relay-edit').addEventListener('click', () => {
    setRelayEditing(true, {resetForm: true, focus: true});
  });
  byId('v1-relay-setup').addEventListener('click', () => {
    setRelayEditing(true, {resetForm: true, focus: true});
  });
  byId('v1-relay-cancel').addEventListener('click', () => {
    setRelayEditing(false);
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden && relayEditing) setRelayEditing(false);
  });
  relayCores.addEventListener('change', renderRelayPorts);
  relayForm.elements.domain.addEventListener('input', renderRelayPorts);
  function openRelayPorts() {
    showPublicTab('vps');
    void refreshRelay(true).then(() => {
      if (relay) setRelayEditing(true, {resetForm: true, focus: true});
    });
  }
  byId('v1-relay-configure').addEventListener('click', openRelayPorts);
  byId('v1-configure-port').addEventListener('click', () => {
    portTarget.replaceChildren(new Option('请选择', ''), ...knownCores().map(core => new Option(core.name, core.id)),
      new Option('VPS 公网端口（FRP）', 'relay'));
    portDialog.showModal();
  });
  portDialog.addEventListener('close', () => {
    if (portDialog.returnValue !== 'edit') return;
    portDialog.returnValue = '';
    const target = portTarget.value;
    if (target === 'relay') openRelayPorts();
    else {
      const core = knownCores().find(item => item.id === target);
      if (core) void openCoreProfile(core);
    }
  });
  byId('v1-relay-stop').addEventListener('click', () => openConfirm('停止 FRP 隧道',
    'VPS 上的映射会断开，已运行的本机节点继续监听。', '停止隧道', () => changeRelay({action: 'stop'})));
  byId('v1-relay-log').addEventListener('click', async () => {
    try {
      const {text} = await call('/api/v1/relay/logs');
      openLog('frpc 日志', text || '暂无 frpc 日志');
    } catch (error) { note(error.message, {error: true}); }
  });

  async function recheckPublicNetwork() {
    const button = byId('v1-public-recheck');
    if (button.disabled) return;
    button.disabled = true;
    publicCheckNote.textContent = '正在检测网络和 DNS…';
    try {
      await refresh();
      const local = overview?.local_node;
      if (!local) return;
      const [hints, dns, addresses] = await Promise.all([
        call('/api/local/dns-hints'),
        call('/api/local/dns-verify?force=1'),
        call('/api/v1/public-network'),
      ]);
      if (overview?.local_node?.public_host !== local.public_host) {
        publicCheckNote.textContent = '入口地址已变更，请重新检测。';
        return;
      }
      directAddresses = addresses;
      renderPublicAccess(overview.local_node, hints, dns);
      publicCheckNote.textContent = `更新于 ${new Date().toLocaleTimeString()} · 公网入站待验证`;
    } catch (error) {
      publicCheckNote.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  function exportPublicPorts(rows) {
    if (!rows.length) return;
    const ports = rows.map(({name, protocol, address, port, publicPort, core, relay}) =>
      ({name, protocol, address, port: publicPort ?? port, local_port: port, core, mode: relay ? 'frp' : 'direct'}));
    const mode = ports.every(port => port.mode === ports[0].mode) ? ports[0].mode : 'mixed';
    const body = JSON.stringify({mode, ports}, null, 2);
    saveBlob(new Blob([body + '\n'], {type: 'application/json'}), 'hopworld-access.json');
  }
  const pickedPublicPorts = () => publicPortRows.filter(row => publicPortSelection.has(row.name));

  publicPortsBody.addEventListener('change', event => {
    const check = event.target.closest('input[type="checkbox"]');
    if (!check) return;
    if (check.checked) publicPortSelection.add(check.value);
    else publicPortSelection.delete(check.value);
    syncPublicSelection();
  });
  publicSelectAll.addEventListener('change', () => {
    publicPortSelection.clear();
    if (publicSelectAll.checked) publicPortRows.forEach(row => publicPortSelection.add(row.name));
    syncPublicSelection();
  });
  for (const button of document.querySelectorAll('[data-public-tab]')) {
    button.addEventListener('click', () => {
      showPublicTab(button.dataset.publicTab);
      void refreshRelay(true);
    });
  }
  const publicLogActions = byId('v1-public-log-actions');
  function renderPublicLogs() {
    const ids = new Set(publicPortRows.map(row => row.core_id));
    const cores = (system?.cores || []).filter(core => core.managed && ids.has(core.id));
    const status = byId('v1-public-log-status');
    status.hidden = !!cores.length;
    status.textContent = system ? '暂无可查看日志的接入内核。' : '正在读取接入服务…';
    const existing = [...publicLogActions.querySelectorAll('[data-public-log]')];
    const labels = cores.map(core => `${core.name} 日志`);
    if (existing.length === cores.length && existing.every((button, index) =>
      button.dataset.publicLog === cores[index].id && button.textContent === labels[index])) return;
    existing.forEach(button => button.remove());
    publicLogActions.append(...cores.map((core, index) => {
      const button = element('button', labels[index], 'secondary');
      button.type = 'button';
      button.dataset.publicLog = core.id;
      return button;
    }));
  }
  publicLogActions.addEventListener('click', event => {
    const id = event.target.closest('[data-public-log]')?.dataset.publicLog;
    const core = system?.cores?.find(item => item.id === id);
    if (core) void askLogs(core);
  });
  byId('v1-public-recheck').addEventListener('click', recheckPublicNetwork);
  byId('v1-public-probe-command').addEventListener('click', () => {
    if (directProbeCommand) void copyText(directProbeCommand, '已复制命令，请在独立外网客户端运行');
  });
  byId('v1-public-refresh').addEventListener('click', () => refresh()
    .then(() => refreshRelay(true))
    .then(() => note('接入状态已刷新')).catch(error => note(error.message, {error: true})));
  byId('v1-public-copy-selected').addEventListener('click', () => {
    closeMenus();
    const rows = pickedPublicPorts().filter(row => row.address);
    if (rows.length) void copyText(rows.map(row => `${row.protocol} ${row.entrance}`).join('\n'), `已复制 ${rows.length} 个入口`);
  });
  byId('v1-public-export-selected').addEventListener('click', () => {
    closeMenus();
    exportPublicPorts(pickedPublicPorts());
  });
  byId('v1-public-export').addEventListener('click', () => exportPublicPorts(publicPortRows));
  byId('v1-public-guide').addEventListener('click', () => openLog('接入向导',
    '1. 填写 Router 可供客户端访问的域名或 IP，并启用本地发布节点。\n' +
    '2. 按端口列表放行 TCP 或 UDP。在家庭路由器后部署时，为相应协议配置端口转发。\n' +
    '3. 从蜂窝网络或异地网络使用真实客户端分别测试 Reality 和 UDP / QUIC。\n' +
    '4. CGNAT 环境使用「VPS 管理」。在自有 VPS 运行启用 TLS 的 frps，将域名指向 VPS，再选择同域名、仅监听本机的独立节点。'));
  byId('v1-public-details-open').addEventListener('click', () => {
    const details = byId('v1-public-details');
    details.focus({preventScroll: true});
    details.scrollIntoView({block: 'nearest'});
  });
  byId('v1-public-diagnostics-open').addEventListener('click', () => {
    showPublicTab('diagnostics');
    const tab = document.querySelector('[data-public-tab="diagnostics"]');
    tab.focus({preventScroll: true});
    tab.scrollIntoView({block: 'nearest'});
  });
  byId('v1-public-disconnect').addEventListener('click', () => openConfirm('断开本机接入',
    '将停用本机的独立和兼容发布节点，并断开相关连接。公网订阅服务和 IKEv2 继续运行。', '断开接入', async () => {
      try {
        await call('/api/v1/publishers/disconnect', {});
        await refresh();
        note('本机接入已断开');
      } catch (error) {
        note(error.message, {error: true});
      }
    }));

  const settingsForm = byId('v1-settings-form');
  const settingsFeedback = byId('v1-settings-feedback');
  const basicSettingsFeedback = byId('v1-settings-basic-feedback');
  const settingsRetry = byId('v1-settings-retry');
  const adminEdit = byId('v1-admin-edit');
  const adminDialog = byId('v1-admin-dialog');
  const adminForm = byId('v1-admin-form');
  const adminSave = byId('v1-admin-save');
  const adminClose = byId('v1-admin-close');
  const adminFeedback = byId('v1-admin-feedback');
  const settingsDefaults = {name: 'HopWorld-Router', timezone: 'browser', auto_check: true, auto_apply: true, probe_target: 'google'};
  let savedSettings;
  let savingSettings = false;
  let settingsClockZone;
  let consoleName = settingsDefaults.name;
  const managementPort = byId('v1-settings-port');
  const managementHttps = byId('v1-settings-https');
  const managementApply = byId('v1-management-apply');
  const managementFeedback = byId('v1-settings-access');
  const autostartSwitch = byId('v1-settings-autostart');
  const restartButton = byId('v1-settings-restart');
  const restartFeedback = byId('v1-settings-restart-status');
  const restartScreen = byId('v1-restart-screen');
  const restartTitle = byId('v1-restart-title');
  const restartProgress = byId('v1-restart-progress');
  const restartSpinner = byId('v1-restart-spinner');
  const restartDismiss = byId('v1-restart-dismiss');
  let managementAccess;
  let managementBusy = false;
  let managementPoll;
  let autostartState;
  let restartingRouter = false;

  adminEdit.addEventListener('click', () => {
    adminForm.reset();
    adminFeedback.textContent = '';
    adminForm.elements.username.value = savedSettings.admin_username;
    adminDialog.showModal();
  });
  adminClose.addEventListener('click', () => adminDialog.close());
  adminDialog.addEventListener('close', () => adminForm.reset());
  adminDialog.addEventListener('cancel', event => {
    if (adminSave.disabled) event.preventDefault();
  });
  adminForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (adminSave.disabled) return;
    const data = Object.fromEntries(new FormData(adminForm));
    if (data.new_password !== byId('v1-admin-repeat').value) {
      adminFeedback.textContent = '两次输入的新密码不一致。';
      byId('v1-admin-repeat').focus();
      return;
    }
    adminSave.disabled = adminClose.disabled = true;
    adminFeedback.textContent = '正在保存…';
    try {
      await call('/api/v1/management/account', data);
      adminForm.reset();
      location.replace('/login?changed=1');
    } catch (error) {
      adminFeedback.textContent = error.message;
      adminSave.disabled = adminClose.disabled = false;
    }
  });

  restartButton.addEventListener('click', () => openConfirm(
    '重启 Router', '节点和订阅下载可能短暂中断。后台恢复后需要重新登录。',
    '确认重启', restartRouter,
  ));
  restartScreen.addEventListener('cancel', event => {
    if (restartDismiss.hidden) event.preventDefault();
  });
  restartDismiss.addEventListener('click', () => restartScreen.close());

  async function restartRouter() {
    if (restartingRouter) return;
    restartingRouter = restartButton.disabled = true;
    restartTitle.textContent = '正在重启 Router';
    restartProgress.textContent = '启动后会自动跳转到登录页。';
    restartSpinner.hidden = false;
    restartDismiss.hidden = true;
    restartScreen.showModal();
    try {
      const {instance} = await call('/api/v1/management/restart', {});
      const started = Date.now();
      while (restartScreen.open) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        if (!restartScreen.open) break;
        const response = await fetch('/health/ready', {cache: 'no-store', signal: AbortSignal.timeout(2500)}).catch(() => null);
        if (!restartScreen.open) break;
        const current = response?.headers.get('X-HopWorld-Instance');
        if (response?.ok && current && current !== instance) {
          restartProgress.textContent = 'Router 已就绪，正在跳转到登录页…';
          location.replace('/login' + location.hash);
          return;
        }
        if (restartDismiss.hidden && Date.now() - started >= 45000) {
          restartProgress.textContent = '启动时间较长，仍在等待 Router 就绪。';
          restartDismiss.hidden = false;
        }
      }
    } catch (error) {
      restartTitle.textContent = '无法请求重启';
      restartProgress.textContent = error.message;
      restartSpinner.hidden = true;
      restartDismiss.hidden = false;
      await new Promise(resolve => restartScreen.addEventListener('close', resolve, {once: true}));
    }
    restartingRouter = false;
    syncRestartButton();
  }

  function syncRestartButton() {
    const available = savedSettings?.restart_available;
    const pendingEntry = managementAccess?.can_confirm;
    restartButton.disabled = restartingRouter || available !== true || pendingEntry;
    restartFeedback.textContent = available === undefined ? '请更新 Router 并重新启动一次。'
      : !available ? '当前启动方式不支持从后台重启。'
      : pendingEntry ? '请先确认新的管理入口。' : '';
  }

  function syncManagementInputs() {
    const active = managementAccess?.active;
    const editable = managementAccess?.available && !managementBusy && !managementAccess.pending;
    managementPort.disabled = managementHttps.disabled = !editable;
    managementApply.disabled = !editable || !managementPort.checkValidity() ||
      (managementPort.valueAsNumber === active.port && managementHttps.checked === active.https);
  }

  function managementURL(entry) {
    const host = entry.https ? entry.domain : location.hostname === 'localhost' ? 'localhost' : entry.host;
    return `${entry.https ? 'https' : 'http'}://${endpoint(host, entry.port)}/#settings`;
  }

  function renderManagementAccess(value) {
    managementAccess = value;
    if (savedSettings) syncRestartButton();
    clearTimeout(managementPoll);
    const pending = value?.pending;
    byId('v1-management-confirm').hidden = !pending;
    if (value?.active) {
      managementPort.value = value.active.port;
      managementHttps.checked = value.active.https;
    }
    managementFeedback.textContent = value?.error || (!value?.available
      ? '入口切换需运行受支持的回环管理服务。'
      : value.certificate_domain ? `HTTPS 证书域名 ${value.certificate_domain}` : '后台 HTTPS 需要先签发域名证书。');
    syncManagementInputs();
    if (!pending) return;
    const open = byId('v1-management-open');
    open.href = managementURL(pending);
    open.hidden = value.can_confirm;
    byId('v1-management-confirm-button').hidden = !value.can_confirm;
    byId('v1-management-cancel').hidden = value.can_confirm;
    const end = new Date(pending.expires_at * 1000).toLocaleTimeString([], {timeZone: settingsClockZone});
    byId('v1-management-pending-text').textContent = value.can_confirm
      ? `这里是新入口。请在 ${end} 前确认切换。`
      : `请在 ${end} 前从新入口登录并确认。远程部署需转发对应端口，HTTPS 请使用证书域名。`;
    managementPoll = setTimeout(async () => {
      if (managementBusy) return;
      try {
        const status = await call('/api/v1/management/access');
        renderManagementAccess(status);
        if (!status?.pending) managementFeedback.textContent = '本次验证已结束。请使用已确认的后台入口。';
      } catch {
        managementFeedback.textContent = '无法读取入口状态。请使用已确认的后台地址。';
      }
    }, 5000);
  }

  async function changeManagementAccess(action) {
    if (managementBusy) return;
    const data = action === 'stage'
      ? {action, port: managementPort.valueAsNumber, https: managementHttps.checked} : {action};
    managementBusy = true;
    syncManagementInputs();
    try {
      const value = await call('/api/v1/management/access', data);
      renderManagementAccess(value);
      if (action === 'confirm') note('后台入口已切换');
      if (action === 'cancel') note('已取消切换');
    } catch (error) {
      note(error.message, {error: true});
    } finally {
      managementBusy = false;
      syncManagementInputs();
    }
  }

  function renderAutostart(value) {
    autostartState = value;
    autostartSwitch.checked = value?.enabled === true;
    autostartSwitch.disabled = !value?.writable;
    byId('v1-settings-autostart-label').textContent = value?.label || '开机自启动';
    byId('v1-settings-autostart-detail').textContent = value?.detail || '';
  }
  for (const field of [managementPort, managementHttps]) field.addEventListener('input', syncManagementInputs);
  managementApply.addEventListener('click', () => changeManagementAccess('stage'));
  byId('v1-management-confirm-button').addEventListener('click', () => changeManagementAccess('confirm'));
  byId('v1-management-cancel').addEventListener('click', () => changeManagementAccess('cancel'));
  autostartSwitch.addEventListener('change', async () => {
    autostartSwitch.disabled = true;
    try {
      renderAutostart(await call('/api/v1/management/autostart', {enabled: autostartSwitch.checked}));
      note(autostartSwitch.checked ? '已开启自启动' : '已关闭自启动');
    } catch (error) {
      renderAutostart(autostartState);
      note(error.message, {error: true});
    }
  });
  byId('v1-logout').addEventListener('click', async () => {
    try {
      await call('/logout', {});
      location.replace('/login');
    } catch (error) {
      note(error.message, {error: true});
    }
  });

  function renderSettingsNetwork(acme) {
    const tags = byId('v1-settings-publish-protocols');
    const publishers = Object.keys(coreSwitchGuide).map(protocol => [protocol, currentProtocolPublisher(protocol)?.core_id]);
    const state = JSON.stringify(publishers.map(([protocol, coreId]) => [protocol, coreId, preferredProtocolCore(coreId, protocol)]));
    if (tags.dataset.state !== state) {
      tags.dataset.state = state;
      tags.replaceChildren(...publishers.map(([protocol, coreId]) => protocolChip(protocol, coreId)));
    }
    byId('v1-settings-notify-cert').textContent = acme.error ? '签发或续签异常' :
      !acme.automatic ? '自动续签未开启' :
      Number.isFinite(acme.days_left) ? acme.days_left <= 0 ? '证书已过期' :
        acme.days_left <= 14 ? `即将到期 · 剩余 ${acme.days_left} 天` : `有效 · 剩余 ${acme.days_left} 天` : '等待签发';
  }
  function showSettingsTab(tab) {
    for (const button of document.querySelectorAll('[data-settings-tab]')) {
      button.setAttribute('aria-pressed', String(button.dataset.settingsTab === tab));
    }
    for (const card of document.querySelectorAll('[data-settings-card]')) {
      const category = card.dataset.settingsCard;
      const showInBase = tab === 'base' && !['network-security', 'maintenance'].includes(category);
      card.hidden = !showInBase && !category.split(' ').includes(tab);
    }
    settingsForm.querySelector('.v1-settings-footer').hidden = tab === 'network-security';
    if (tab === 'network-security' && !securityBusy) {
      runSecurityAction('正在读取状态…', async () => {
        await loadNetworkSecurity();
        return '';
      });
    }
  }
  let showMaintenanceOnRoute = false;
  byId('v1-updates-settings').addEventListener('click', () => {
    if (page === 'settings') showSettingsTab('maintenance');
    else showMaintenanceOnRoute = true;
  });
  byId('v1-about-maintenance').addEventListener('click', () => {
    showMaintenanceOnRoute = true;
  });
  const securityApi = '/api/v1/security/';
  const securityLabels = {
    NOT_CONFIGURED: '未配置', CONFIGURED_NOT_APPLIED: '已保存，待应用', ACTIVE_UNVERIFIED: '已应用，待验证',
    PROTECTION_FAILED: '已应用版本未运行', VERIFIED_FOR_SCOPE: '已验证指定范围', UNSUPPORTED: '未支持',
    INCONCLUSIVE: '待核验', PASS: '通过', FAIL: '失败', PENDING: '等待运行', RUNNING: '运行中',
    apply: '应用策略', rollback: '回滚策略', test: '诊断发布入口', validate: '校验配置',
    'policy.save': '保存策略', 'resolver.create': '新增解析器',
    DNS_CONFIG: '运行版本', DNS_A: 'DNS A', DNS_AAAA: 'DNS AAAA', IPV4: 'IPv4 出口', IPV6: 'IPv6 出口',
    UDP: 'UDP 出口', WEBRTC: 'WebRTC', KILL_SWITCH: '出口阻断',
  };
  const securityPublisher = byId('v1-security-publisher');
  const securityMode = byId('v1-security-mode');
  const securityResolver = byId('v1-security-resolver');
  const securityFeedback = byId('v1-security-feedback');
  const securitySave = byId('v1-security-save');
  const securityActions = document.querySelectorAll('[data-security-action]');
  let networkSecurity;
  let securityBusy = false;
  function currentNetworkPolicy() {
    return networkSecurity?.policies.find(item => item.profile_id === securityPublisher.value);
  }
  function syncSecurityButtons() {
    const publisher = networkSecurity?.publishers.find(item => item.id === securityPublisher.value);
    const policy = currentNetworkPolicy();
    const supported = publisher?.core_id === 'sing-box' && publisher.egress === 'fixed' &&
      networkSecurity.capabilities.modes.compatibility === 'CONFIGURABLE';
    securityPublisher.disabled = securityBusy || !networkSecurity?.publishers.length;
    securityMode.disabled = securityBusy || !supported;
    securityResolver.disabled = securityBusy || !supported;
    securitySave.disabled = securityBusy || !supported || !securityResolver.value;
    for (const button of securityActions) {
      const action = button.dataset.securityAction;
      button.disabled = securityBusy || !policy || (['rollback', 'test'].includes(action) && !policy.active_revision);
    }
    byId('v1-security-reload').disabled = byId('v1-security-resolver-add').disabled = securityBusy;
  }
  async function renderSecuritySelection() {
    const policy = currentNetworkPolicy();
    const capabilities = networkSecurity.capabilities;
    const gatewayMissing = capabilities.gateway_missing_requirements || [];
    const gatewayStatus = capabilities.platform === 'linux'
      ? (gatewayMissing.length ? '当前缺少 ' + gatewayMissing.join('、') + '。' : '') +
        '运行 sudo python3 -m router.linux_gateway preflight 检查实际能力。'
      : '严格网关需要 Linux 原生环境。';
    securityMode.value = policy?.mode || 'compatibility';
    securityResolver.value = policy?.resolver_id || '';
    definitions('v1-security-status', [
      ['策略状态', securityLabels[policy?.status || 'NOT_CONFIGURED']],
      ['运行状态', securityLabels[policy?.runtime_status || 'NOT_CONFIGURED']],
      ['保存 / 应用 / 验证版本', [policy?.desired_revision, policy?.active_revision, policy?.verified_revision].map(value => value ?? dash).join(' / ')],
      ['运行环境', ({linux: 'Linux', darwin: 'macOS', win32: 'Windows'}[capabilities.platform] || capabilities.platform) +
        ' · 适配 sing-box ' + capabilities.sing_box_version],
      ['严格 LAN 网关', gatewayStatus + ' 后台严格发布尚未开放。'],
      ['控制面例外', '无。业务策略的 DNS 仅作用于所选实例。'],
    ]);
    syncSecurityButtons();
    const report = byId('v1-security-report');
    report.hidden = true;
    if (policy?.last_test) {
      try {
        const job = await call(securityApi + 'tests/' + policy.last_test);
        if (currentNetworkPolicy()?.last_test !== job.id) return;
        definitions('v1-security-findings', [
          ['诊断结果', `${securityLabels[job.status]} · ${job.detail}`],
          ['检测时间', formatWhen(job.finished_at)],
          ...job.findings.map(item => [securityLabels[item.code] || item.code,
            `${securityLabels[item.status]} · ${item.detail}`]),
        ]);
        report.hidden = false;
      } catch (error) {
        securityFeedback.textContent = error.message;
      }
    }
  }
  async function loadNetworkSecurity() {
    const [status, capabilities, {resolvers}, {events}] = await Promise.all(
      ['status', 'capabilities', 'dns/resolvers', 'events'].map(path => call(securityApi + path)));
    networkSecurity = {...status, capabilities};
    const prior = securityPublisher.value;
    securityPublisher.replaceChildren(new Option('请选择发布实例', ''), ...status.publishers.map(item =>
      new Option(`${coreCatalog[item.core_id]?.name || item.core_id} · ${item.port}${item.enabled ? '' : ' · 已停止'}`, item.id)));
    securityPublisher.value = status.publishers.some(item => item.id === prior) ? prior
      : status.publishers.find(item => item.core_id === 'sing-box' && item.egress === 'fixed')?.id || '';
    securityResolver.replaceChildren(new Option('请选择解析器', ''), ...resolvers.map(item =>
      new Option(`${item.name} · ${item.protocol === 'doh' ? 'DoH' : 'DoT'} · ${item.server_name}`, item.id)));
    definitions('v1-security-events', events.length ? events.slice(0, 8).map(event =>
      [securityLabels[event.action] || event.action,
        `${securityLabels[event.result]} · ${formatWhen(event.timestamp)}`]) : [['操作记录', '暂无记录']]);
    await renderSecuritySelection();
  }
  function runSecurityAction(message, task) {
    if (securityBusy) return;
    securityBusy = true;
    securityFeedback.textContent = message;
    syncSecurityButtons();
    void (async () => {
      try { securityFeedback.textContent = await task(); }
      catch (error) { securityFeedback.textContent = error.message; }
      finally { securityBusy = false; syncSecurityButtons(); }
    })();
  }
  byId('v1-security-reload').addEventListener('click', () => runSecurityAction('正在读取状态…', async () => {
    await loadNetworkSecurity();
    return '状态已刷新';
  }));
  securityPublisher.addEventListener('change', renderSecuritySelection);
  securityResolver.addEventListener('change', syncSecurityButtons);
  byId('v1-security-dns-protocol').addEventListener('change', event => {
    const doh = event.target.value === 'doh';
    byId('v1-security-dns-port').value = doh ? 443 : 853;
    byId('v1-security-dns-path').disabled = !doh;
  });
  byId('v1-security-resolver-add').addEventListener('click', () => runSecurityAction('正在添加解析器…', async () => {
    const field = key => byId('v1-security-dns-' + key).value.trim();
    const resolver = await call(securityApi + 'dns/resolvers', {
      name: field('name'), protocol: field('protocol'), address: field('address'), port: Number(field('port')),
      server_name: field('host'), path: field('protocol') === 'doh' ? field('path') : '',
    });
    await loadNetworkSecurity();
    securityResolver.value = resolver.id;
    return '已添加解析器';
  }));
  securitySave.addEventListener('click', () => runSecurityAction('正在保存策略…', async () => {
    const publisher = networkSecurity.publishers.find(item => item.id === securityPublisher.value);
    const id = currentNetworkPolicy()?.id || publisher.id.replace('@', '-');
    await call(securityApi + 'policies/' + id, {
      mode: securityMode.value, profile_id: publisher.id, upstream_id: publisher.upstream_id,
      resolver_id: securityResolver.value,
    }, 'PUT');
    await loadNetworkSecurity();
    return '策略已保存';
  }));
  for (const button of securityActions) button.addEventListener('click', () => {
    const action = button.dataset.securityAction;
    const policy = currentNetworkPolicy();
    if (!policy) return;
    const run = () => runSecurityAction('正在处理网络安全作业…', async () => {
      let job = action === 'test' ? await call(securityApi + 'tests', {policy_id: policy.id})
        : await call(securityApi + 'policies/' + policy.id + '/' + action, {});
      for (let tries = 0; ['PENDING', 'RUNNING'].includes(job.status) && tries < 50; tries++) {
        await new Promise(resolve => setTimeout(resolve, 900));
        job = await call(securityApi + 'tests/' + job.id);
      }
      await loadNetworkSecurity();
      return `${securityLabels[job.status] || job.status} · ${job.detail || '请刷新查看结果'}`;
    });
    if (action === 'test') {
      openConfirm('诊断发布入口', '诊断通过此节点查询 DNS 并访问 ipify 的 IPv4 和 IPv6 观察点。', '开始诊断', run);
    } else if (action === 'rollback' || action === 'apply') {
      openConfirm(action === 'rollback' ? '回滚 DNS 策略' : '应用 DNS 策略',
        '此操作会重启所选发布实例。回滚首个版本会恢复原节点的 DNS 设置。', '继续', run);
    } else run();
  });
  function readSettingsDraft() {
    const fields = settingsForm.elements;
    return {timezone: fields.timezone.value, auto_check: fields.auto_check.checked,
      auto_apply: fields.auto_apply.checked, probe_target: fields.probe_target.value};
  }
  function fillSettingsDraft(value) {
    const fields = settingsForm.elements;
    fields.timezone.value = value.timezone;
    fields.auto_check.checked = value.auto_check;
    fields.auto_apply.checked = value.auto_apply;
    fields.probe_target.value = value.probe_target || settingsDefaults.probe_target;
    syncSettingsButtons();
  }
  function syncSettingsButtons() {
    const draft = readSettingsDraft();
    const dirty = savedSettings && (savedSettings.timezone !== draft.timezone ||
      savedSettings.auto_check !== draft.auto_check ||
      savedSettings.auto_apply !== draft.auto_apply || savedSettings.probe_target !== draft.probe_target);
    byId('v1-settings-save').disabled = savingSettings || !dirty;
    byId('v1-settings-reset').disabled = !savedSettings || savingSettings;
  }
  function applyConsolePreferences(value) {
    consoleName = value.name;
    settingsClockZone = value.timezone === 'browser' ? undefined : value.timezone;
    const brand = document.querySelector('.brand');
    brand.querySelector('.brand-name').firstChild.textContent = consoleName + ' ';
    brand.setAttribute('aria-label', consoleName + ' 首页');
    document.title = pages[page][0] + ' · ' + consoleName + ' V1';
    clock();
  }
  async function loadManagementSettings() {
    settingsRetry.hidden = true;
    basicSettingsFeedback.textContent = '正在读取设置…';
    try {
      const value = await call('/api/v1/settings');
      savedSettings = value;
      byId('v1-admin-username').textContent = value.admin_username || '—';
      adminEdit.disabled = !value.admin_username;
      applyConsolePreferences(value);
      byId('v1-settings-name').textContent = value.name || '—';
      fillSettingsDraft(value);
      managementPort.value = value.port || '';
      renderManagementAccess(value.access);
      renderAutostart(value.autostart);
      byId('v1-settings-listener').textContent = value.host ? `监听 ${endpoint(value.host, value.port)}` : dash;
      if (value.host?.startsWith('127.') || ['::1', 'localhost'].includes(value.host)) {
        byId('v1-settings-network-lan').textContent = '管理端仅监听本机';
      }
      byId('v1-settings-zone').disabled = false;
      byId('v1-settings-probe-target').disabled = false;
      basicSettingsFeedback.textContent = '';
    } catch (error) {
      basicSettingsFeedback.textContent = error.message;
      settingsRetry.hidden = false;
    }
  }
  settingsRetry.addEventListener('click', loadManagementSettings);
  for (const button of document.querySelectorAll('[data-settings-tab]')) {
    button.addEventListener('click', () => showSettingsTab(button.dataset.settingsTab));
  }
  settingsForm.addEventListener('input', syncSettingsButtons);
  byId('v1-settings-reset').addEventListener('click', () => {
    fillSettingsDraft(settingsDefaults);
    settingsFeedback.textContent = byId('v1-settings-save').disabled ? '当前已是默认设置。' : '已填入默认值，点「保存设置」应用。';
  });
  settingsForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (!savedSettings || savingSettings) return;
    const draft = readSettingsDraft();
    savingSettings = true;
    syncSettingsButtons();
    settingsFeedback.textContent = '正在保存…';
    try {
      const saved = await persistManagementSettings(draft);
      savedSettings = {...savedSettings, ...saved};
      applyConsolePreferences(saved);
      renderDefaultProbeTarget(savedSettings.probe_target);
      settingsFeedback.textContent = '设置已保存';
    } catch (error) {
      settingsFeedback.textContent = error.message;
    } finally {
      savingSettings = false;
      syncSettingsButtons();
    }
  });
  byId('v1-settings-backup').addEventListener('click', async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const response = await fetch('/api/v1/settings/backup');
      if (!response.ok) throw new Error((await response.json()).error || '数据库备份失败');
      const filename = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') || '')?.[1];
      saveBlob(await response.blob(), filename || 'hopworld-router.db');
      note('数据库备份已生成');
    } catch (error) {
      note(error.message, {error: true});
    } finally {
      button.disabled = false;
    }
  });
  const settingsLogCore = byId('v1-settings-log-core');
  const settingsViewLog = byId('v1-settings-view-log');
  const settingsExportLogs = byId('v1-settings-export-logs');
  let exportingLogs = false;
  const managedLogCores = () => (system?.cores || []).filter(core => core.managed);
  function renderSettingsLogCores() {
    const cores = managedLogCores();
    const options = [[cores.length ? '选择内核' : '暂无可查看的内核', ''], ...cores.map(core => [core.name, core.id])];
    const selected = settingsLogCore.value;
    if (settingsLogCore.options.length !== options.length || options.some(([label, id], index) =>
      settingsLogCore.options[index].value !== id || settingsLogCore.options[index].text !== label)) {
      settingsLogCore.replaceChildren(...options.map(([label, id]) => new Option(label, id)));
      if (cores.some(core => core.id === selected)) settingsLogCore.value = selected;
    }
    settingsLogCore.disabled = !cores.length;
    settingsViewLog.disabled = !settingsLogCore.value;
    settingsExportLogs.disabled = exportingLogs || !cores.length;
  }
  settingsLogCore.addEventListener('change', () => {
    settingsViewLog.disabled = !settingsLogCore.value;
  });
  settingsViewLog.addEventListener('click', () => {
    const core = managedLogCores().find(item => item.id === settingsLogCore.value);
    if (core) void askLogs(core);
  });
  settingsExportLogs.addEventListener('click', async () => {
    const cores = managedLogCores();
    if (exportingLogs || !cores.length) return;
    exportingLogs = settingsExportLogs.disabled = true;
    try {
      const results = await Promise.allSettled(cores.map(core => call('/api/v1/cores/logs', {id: core.id})));
      const text = results.map((result, index) => {
        const output = result.status === 'fulfilled' ? result.value.text || '暂无日志' : `读取失败 · ${result.reason.message}`;
        return `[${cores[index].name}]\n${output}`;
      }).join('\n\n');
      saveBlob(new Blob([text], {type: 'text/plain;charset=utf-8'}), 'hopworld-core-logs.txt');
    } catch (error) {
      note(error.message, {error: true});
    } finally {
      exportingLogs = false;
      renderSettingsLogCores();
    }
  });
  function formatAppVersion(version) {
    const value = typeof version === 'string' ? version.trim() : '';
    if (!value) return '';
    return /^\d/.test(value) ? 'v' + value : value;
  }
  function renderAppVersion(version) {
    const text = formatAppVersion(version);
    const label = byId('v1-sidebar-version-label');
    if (label) label.textContent = text;
    const about = byId('v1-about-version');
    if (about) about.textContent = text || dash;
  }
  function renderSystem(value) {
    system = value;
    const cores = value.cores || [];
    renderCores();
    renderSettingsLogCores();
    renderPublicLogs();
    byId('v1-cores-overview').replaceChildren(...cores.map(core => {
      const row = element('li', null, 'v1-core-row');
      const mark = coreMark(core.id);
      const name = element('span', core.name);
      name.append(element('small', core.version || '版本未读取'));
      const configuredPorts = [...(core.tcp_ports || []).map(port => 'TCP ' + port), ...(core.udp_ports || []).map(port => 'UDP ' + port)];
      const state = element('span', null, 'v1-core-state');
      state.append(badge(core));
      if (configuredPorts.length) state.append(element('small', configuredPorts.join(' · ')));
      row.append(mark, name, state);
      return row;
    }));
    const usage = item => item?.total_bytes ? `${bytes(item.used_bytes)} / ${bytes(item.total_bytes)}` : dash;
    const resources = [
      ['CPU', value.cpu_percent, `${value.cpu_count ?? dash} 核`, themeColor('--v1-resource-cpu', '#57dd78')],
      ['内存', value.memory?.total_bytes ? 100 * value.memory.used_bytes / value.memory.total_bytes : null, usage(value.memory), themeColor('--v1-resource-memory', '#37d9ba')],
      ['磁盘', value.disk?.total_bytes ? 100 * value.disk.used_bytes / value.disk.total_bytes : null, usage(value.disk), themeColor('--v1-resource-disk', '#42aaff')],
    ];
    function paintResourceRing(dial, percent, color) {
      const context = dial.getContext('2d');
      context.clearRect(0, 0, 176, 176);
      context.lineWidth = 13;
      context.strokeStyle = themeColor('--v1-resource-track', '#304156');
      context.beginPath();
      context.arc(88, 88, 77, 0, 2 * Math.PI);
      context.stroke();
      if (Number.isFinite(percent) && percent > 0) {
        context.beginPath();
        context.strokeStyle = color;
        context.lineCap = 'round';
        context.arc(88, 88, 77, -.5 * Math.PI, (Math.min(percent, 100) / 50 - .5) * Math.PI);
        context.stroke();
      }
    }
    function applyResourceRing(dial, percent, color) {
      const motion = window.hopworldTheme && window.hopworldTheme.motion;
      if (motion && typeof motion.animateResourceRing === 'function') {
        motion.animateResourceRing(dial, percent, color, (value, paintColor) => {
          paintResourceRing(dial, value, paintColor);
        });
        return;
      }
      paintResourceRing(dial, percent, color);
    }
    const systemBox = byId('v1-overview-system');
    const existingCells = [...systemBox.querySelectorAll('.v1-resource')];
    const sameLayout = existingCells.length === resources.length
      && resources.every((item, index) => existingCells[index]?.querySelector('span')?.textContent === item[0]);
    if (sameLayout) {
      resources.forEach(([label, percent, noteText, color], index) => {
        const cell = existingCells[index];
        const dial = cell.querySelector('canvas');
        const strong = cell.querySelector('strong');
        const note = cell.querySelector('small');
        applyResourceRing(dial, percent, color);
        window.hopworldTheme?.motion?.animateMetricText?.(strong, Number.isFinite(percent) ? Math.round(percent) + '%' : dash);
        if (note) note.textContent = noteText;
      });
    } else {
      systemBox.replaceChildren(...resources.map(([label, percent, noteText, color]) => {
        const cell = element('div', null, 'v1-resource');
        const ring = element('div', null, 'v1-resource-ring');
        const dial = element('canvas');
        dial.width = dial.height = 176;
        dial.setAttribute('aria-hidden', 'true');
        applyResourceRing(dial, percent, color);
        ring.append(dial, element('strong', Number.isFinite(percent) ? Math.round(percent) + '%' : dash));
        cell.append(element('span', label), ring, element('small', noteText));
        return cell;
      }));
    }
    const formatUsage = item => item?.total_bytes
      ? `${bytes(item.total_bytes)}（已用 ${bytes(item.used_bytes)}，${Math.round(100 * item.used_bytes / item.total_bytes)}%）` : dash;
    definitions('v1-system-details', [
      ['系统版本', `HopWorld-Router ${value.version}`], ['运行环境', value.platform], ['架构', value.architecture],
      ['CPU', value.cpu_count == null ? null : `${value.cpu_count} 核`],
      ['内存', formatUsage(value.memory)], ['磁盘', formatUsage(value.disk)], ['服务运行时长', duration(value.uptime_seconds)],
    ]);
    definitions('v1-settings-resources', [
      ['CPU 使用率', value.cpu_percent == null ? null : `${value.cpu_percent}%`],
      ['内存使用', usage(value.memory)], ['磁盘使用', usage(value.disk)],
      ['运行内核', `${cores.filter(core => core.status === 'running').length} / ${cores.length}`],
      ['连接及带宽限额', '由部署与内核配置管理'],
    ]);
    byId('v1-settings-version').textContent = value.version || dash;
    renderAppVersion(value.version);
    renderOverview();
  }
  function themeColor(name, fallback) {
    try {
      const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      if (value) return value;
    } catch (error) {}
    return fallback;
  }
  function trafficSeries() {
    return [
      ['down_bytes', '下载', themeColor('--v1-chart-down', '#2597fc')],
      ['up_bytes', '上传', themeColor('--v1-chart-up', '#35db87')],
      ['total_bytes', '总流量', themeColor('--v1-chart-total', '#b477fc')],
    ];
  }
  function chartAllowsGlow() {
    return document.documentElement.getAttribute('data-theme') === 'neon-fusion'
      && !(typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches);
  }
  function setChartGlow(ctx, color, on) {
    if (on && chartAllowsGlow()) {
      ctx.shadowColor = color;
      ctx.shadowBlur = 6;
    } else {
      ctx.shadowColor = 'transparent';
      ctx.shadowBlur = 0;
    }
  }
  const chartLabel = sample => sample.date?.slice(5) || (sample.timestamp
    ? new Date(sample.timestamp * 1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}) : '');

  function chart(id, samples, axisFormat = bytes, live = false, options = {}) {
    const canvas = byId(id);
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (!width || !height) return;
    const motion = window.hopworldTheme && window.hopworldTheme.motion;
    if (motion && typeof motion.watchCanvas === 'function') motion.watchCanvas(canvas);
    const displaySamples = live && motion && typeof motion.liveChartSamples === 'function'
      ? motion.liveChartSamples(id, samples, () => { chart(id, samples, axisFormat, live, options); })
      : samples;
    const scale = window.devicePixelRatio || 1;
    canvas.width = width * scale;
    canvas.height = height * scale;
    const ctx = canvas.getContext('2d');
    ctx.scale(scale, scale);
    const seriesSamples = displaySamples || samples;
    const measured = samples.filter(s => Number.isFinite(s.total_bytes));
    const caption = byId(id + '-caption');
    caption.textContent = measured.length
      ? options.caption || '蓝色为下行，绿色为上行。未采样时段留空。'
      : options.empty || '暂无流量采样。';
    if (!measured.length && !options.grid) return;
    const series = options.series || trafficSeries().slice(0, 2);
    const peak = Math.max(4, ...measured.map(sample => sample.total_bytes));
    const unit = 10 ** Math.floor(Math.log10(peak / 4));
    const max = Math.ceil(peak / (4 * unit)) * 4 * unit;
    const left = Math.min(80, width / 4);
    const w = width - left - 10;
    const h = height - 30;
    const bottom = 10 + h;
    const step = w / Math.max(1, samples.length);
    const pointX = index => left + step * (index + .5);
    ctx.font = '11px system-ui';
    ctx.fillStyle = themeColor('--v1-chart-axis', '#aebed3');
    ctx.strokeStyle = themeColor('--v1-chart-grid', '#24374b');
    for (let i = 0; i <= 4; i++) {
      const y = 10 + h * i / 4;
      if (measured.length) ctx.fillText(axisFormat(Math.round(max * (1 - i / 4))), 2, y + 3);
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(width - 5, y);
      ctx.stroke();
    }
    if (!live) seriesSamples.forEach((sample, index) => {
      if (!Number.isFinite(sample.total_bytes)) return;
      const bar = Math.min(54, step * .7);
      let offset = bottom;
      for (const [key, , color] of series) {
        if (key === 'total_bytes') continue;
        const size = h * sample[key] / max;
        ctx.fillStyle = color;
        setChartGlow(ctx, color, true);
        offset -= size;
        ctx.fillRect(pointX(index) - bar / 2, offset, bar, size);
        setChartGlow(ctx, color, false);
      }
    });
    for (const [key, , color] of series) {
      if (!live && key !== 'total_bytes') continue;
      let points = [];
      function strokeSegment() {
        if (!points.length) return;
        ctx.beginPath();
        ctx.moveTo(...points[0]);
        for (const point of points.slice(1)) ctx.lineTo(...point);
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        setChartGlow(ctx, color, true);
        ctx.stroke();
        setChartGlow(ctx, color, false);
        if (options.fill && key !== 'total_bytes' && points.length > 1) {
          ctx.lineTo(points.at(-1)[0], bottom);
          ctx.lineTo(points[0][0], bottom);
          ctx.closePath();
          const gradient = ctx.createLinearGradient(0, 10, 0, bottom);
          gradient.addColorStop(0, color + '7a');
          gradient.addColorStop(1, color + '0d');
          ctx.fillStyle = gradient;
          ctx.fill();
        }
        ctx.beginPath();
        ctx.arc(...points.at(-1), 2.5, 0, Math.PI * 2);
        ctx.fillStyle = color;
        setChartGlow(ctx, color, true);
        ctx.fill();
        setChartGlow(ctx, color, false);
        points = [];
      }
      seriesSamples.forEach((sample, index) => {
        if (Number.isFinite(sample[key])) points.push([pointX(index), bottom - h * sample[key] / max]);
        else strokeSegment();
      });
      strokeSegment();
    }
    ctx.fillStyle = '#aebed3';
    ctx.textAlign = 'center';
    const count = Math.min(samples.length, options.labels ? Math.max(2, Math.floor(w / 90)) : 2);
    for (let index = 0; index < count; index++) {
      const at = count === 1 ? 0 : Math.round(index * (samples.length - 1) / (count - 1));
      const x = Math.min(width - 27, Math.max(left + 23, pointX(at)));
      ctx.fillText(chartLabel(samples[at]), x, height - 5);
    }
    if (options.point != null && samples[options.point]) {
      const x = pointX(options.point);
      ctx.strokeStyle = '#a7bfe6';
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(x, 10);
      ctx.lineTo(x, bottom);
      ctx.stroke();
    }
    return {left, step, pointX};
  }
  const trafficRanges = {
    live: ['实时流量曲线', '最近 30 分钟，每 10 秒保留一个采样点。'],
    recent: ['近1小时流量', '每 5 分钟一段，最后一段持续更新。'],
    hourly: ['近24小时流量', '按小时汇总。'],
    daily: ['近7天流量', '按服务器日期汇总，含今天。'],
    last_30_daily: ['近30天流量', '按服务器日期汇总，含今天。'],
    custom: ['自定义时段流量', '按服务器日期汇总。'],
  };
  const trafficCanvas = byId('v1-traffic-chart');
  const trafficPoint = byId('v1-traffic-chart-point');
  const seriesToggles = [...document.querySelectorAll('.v1-traffic-legend input')];
  let trafficRange = 'live';
  let trafficIndex = null;
  let customTraffic = [];
  let trafficGraph;
  const trafficScope = byId('v1-traffic-scope');
  const activeTraffic = () => trafficScope.value === 'legacy' ? traffic?.legacy || {} : traffic || {};
  function publisherBytes(profile, port) {
    const rows = (traffic?.publishers || []).filter(row => row.publisher === profile && row.port === port);
    return rows.length ? rows.reduce((total, row) => total + row.total_bytes, 0) : null;
  }

  function selectedTrafficSamples() {
    const view = activeTraffic();
    if (trafficRange === 'live') return view.live || (trafficScope.value === 'legacy' ? [] : liveSamples);
    if (trafficRange !== 'custom') return view[trafficRange] || [];
    const today = view.daily?.at(-1);
    return customTraffic.map(item => item.date === today?.date ? today : item);
  }

  function renderTrafficChart() {
    const samples = selectedTrafficSamples();
    const live = trafficRange === 'live';
    const [title, timing] = trafficRanges[trafficRange];
    const formatter = live ? rate : bytes;
    const series = trafficSeries().filter(([key]) => seriesToggles.some(control => control.value === key && control.checked));
    const approximate = samples.some(item => item.estimated);
    byId('v1-traffic-chart-title').textContent = title;
    trafficCanvas.setAttribute('aria-label', title + '。聚焦后使用左右方向键查看数据。');
    trafficGraph = chart('v1-traffic-chart', samples, formatter, live, {
      series, labels: true, grid: true, fill: live, point: trafficIndex,
      caption: timing + (approximate ? ' 部分时段流量为估算值。' : ' 未采样时段留空。'),
      empty: trafficRange === 'recent' ? '暂无 5 分钟采样记录，采集启动后会逐步补齐。' : '这个时段暂无流量采样。',
    });
    const sample = samples[trafficIndex];
    trafficPoint.hidden = !sample || !trafficGraph;
    if (trafficPoint.hidden) return;
    const stamp = sample.date || new Date(sample.timestamp * 1000).toLocaleString();
    const values = Number.isFinite(sample.total_bytes)
      ? series.map(([key, label]) => `${label} ${formatter(sample[key])}`)
      : ['暂无采样'];
    const pointWidth = Math.min(230, trafficCanvas.clientWidth);
    const x = trafficGraph.pointX(trafficIndex);
    trafficPoint.style.left = `${Math.max(0, Math.min(trafficCanvas.clientWidth - pointWidth, x + 9))}px`;
    trafficPoint.textContent = [stamp, ...values, ...(sample.estimated ? ['估算值'] : [])].join('\n');
  }

  function setTrafficRange(range) {
    trafficRange = range;
    trafficIndex = null;
    for (const button of document.querySelectorAll('[data-traffic-range]')) {
      button.setAttribute('aria-pressed', String(button.dataset.trafficRange === range));
    }
    renderTrafficChart();
  }

  function renderTrafficDashboard(value) {
    const totalRate = Number.isFinite(value.up_bps) && Number.isFinite(value.down_bps) ? value.up_bps + value.down_bps : null;
    metrics('v1-traffic-metrics', [
      ['实时下载', rate(value.down_bps), 'arrow-down'], ['实时上传', rate(value.up_bps), 'arrow-up', 'violet'],
      ['实时总速率', rate(totalRate), 'activity', 'cyan'], ['今日流量', bytes(value.today?.total_bytes), 'arrow-left-right', 'violet'],
      ['本月流量', bytes(value.month?.total_bytes), 'calendar', 'violet'], ['累计流量', bytes(value.cumulative?.total_bytes), 'chart-column'],
    ]);
    const today = value.today?.total_bytes;
    const hasTraffic = today > 0;
    const legacy = trafficScope.value === 'legacy';
    byId('v1-traffic-donut-total').textContent = bytes(today);
    byId('v1-traffic-donut').classList.toggle('has-traffic', hasTraffic);
    byId('v1-traffic-coverage').textContent = legacy ? '旧版 Xray 独立保留的历史计数。' :
      `已采集 ${value.tracked_publishers ?? 0} / ${value.active_publishers ?? 0} 个运行实例，合计仅含有计数器的实例。`;
    byId('v1-traffic-cores').replaceChildren(...knownCores().map(({id, name}) => {
      const total = legacy ? id === 'xray-core' ? today : null : value.by_core?.[id]?.total_bytes;
      const row = element('li');
      row.dataset.core = id;
      const mark = element('i', null, 'v1-traffic-core-dot');
      mark.setAttribute('aria-hidden', 'true');
      const share = Number.isFinite(total) && hasTraffic ? `${(100 * total / today).toFixed(1)}%` : dash;
      const amount = bytes(total);
      row.append(mark, element('span', id === 'shadowsocks-rust' ? 'Shadowsocks' : name), element('span', share), element('span', amount));
      if (amount === dash) {
        const status = '今日暂无可用采样';
        row.title = status;
        row.append(element('span', status, 'sr-only'));
      }
      return row;
    }));
    const grouped = new Map();
    const measured = (value.publishers || []).filter(row => row.protocol && row.port);
    for (const row of measured) grouped.set(row.protocol, (grouped.get(row.protocol) || 0) + row.total_bytes);
    definitions('v1-traffic-protocols', [...grouped].map(([protocol, total]) => [protocolName(protocol), bytes(total)]));
    byId('v1-traffic-protocol-hint').textContent = measured.length
      ? '仅合计可归属到单一协议的实例。' : '暂无可归属到单一协议的采样。';
    const ranking = measured.sort((a, b) => b.total_bytes - a.total_bytes).slice(0, 10);
    const rows = ranking.map((item, index) => {
      const row = element('tr');
      row.append(...[index + 1, `${coreName(item.core)}:${item.port}`, protocolName(item.protocol),
        bytes(item.total_bytes), hasTraffic ? `${(100 * item.total_bytes / today).toFixed(1)}%` : dash].map(label => element('td', label)));
      return row;
    });
    if (!rows.length) {
      const row = element('tr');
      const cell = element('td', '暂无可单独归属的发布端点采样', 'v1-traffic-empty');
      cell.colSpan = 5;
      row.append(cell);
      rows.push(row);
    }
    byId('v1-traffic-ranking').replaceChildren(...rows);

    const days = (value.daily || []).filter(point => Number.isFinite(point.total_bytes));
    const sum = (points, key) => points.reduce((total, item) => total + item[key], 0);
    const total = sum(days, 'total_bytes');
    byId('v1-traffic-week-down').textContent = bytes(days.length ? sum(days, 'down_bytes') : null);
    byId('v1-traffic-week-up').textContent = bytes(days.length ? sum(days, 'up_bytes') : null);
    byId('v1-traffic-week-total').textContent = bytes(days.length ? total : null);
    const previous = (value.last_30_daily || []).slice(-14, -7).filter(point => Number.isFinite(point.total_bytes));
    const previousTotal = sum(previous, 'total_bytes');
    const comparison = days.length === 7 && previous.length === 7 && previousTotal > 0
      ? (total - previousTotal) / previousTotal * 100 : null;
    byId('v1-traffic-week-change').textContent = comparison === null ? dash :
      `${comparison > 0 ? '+' : ''}${comparison.toFixed(1)}%`;
    byId('v1-traffic-week-status').textContent = days.length
      ? `已有 ${days.length} / 7 天采样，含今天${days.some(point => point.estimated) ? '，部分数据为估算值' : ''}。`
      : '暂无最近 7 天采样。';
    const states = {ok:'正在采集', inactive:'没有运行中的发布实例', stopped:'本地节点未运行', unavailable:'计数器不可用'};
    definitions('v1-traffic-details', [
      ['采集状态', states[value.state] || '正在读取'],
      ['最近采样', value.sampled_at ? new Date(value.sampled_at * 1000).toLocaleString() : dash],
      ['累计上传', bytes(value.cumulative?.up_bytes)], ['累计下载', bytes(value.cumulative?.down_bytes)],
    ]);
  }

  document.addEventListener('hopworld-theme-change', () => { renderCharts(); });
  function renderCharts() {
    if (page === 'overview') {
      const series = {live: liveSamples, today: traffic?.today_hourly, week: traffic?.daily, month: traffic?.month_daily};
      chart('v1-home-chart', series[homeRange] || [], homeRange === 'live' ? rate : bytes, homeRange === 'live');
    }
    if (page === 'traffic') {
      renderTrafficChart();
      chart('v1-daily-chart', activeTraffic().daily || [], bytes, false, {
        labels: true, grid: true, caption: '未采样的日期留空。',
      });
    }
  }
  function renderTraffic(value) {
    traffic = value;
    const sampledAt = value.sampled_at;
    if (sampledAt && sampledAt !== liveSamples.at(-1)?.timestamp) {
      const measured = Number.isFinite(value.up_bps) && Number.isFinite(value.down_bps);
      liveSamples.push({timestamp: sampledAt, up_bytes: value.up_bps, down_bytes: value.down_bps,
        total_bytes: measured ? value.up_bps + value.down_bps : null});
      if (liveSamples.length > 45) liveSamples.shift();
    }
    if (page === 'traffic') renderTrafficDashboard(activeTraffic());
    renderCharts();
    renderOverview();
    if (page === 'cores') renderCores();
    if (page === 'nodes') renderNodes();
    if (page === 'public') renderPublicPorts();
  }

  function remainText(unix) {
    const delta = unix * 1000 - Date.now();
    if (delta <= 0) return '即将更新';
    const total = Math.round(delta / 1000);
    const hours = Math.floor(total / 3600);
    if (hours >= 48) return `${Math.floor(hours / 24)} 天后`;
    const minutes = Math.floor((total % 3600) / 60);
    const seconds = total % 60;
    return [hours, minutes, seconds].map(part => String(part).padStart(2, '0')).join(':');
  }
  function formatInterval(seconds) {
    const minutes = Math.round((seconds || 0) / 60);
    if (minutes >= 1440 && minutes % 1440 === 0) return `每 ${minutes / 1440} 天`;
    if (minutes >= 60 && minutes % 60 === 0) return `每 ${minutes / 60} 小时`;
    return `每 ${minutes} 分钟`;
  }
  function formatWhen(unix) {
    return unix ? new Date(unix * 1000).toLocaleString() : dash;
  }
  function sourceNodes(source) {
    return (overview?.nodes || []).filter(node => node.groups?.includes('source:' + source.id));
  }
  function sourceRate(source) {
    const nodes = sourceNodes(source);
    const known = nodes.filter(node => node.probe && node.probe.status !== 'testing');
    if (!known.length) return dash;
    const ok = known.filter(node => node.probe.status === 'ok').length;
    return `${Math.round(100 * ok / known.length)}% · ${ok}/${known.length} 已测`;
  }
  function renderSubscriptions() {
    const body = byId('v1-sub-rows');
    if (!body) return;
    const sources = overview?.sources || [];
    const query = byId('v1-sub-query')?.value.trim().toLowerCase() || '';
    const counts = {
      all: sources.length,
      enabled: sources.filter(source => source.enabled !== false).length,
      disabled: sources.filter(source => source.enabled === false).length,
    };
    for (const button of document.querySelectorAll('[data-sub-filter]')) {
      const key = button.dataset.subFilter;
      button.textContent = `${{all: '全部订阅', enabled: '已启用', disabled: '已停用'}[key]} (${counts[key] || 0})`;
      button.setAttribute('aria-pressed', String(key === subFilter));
    }
    const matched = sources.filter(source => {
      if (query && !`${source.name} ${source.url || ''}`.toLowerCase().includes(query)) return false;
      if (subFilter === 'enabled') return source.enabled !== false;
      if (subFilter === 'disabled') return source.enabled === false;
      return true;
    });
    if (!matched.some(source => String(source.id) === String(selectedSourceId))) selectedSourceId = matched[0]?.id ?? null;
    body.replaceChildren(...(matched.length ? matched.map(source => {
      const row = element('tr');
      row.dataset.subPick = source.id;
      row.classList.toggle('is-selected', String(source.id) === String(selectedSourceId));
      const name = element('th');
      name.scope = 'row';
      const pick = element('button', null, 'v1-core-pick');
      pick.type = 'button';
      pick.dataset.subAct = 'select';
      pick.dataset.subId = source.id;
      const line = element('span', null, 'v1-sub-name');
      line.append(element('strong', source.name), element('small', source.url || dash));
      pick.append(line);
      name.append(pick);
      const state = element('td');
      const stateBox = element('span', null, 'v1-sub-state');
      const badge = element('span', source.error ? '更新失败' : source.enabled === false ? '已停用' : '已启用', 'v1-status');
      badge.dataset.state = source.error || source.enabled === false ? 'stopped' : 'running';
      if (source.error) badge.title = source.error;
      const enabled = element('button', null, 'v1-switch');
      enabled.type = 'button';
      enabled.dataset.subAct = 'enabled';
      enabled.dataset.subId = source.id;
      enabled.dataset.on = String(source.enabled !== false);
      enabled.setAttribute('aria-pressed', String(source.enabled !== false));
      enabled.setAttribute('aria-label', (source.enabled === false ? '启用 ' : '停用 ') + source.name);
      stateBox.append(badge, enabled);
      state.append(stateBox);
      const auto = element('td');
      const knob = element('button', null, 'v1-switch');
      knob.type = 'button';
      knob.dataset.subAct = 'auto';
      knob.dataset.subId = source.id;
      knob.dataset.on = String(source.auto_update !== false);
      knob.setAttribute('aria-pressed', String(source.auto_update !== false));
      knob.setAttribute('aria-label', (source.auto_update === false ? '开启自动更新 ' : '关闭自动更新 ') + source.name);
      auto.append(knob);
      const ops = element('td');
      const box = element('div', null, 'v1-core-ops');
      for (const [name, label, act] of [['pencil', '设置 ', 'edit'], ['refresh-cw', '更新 ', 'refresh'], ['x', '删除 ', 'delete']]) {
        const button = element('button', null, 'v1-icon-btn');
        button.type = 'button';
        button.setAttribute('aria-label', label + source.name);
        button.dataset.subAct = act;
        button.dataset.subId = source.id;
        if (act === 'refresh') button.disabled = source.enabled === false;
        setIcon(button, name, 20);
        box.append(button);
      }
      ops.append(box);
      row.append(name, element('td', String(source.count || 0)), element('td', sourceRate(source)), element('td', formatInterval(source.update_interval)), element('td', formatWhen(source.updated)), state, auto, ops);
      return row;
    }) : [(() => { const row = element('tr'); const cell = element('td', overview ? '没有符合当前筛选的订阅' : '正在读取订阅…'); cell.colSpan = 8; row.append(cell); return row; })()]));
    renderSourceDetail(sources.find(source => source.id === selectedSourceId));
  }
  function renderSourceDetail(source) {
    const panel = byId('v1-sub-detail');
    const preview = byId('v1-sub-preview');
    if (!panel || !preview) return;
    if (!source) {
      panel.replaceChildren(element('p', '在左侧选择一个订阅。'));
      preview.replaceChildren(element('p', '选择订阅后，这里显示它的节点。'));
      return;
    }
    const nodes = sourceNodes(source);
    const known = nodes.filter(node => node.probe && node.probe.status !== 'testing');
    const ok = known.filter(node => node.probe.status === 'ok').length;
    const bad = known.filter(node => node.probe.status !== 'ok').length;
    const head = element('div', null, 'v1-core-head');
    head.append(element('h2', source.name), element('p', source.url || dash));
    const facts = element('dl', null, 'v1-definitions');
    for (const [label, value] of [
      ['订阅地址', source.url || dash],
      ['更新周期', formatInterval(source.update_interval)],
      ['最后更新', formatWhen(source.updated)],
      ['下次更新', source.next_update ? formatWhen(source.next_update) : '未安排'],
      ['节点数', String(source.count || 0)],
      ['可用节点', known.length ? String(ok) : '未测试'],
      ['异常节点', known.length ? String(bad) : '未测试'],
    ]) facts.append(detailLine(label, value));
    const quick = element('div', null, 'v1-core-quick');
    for (const [icon, label, act, enabled] of [['refresh-cw', '立即更新', 'refresh', source.enabled !== false], ['gauge', '测试节点', 'probe', true], ['arrow-right', '查看节点', 'nodes', true], ['x', '删除', 'delete', true]]) {
      const button = tile(icon, label, act, enabled);
      button.dataset.subAct = button.dataset.coreAct;
      button.dataset.subId = source.id;
      delete button.dataset.coreAct;
      delete button.dataset.coreId;
      quick.append(button);
    }
    panel.replaceChildren(head, facts, detailHeading('zap', '操作'), quick);
    const title = element('div', null, 'v1-panel-head');
    title.append(element('h2', '节点预览'), element('button', '查看全部', 'text-button'));
    title.lastChild.type = 'button';
    title.lastChild.dataset.subAct = 'nodes';
    title.lastChild.dataset.subId = source.id;
    const table = element('table', null, 'v1-table v1-sub-preview');
    const thead = element('thead');
    const headRow = element('tr');
    for (const label of ['节点名称', '类型', '延迟', '状态']) headRow.append(element('th', label));
    thead.append(headRow);
    const rows = element('tbody');
    const shown = nodes.slice(0, 5);
    if (!shown.length) rows.append((() => { const row = element('tr'); const cell = element('td', '这个订阅还没有节点'); cell.colSpan = 4; row.append(cell); return row; })());
    for (const node of shown) {
      const row = element('tr');
      const probe = node.probe;
      const latency = probe?.status === 'ok' ? `${probe.latency_ms} ms` : dash;
      const label = probeLabel(probe, false);
      row.append(element('td', node.title), element('td', protocolName(node.protocol)), element('td', latency), element('td', label));
      rows.append(row);
    }
    table.append(thead, rows);
    preview.replaceChildren(title, table);
  }
  async function runSourceAct(action, id) {
    const source = (overview?.sources || []).find(item => String(item.id) === String(id));
    if (action === 'select' && source) { selectedSourceId = source.id; renderSubscriptions(); return; }
    if (!source) return;
    if (action === 'nodes') {
      syncProbeGroups();
      byId('v1-node-sub-options').value = `source:${source.id}`;
      byId('v1-node-query').value = '';
      for (const field of ['v1-node-protocol', 'v1-node-source', 'v1-node-connectivity']) byId(field).value = 'all';
      nodeFilter = 'all';
      nodePageIndex = 0;
      location.hash = '#nodes';
      return;
    }
    if (action === 'edit') { openSourceSettings(source); return; }
    if (action === 'refresh') {
      note('正在更新 ' + source.name + '…');
      try { await call('/api/refresh', {id: source.id}); await refresh(); note('订阅已更新'); }
      catch (error) { note(error.message, {error: true}); }
      return;
    }
    if (action === 'probe') {
      note('正在测试 ' + source.name + ' 的节点…');
      try {
        await call('/api/batch/probe', {...nodeProbePayload(), groups: [`source:${source.id}`]});
        await refresh();
        note('已开始测试这个订阅的节点');
      } catch (error) { note(error.message, {error: true}); }
      return;
    }
    if (action === 'delete') {
      openConfirm('删除订阅', `删除「${source.name}」后，这个来源的节点会从列表里消失。`, '删除', async () => {
        try { await call('/api/delete', {id: source.id}); await refresh(); note('订阅已删除'); }
        catch (error) { note(error.message, {error: true}); }
      });
      return;
    }
    if (action === 'enabled' || action === 'auto') {
      const patch = action === 'enabled' ? {enabled: source.enabled === false} : {auto_update: source.auto_update === false};
      try { await call('/api/sources/settings', {id: source.id, ...patch}); await refresh(); }
      catch (error) { note(error.message, {error: true}); }
    }
  }
  function nodeKind(node) {
    return node.local ? 'local' : node.manual ? 'manual' : 'source';
  }
  function protocolName(protocol) {
    return {vless: 'VLESS', vmess: 'VMess', trojan: 'Trojan', hysteria2: 'Hysteria2', hy2: 'Hysteria2', tuic: 'TUIC', ss: 'Shadowsocks', shadowsocks: 'Shadowsocks'}[protocol] || (protocol || dash).toUpperCase();
  }
  function protocolLogo(protocol) {
    if (protocol === 'hysteria2' || protocol === 'hy2') return 'hysteria';
    if (protocol === 'tuic') return 'tuic';
    if (protocol === 'ss' || protocol === 'shadowsocks') return 'ss';
    return 'generic';
  }
  function nodeMark(node) {
    return node.core_id ? coreMark(node.core_id) : serviceMark(protocolLogo(node.protocol));
  }
  function nodeDomain(node) {
    if (!node.local) return '原始地址';
    return node.public_host || overview?.local_node?.public_host || '未设置';
  }
  function nodePort(node) {
    const local = overview?.local_node;
    if (!node.local || !local) return dash;
    if (Number.isInteger(node.public_port)) return node.public_port;
    if (Number.isInteger(node.port)) return node.port;
    if (node.protocol === 'hysteria2' || node.protocol === 'hy2') return local.quic?.hy2_port || dash;
    if (node.protocol === 'tuic') return local.quic?.tuic_port || dash;
    return local.port || dash;
  }
  function nodeUpstream(node) {
    if (!node.local) return node.enabled === false ? '已停用' : '作为上游可用';
    const labels = {direct: '本机直连', default: '直连', auto: '自动换线', local_proxy: '本机代理', fixed: '固定上游'};
    return labels[node.egress || overview?.local_node?.egress_mode] || '跟随本地节点';
  }
  function nodeState(node) {
    if (node.enabled === false) return ['已停用', 'stopped'];
    if (node.core_id || (node.local && overview?.local_node?.running)) return ['运行中', 'running'];
    if (node.local) return ['未运行', 'stopped'];
    return ['已启用', 'running'];
  }
  function nodeProbeState(probe) {
    const status = probe?.status || 'untested';
    return status === 'unsupported' ? 'unavailable' : status;
  }
  function probeLabel(probe, includeLatency = true) {
    const status = nodeProbeState(probe);
    if (status === 'ok') return includeLatency ? `可用 · ${probe.latency_ms} ms` : '可用';
    return {failed: '失败', unavailable: '不可用', untested: '未测试', testing: '测试中…'}[status] || '失败';
  }
  function nodeProbeText(probe) {
    return [probe?.mode === 'light' ? '轻量转发' : '', probe?.target, probeLabel(probe)].filter(Boolean).join(' · ');
  }
  function nodeVisible(node) {
    const group = byId('v1-node-sub-options').value || 'all';
    if (group !== 'all' && !node.groups?.includes(group)) return false;
    const query = byId('v1-node-query')?.value.trim().toLowerCase() || '';
    if (query && ![node.title, nodeDomain(node), nodeUpstream(node)].join(' ').toLowerCase().includes(query)) return false;
    const protocol = byId('v1-node-protocol')?.value || 'all';
    const nodeProtocol = ({hy2: 'hysteria2', ss: 'shadowsocks'}[node.protocol] || node.protocol);
    if (protocol !== 'all' && protocol !== nodeProtocol) return false;
    const source = byId('v1-node-source')?.value || 'all';
    if (source !== 'all' && nodeKind(node) !== source) return false;
    const connectivity = byId('v1-node-connectivity').value;
    if (connectivity !== 'all' && nodeProbeState(node.probe) !== connectivity) return false;
    if (nodeFilter === 'local') return !!node.local;
    if (nodeFilter === 'source') return nodeKind(node) === 'source';
    if (nodeFilter === 'tcp') return !udpProtocols.has(node.protocol);
    if (nodeFilter === 'udp') return udpProtocols.has(node.protocol);
    if (nodeFilter === 'disabled') return node.enabled === false;
    return true;
  }
  function renderNodeDetail(node) {
    const current = byId('v1-node-current');
    const panel = byId('v1-node-detail');
    if (!current || !panel) return;
    if (!node) {
      nodeDomainSecretNodeId = null;
      overviewSecrets.conceal?.('node-detail-domain', false);
      current.replaceChildren(element('p', '在左侧选择一个节点。'));
      panel.replaceChildren(element('p', '选择节点后，这里显示域名、端口和出口。'));
      return;
    }
    if (nodeDomainSecretNodeId !== node.id) {
      overviewSecrets.conceal?.('node-detail-domain', false);
      nodeDomainSecretNodeId = node.id;
    }
    const [label, state] = nodeState(node);
    const head = element('div', null, 'v1-node-current-body');
    head.append(nodeMark(node));
    const title = element('div');
    title.append(element('strong', node.title), element('small', protocolName(node.protocol) + ' / ' + ({local: '本地发布', source: '订阅导入', manual: '手动导入'}[nodeKind(node)])));
    const badge = element('span', label, 'v1-status');
    badge.dataset.state = state;
    current.replaceChildren(head);
    head.append(title, badge);
    const facts = element('dl', null, 'v1-definitions');
    const kindLabel = {local: '本地发布节点', source: '订阅导入', manual: '手动导入'}[nodeKind(node)];
    const port = nodePort(node);
    const transport = udpProtocols.has(node.protocol) ? 'UDP' : node.local ? 'TCP' : dash;
    facts.append(
      detailLine('节点类型', kindLabel),
      detailLine('协议类型', protocolName(node.protocol)),
      detailLine('统一域名', nodeDomain(node), {id: 'node-detail-domain', label: '统一域名', before: true}),
      detailLine('入口端口', port === dash ? dash : transport + '  ' + port),
      detailLine('上游出口', nodeUpstream(node)),
      detailLine('连通性', nodeProbeText(node.probe)),
      detailLine('最终出口策略', node.core_id ? nodeUpstream(node) : node.local ? (overview?.local_node?.egress_protocol_info?.detail || '跟随本地节点') : '不经本机转发'),
      detailLine('今日流量', node.local && node.core_id ? bytes(publisherBytes(node.profile_id || node.core_id, node.port)) : '未单独统计'),
    );
    if (node.probe?.detail && !['ok', 'testing'].includes(node.probe.status)) {
      facts.append(detailLine('测试详情', node.probe.detail));
    }
    facts.append(
      detailLine('TCP 代理', node.probe?.status === 'ok' ? '连通性测试通过' : '待实测'),
      detailLine('UDP 中继', ['http', 'https'].includes(node.protocol) ? '当前适配不支持' : '待实测'),
    );
    if (node.local) {
      facts.append(detailLine('业务加密 DNS', securityLabels[node.security?.dns || 'NOT_CONFIGURED']));
      facts.append(detailLine('严格出口 / 客户端 WebRTC', '未支持 / 待客户端实测'));
    } else if (['socks', 'socks5'].includes(node.protocol)) {
      facts.append(detailLine('安全策略上游', '兼容模式要求固定 IP SOCKS5。双栈出口需实测。'));
    }
    const quick = element('div', null, 'v1-core-quick');
    quick.append(
      tile('pencil', '编辑节点', 'edit', true),
      tile('gauge', node.probe?.status === 'testing' ? '测试中…' : '测试连通', 'probe', node.probe?.status !== 'testing'),
      tile('scroll-text', '查看日志', 'logs', true),
      tile('download', '导出节点', 'export', node.local),
      tile('copy', '复制节点链接', 'copy-uri', node.local),
    );
    quick.querySelectorAll('button').forEach(button => retargetNodeAction(button, node.id));
    const caps = element('div', null, 'v1-chips');
    const names = [];
    if (udpProtocols.has(node.protocol)) names.push('QUIC');
    if (node.local && overview?.local_node?.public_host) names.push('统一域名映射');
    for (const name of names) caps.append(element('span', name, 'v1-chip'));
    panel.replaceChildren(detailHeading('layout-grid', '节点信息'), facts, detailHeading('zap', '快捷操作'), quick);
    if (names.length) panel.append(detailHeading('shield', '协议与接入'), caps);
  }
  function syncNodeProbeControls(matched) {
    const job = overview?.jobs?.probe;
    const busy = nodeProbePending || job?.running;
    const selected = document.querySelector('[data-node-batch="probe"]');
    selected.textContent = nodeChecks.size ? `测试所选节点（${nodeChecks.size}）` : '测试所选节点';
    selected.disabled = busy || !nodeChecks.size;
    document.querySelector('[data-node-batch="probe-filtered"]').disabled = busy || !matched.length;
    const stop = byId('v1-node-probe-stop');
    stop.hidden = !job?.running;
    stop.disabled = nodeProbePending || !!job?.stopping;
    const progress = byId('v1-node-probe-progress');
    const state = job?.stopping ? '正在停止' : job?.running ? '测试中' : job?.cancelled ? '已停止' : '上次';
    const label = job?.total ? (job.running || job.cancelled
      ? `${job.target} · ${state} ${job.done}/${job.total}`
      : `上次 ${job.target} · 通过 ${job.done - job.failed}/${job.total}`) : '';
    if (progress.textContent !== label) progress.textContent = label;
    progress.title = job?.total ? `${job.mode === 'light' ? '轻量转发测试' : '完整测试'} · ${job.target} · 已测 ${job.done}/${job.total} · 通过 ${job.done - job.failed} · 未通过或跳过 ${job.failed}` : '';
    const allBtn = document.querySelector('[data-node-batch="probe-all"]');
    const chosen = selectedProbeGroups();
    allBtn.disabled = busy || (chosen === null ? !(overview?.nodes || []).length : !chosen.length);
  }
  function nodeProbePayload() {
    return {mode: byId('v1-node-probe-mode').value, ...probeTargetPayload()};
  }
  function syncNodeProbeMode() {
    const mode = byId('v1-node-probe-mode');
    const hint = mode.value === 'light'
      ? '通过节点访问选定网站。轻量客户端支持 VLESS、Trojan 的 TCP / WebSocket，以及 Hysteria 2、HTTP(S) 和 SOCKS5。'
      : '通过节点访问选定网站。缺少客户端时，请在内核管理下载对应内核。';
    byId('v1-node-probe-hint').textContent = hint;
  }
  byId('v1-node-probe-settings-open').addEventListener('click', () => {
    closeMenus();
    byId('v1-node-probe-settings').showModal();
  });
  byId('v1-node-probe-mode').addEventListener('change', syncNodeProbeMode);
  syncNodeProbeMode();
  function probeGroupItems() {
    return (overview?.sources || []).map(source => ({id: `source:${source.id}`, name: source.name}));
  }
  function selectedProbeGroups() {
    const selected = byId('v1-node-sub-options')?.value || 'all';
    if (selected === 'all') return null;
    const groups = probeGroupItems();
    return groups.some(group => group.id === selected) ? [selected] : [];
  }
  function syncProbeGroups() {
    const select = byId('v1-node-sub-options');
    if (!select) return;
    const groups = probeGroupItems();
    const previous = select.value || 'all';
    const options = [{id: 'all', name: '全部'}, ...groups];
    const signature = options.map(group => group.id + '\0' + group.name).join('\n');
    if (select.dataset.signature !== signature) {
      select.replaceChildren(...options.map(group => {
        const option = element('option', '订阅：' + group.name);
        option.value = group.id;
        return option;
      }));
      select.dataset.signature = signature;
    }
    select.value = options.some(group => group.id === previous) ? previous : 'all';
  }
  async function runNodeBatchProbe(action) {
    const nodes = overview?.nodes || [];
    const matched = nodes.filter(nodeVisible);
    if (nodeProbePending) return;
    const stop = action === 'stop';
    let body;
    if (stop) body = {};
    else if (action === 'probe-all') {
      syncProbeGroups();
      const groups = selectedProbeGroups();
      if (groups === null) {
        if (!(overview?.nodes || []).length) { note('暂无节点可测试'); return; }
        body = {...nodeProbePayload()};
      } else {
        if (!groups.length) { note('暂无订阅组可测试'); return; }
        body = {...nodeProbePayload(), groups};
      }
    } else {
      const picked = action === 'probe' ? nodes.filter(node => nodeChecks.has(node.id)) : matched;
      if (!picked.length) return;
      body = {...nodeProbePayload(), node_ids: picked.map(node => node.id)};
    }
    nodeProbePending = true;
    syncNodeProbeControls(matched);
    try {
      if (stop) {
        await call('/api/batch/probe/stop', {});
        note('停止请求已发送，正在结束当前任务');
      } else {
        await call('/api/batch/probe', body);
        note(action === 'probe-all' ? '已开始测试所选订阅组的节点' : '已开始测试节点');
      }
      await refresh();
    } catch (error) {
      note(error.message, {error: true});
    } finally {
      nodeProbePending = false;
      syncNodeProbeControls((overview?.nodes || []).filter(nodeVisible));
    }
  }
  function syncNodeCheckAll() {
    const all = byId('v1-node-check-all');
    const boxes = [...byId('v1-node-rows').querySelectorAll('[data-node-check]')];
    const checked = boxes.filter(box => box.checked).length;
    all.disabled = !boxes.length;
    all.checked = !!boxes.length && checked === boxes.length;
    all.indeterminate = checked > 0 && checked < boxes.length;
  }
  function renderNodes() {
    const rows = byId('v1-node-rows');
    if (!rows) return;
    const nodes = overview?.nodes || [];
    const nodeIds = new Set(nodes.map(node => node.id));
    for (const id of nodeChecks) if (!nodeIds.has(id)) nodeChecks.delete(id);
    const counts = {
      all: ['全部节点', nodes.length],
      local: ['本地发布', nodes.filter(node => node.local).length],
      source: ['订阅导入', nodes.filter(node => nodeKind(node) === 'source').length],
      tcp: ['TCP 节点', nodes.filter(node => !udpProtocols.has(node.protocol)).length],
      udp: ['UDP/QUIC 节点', nodes.filter(node => udpProtocols.has(node.protocol)).length],
      disabled: ['已停用', nodes.filter(node => node.enabled === false).length],
    };
    for (const button of document.querySelectorAll('[data-node-filter]')) {
      const [label, count] = counts[button.dataset.nodeFilter];
      button.textContent = `${label} (${count})`;
      button.setAttribute('aria-pressed', String(button.dataset.nodeFilter === nodeFilter));
    }
    syncProbeGroups();
    const matched = nodes.filter(nodeVisible);
    syncNodeProbeControls(matched);
    const pages = Math.max(1, Math.ceil(matched.length / nodePageSize));
    if (nodePageIndex >= pages) nodePageIndex = pages - 1;
    if (nodePageIndex < 0) nodePageIndex = 0;
    const visible = matched.slice(nodePageIndex * nodePageSize, (nodePageIndex + 1) * nodePageSize);
    if (!matched.some(node => node.id === selectedNodeId)) selectedNodeId = matched[0]?.id || null;
    const kindText = {local: '本地发布', source: '订阅导入', manual: '手动导入'};
    const sourceText = {local: '本', source: '订', manual: '手'};
    rows.replaceChildren(...(visible.length ? visible.map(node => {
      const row = element('tr');
      row.dataset.nodePick = node.id;
      row.classList.toggle('is-selected', node.id === selectedNodeId);
      const check = element('td');
      const label = element('label', null, 'v1-check');
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = nodeChecks.has(node.id);
      input.dataset.nodeCheck = node.id;
      input.setAttribute('aria-label', '选择 ' + node.title);
      label.append(input);
      check.append(label);
      const name = element('th');
      name.scope = 'row';
      const pick = element('button', null, 'v1-core-pick');
      pick.type = 'button';
      pick.dataset.nodeAct = 'select';
      pick.dataset.nodeId = node.id;
      const nameLine = element('span', null, 'v1-node-title');
      nameLine.append(nodeMark(node), element('strong', node.title));
      pick.title = node.title;
      pick.append(nameLine);
      name.append(pick);
      const status = element('td');
      const result = element('div', probeLabel(node.probe, false), 'v1-node-probe-result');
      if (node.probe) result.dataset.status = node.probe.status;
      if (node.probe?.status === 'ok') {
        result.append(element('span', `${node.probe.latency_ms} ms`, 'v1-node-probe-latency'));
      }
      status.append(result);
      const enabled = element('td');
      const knob = element('button', null, 'v1-switch');
      knob.type = 'button';
      knob.dataset.nodeAct = 'toggle';
      knob.dataset.nodeId = node.id;
      knob.dataset.on = String(node.enabled !== false);
      knob.setAttribute('aria-pressed', String(node.enabled !== false));
      knob.setAttribute('aria-label', (node.enabled === false ? '启用 ' : '停用 ') + node.title);
      enabled.append(knob);
      const ops = element('td');
      const box = element('div', null, 'v1-core-ops');
      const test = nodeIconButton('probe', '测试 ' + node.title, node.id);
      test.disabled = node.probe?.status === 'testing';
      box.append(nodeIconButton('edit', '编辑 ' + node.title, node.id), test, nodeIconButton('logs', '日志 ' + node.title, node.id));
      ops.append(box);
      const proto = element('td');
      const pill = element('span', protocolName(node.protocol), 'v1-proto');
      pill.dataset.proto = node.protocol === 'hy2' ? 'hysteria2' : node.protocol === 'ss' ? 'shadowsocks' : node.protocol;
      proto.append(pill);
      row.append(check, name, element('td', kindText[nodeKind(node)]), element('td', sourceText[nodeKind(node)]), proto, element('td', udpProtocols.has(node.protocol) ? 'UDP+QUIC' : node.local ? 'TCP/TLS' : dash), element('td', nodeDomain(node)), element('td', String(nodePort(node))), element('td', nodeUpstream(node)), status, enabled, ops);
      return row;
    }) : [(() => { const row = element('tr'); const cell = element('td', overview ? '没有符合当前筛选的节点' : '正在读取节点…'); cell.colSpan = 12; row.append(cell); return row; })()]));
    byId('v1-node-count-label').textContent = `共 ${matched.length} 条记录`;
    const pager = byId('v1-node-pager');
    pager.replaceChildren();
    const previous = element('button', '‹', 'secondary');
    previous.type = 'button';
    previous.disabled = nodePageIndex === 0;
    previous.dataset.nodePage = String(nodePageIndex - 1);
    previous.setAttribute('aria-label', '上一页');
    pager.append(previous);
    const pageStart = Math.max(0, Math.min(nodePageIndex - 2, pages - 5));
    for (let index = pageStart; index < pages && index < pageStart + 5; index += 1) {
      const button = element('button', String(index + 1), 'secondary');
      button.type = 'button';
      button.dataset.nodePage = String(index);
      button.setAttribute('aria-pressed', String(index === nodePageIndex));
      pager.append(button);
    }
    const next = element('button', '›', 'secondary');
    next.type = 'button';
    next.disabled = nodePageIndex >= pages - 1;
    next.dataset.nodePage = String(nodePageIndex + 1);
    next.setAttribute('aria-label', '下一页');
    pager.append(next);
    syncNodeCheckAll();
    renderNodeDetail(nodes.find(node => node.id === selectedNodeId));
  }
  function retargetNodeAction(button, id) {
    button.dataset.nodeAct = button.dataset.coreAct;
    button.dataset.nodeId = id;
    delete button.dataset.coreAct;
    delete button.dataset.coreId;
  }
  const exportNodeDialog = byId('v1-node-export');
  const exportNodeForm = byId('v1-node-export-form');
  const exportNodeStatus = byId('v1-node-export-status');
  const exportNodeButton = exportNodeForm.querySelector('button[type="submit"]');
  const exportQrButton = byId('v1-node-show-qr');
  const exportQrCanvas = byId('v1-node-export-qr');
  let exportSelection;
  byId('v1-node-export-close').addEventListener('click', () => exportNodeDialog.close());
  exportNodeDialog.addEventListener('close', () => {
    exportQrCanvas.hidden = true;
    exportQrCanvas.width = 0;
  });
  function drawNodeQr(uri) {
    const previousEncoding = qrcode.stringToBytes;
    try {
      qrcode.stringToBytes = qrcode.stringToBytesFuncs['UTF-8'];
      const qr = qrcode(0, 'M');
      qr.addData(uri);
      qr.make();
      const unit = 4;
      const border = 4 * unit;
      exportQrCanvas.width = exportQrCanvas.height = qr.getModuleCount() * unit + border * 2;
      const context = exportQrCanvas.getContext('2d');
      context.fillStyle = 'white';
      context.fillRect(0, 0, exportQrCanvas.width, exportQrCanvas.height);
      context.translate(border, border);
      qr.renderTo2dContext(context, unit);
      exportQrCanvas.hidden = false;
    } finally {
      qrcode.stringToBytes = previousEncoding;
    }
  }
  async function exportNode(showQr) {
    if (exportNodeButton.disabled) return;
    const selection = exportSelection;
    const format = showQr ? 'uri' : exportNodeForm.elements.format.value;
    if (showQr) {
      exportQrCanvas.hidden = true;
      exportQrCanvas.width = 0;
    }
    exportNodeButton.disabled = exportQrButton.disabled = true;
    exportNodeStatus.textContent = showQr ? '正在生成二维码…' : '正在生成文件…';
    try {
      const result = await call('/api/v1/nodes/export?' + new URLSearchParams({id: selection.id, format}));
      if (!exportNodeDialog.open || exportSelection !== selection) return;
      if (showQr) {
        try { drawNodeQr(result.uri); }
        catch { throw new Error('无法生成二维码，可以下载节点链接后手动导入。'); }
        exportNodeStatus.textContent = '用代理客户端扫描二维码导入节点。';
        exportQrCanvas.scrollIntoView({block: 'nearest'});
      } else {
        const content = format === 'uri' ? result.uri + '\n' : result.content;
        saveBlob(new Blob([content], {type: 'text/plain;charset=utf-8'}), result.filename || 'hopworld-node.txt');
        exportNodeDialog.close();
      }
    } catch (error) {
      if (exportNodeDialog.open && exportSelection === selection) exportNodeStatus.textContent = error.message;
    } finally {
      if (exportSelection === selection) exportNodeButton.disabled = exportQrButton.disabled = false;
    }
  }
  exportQrButton.addEventListener('click', () => exportNode(true));
  exportNodeForm.addEventListener('submit', event => {
    event.preventDefault();
    void exportNode(false);
  });
  const upstreamNameDialog = byId('v1-upstream-name');
  const upstreamNameForm = byId('v1-upstream-name-form');
  const upstreamNameStatus = byId('v1-upstream-name-status');
  const upstreamNameSave = upstreamNameForm.querySelector('button[type="submit"]');
  const upstreamRemove = byId('v1-upstream-remove');
  let upstreamSelection;
  byId('v1-upstream-name-close').addEventListener('click', () => upstreamNameDialog.close());
  upstreamNameForm.addEventListener('submit', async event => {
    event.preventDefault();
    const selection = upstreamSelection;
    upstreamNameSave.disabled = upstreamRemove.disabled = true;
    upstreamNameStatus.hidden = true;
    try {
      await call('/api/v1/nodes/name', {id: selection.id, name: upstreamNameForm.elements.alias.value});
      if (upstreamSelection === selection) upstreamNameDialog.close();
      await refresh();
      note('节点名称已保存');
    } catch (error) {
      if (upstreamSelection === selection && upstreamNameDialog.open) {
        upstreamNameStatus.textContent = error.message;
        upstreamNameStatus.hidden = false;
      } else note(error.message, {error: true});
    } finally {
      if (upstreamSelection === selection) upstreamNameSave.disabled = upstreamRemove.disabled = false;
    }
  });
  upstreamRemove.addEventListener('click', () => {
    const {id} = upstreamSelection;
    upstreamNameDialog.close();
    openConfirm('移除手动节点', '从手动导入列表中移除此节点？', '移除', async () => {
      try { await call('/api/manual/delete', {id}); await refresh(); note('手动节点已移除'); }
      catch (error) { note(error.message, {error: true}); }
    });
  });
  function probeOutcome(id) {
    return new Promise(resolve => {
      const started = Date.now();
      const tick = () => {
        const current = (overview?.nodes || []).find(item => item.id === id)?.probe;
        if (current && current.status !== 'testing') { resolve(current); return; }
        if (Date.now() - started > 20000) { resolve(current || {status: 'failed', detail: '测试没有在时限内结束'}); return; }
        setTimeout(tick, 400);
      };
      tick();
    });
  }
  async function runNodeAct(action, id) {
    const node = (overview?.nodes || []).find(item => item.id === id);
    if (action === 'select' && node) { selectedNodeId = node.id; renderNodes(); return; }
    if (action === 'edit') {
      if (!node) return;
      if (node.local) {
        const protocol = node.protocol === 'hy2' ? 'hysteria2' : node.protocol;
        const coreId = node.core_id || publicPortRows.find(row =>
          row.legacy && row.nodeProtocol === protocol && row.port === node.port)?.core_id;
        const core = knownCores().find(item => item.id === coreId);
        if (!core) { note('未找到此节点的发布内核，请刷新节点列表', {error: true}); return; }
        await openCoreProfile(core, {source: coreId, port: node.port, protocol}, node.profile_id);
        return;
      }
      upstreamSelection = {id: node.id};
      upstreamNameForm.elements.alias.value = node.custom_name || '';
      upstreamNameStatus.hidden = true;
      upstreamNameSave.disabled = upstreamRemove.disabled = false;
      upstreamRemove.hidden = !node.manual;
      upstreamNameDialog.showModal();
      return;
    }
    if (action === 'probe' && node) {
      note('正在测试 ' + node.title + '…');
      try {
        await call('/api/probe', {id: node.id, ...nodeProbePayload()});
        await refresh();
        const result = await probeOutcome(node.id);
        const failed = result?.status !== 'ok';
        if (result?.status === 'unavailable') {
          openConfirm('转发测试缺少客户端', result.detail, '前往内核管理', () => { location.hash = '#cores'; });
          return;
        }
        note(failed ? (result?.detail || '测试失败') : `${node.title} · ${probeLabel(result)}`, {error: failed});
      } catch (error) { note(error.message, {error: true}); }
      return;
    }
    if (action === 'logs' && node) {
      if (!node.local) { openConfirm('没有这个节点的日志', '导入的节点不在本机运行，没有单独日志。'); return; }
      const core = knownCores().find(item => item.id === node.core_id);
      if (core) void askLogs(core);
      else openConfirm('查看本地节点日志', '可在内核管理查看对应内核的日志。', '去内核管理', () => { location.hash = '#cores'; });
      return;
    }
    if (action === 'export' && node?.local) {
      exportSelection = {id};
      exportNodeForm.reset();
      exportNodeButton.disabled = exportQrButton.disabled = false;
      for (const option of exportNodeForm.elements.format.options) {
        option.hidden = option.disabled = option.value !== 'uri' && !node.export_formats?.includes(option.value);
      }
      exportNodeStatus.textContent = '';
      exportNodeDialog.showModal();
      return;
    }
    if (action === 'copy-uri' && node?.local) {
      try {
        const {uri} = await call('/api/v1/nodes/export?id=' + encodeURIComponent(id));
        await copyText(uri, '已复制节点链接');
      } catch (error) { note(error.message, {error: true}); }
      return;
    }
    if (action === 'toggle' && node) {
      try {
        await call('/api/node', {id: node.id, enabled: node.enabled === false});
        await refresh();
        note(node.enabled === false ? '已加入订阅' : '已从订阅移除');
      } catch (error) { note(error.message, {error: true}); }
    }
  }

  function applyFilters() {
    const query = byId('v1-node-search').value.trim().toLowerCase();
    const kind = byId('v1-node-type').value;
    for (const row of byId('nodes').querySelectorAll('[data-node-title]')) {
      const {nodeTitle, nodeKind, nodeEnabled} = row.dataset;
      row.hidden = !nodeTitle.toLowerCase().includes(query) ||
        !(kind === 'all' || kind === nodeKind || (kind === 'disabled' && nodeEnabled === 'false'));
    }
    const source = byId('v1-source-search').value.trim().toLowerCase();
    const sourceState = byId('v1-source-state').value;
    for (const row of byId('sources').querySelectorAll('[data-source-name]')) {
      const matchesState = sourceState === 'all' || (sourceState === 'enabled') === (row.dataset.sourceEnabled === 'true');
      row.hidden = !row.dataset.sourceName.toLowerCase().includes(source) || !matchesState;
    }
  }
  for (const id of ['v1-node-search', 'v1-source-search']) byId(id).addEventListener('input', applyFilters);
  for (const id of ['v1-node-type', 'v1-source-state']) byId(id).addEventListener('change', applyFilters);
  const compactNav = window.matchMedia('(max-width: 710px)');
  const menuToggle = byId('v1-menu-toggle');
  const navigation = byId('v1-navigation');
  function setMenu(open) {
    menuToggle.hidden = !compactNav.matches;
    menuToggle.setAttribute('aria-expanded', String(compactNav.matches && open));
    navigation.hidden = compactNav.matches && !open;
  }
  menuToggle.addEventListener('click', () => setMenu(menuToggle.getAttribute('aria-expanded') !== 'true'));
  compactNav.addEventListener('change', () => setMenu(false));
  navigation.addEventListener('click', event => {
    if (compactNav.matches && event.target.closest('a')) {
      setMenu(false);
      menuToggle.focus({preventScroll: true});
    }
  });
  navigation.addEventListener('keydown', event => {
    if (event.key === 'Escape' && compactNav.matches) {
      setMenu(false);
      menuToggle.focus();
    }
  });

  function route() {
    closeMenus();
    const requested = location.hash.slice(1);
    const next = requested === 'ikev2-monitor' ? 'public' : Object.hasOwn(pages, requested) ? requested : 'overview';
    if (page !== next && (page === 'overview' || next === 'overview' || page === 'nodes' || next === 'nodes')) hideOverviewSecrets();
    if (page !== next && page === 'public' && relayEditing) setRelayEditing(false);
    page = next;
    if (page === 'settings') {
      showSettingsTab(showMaintenanceOnRoute ? 'maintenance' : 'base');
      showMaintenanceOnRoute = false;
    }
    for (const view of document.querySelectorAll('[data-v1-view]')) view.hidden = view.dataset.v1View !== page;
    for (const link of document.querySelectorAll('[data-v1-link]')) {
      if (link.dataset.v1Link === page) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
    const [title, subtitle, actions = []] = pages[page];
    document.title = title + ' · ' + consoleName + ' V1';
    byId('v1-title').textContent = title;
    byId('v1-menu-label').textContent = title;
    setMenu(false);
    byId('v1-subtitle').textContent = subtitle;
    byId('v1-heading-actions').replaceChildren(...actions.map(([label, target], index) => {
      const button = element('button', label, index === actions.length - 1 ? 'primary' : 'secondary');
      button.type = 'button';
      button.dataset.v1Action = target;
      return button;
    }), ...(page === 'cores' ? coreHeadingButtons() : []));
    byId('v1-heading-actions').hidden = page === 'traffic';
    byId('v1-traffic-ranges').hidden = page !== 'traffic';
    if (page === 'traffic') renderTrafficDashboard(activeTraffic());
    else byId('v1-traffic-dates').close();
    if (page === 'traffic' || page === 'overview') scheduleTrafficPoll(0);
    if (page === 'cores' || page === 'nodes') {
      if (page === 'cores') renderCores();
      else renderNodes();
      void call('/api/v1/traffic').then(renderTraffic).catch(() => {});
    }
    if (page === 'subscriptions') renderSubscriptions();
    if (page === 'public') {
      void call('/api/v1/traffic').then(renderTraffic).catch(() => {});
      void refreshRelay();
    }
    if (requested === 'ikev2-monitor') byId('ikev2-monitor').scrollIntoView();
    else window.scrollTo(0, 0);
    requestAnimationFrame(renderCharts);
  }

  let fetchingSystem = false;
  async function loadSystem(showError = false) {
    if (fetchingSystem) return;
    fetchingSystem = true;
    try { renderSystem(await call('/api/v1/system')); }
    catch (error) { if (showError) note(error.message, {error: true}); }
    finally { fetchingSystem = false; }
  }
  const trafficRateSelect = byId('v1-traffic-refresh');
  let trafficTimer;
  let trafficLoading = false;
  function scheduleTrafficPoll(delay) {
    clearTimeout(trafficTimer);
    trafficTimer = setTimeout(pollTraffic, delay);
  }
  async function pollTraffic() {
    if (trafficLoading || document.hidden || !['traffic', 'overview'].includes(page)) return;
    const detailed = page === 'traffic';
    trafficLoading = true;
    try {
      const value = await call('/api/v1/traffic' + (detailed ? '?dashboard=1' : ''));
      if (detailed || page !== 'traffic') renderTraffic(value);
    } catch {
      renderTraffic({...traffic, state: 'unavailable', up_bps: null, down_bps: null});
    } finally {
      trafficLoading = false;
      const delay = detailed ? Number(trafficRateSelect.value) : page === 'traffic' ? 0 : 4000;
      scheduleTrafficPoll(delay);
    }
  }
  trafficRateSelect.addEventListener('change', () => scheduleTrafficPoll(0));
  trafficScope.addEventListener('change', () => {
    setTrafficRange('live');
    renderTrafficDashboard(activeTraffic());
    renderCharts();
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) hideOverviewSecrets();
    else scheduleTrafficPoll(0);
  });

  const datesDialog = byId('v1-traffic-dates');
  const datesForm = byId('v1-traffic-date-form');
  const datesStatus = byId('v1-traffic-date-status');
  for (const button of document.querySelectorAll('[data-traffic-range]')) {
    button.addEventListener('click', () => {
      if (button.dataset.trafficRange !== 'custom') {
        setTrafficRange(button.dataset.trafficRange);
        return;
      }
      const daily = activeTraffic().daily;
      const today = daily?.at(-1)?.date || new Date().toLocaleDateString('sv-SE');
      datesForm.elements.start.value ||= daily?.[0]?.date || today;
      datesForm.elements.end.value ||= today;
      datesStatus.textContent = '按服务器日期统计，最多选择 366 天。';
      datesDialog.showModal();
    });
  }
  byId('v1-traffic-date-close').addEventListener('click', () => datesDialog.close());
  datesForm.addEventListener('submit', async event => {
    event.preventDefault();
    const query = new URLSearchParams(new FormData(datesForm));
    const submit = datesForm.querySelector('button[type="submit"]');
    submit.disabled = true;
    datesStatus.textContent = '正在读取所选时段…';
    try {
      const result = await call('/api/v1/traffic?' + query);
      if (!datesDialog.open) return;
      customTraffic = trafficScope.value === 'legacy' ? result.legacy_daily : result.daily;
      datesDialog.close();
      setTrafficRange('custom');
    } catch (error) {
      datesStatus.textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });
  for (const control of seriesToggles) {
    control.addEventListener('change', () => {
      if (!seriesToggles.some(item => item.checked)) control.checked = true;
      renderTrafficChart();
    });
  }
  const followTrafficPointer = event => {
    if (!trafficGraph || !selectedTrafficSamples().length) return;
    const index = Math.floor((event.offsetX - trafficGraph.left) / trafficGraph.step);
    const point = index >= 0 && index < selectedTrafficSamples().length ? index : null;
    if (point === trafficIndex) return;
    trafficIndex = point;
    renderTrafficChart();
  };
  const hideTrafficPoint = () => {
    if (trafficIndex === null) return;
    trafficIndex = null;
    renderTrafficChart();
  };
  trafficCanvas.addEventListener('pointermove', followTrafficPointer);
  trafficCanvas.addEventListener('pointerdown', followTrafficPointer);
  trafficCanvas.addEventListener('pointerleave', hideTrafficPoint);
  trafficCanvas.addEventListener('blur', hideTrafficPoint);
  trafficCanvas.addEventListener('keydown', event => {
    if (event.key === 'Escape') { hideTrafficPoint(); return; }
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    const points = selectedTrafficSamples().flatMap((point, index) => Number.isFinite(point.total_bytes) ? [index] : []);
    if (!points.length) return;
    event.preventDefault();
    trafficIndex = event.key === 'ArrowRight'
      ? points.find(index => index > (trafficIndex ?? -1)) ?? points.at(-1)
      : points.findLast(index => index < (trafficIndex ?? Infinity)) ?? points[0];
    renderTrafficChart();
  });

  for (const button of document.querySelectorAll('[data-home-range]')) {
    button.addEventListener('click', () => {
      homeRange = button.dataset.homeRange;
      for (const item of document.querySelectorAll('[data-home-range]')) item.setAttribute('aria-pressed', String(item === button));
      renderCharts();
    });
  }
  if (window.hopworldStatus) renderManagement(window.hopworldStatus);
  window.addEventListener('hopworld:status', event => renderManagement(event.detail));
  window.addEventListener('hashchange', route);
  window.addEventListener('resize', renderCharts);
  document.addEventListener('click', event => {
    const trigger = event.target.closest('[data-v1-action]');
    if (trigger?.dataset.v1Action === 'publish-core-open') void openPublishedCorePicker();
    else if (trigger) byId(trigger.dataset.v1Action)?.click();
  });
  byId('toast-stop')?.addEventListener('click', () => { void stopInstall(); });
  byId('toast-pause')?.addEventListener('click', () => { void toggleInstallPause(); });
  byId('v1-system-refresh').addEventListener('click', () => loadSystem(true));
  byId('v1-core-rows').addEventListener('change', event => {
    const box = event.target.closest('[data-core-check]');
    if (!box) return;
    if (box.checked) coreChecks.add(box.dataset.coreCheck);
    else coreChecks.delete(box.dataset.coreCheck);
    syncCheckAll();
  });
  byId('v1-node-rows').addEventListener('change', event => {
    const box = event.target.closest('[data-node-check]');
    if (!box) return;
    if (box.checked) nodeChecks.add(box.dataset.nodeCheck);
    else nodeChecks.delete(box.dataset.nodeCheck);
    syncNodeCheckAll();
    syncNodeProbeControls((overview?.nodes || []).filter(nodeVisible));
  });
  byId('v1-node-check-all').addEventListener('change', () => {
    const on = byId('v1-node-check-all').checked;
    for (const box of byId('v1-node-rows').querySelectorAll('[data-node-check]')) {
      box.checked = on;
      if (on) nodeChecks.add(box.dataset.nodeCheck);
      else nodeChecks.delete(box.dataset.nodeCheck);
    }
    syncNodeCheckAll();
    syncNodeProbeControls((overview?.nodes || []).filter(nodeVisible));
  });
  byId('v1-node-probe-stop').addEventListener('click', () => { void runNodeBatchProbe('stop'); });
  byId('v1-node-query').addEventListener('input', () => { nodePageIndex = 0; renderNodes(); });
  byId('v1-sub-query').addEventListener('input', () => renderSubscriptions());
  for (const id of ['v1-node-protocol', 'v1-node-source', 'v1-node-connectivity', 'v1-node-sub-options']) {
    const select = byId(id);
    select.addEventListener('change', () => { nodePageIndex = 0; renderNodes(); });
    select.setAttribute('aria-haspopup', 'listbox');
    select.setAttribute('aria-expanded', 'false');
  }
  byId('v1-core-check-all').addEventListener('change', () => {
    const on = byId('v1-core-check-all').checked;
    for (const box of byId('v1-core-rows').querySelectorAll('[data-core-check]')) {
      box.checked = on;
      if (on) coreChecks.add(box.dataset.coreCheck);
      else coreChecks.delete(box.dataset.coreCheck);
    }
  });
  document.addEventListener('click', event => {
    if (!event.target.closest('#v1-global-probe')) byId('v1-global-probe').open = false;
    const installItem = event.target.closest('[data-core-install]');
    if (installItem) {
      closeMenus();
      const id = installItem.dataset.coreInstall;
      askUpdate((system?.cores || []).find(item => item.id === id) || {id, name: coreCatalog[id]?.name || '内核', installed: false});
      return;
    }
    const batchItem = event.target.closest('[data-core-batch]');
    if (batchItem) { closeMenus(); runBatch(batchItem.dataset.coreBatch); return; }
    const act = event.target.closest('[data-core-act]');
    if (act) { runCoreAct(act.dataset.coreAct, act.dataset.coreId); return; }
    const filter = event.target.closest('[data-core-filter]');
    if (filter) { coreFilter = filter.dataset.coreFilter; renderCores(); return; }
    const nodeFilterButton = event.target.closest('[data-node-filter]');
    if (nodeFilterButton) { nodeFilter = nodeFilterButton.dataset.nodeFilter; nodePageIndex = 0; renderNodes(); return; }
    const subFilterButton = event.target.closest('[data-sub-filter]');
    if (subFilterButton) { subFilter = subFilterButton.dataset.subFilter; renderSubscriptions(); return; }
    const subAct = event.target.closest('[data-sub-act]');
    if (subAct) { void runSourceAct(subAct.dataset.subAct, subAct.dataset.subId); return; }
    const subRow = event.target.closest('#v1-sub-rows tr');
    if (subRow?.dataset.subPick && !event.target.closest('input, button, a, label')) {
      const picked = (overview?.sources || []).find(item => String(item.id) === subRow.dataset.subPick);
      if (picked) { selectedSourceId = picked.id; renderSubscriptions(); }
      return;
    }
    const nodeAct = event.target.closest('[data-node-act]');
    if (nodeAct) { void runNodeAct(nodeAct.dataset.nodeAct, nodeAct.dataset.nodeId); return; }
    const nodeRow = event.target.closest('#v1-node-rows tr');
    if (nodeRow?.dataset.nodePick && !event.target.closest('input, button, a, label')) {
      selectedNodeId = nodeRow.dataset.nodePick;
      renderNodes();
      return;
    }
    const nodePageButton = event.target.closest('[data-node-page]');
    if (nodePageButton && !nodePageButton.disabled) { nodePageIndex = Number(nodePageButton.dataset.nodePage); renderNodes(); return; }
    const nodeBatch = event.target.closest('[data-node-batch]');
    if (nodeBatch) {
      closeMenus();
      if (nodeBatch.dataset.nodeBatch !== 'copy') { void runNodeBatchProbe(nodeBatch.dataset.nodeBatch); return; }
      const picked = (overview?.nodes || []).filter(node => nodeChecks.has(node.id));
      if (!picked.length) { note('先勾选节点'); return; }
      void copyText(picked.map(node => node.title).join('\n'), `已复制 ${picked.length} 个节点名称`);
      return;
    }
    if (event.target.closest('#v1-core-wizard-open')) { openWizard(selectedCoreId); return; }
    const row = event.target.closest('#v1-core-rows tr');
    if (row?.dataset.corePick && !event.target.closest('input, button, a, label')) { selectCore(row.dataset.corePick, true); return; }
    const toggle = event.target.closest('[data-menu-toggle]');
    if (toggle) {
      const panel = byId(toggle.getAttribute('aria-controls'));
      const willOpen = !!panel?.hidden;
      closeMenus();
      if (willOpen) {
        panel.hidden = false;
        toggle.setAttribute('aria-expanded', 'true');
        placeMenu(toggle, panel);
      }
      return;
    }
    if (!event.target.closest('.v1-menu, .v1-node-filters select, #v1-filter-select-menu')) closeMenus();
  });
  document.addEventListener('mousedown', event => {
    if (document.documentElement.dataset.theme !== 'neon-fusion') return;
    const select = event.target.closest('.v1-node-filters select');
    if (!select) return;
    event.preventDefault();
    select.focus({preventScroll: true});
    openFilterSelectMenu(select);
  }, true);
  document.addEventListener('click', event => {
    const option = event.target.closest('#v1-filter-select-menu [role="option"]');
    if (!option || option.disabled) return;
    const panel = byId('v1-filter-select-menu');
    const select = panel?.dataset.selectId ? byId(panel.dataset.selectId) : null;
    if (!select) return;
    const next = option.dataset.value ?? '';
    if (select.value !== next) {
      select.value = next;
      select.dispatchEvent(new Event('change', {bubbles: true}));
    }
    closeMenus();
    select.focus({preventScroll: true});
  });
  const hoverTitles = new WeakMap();
  document.addEventListener('pointerover', event => {
    const control = event.target.closest('button[aria-label]:not(.modal-backdrop), a[aria-label], [role="button"][aria-label]');
    if (!control || (control.title && control.title !== hoverTitles.get(control))) return;
    const label = control.getAttribute('aria-label').trim();
    if (label && label !== control.textContent.trim()) {
      control.title = label;
      hoverTitles.set(control, label);
    }
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      closeMenus();
      const probe = byId('v1-global-probe');
      if (probe.open) {
        probe.open = false;
        if (probe.contains(document.activeElement)) probe.querySelector('summary').focus();
      }
      return;
    }
    if (document.documentElement.dataset.theme !== 'neon-fusion') return;
    const select = event.target.closest?.('.v1-node-filters select') || (event.target.matches?.('.v1-node-filters select') ? event.target : null);
    if (!select || select !== document.activeElement) return;
    if (event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      openFilterSelectMenu(select);
    }
  });
  const clock = () => { byId('v1-clock').textContent = new Date().toLocaleString('zh-CN', {hour12: false, timeZone: settingsClockZone}); };
  clock();
  setInterval(clock, 1000);
  route();
  renderOverview();
  if (byId('v1-health')?.parentElement?.dataset.ready !== 'true') {
    window.hopworldTheme?.motion?.noteStatus?.(byId('v1-health').parentElement, 'connecting');
  }
  void loadSystem();
  void loadManagementSettings();
  setInterval(() => { if (!document.hidden) void loadSystem(); }, 30000);
  setInterval(() => { if (page === 'public' && !document.hidden) void refreshRelay(); }, 5000);
})();
