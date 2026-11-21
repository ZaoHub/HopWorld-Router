const dialog = document.getElementById('first-login-dialog');
const form = document.getElementById('first-login-form');
const password = document.getElementById('first-login-password');
const confirm = document.getElementById('first-login-confirm');
const error = document.getElementById('first-login-error');
const save = document.getElementById('first-login-save');
const logout = document.getElementById('first-login-logout');

function showError(message) {
  error.hidden = !message;
  error.textContent = message;
}

async function post(path, data) {
  const response = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(data),
  });
  if (response.status === 401) {
    location.replace('/login');
    return false;
  }
  if (!response.ok) {
    const result = await response.json();
    throw new Error(result.error || '操作失败，请重试。');
  }
  return true;
}

dialog.addEventListener('cancel', event => event.preventDefault());
dialog.showModal();

form.addEventListener('input', () => {
  confirm.removeAttribute('aria-invalid');
  showError('');
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (save.disabled) return;
  if (password.value !== confirm.value) {
    confirm.setAttribute('aria-invalid', 'true');
    showError('两次输入的密码不一致，请再确认一次。');
    confirm.focus();
    return;
  }
  save.disabled = logout.disabled = true;
  save.textContent = '正在保存…';
  try {
    if (await post('/api/v1/management/initial-password', { new_password: password.value })) {
      location.reload();
    }
  } catch (failure) {
    showError(failure.message || '暂时无法保存，请重试。');
  } finally {
    save.disabled = logout.disabled = false;
    save.textContent = '保存并进入后台';
  }
});

logout.addEventListener('click', async () => {
  save.disabled = logout.disabled = true;
  try {
    if (await post('/logout', {})) location.replace('/login');
  } catch (failure) {
    showError(failure.message || '暂时无法退出，请重试。');
  } finally {
    save.disabled = logout.disabled = false;
  }
});
