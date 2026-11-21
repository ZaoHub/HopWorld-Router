const form = document.getElementById('login-form');
const password = document.getElementById('login-password');
const showPassword = document.getElementById('show-password');
const submit = document.getElementById('login-submit');
const feedback = document.getElementById('login-feedback');
const setup = document.getElementById('initial-password-fields');
const newPassword = document.getElementById('new-password');
const repeatPassword = document.getElementById('repeat-password');

function showInitialSetup() {
  setup.hidden = false;
  newPassword.disabled = repeatPassword.disabled = false;
  newPassword.required = repeatPassword.required = true;
  document.getElementById('login-title').textContent = '设置管理员密码';
  document.getElementById('login-caption').textContent = '首次登录请使用 admin / admin，并设置新密码。';
  submit.textContent = '保存并登录';
}

if (form.dataset.initialSetup === 'true') {
  document.getElementById('login-user').value = 'admin';
  showInitialSetup();
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
  if (!setup.hidden && newPassword.value !== repeatPassword.value) {
    feedback.textContent = '两次输入的新密码不一致。';
    repeatPassword.focus();
    return;
  }
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
    if (result.setup_required) {
      showInitialSetup();
      newPassword.focus();
      return;
    }
    if (location.pathname === '/') location.reload();
    else location.replace('/' + (location.hash || '#overview'));
  } catch (error) {
    feedback.textContent = error.message || '暂时无法连接到管理后台。';
  } finally {
    submit.disabled = false;
    submit.textContent = setup.hidden ? '登录' : '保存并登录';
  }
});
