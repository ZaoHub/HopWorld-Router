(() => {
  const MASK = '********';
  const REVEAL_MS = 5000;
  const PLACEHOLDERS = new Set(['', '—', '–', '-', '未配置', '待配置', '未获取', '待检测', '待签发', '无', '未设置', '原始地址']);
  const ICONS = {
    show: '<svg class="v1-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0" /><circle cx="12" cy="12" r="3" /></svg>',
    hide: '<svg class="v1-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49" /><path d="M14.084 14.158a3 3 0 0 1-4.242-4.242" /><path d="M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143" /><path d="m2 2 20 20" /></svg>',
  };

  function textOf(value) {
    return value == null ? '' : String(value).trim();
  }

  function isSecretValue(value) {
    const text = textOf(value);
    return !PLACEHOLDERS.has(text);
  }

  function displayValue(value, revealed) {
    const text = textOf(value);
    if (!isSecretValue(text)) return text || '—';
    return revealed ? String(value) : MASK;
  }

  function viewState(label, snapshot) {
    const view = {
      text: snapshot.display,
      maskable: snapshot.maskable,
      revealed: snapshot.revealed,
      button: null,
    };
    if (snapshot.maskable) {
      view.button = {
        ariaLabel: snapshot.revealed ? `隐藏${label}` : `显示${label}`,
        ariaPressed: snapshot.revealed ? 'true' : 'false',
        icon: snapshot.revealed ? 'hide' : 'show',
      };
    }
    return view;
  }

  function createOverviewSecrets(options = {}) {
    const now = options.now || (() => Date.now());
    const setTimer = options.setTimeout || ((fn, delay) => setTimeout(fn, delay));
    const clearTimer = options.clearTimeout || (id => clearTimeout(id));
    const duration = options.duration ?? REVEAL_MS;
    const onChange = options.onChange || (() => {});
    const fields = new Map();

    function state(id) {
      if (!fields.has(id)) fields.set(id, {value: '', until: null, timer: null});
      return fields.get(id);
    }

    function remaining(id) {
      const item = fields.get(id);
      if (!item || item.until == null) return 0;
      return Math.max(0, item.until - now());
    }

    function isRevealed(id) {
      return remaining(id) > 0;
    }

    function snapshot(id) {
      const item = state(id);
      const revealed = isRevealed(id);
      return {
        value: item.value,
        maskable: isSecretValue(item.value),
        revealed,
        until: item.until,
        display: displayValue(item.value, revealed),
      };
    }

    function clearTimerFor(item) {
      if (item.timer != null) clearTimer(item.timer);
      item.timer = null;
    }

    function conceal(id, notify = true) {
      const item = state(id);
      clearTimerFor(item);
      const changed = item.until != null;
      item.until = null;
      if (notify && changed) onChange(id);
      return snapshot(id);
    }

    function schedule(id) {
      const item = state(id);
      clearTimerFor(item);
      const wait = remaining(id);
      if (wait <= 0) {
        conceal(id);
        return;
      }
      item.timer = setTimer(() => {
        if (remaining(id) <= 0) conceal(id);
        else schedule(id);
      }, wait);
    }

    function setValue(id, value) {
      const item = state(id);
      item.value = value == null ? '' : String(value);
      if (!isSecretValue(item.value) || (item.until != null && remaining(id) <= 0)) {
        conceal(id, false);
      } else if (isRevealed(id)) {
        schedule(id);
      }
      return snapshot(id);
    }

    function reveal(id) {
      const item = state(id);
      if (!isSecretValue(item.value)) return snapshot(id);
      item.until = now() + duration;
      schedule(id);
      onChange(id);
      return snapshot(id);
    }

    function toggle(id) {
      return isRevealed(id) ? conceal(id) : reveal(id);
    }

    function hideAll() {
      for (const id of fields.keys()) conceal(id, false);
      onChange(null);
    }

    return {setValue, toggle, reveal, conceal, hideAll, isRevealed, snapshot, remaining};
  }

  const api = {MASK, REVEAL_MS, ICONS, isSecretValue, displayValue, viewState, createOverviewSecrets};
  if (typeof module === 'object' && module.exports) module.exports = api;
  const root = typeof window !== 'undefined' ? window : globalThis;
  root.hopworldOverviewSecret = api;
})();
