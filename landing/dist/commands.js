const search = document.getElementById('command-search');
const empty = document.getElementById('command-empty');
const entries = [...document.querySelectorAll('.command-entry')];
const sections = [...document.querySelectorAll('.catalog-section')];
const searchable = entries.map(entry => entry.textContent.toLowerCase());

search.addEventListener('input', () => {
  const query = search.value.trim().toLowerCase();
  for (const [index, entry] of entries.entries()) {
    entry.hidden = !searchable[index].includes(query);
    if (query && !entry.hidden) entry.open = true;
  }
  for (const section of sections) {
    section.hidden = !section.querySelector('.command-entry:not([hidden])');
  }
  empty.hidden = entries.some(entry => !entry.hidden);
});

for (const code of document.querySelectorAll('.command-entry pre')) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'button secondary';
  button.textContent = '复制命令';
  code.after(button);
  let reset;
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(code.textContent);
      button.textContent = '已复制';
    } catch {
      button.textContent = '请手动复制';
    }
    clearTimeout(reset);
    reset = setTimeout(() => { button.textContent = '复制命令'; }, 1800);
  });
}
