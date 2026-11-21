const byId = id => document.getElementById(id);
const form = byId('install-form');
const availability = byId('installer-availability');
const mode = byId('connection-mode');
const result = byId('installation-result');
const status = byId('installation-status');
const log = byId('installation-log');
const access = byId('access');
const submit = byId('install-submit');
const password = form.elements.password;
const toggle = byId('password-toggle');
const retry = byId('installer-retry');
const confirmation = byId('ssh-confirmation');
const confirmationForm = byId('ssh-confirmation-form');
const tokenInput = confirmationForm.elements.operatorToken;
const fingerprintInput = confirmationForm.elements.fingerprint;

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
    if (!ready.ok || (await ready.json()).installer !== true) throw new Error();
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

function confirmTarget(target, needsToken, needsFingerprint) {
  byId('confirmation-target').textContent = `${target.username} · ${target.host} · SSH ${target.port}`;
  byId('operator-field').hidden = !needsToken;
  byId('fingerprint-field').hidden = !needsFingerprint;
  tokenInput.disabled = !(tokenInput.required = needsToken);
  fingerprintInput.disabled = !(fingerprintInput.required = needsFingerprint);
  confirmation.returnValue = '';
  return new Promise(resolve => {
    confirmation.addEventListener('close', () => {
      const values = confirmation.returnValue === 'confirm'
        ? { token: tokenInput.value, fingerprint: fingerprintInput.value }
        : null;
      confirmationForm.reset();
      resolve(values);
    }, { once: true });
    confirmation.showModal();
    (needsToken ? tokenInput : fingerprintInput).focus();
  });
}

byId('cancel-confirmation').addEventListener('click', () => confirmation.close());

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
          log.textContent = (log.textContent + event.text).slice(-24000);
          log.scrollTop = log.scrollHeight;
        } else if (event.type === 'status') {
          updateStatus(event.text);
        } else if (event.type === 'error') {
          if (event.text.startsWith('主机指纹不匹配')) rememberFingerprint(fingerprintKey);
          updateStatus(event.text, 'error');
          ended = true;
        } else if (event.type === 'success') {
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
  if (submit.disabled || !apiBase || confirmation.open) return;
  const target = Object.fromEntries(new FormData(form));
  target.host = target.host.trim();
  target.username = target.username.trim();
  target.port = Number(target.port);
  const fingerprintKey = `hopworld-router:ssh-host:${target.host.toLowerCase()}:${target.port}`;
  target.fingerprint = readFingerprint(fingerprintKey);
  const needsToken = !local && !pageToken;
  const needsFingerprint = !target.fingerprint;
  const confirmed = needsToken || needsFingerprint
    ? await confirmTarget(target, needsToken, needsFingerprint)
    : {};
  password.value = '';
  password.type = 'password';
  toggle.textContent = '显示';
  toggle.setAttribute('aria-pressed', 'false');
  if (!confirmed) return;
  target.fingerprint ||= confirmed.fingerprint;
  const token = pageToken || confirmed.token;
  const headers = { 'Content-Type': 'application/json' };
  if (!local) headers.Authorization = `Bearer ${token}`;
  submit.disabled = true;
  submit.textContent = '正在安装…';
  log.textContent = '';
  access.hidden = true;
  result.hidden = false;
  updateStatus('正在准备 SSH 连接…');
  try {
    const body = JSON.stringify(target);
    delete target.password;
    const reply = await fetch(`${apiBase}/api/install`, {
      method: 'POST',
      headers,
      redirect: 'error',
      body,
    });
    if (reply.status === 401) pageToken = undefined;
    if (!reply.ok) throw new Error((await reply.json()).error);
    if (!local) pageToken = token;
    await consume(reply, target, fingerprintKey);
  } catch (error) {
    updateStatus(error.message || '无法连接安装服务。', 'error');
  } finally {
    delete target.password;
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
