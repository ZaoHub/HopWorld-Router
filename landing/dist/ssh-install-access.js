const byId = id => document.getElementById(id);
const form = byId('install-form');
const availability = byId('installer-availability');
const mode = byId('connection-mode');
const result = byId('installation-result');
const status = byId('installation-status');
const connection = byId('ssh-connection-state');
const log = byId('installation-log');
const access = byId('access');
const submit = byId('install-submit');
const password = form.elements.password;
const privateKey = form.elements.privateKey;
const passphrase = form.elements.passphrase;
const toggle = byId('password-toggle');
const retry = byId('installer-retry');
const verification = byId('ssh-verification');
const tokenInput = form.elements.operatorToken;
const fingerprintInput = form.elements.fingerprint;
const fingerprintHint = byId('fingerprint-hint');
const verificationNote = byId('verification-note');
const localLauncher = byId('local-launcher');

const local = location.hostname === '127.0.0.1' && location.protocol === 'http:';
const fingerprintPattern = /^SHA256:[A-Za-z0-9+/]{43}$/;
const tokenPattern = /^[0-9a-f]{64}$/;
let apiBase;
let pageToken;
let pendingFingerprint = '';

function tokenFromLocation() {
  try {
    const hash = new URLSearchParams(location.hash.replace(/^#/, ''));
    const query = new URLSearchParams(location.search);
    const value = hash.get('token') || query.get('token') || '';
    return tokenPattern.test(value) ? value : '';
  } catch {
    return '';
  }
}

function readStoredToken() {
  try {
    const value = sessionStorage.getItem('hopworld-router:install-token');
    return tokenPattern.test(value) ? value : '';
  } catch {
    return '';
  }
}

function rememberToken(value) {
  try {
    if (value) sessionStorage.setItem('hopworld-router:install-token', value);
    else sessionStorage.removeItem('hopworld-router:install-token');
  } catch {
    // Session storage can be unavailable.
  }
}

async function initialize() {
  retry.hidden = true;
  availability.hidden = false;
  availability.textContent = '正在检查在线安装服务…';
  if (localLauncher) localLauncher.hidden = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  const options = { cache: 'no-store', redirect: 'error', signal: controller.signal, mode: 'cors' };
  try {
    if (local) {
      apiBase = location.origin;
    } else {
      const config = await fetch('install-config.json', options);
      if (!config.ok) throw new Error('config');
      const configured = (await config.json()).apiOrigin;
      const endpoint = new URL(configured);
      if (endpoint.protocol !== 'https:' || endpoint.origin !== configured) throw new Error('origin');
      apiBase = endpoint.origin + '/hopworld-installer';
    }
    const ready = await fetch(`${apiBase}/api/ready`, options);
    if (!ready.ok) throw new Error('ready');
    const capabilities = await ready.json();
    if (capabilities.installer !== true) throw new Error('capabilities');
    if (capabilities.sshKey === true) {
      const option = form.elements.method.querySelector('[value="key"]');
      option.hidden = false;
      option.disabled = false;
    }
    if (local) {
      byId('host-label').textContent = '服务器 IP 或域名';
      const agent = form.elements.method.querySelector('[value="agent"]');
      agent.hidden = false;
      agent.disabled = false;
    }
    pageToken = pageToken || tokenFromLocation() || readStoredToken() || undefined;
    if (pageToken) rememberToken(pageToken);
    // Gateway may allow official-origin installs without a bearer token.
    window.__hopworldRequireToken = capabilities.requireToken === true;
    availability.hidden = true;
    mode.textContent = local ? '本机安装器' : '网页安装';
    submit.disabled = false;
    if (!local && localLauncher) {
      localLauncher.hidden = false;
      const copy = localLauncher.querySelector('p');
      if (copy) {
        copy.textContent = window.__hopworldRequireToken
          ? '没有安装服务访问令牌时，请用本机安装器：SSH 密码只留在你的电脑，不经过网页安装网关。'
          : '也可以下载本机安装器：SSH 密码只留在你的电脑，不经过网页安装网关。';
      }
    }
  } catch {
    apiBase = undefined;
    mode.textContent = '服务未就绪';
    availability.textContent = '暂时无法连接安装服务。可下载本机安装器，或使用下方终端安装命令。';
    retry.hidden = false;
    if (localLauncher) localLauncher.hidden = false;
  } finally {
    clearTimeout(timeout);
  }
}
initialize();
retry.addEventListener('click', initialize);

form.elements.method.addEventListener('change', () => {
  const usePassword = form.elements.method.value === 'password';
  byId('password-field').hidden = !usePassword;
  password.required = usePassword;
  password.disabled = !usePassword;
  if (!usePassword) password.value = '';

  const useKey = form.elements.method.value === 'key';
  byId('private-key-field').hidden = !useKey;
  byId('passphrase-field').hidden = !useKey;
  privateKey.required = useKey;
  privateKey.disabled = !useKey;
  passphrase.disabled = !useKey;
  if (!useKey) {
    privateKey.value = '';
    passphrase.value = '';
  }
});

toggle.addEventListener('click', () => {
  const show = password.type === 'password';
  password.type = show ? 'text' : 'password';
  toggle.textContent = show ? '隐藏' : '显示';
  toggle.setAttribute('aria-pressed', String(show));
});

function updateStatus(message, state = '') {
  status.textContent = message;
  status.dataset.state = state;
}

function updateConnection(message, state = '') {
  connection.textContent = message;
  connection.dataset.state = state;
}

function readFingerprint(key) {
  try {
    const value = localStorage.getItem(key);
    if (fingerprintPattern.test(value)) return value;
  } catch {
    // Browser storage can be unavailable.
  }
}

function rememberFingerprint(key, value) {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    // Installation still works without browser storage.
  }
}

function extractFingerprint(text) {
  const match = String(text || '').match(/SHA256:[A-Za-z0-9+/]{43}/);
  return match ? match[0] : '';
}

function showVerification(needsToken, needsFingerprint, note) {
  verification.hidden = !needsToken && !needsFingerprint;
  byId('operator-field').hidden = !needsToken;
  byId('fingerprint-field').hidden = !needsFingerprint;
  tokenInput.disabled = !needsToken;
  tokenInput.required = needsToken;
  fingerprintInput.disabled = !needsFingerprint;
  fingerprintInput.required = needsFingerprint;
  if (verificationNote) {
    verificationNote.textContent = note || '请补齐以下信息，再点击安装。';
  }
  if (fingerprintHint) {
    fingerprintHint.textContent = pendingFingerprint
      ? '已探测到上方指纹。确认与服务器控制台一致后，再次点击安装。'
      : '未填指纹时会先自动探测；也可从服务器控制台复制安装器实际使用的那枚 SHA256 指纹。';
  }
}

for (const field of [form.elements.host, form.elements.port]) {
  field.addEventListener('input', () => {
    fingerprintInput.value = '';
    pendingFingerprint = '';
    showVerification(false, false);
  });
}

async function consume(response, target, fingerprintKey) {
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let pending = '';
  let ended = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      const lines = (pending + value).split('\n');
      pending = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        const event = JSON.parse(line);
        if (event.type === 'output') {
          log.hidden = false;
          log.textContent = (log.textContent + event.text).slice(-24000);
          log.scrollTop = log.scrollHeight;
        } else if (event.type === 'status') {
          if (event.text.startsWith('SSH 已连接')) updateConnection('SSH 登录成功', 'success');
          updateStatus(event.text);
        } else if (event.type === 'error') {
          const offered = extractFingerprint(event.text);
          if (event.text.startsWith('主机指纹不匹配')) {
            rememberFingerprint(fingerprintKey);
            if (offered) {
              pendingFingerprint = offered;
              fingerprintInput.value = offered;
              showVerification(false, true, '探测到的主机指纹与填写值不一致。已填入服务器当前指纹，确认无误后再次安装。');
              updateConnection('主机指纹待确认', 'error');
            } else {
              updateConnection('主机指纹不匹配', 'error');
            }
          } else if (event.text.startsWith('SSH 身份验证失败')) {
            updateConnection('SSH 登录失败', 'error');
          } else if (connection.dataset.state !== 'success') {
            updateConnection('SSH 连接失败', 'error');
          }
          updateStatus(event.text, 'error');
          ended = true;
        } else if (event.type === 'success') {
          updateConnection('SSH 登录成功', 'success');
          rememberFingerprint(fingerprintKey, target.fingerprint);
          updateStatus('安装完成，后台已就绪。', 'success');
          const host = target.host.includes(':') ? `[${target.host}]` : target.host;
          byId('access-endpoint').textContent = `${host}:${target.port}`;
          byId('access-command').textContent =
            `ssh -N -p ${target.port} -L 18080:127.0.0.1:8080 ${target.username}@${target.host}`;
          access.hidden = false;
          ended = true;
        }
      }
    }
    if (!ended) throw new Error('安装连接已中断，请检查服务器上的服务状态。');
  } finally {
    await reader.cancel().catch(() => {});
  }
}

async function probeFingerprint(target, headers) {
  updateConnection('正在探测主机指纹…', 'checking');
  updateStatus('正在读取服务器 SSH 主机密钥…');
  result.hidden = false;
  const reply = await fetch(`${apiBase}/api/host-key`, {
    method: 'POST',
    headers,
    redirect: 'error',
    mode: 'cors',
    body: JSON.stringify({ host: target.host, port: target.port }),
  });
  if (reply.status === 401) {
    pageToken = undefined;
    rememberToken('');
  }
  if (!reply.ok) {
    const detail = await reply.json().catch(() => ({}));
    throw new Error(detail.error || '无法探测 SSH 主机指纹。');
  }
  const probed = await reply.json();
  if (!fingerprintPattern.test(probed.fingerprint)) throw new Error('安装服务返回的主机指纹无效。');
  pendingFingerprint = probed.fingerprint;
  fingerprintInput.value = probed.fingerprint;
  showVerification(false, true, `已探测到 ${probed.keyType || 'SSH'} 主机指纹。请与服务器控制台核对后，再次点击安装。`);
  updateConnection('主机指纹待确认', 'checking');
  updateStatus(`服务器主机指纹：${probed.fingerprint}`);
  fingerprintInput.focus();
  fingerprintInput.select();
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (submit.disabled || !apiBase) return;
  const target = Object.fromEntries(new FormData(form));
  target.host = target.host.trim();
  target.username = target.username.trim();
  target.port = Number(target.port);
  const fingerprintKey = `hopworld-router:ssh-host:${target.host.toLowerCase()}:${target.port}`;
  target.fingerprint = readFingerprint(fingerprintKey) || fingerprintInput.value.trim() || pendingFingerprint;
  const token = pageToken || target.operatorToken || tokenFromLocation() || readStoredToken();
  delete target.operatorToken;
  const needsToken = !local && window.__hopworldRequireToken === true && !token;
  if (needsToken) {
    showVerification(true, false,
      '网页安装需要安装服务访问令牌。也可改用本机安装器：下载解压后运行 bash start.sh，SSH 密码只留在你的电脑上。');
    if (localLauncher) localLauncher.hidden = false;
    result.hidden = false;
    updateConnection('尚未开始 SSH', 'error');
    updateStatus('缺少安装服务访问令牌，还不是 SSH 连接失败。', 'error');
    tokenInput.focus();
    return;
  }
  const headers = { 'Content-Type': 'application/json' };
  if (!local && token) headers.Authorization = `Bearer ${token}`;

  if (!target.fingerprint) {
    submit.disabled = true;
    submit.textContent = '正在探测指纹…';
    try {
      await probeFingerprint(target, headers);
      if (!local) {
        pageToken = token;
        rememberToken(token);
      }
    } catch (error) {
      result.hidden = false;
      updateConnection('指纹探测失败', 'error');
      updateStatus(error.message || '无法探测 SSH 主机指纹。请检查公网 IP、SSH 端口，或改用本机安装器。', 'error');
      if (String(error.message || '').includes('访问令牌')) {
        showVerification(true, false, '安装服务访问令牌无效。请重新输入，或改用本机安装器。');
        pageToken = undefined;
        rememberToken('');
      }
    } finally {
      submit.disabled = false;
      submit.textContent = '立即安装到服务器';
    }
    return;
  }

  showVerification(false, false);
  tokenInput.value = '';
  fingerprintInput.value = '';
  const body = JSON.stringify(target);
  password.value = '';
  privateKey.value = '';
  passphrase.value = '';
  password.type = 'password';
  toggle.textContent = '显示';
  toggle.setAttribute('aria-pressed', 'false');
  delete target.password;
  delete target.privateKey;
  delete target.passphrase;
  submit.disabled = true;
  submit.textContent = '正在安装…';
  log.textContent = '';
  log.hidden = true;
  access.hidden = true;
  result.hidden = false;
  updateConnection('正在检测 SSH…', 'checking');
  updateStatus('正在连接服务器…');
  try {
    const reply = await fetch(`${apiBase}/api/install`, {
      method: 'POST',
      headers,
      redirect: 'error',
      mode: 'cors',
      body,
    });
    if (reply.status === 401) {
      pageToken = undefined;
      rememberToken('');
    }
    if (!reply.ok) {
      updateConnection('SSH 尚未检测');
      throw new Error((await reply.json()).error);
    }
    if (!local) {
      pageToken = token;
      rememberToken(token);
    }
    pendingFingerprint = '';
    await consume(reply, target, fingerprintKey);
  } catch (error) {
    if (connection.dataset.state === 'checking') updateConnection('未收到 SSH 检测结果', 'error');
    updateStatus(error.message || '无法连接安装服务。', 'error');
  } finally {
    submit.disabled = false;
    submit.textContent = '立即安装到服务器';
  }
});

for (const [buttonId, sourceId] of [['copy-terminal', 'terminal-command'], ['copy-access', 'access-command']]) {
  const button = byId(buttonId);
  if (!button) continue;
  const initial = button.textContent;
  let reset;
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(byId(sourceId).textContent);
      button.textContent = '已复制';
    } catch {
      button.textContent = '请手动复制';
    }
    clearTimeout(reset);
    reset = setTimeout(() => { button.textContent = initial; }, 1800);
  });
}
