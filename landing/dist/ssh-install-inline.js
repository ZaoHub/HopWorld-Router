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

const local = location.hostname === '127.0.0.1' && location.protocol === 'http:';
let apiBase;
let pageToken;

async function initialize() {
  retry.hidden = true;
  availability.textContent = '正在检查在线安装服务…';
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  const options = { cache: 'no-store', redirect: 'error', signal: controller.signal };
  try {
    if (local) {
      apiBase = location.origin;
    } else {
      const config = await fetch('install-config.json', options);
      if (!config.ok) throw new Error();
      const configured = (await config.json()).apiOrigin;
      const endpoint = new URL(configured);
      if (endpoint.protocol !== 'https:' || endpoint.origin !== configured) throw new Error();
      apiBase = endpoint.origin + '/hopworld-installer';
    }
    const ready = await fetch(`${apiBase}/api/ready`, options);
    if (!ready.ok) throw new Error();
    const capabilities = await ready.json();
    if (capabilities.installer !== true) throw new Error();
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
    availability.hidden = true;
    mode.textContent = local ? '本机安装器' : '网页安装';
    submit.disabled = false;
  } catch {
    apiBase = undefined;
    mode.textContent = '服务未就绪';
    availability.textContent = '暂时无法连接安装服务。你可以重试，或使用下方的终端安装命令。';
    retry.hidden = false;
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
    if (/^SHA256:[A-Za-z0-9+/]{43}$/.test(value)) return value;
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

function showVerification(needsToken, needsFingerprint) {
  verification.hidden = !needsToken && !needsFingerprint;
  byId('operator-field').hidden = !needsToken;
  byId('fingerprint-field').hidden = !needsFingerprint;
  tokenInput.disabled = !needsToken;
  tokenInput.required = needsToken;
  fingerprintInput.disabled = !needsFingerprint;
  fingerprintInput.required = needsFingerprint;
}

for (const field of [form.elements.host, form.elements.port]) {
  field.addEventListener('input', () => {
    fingerprintInput.value = '';
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
        const event = JSON.parse(line);
        if (event.type === 'output') {
          log.hidden = false;
          log.textContent = (log.textContent + event.text).slice(-24000);
          log.scrollTop = log.scrollHeight;
        } else if (event.type === 'status') {
          if (event.text.startsWith('SSH 已连接')) updateConnection('SSH 登录成功', 'success');
          updateStatus(event.text);
        } else if (event.type === 'error') {
          if (connection.dataset.state !== 'success') updateConnection('SSH 连接失败', 'error');
          if (event.text.startsWith('主机指纹不匹配')) rememberFingerprint(fingerprintKey);
          updateStatus(event.text, 'error');
          ended = true;
        } else if (event.type === 'success') {
          updateConnection('SSH 登录成功', 'success');
          rememberFingerprint(fingerprintKey, target.fingerprint);
          updateStatus('安装完成，后台已就绪。', 'success');
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

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (submit.disabled || !apiBase) return;
  const target = Object.fromEntries(new FormData(form));
  target.host = target.host.trim();
  target.username = target.username.trim();
  target.port = Number(target.port);
  const fingerprintKey = `hopworld-router:ssh-host:${target.host.toLowerCase()}:${target.port}`;
  target.fingerprint = readFingerprint(fingerprintKey) || fingerprintInput.value.trim();
  const token = pageToken || target.operatorToken;
  delete target.operatorToken;
  const needsToken = !local && !token;
  const needsFingerprint = !target.fingerprint;
  if (needsToken || needsFingerprint) {
    showVerification(needsToken, needsFingerprint);
    (needsToken ? tokenInput : fingerprintInput).focus();
    return;
  }
  showVerification(false, false);
  tokenInput.value = '';
  fingerprintInput.value = '';
  password.value = '';
  privateKey.value = '';
  passphrase.value = '';
  password.type = 'password';
  toggle.textContent = '显示';
  toggle.setAttribute('aria-pressed', 'false');
  const headers = { 'Content-Type': 'application/json' };
  if (!local) headers.Authorization = `Bearer ${token}`;
  submit.disabled = true;
  submit.textContent = '正在安装…';
  log.textContent = '';
  log.hidden = true;
  access.hidden = true;
  result.hidden = false;
  updateConnection('正在检测 SSH…', 'checking');
  updateStatus('正在连接服务器…');
  try {
    const body = JSON.stringify(target);
    delete target.password;
    delete target.privateKey;
    delete target.passphrase;
    const reply = await fetch(`${apiBase}/api/install`, {
      method: 'POST',
      headers,
      redirect: 'error',
      body,
    });
    if (reply.status === 401) pageToken = undefined;
    if (!reply.ok) {
      updateConnection('SSH 尚未检测');
      throw new Error((await reply.json()).error);
    }
    if (!local) pageToken = token;
    await consume(reply, target, fingerprintKey);
  } catch (error) {
    if (connection.dataset.state === 'checking') updateConnection('未收到 SSH 检测结果', 'error');
    updateStatus(error.message || '无法连接安装服务。', 'error');
  } finally {
    delete target.password;
    delete target.privateKey;
    delete target.passphrase;
    submit.disabled = false;
    submit.textContent = '立即安装到服务器';
  }
});

for (const [buttonId, sourceId] of [['copy-terminal', 'terminal-command'], ['copy-access', 'access-command']]) {
  const button = byId(buttonId);
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
