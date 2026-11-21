const byId = id => document.getElementById(id);
const form = byId('install-form');
const launcher = byId('launcher');
const mode = byId('connection-mode');
const result = byId('installation-result');
const status = byId('installation-status');
const log = byId('installation-log');
const access = byId('access');
const submit = byId('install-submit');
const password = form.elements.password;
const toggle = byId('password-toggle');

async function initialize() {
  if (location.hostname === '127.0.0.1') {
    try {
      const response = await fetch('/api/ready', { cache: 'no-store', signal: AbortSignal.timeout(2500) });
      if (response.ok && (await response.json()).installer === true) {
        mode.textContent = '本机安装器';
        form.hidden = false;
        return;
      }
    } catch {
      // Fall back to the downloadable launcher.
    }
  }
  mode.textContent = '下载后开始';
  launcher.hidden = false;
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
  if (submit.disabled) return;
  const target = Object.fromEntries(new FormData(form));
  for (const name of ['host', 'username', 'fingerprint']) target[name] = target[name].trim();
  target.port = Number(target.port);
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
    const reply = await fetch('/api/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(target),
    });
    delete target.password;
    if (!reply.ok) throw new Error((await reply.json()).error);
    await consume(reply, target);
  } catch (error) {
    updateStatus(error.message || '无法连接本机安装器。', 'error');
  } finally {
    delete target.password;
    submit.disabled = false;
    submit.textContent = '安装到服务器';
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
