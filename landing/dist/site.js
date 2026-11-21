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

const local = location.hostname === '127.0.0.1' && location.protocol === 'http:';
let apiBase;

async function initialize() {
  try {
    if (local) {
      apiBase = location.origin;
    } else {
      const config = await fetch('install-config.json', { cache: 'no-store' });
      if (!config.ok) throw new Error();
      const configured = (await config.json()).apiOrigin;
      const endpoint = new URL(configured);
      if (endpoint.protocol !== 'https:' || endpoint.origin !== configured) throw new Error();
      apiBase = endpoint.origin + '/hopworld-installer';
    }
    const ready = await fetch(`${apiBase}/api/ready`, {
      cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(4000),
    });
    if (!ready.ok || (await ready.json()).installer !== true) throw new Error();
    if (!local) {
      form.elements.host.labels[0].firstChild.textContent = '服务器公网 IP';
      form.elements.method.querySelector('[value="agent"]').remove();
      byId('operator-field').hidden = false;
      form.elements.operatorToken.disabled = false;
      form.elements.operatorToken.required = true;
      availability.textContent = '安装服务通过 HTTPS 接收本次 SSH 凭据，连接前会校验服务器指纹。';
    } else {
      availability.hidden = true;
    }
    mode.textContent = local ? '本机安装器' : '网页安装';
    form.hidden = false;
  } catch {
    apiBase = undefined;
    mode.textContent = '服务未就绪';
    availability.textContent = '在线安装服务暂不可用。你可以使用下方的终端安装命令。';
  }
}
initialize();

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

async function consume(response, target) {
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
          updateStatus(event.text, 'error');
          ended = true;
        } else if (event.type === 'success') {
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
  for (const name of ['host', 'username', 'fingerprint']) target[name] = target[name].trim();
  target.port = Number(target.port);
  const headers = { 'Content-Type': 'application/json' };
  if (!local) {
    headers.Authorization = `Bearer ${target.operatorToken}`;
    form.elements.operatorToken.value = '';
    delete target.operatorToken;
  }
  password.value = '';
  password.type = 'password';
  toggle.textContent = '显示';
  toggle.setAttribute('aria-pressed', 'false');
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
    if (!reply.ok) throw new Error((await reply.json()).error);
    await consume(reply, target);
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
