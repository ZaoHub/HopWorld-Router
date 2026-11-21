const form = document.getElementById('login-form');
const password = document.getElementById('login-password');
const showPassword = document.getElementById('show-password');
const submit = document.getElementById('login-submit');
const feedback = document.getElementById('login-feedback');
if (form.dataset.initialSetup === 'true') {
  document.getElementById('login-user').value = 'admin';
  document.getElementById('login-caption').textContent = '首次登录请使用默认账号和密码 admin / admin。';
} else if (new URLSearchParams(location.search).get('changed') === '1') {
  feedback.textContent = '账号设置已保存，请重新登录。';
}

showPassword.addEventListener('click', () => {
  const shown = password.type === 'password';
  password.type = shown ? 'text' : 'password';
  showPassword.textContent = shown ? '隐藏' : '显示';
  showPassword.setAttribute('aria-pressed', String(shown));
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (submit.disabled) return;
  const data = Object.fromEntries(new FormData(form));
  submit.disabled = true;
  feedback.textContent = '';
  submit.textContent = '正在登录…';
  try {
    const response = await fetch('/login', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '登录失败');
    if (location.pathname === '/') location.reload();
    else location.replace('/' + (location.hash || '#overview'));
  } catch (error) {
    feedback.textContent = error.message || '暂时无法连接到管理后台。';
  } finally {
    submit.disabled = false;
    submit.textContent = '登录';
  }
});
