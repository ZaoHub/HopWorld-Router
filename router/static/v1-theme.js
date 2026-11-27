(() => {
  const STORAGE_KEY = 'hopworld-v1-theme';
  const MOTION_STORAGE_KEY = 'hopworld-v1-theme-neon-fusion';
  const DEFAULT_THEME = 'default';
  const DEFAULT_MOTION_MODE = 'subtle';
  const MOTION_MODES = {full: true, subtle: true, off: true};
  const THEMES = {
    default: {id: 'default', name: '默认', nameEn: 'Default'},
    'neon-fusion': {id: 'neon-fusion', name: '霓虹幻彩', nameEn: 'Neon Fusion'},
  };
  const METRIC_MS = {full: 500, subtle: 400, off: 0};
  const CHART_MS = {full: 520, subtle: 400, off: 0};
  const PARTICLE_COUNTS = {full: 8, subtle: 0, off: 0};
  const ORB_COUNTS = {full: 2, subtle: 2, off: 0};

  function normalizeTheme(value) {
    const id = value == null ? '' : String(value).trim();
    return THEMES[id] ? id : DEFAULT_THEME;
  }

  function readStoredTheme(storage) {
    try {
      const store = storage || (typeof localStorage !== 'undefined' ? localStorage : null);
      if (!store) return DEFAULT_THEME;
      return normalizeTheme(store.getItem(STORAGE_KEY));
    } catch (error) {
      return DEFAULT_THEME;
    }
  }

  function writeStoredTheme(id, storage) {
    try {
      const store = storage || (typeof localStorage !== 'undefined' ? localStorage : null);
      if (!store) return false;
      store.setItem(STORAGE_KEY, normalizeTheme(id));
      return true;
    } catch (error) {
      return false;
    }
  }

  function normalizeMotionPrefs(raw) {
    const src = raw && typeof raw === 'object' ? raw : {};
    const motionMode = MOTION_MODES[src.motionMode] ? src.motionMode : DEFAULT_MOTION_MODE;
    const ambientEffectsEnabled = src.ambientEffectsEnabled !== false;
    return {motionMode, ambientEffectsEnabled};
  }

  function readMotionPrefs(storage) {
    try {
      const store = storage || (typeof localStorage !== 'undefined' ? localStorage : null);
      if (!store) return normalizeMotionPrefs(null);
      const raw = store.getItem(MOTION_STORAGE_KEY);
      if (raw == null || raw === '') return normalizeMotionPrefs(null);
      return normalizeMotionPrefs(JSON.parse(raw));
    } catch (error) {
      return normalizeMotionPrefs(null);
    }
  }

  function writeMotionPrefs(prefs, storage) {
    try {
      const store = storage || (typeof localStorage !== 'undefined' ? localStorage : null);
      if (!store) return false;
      store.setItem(MOTION_STORAGE_KEY, JSON.stringify(normalizeMotionPrefs(prefs)));
      return true;
    } catch (error) {
      return false;
    }
  }

  function detectEnvironment(win) {
    const w = win || (typeof window !== 'undefined' ? window : null);
    const nav = w && w.navigator ? w.navigator : {};
    let reducedMotion = false;
    try {
      reducedMotion = !!(w && w.matchMedia && w.matchMedia('(prefers-reduced-motion: reduce)').matches);
    } catch (error) {}
    const connection = nav.connection || nav.mozConnection || nav.webkitConnection;
    const saveData = !!(connection && connection.saveData);
    const deviceMemory = Number(nav.deviceMemory);
    const cores = Number(nav.hardwareConcurrency);
    const lowPower = saveData
      || (Number.isFinite(deviceMemory) && deviceMemory > 0 && deviceMemory <= 2)
      || (Number.isFinite(cores) && cores > 0 && cores <= 2);
    return {reducedMotion, saveData, lowPower};
  }

  function effectiveMotion(prefs, env) {
    const normalized = normalizeMotionPrefs(prefs);
    const environment = env || {};
    if (normalized.motionMode === 'off') {
      return {mode: 'off', ambient: false, reason: 'user-off'};
    }
    if (environment.reducedMotion) {
      return {mode: 'off', ambient: false, reason: 'reduced-motion'};
    }
    let mode = normalized.motionMode;
    let ambient = normalized.ambientEffectsEnabled;
    if (environment.lowPower || environment.saveData) {
      if (mode === 'full') mode = 'subtle';
      ambient = false;
    }
    if (!normalized.ambientEffectsEnabled) ambient = false;
    if (mode === 'off') ambient = false;
    return {mode, ambient, reason: environment.lowPower || environment.saveData ? 'low-power' : 'user'};
  }

  function currentTheme(doc) {
    const root = (doc || (typeof document !== 'undefined' ? document : null));
    if (!root || !root.documentElement) return DEFAULT_THEME;
    return normalizeTheme(root.documentElement.getAttribute('data-theme'));
  }

  function applyThemeAttrs(theme, root) {
    if (!root || !root.documentElement) return theme;
    if (theme === DEFAULT_THEME) root.documentElement.removeAttribute('data-theme');
    else root.documentElement.setAttribute('data-theme', theme);
    return theme;
  }

  function applyMotionAttrs(effective, root) {
    if (!root || !root.documentElement) return effective;
    const html = root.documentElement;
    if (!effective || currentTheme(root) !== 'neon-fusion') {
      html.removeAttribute('data-nf-motion');
      html.removeAttribute('data-nf-ambient');
      return effective;
    }
    html.setAttribute('data-nf-motion', effective.mode);
    html.setAttribute('data-nf-ambient', effective.ambient ? 'on' : 'off');
    return effective;
  }

  function integerText(value) {
    return /^-?\d+$/.test(String(value).trim());
  }

  function parseMetricNumber(value) {
    const text = String(value == null ? '' : value).trim();
    if (/^-?\d+$/.test(text)) return {value: Number(text), suffix: '', raw: text};
    const percent = text.match(/^(-?\d+)%$/);
    if (percent) return {value: Number(percent[1]), suffix: '%', raw: text};
    return null;
  }

  function lerp(from, to, t) {
    return from + (to - from) * t;
  }

  function ease(t) {
    return 1 - Math.pow(1 - t, 2);
  }

  function lastFiniteIndex(samples, key) {
    if (!samples) return -1;
    for (let i = samples.length - 1; i >= 0; i -= 1) {
      if (Number.isFinite(samples[i] && samples[i][key])) return i;
    }
    return -1;
  }

  function cloneSamples(samples) {
    return (samples || []).map(item => (item && typeof item === 'object' ? Object.assign({}, item) : item));
  }

  function sameChartTail(a, b) {
    if (!a || !b || a.length !== b.length) return false;
    const keys = ['up_bytes', 'down_bytes', 'total_bytes', 'timestamp'];
    const lastA = a[a.length - 1];
    const lastB = b[b.length - 1];
    if (!lastA || !lastB) return lastA === lastB;
    return keys.every(key => lastA[key] === lastB[key]);
  }

  function canLerpChart(previous, next) {
    if (!previous || !next || !next.length) return false;
    if (next.length === previous.length + 1 || next.length === previous.length) {
      const keys = ['up_bytes', 'down_bytes', 'total_bytes'];
      const fromIndex = lastFiniteIndex(previous, 'total_bytes');
      const toIndex = lastFiniteIndex(next, 'total_bytes');
      if (fromIndex < 0 || toIndex < 0) return false;
      if (next.length === previous.length && fromIndex !== toIndex) return false;
      if (next.length === previous.length + 1 && toIndex !== next.length - 1) return false;
      const from = previous[fromIndex];
      const to = next[toIndex];
      return keys.some(key => Number.isFinite(from[key]) && Number.isFinite(to[key]) && from[key] !== to[key]);
    }
    return false;
  }

  function interpolateChartSamples(previous, next, t) {
    const display = cloneSamples(next);
    const keys = ['up_bytes', 'down_bytes', 'total_bytes'];
    const fromIndex = lastFiniteIndex(previous, 'total_bytes');
    const toIndex = lastFiniteIndex(next, 'total_bytes');
    if (fromIndex < 0 || toIndex < 0) return next;
    const from = previous[fromIndex];
    const to = next[toIndex];
    const point = Object.assign({}, display[toIndex]);
    keys.forEach(key => {
      if (Number.isFinite(from[key]) && Number.isFinite(to[key])) point[key] = lerp(from[key], to[key], t);
    });
    display[toIndex] = point;
    return display;
  }

  function createMotionRuntime(options) {
    const opts = options || {};
    const now = opts.now || (() => Date.now());
    const requestFrame = opts.requestAnimationFrame || (typeof requestAnimationFrame === 'function' ? requestAnimationFrame.bind(typeof window !== 'undefined' ? window : globalThis) : (fn => setTimeout(() => fn(now()), 16)));
    const cancelFrame = opts.cancelAnimationFrame || (typeof cancelAnimationFrame === 'function' ? cancelAnimationFrame.bind(typeof window !== 'undefined' ? window : globalThis) : clearTimeout);
    const charts = new Map();
    const statusMemory = new Map();
    const metricTimers = new Map();
    const ringTimers = new Map();
    const observers = [];
    const listeners = [];
    let introTimer = null;
    let introPlayed = false;
    let ambientNode = null;
    let bound = false;
    let paused = false;
    let visibleCanvases = typeof WeakSet === 'function' ? new WeakSet() : null;

    function documentOf(runtimeDoc) {
      return runtimeDoc || opts.document || (typeof document !== 'undefined' ? document : null);
    }

    function windowOf() {
      return opts.window || (typeof window !== 'undefined' ? window : null);
    }

    function currentEffective(doc) {
      const root = documentOf(doc);
      const theme = opts.theme || currentTheme(root);
      if (theme !== 'neon-fusion') return {mode: 'off', ambient: false, reason: 'other-theme'};
      return effectiveMotion(readMotionPrefs(opts.storage), detectEnvironment(windowOf()));
    }

    function shouldAnimate(doc) {
      if (paused) return false;
      const effective = currentEffective(doc);
      return effective.mode !== 'off';
    }

    function duration(table, doc) {
      const effective = currentEffective(doc);
      return table[effective.mode] || 0;
    }

    function cancelMap(map) {
      map.forEach(entry => {
        if (entry && entry.raf != null) cancelFrame(entry.raf);
        if (entry && entry.timer != null) clearTimeout(entry.timer);
      });
      map.clear();
    }

    function setPaused(value, doc) {
      paused = !!value;
      const root = documentOf(doc);
      if (root && root.documentElement) {
        if (paused && currentTheme(root) === 'neon-fusion') root.documentElement.setAttribute('data-nf-paused', 'true');
        else root.documentElement.removeAttribute('data-nf-paused');
      }
      if (paused) {
        charts.forEach(entry => {
          if (entry.raf != null) {
            cancelFrame(entry.raf);
            entry.raf = null;
          }
          entry.display = entry.real || entry.display;
        });
        cancelMap(metricTimers);
        cancelMap(ringTimers);
      }
    }

    function ensureAmbient(doc, effective) {
      const root = documentOf(doc);
      if (!root || !root.body || typeof root.createElement !== 'function') return;
      if (!effective.ambient || currentTheme(root) !== 'neon-fusion') {
        if (ambientNode && ambientNode.parentNode) ambientNode.parentNode.removeChild(ambientNode);
        ambientNode = null;
        return;
      }
      const count = PARTICLE_COUNTS[effective.mode] || 0;
      const orbs = ORB_COUNTS[effective.mode] || 0;
      if (!ambientNode) {
        ambientNode = root.createElement('div');
        ambientNode.className = 'nf-ambient';
        ambientNode.setAttribute('aria-hidden', 'true');
      }
      const needed = [];
      ['a', 'b', 'c'].slice(0, orbs).forEach(name => {
        const orb = root.createElement('div');
        orb.className = 'nf-orb nf-orb-' + name;
        needed.push(orb);
      });
      for (let i = 0; i < count; i += 1) {
        const particle = root.createElement('span');
        particle.className = 'nf-particle' + (i >= PARTICLE_COUNTS.subtle ? ' nf-particle-full' : '');
        particle.style.left = (24 + ((i * 17) % 70)) + '%';
        particle.style.top = (18 + ((i * 29) % 66)) + '%';
        particle.style.animationDuration = (12 + (i % 7) * 1.5) + 's';
        particle.style.animationDelay = (i % 5) * -1.2 + 's';
        needed.push(particle);
      }
      ambientNode.replaceChildren(...needed);
      if (ambientNode.parentNode !== root.body) {
        root.body.insertBefore(ambientNode, root.body.firstChild);
      }
    }

    function playIntro(doc, effective) {
      const root = documentOf(doc);
      if (!root || typeof root.querySelector !== 'function') return;
      const topbar = root.querySelector('.topbar') || root.querySelector('.login-brand');
      if (introTimer) {
        clearTimeout(introTimer);
        introTimer = null;
      }
      if (!topbar) return;
      topbar.classList.remove('nf-intro');
      if (!effective || effective.mode === 'off' || currentTheme(root) !== 'neon-fusion') return;
      if (introPlayed) return;
      introPlayed = true;
      void topbar.offsetWidth;
      topbar.classList.add('nf-intro');
      introTimer = setTimeout(() => {
        topbar.classList.remove('nf-intro');
        introTimer = null;
      }, 700);
    }

    function noteStatus(target, state, doc) {
      if (!target) return {triggered: false, state};
      const next = state == null ? '' : String(state);
      const key = target.id || target;
      const previous = statusMemory.has(key) ? statusMemory.get(key) : undefined;
      const animate = shouldAnimate(doc);
      const success = next === 'online' || next === 'running' || next === 'ok';
      const failed = next === 'error' || next === 'failed';
      if (next === previous) {
        if (next === 'connecting' && animate) target.classList.add('nf-is-connecting');
        return {triggered: false, state: next};
      }
      statusMemory.set(key, next);
      target.classList.remove('nf-is-connecting', 'nf-status-ok', 'nf-status-error', 'nf-is-online');
      if (success) target.classList.add('nf-is-online');
      if (next === 'connecting' && animate) {
        target.classList.add('nf-is-connecting');
        return {triggered: true, kind: 'connecting', state: next};
      }
      if (!animate) return {triggered: false, state: next};
      if (success && previous !== 'unknown') {
        target.classList.add('nf-status-ok');
        const timer = setTimeout(() => target.classList.remove('nf-status-ok'), 560);
        metricTimers.set('status-ok:' + key, {timer});
        return {triggered: true, kind: 'ok', state: next};
      }
      if (failed) {
        target.classList.add('nf-status-error');
        const timer = setTimeout(() => target.classList.remove('nf-status-error'), 560);
        metricTimers.set('status-err:' + key, {timer});
        return {triggered: true, kind: 'error', state: next};
      }
      return {triggered: false, state: next};
    }

    function liveChartSamples(id, samples, redraw, doc) {
      const real = samples || [];
      const ms = duration(CHART_MS, doc);
      const entry = charts.get(id) || {real: null, from: null, to: null, start: 0, raf: null, redraw: null, display: null};
      entry.redraw = redraw || entry.redraw;
      if (!ms || paused || !shouldAnimate(doc)) {
        if (entry.raf != null) cancelFrame(entry.raf);
        entry.raf = null;
        entry.real = real;
        entry.display = real;
        charts.set(id, entry);
        return real;
      }
      if (entry.to && sameChartTail(entry.to, real)) {
        return entry.raf != null ? (entry.display || real) : real;
      }
      const previous = entry.real;
      entry.real = real;
      if (!canLerpChart(previous, real)) {
        if (entry.raf != null) cancelFrame(entry.raf);
        entry.raf = null;
        entry.display = real;
        entry.to = real;
        charts.set(id, entry);
        return real;
      }
      entry.from = previous;
      entry.to = real;
      entry.start = now();
      charts.set(id, entry);
      const tick = () => {
        const current = charts.get(id);
        if (!current || current.to !== real) return;
        const t = Math.min(1, (now() - current.start) / ms);
        current.display = t >= 1 ? real : interpolateChartSamples(current.from, current.to, ease(t));
        if (t >= 1) {
          current.raf = null;
          current.display = real;
          if (typeof current.redraw === 'function') current.redraw(real);
          return;
        }
        current.raf = requestFrame(tick);
        if (typeof current.redraw === 'function' && t < 1) current.redraw(current.display);
      };
      entry.display = interpolateChartSamples(previous, real, 0);
      entry.raf = requestFrame(tick);
      return entry.display;
    }

    function animateMetricText(node, nextText, doc) {
      if (!node) return nextText;
      const next = nextText == null ? '' : String(nextText);
      const previous = node.textContent == null ? '' : String(node.textContent);
      const ms = duration(METRIC_MS, doc);
      const dashValues = {'': true, '—': true, '–': true, '-': true, '未配置': true};
      if (!ms || paused || !shouldAnimate(doc) || previous === next) {
        node.textContent = next;
        return next;
      }
      const key = node;
      const existing = metricTimers.get(key);
      if (existing && existing.raf != null) cancelFrame(existing.raf);
      if (existing && existing.timer != null) clearTimeout(existing.timer);
      const fromEmpty = dashValues[previous.trim()] || previous.trim() === '';
      const toEmpty = dashValues[next.trim()] || next.trim() === '';
      if (toEmpty) {
        node.textContent = next;
        metricTimers.delete(key);
        return next;
      }
      if (fromEmpty) {
        node.textContent = next;
        node.classList.add('nf-metric-enter');
        const timer = setTimeout(() => node.classList.remove('nf-metric-enter'), ms);
        metricTimers.set(key, {timer});
        return next;
      }
      const fromNum = parseMetricNumber(previous);
      const toNum = parseMetricNumber(next);
      if (fromNum && toNum && fromNum.suffix === toNum.suffix) {
        node.classList.add('nf-metric-change');
        const start = now();
        const tick = () => {
          const t = Math.min(1, (now() - start) / ms);
          node.textContent = t >= 1 ? next : String(Math.round(lerp(fromNum.value, toNum.value, ease(t)))) + toNum.suffix;
          if (t >= 1) {
            node.textContent = next;
            node.classList.remove('nf-metric-change');
            metricTimers.delete(key);
            return;
          }
          metricTimers.set(key, {raf: requestFrame(tick)});
        };
        metricTimers.set(key, {raf: requestFrame(tick)});
        return next;
      }
      node.style.opacity = '0';
      const timer = setTimeout(() => {
        node.textContent = next;
        node.style.opacity = '1';
        metricTimers.delete(key);
      }, Math.min(ms / 2, 240));
      metricTimers.set(key, {timer});
      return next;
    }

    function animateResourceRing(dial, percent, color, paint, doc) {
      if (!dial || typeof paint !== 'function') return percent;
      const next = Number.isFinite(percent) ? Number(percent) : null;
      const existing = ringTimers.get(dial) || {};
      if (existing.raf != null) cancelFrame(existing.raf);
      const ms = duration(METRIC_MS, doc);
      const first = !ringTimers.has(dial);
      const prev = existing.value;
      const same = (!Number.isFinite(prev) && !Number.isFinite(next)) ||
        (Number.isFinite(prev) && Number.isFinite(next) && Math.abs(prev - next) < 1e-6);
      if (!ms || paused || !shouldAnimate(doc) || first || same || next == null || prev == null) {
        paint(next, color);
        ringTimers.set(dial, {value: next, color});
        return next;
      }
      const start = now();
      const tick = () => {
        const t = Math.min(1, (now() - start) / ms);
        const shown = t >= 1 ? next : lerp(prev, next, ease(t));
        paint(shown, color);
        if (t >= 1) {
          ringTimers.set(dial, {value: next, color});
          return;
        }
        ringTimers.set(dial, {value: next, color, raf: requestFrame(tick)});
      };
      ringTimers.set(dial, {value: next, color, raf: requestFrame(tick)});
      return next;
    }

    function applyMetricCards(box, cards, entries, doc) {
      if (!box || !cards || !entries) return false;
      if (!shouldAnimate(doc)) return false;
      const labels = entries.map(entry => entry[0]);
      const existing = box.querySelectorAll('.v1-metric div > span:first-child');
      const existingLabels = [];
      existing.forEach(node => {
        if (node.parentElement && node.parentElement.tagName === 'DIV') existingLabels.push(node.textContent);
      });
      if (existingLabels.length !== labels.length) return false;
      if (labels.some((label, index) => existingLabels[index] !== label)) return false;
      const strongs = box.querySelectorAll('.v1-metric strong');
      if (strongs.length !== entries.length) return false;
      entries.forEach((entry, index) => {
        const value = entry[1];
        const text = value == null || value === '' ? '—' : String(value);
        animateMetricText(strongs[index], text, doc);
      });
      return true;
    }

    function watchCanvas(canvas, doc) {
      const root = documentOf(doc);
      const w = windowOf();
      if (!canvas || !w || typeof w.IntersectionObserver !== 'function') return;
      if (observers.some(item => item.canvas === canvas)) return;
      let observer = observers.find(item => item.type === 'canvas')?.observer;
      if (!observer) {
        observer = new w.IntersectionObserver(records => {
          records.forEach(record => {
            if (visibleCanvases) {
              if (record.isIntersecting) visibleCanvases.add(record.target);
              else visibleCanvases.delete(record.target);
            }
          });
        }, {threshold: 0.05});
        observers.push({type: 'canvas', observer, canvas: null});
      }
      observer.observe(canvas);
      observers.push({type: 'canvas-target', canvas, observer});
    }

    function bindLifecycle(doc) {
      if (bound) return;
      const root = documentOf(doc);
      if (!root || typeof root.addEventListener !== 'function') return;
      bound = true;
      const onVisibility = () => setPaused(!!root.hidden, root);
      root.addEventListener('visibilitychange', onVisibility);
      listeners.push({target: root, type: 'visibilitychange', fn: onVisibility});
    }

    function unbindLifecycle() {
      listeners.forEach(item => {
        try { item.target.removeEventListener(item.type, item.fn); } catch (error) {}
      });
      listeners.length = 0;
      observers.forEach(item => {
        try { item.observer && item.observer.disconnect(); } catch (error) {}
      });
      observers.length = 0;
      bound = false;
    }

    function sync(doc, options) {
      const root = documentOf(doc);
      const settings = options || {};
      const theme = currentTheme(root);
      const effective = theme === 'neon-fusion'
        ? effectiveMotion(readMotionPrefs(opts.storage), detectEnvironment(windowOf()))
        : {mode: 'off', ambient: false, reason: 'other-theme'};
      applyMotionAttrs(effective, root);
      ensureAmbient(root, effective);
      if (theme === 'neon-fusion') bindLifecycle(root);
      if (settings.intro && theme === 'neon-fusion') playIntro(root, effective);
      if (theme !== 'neon-fusion') {
        setPaused(false, root);
        cancelMap(charts);
        cancelMap(metricTimers);
        cancelMap(ringTimers);
        if (introTimer) { clearTimeout(introTimer); introTimer = null; }
        const topbar = root && root.querySelector && root.querySelector('.topbar');
        if (topbar) topbar.classList.remove('nf-intro');
        introPlayed = false;
      } else if (root && root.hidden) setPaused(true, root);
      syncControls(root, effective, theme);
      return effective;
    }

    function syncControls(doc, effective, theme) {
      const root = documentOf(doc);
      if (!root || !root.getElementById) return;
      const prefs = readMotionPrefs(opts.storage);
      const motionSelect = root.getElementById('v1-theme-motion');
      const ambientToggle = root.getElementById('v1-theme-ambient');
      const fields = root.getElementById('v1-theme-motion-fields');
      if (fields) fields.hidden = theme !== 'neon-fusion';
      if (motionSelect && motionSelect.value !== prefs.motionMode) motionSelect.value = prefs.motionMode;
      if (ambientToggle) ambientToggle.checked = prefs.ambientEffectsEnabled;
    }

    function teardown(doc) {
      const root = documentOf(doc);
      cancelMap(charts);
      cancelMap(metricTimers);
      cancelMap(ringTimers);
      if (introTimer) { clearTimeout(introTimer); introTimer = null; }
      if (ambientNode && ambientNode.parentNode) ambientNode.parentNode.removeChild(ambientNode);
      ambientNode = null;
      if (root && root.documentElement) {
        root.documentElement.removeAttribute('data-nf-motion');
        root.documentElement.removeAttribute('data-nf-ambient');
        root.documentElement.removeAttribute('data-nf-paused');
      }
    }

    return {
      shouldAnimate,
      currentEffective,
      noteStatus,
      liveChartSamples,
      interpolateChartSamples,
      canLerpChart,
      animateMetricText,
      applyMetricCards,
      animateResourceRing,
      watchCanvas,
      sync,
      teardown,
      setPaused,
      playIntro,
      ensureAmbient,
      bindLifecycle,
      unbindLifecycle,
      charts,
      statusMemory,
      metricTimers,
      isBound: () => bound,
      isPaused: () => paused,
      particleCounts: PARTICLE_COUNTS,
    };
  }

  const defaultMotion = createMotionRuntime();

  function applyTheme(id, options) {
    const theme = normalizeTheme(id);
    const opts = options || {};
    const root = opts.document || (typeof document !== 'undefined' ? document : null);
    const previous = currentTheme(root);
    applyThemeAttrs(theme, root);
    if (opts.persist !== false) writeStoredTheme(theme, opts.storage);
    const control = root && root.getElementById && root.getElementById('v1-theme-select');
    if (control && control.value !== theme) control.value = theme;
    const motionApi = opts.motion || defaultMotion;
    if (motionApi && typeof motionApi.sync === 'function') {
      motionApi.sync(root, {intro: theme === 'neon-fusion' && previous !== theme});
    }
    if (opts.emit !== false && root && typeof root.dispatchEvent === 'function' && typeof CustomEvent === 'function') {
      try { root.dispatchEvent(new CustomEvent('hopworld-theme-change', {detail: {theme}})); } catch (error) {}
    }
    return theme;
  }

  function bindThemeControl(doc) {
    const root = doc || (typeof document !== 'undefined' ? document : null);
    if (!root || !root.getElementById) return;
    const control = root.getElementById('v1-theme-select');
    if (control && control.dataset.themeBound !== 'true') {
      control.dataset.themeBound = 'true';
      const applied = applyTheme(readStoredTheme(), {document: root, persist: false, emit: false});
      control.value = applied;
      control.addEventListener('change', () => {
        applyTheme(control.value, {document: root, persist: true, emit: true});
      });
    }
    const motionSelect = root.getElementById('v1-theme-motion');
    const ambientToggle = root.getElementById('v1-theme-ambient');
    if (motionSelect && motionSelect.dataset.themeBound !== 'true') {
      motionSelect.dataset.themeBound = 'true';
      motionSelect.addEventListener('change', () => {
        const prefs = readMotionPrefs();
        writeMotionPrefs({motionMode: motionSelect.value, ambientEffectsEnabled: prefs.ambientEffectsEnabled});
        defaultMotion.sync(root);
      });
    }
    if (ambientToggle && ambientToggle.dataset.themeBound !== 'true') {
      ambientToggle.dataset.themeBound = 'true';
      ambientToggle.addEventListener('change', () => {
        const prefs = readMotionPrefs();
        writeMotionPrefs({motionMode: prefs.motionMode, ambientEffectsEnabled: !!ambientToggle.checked});
        defaultMotion.sync(root);
      });
    }
    defaultMotion.sync(root);
  }

  const api = {
    STORAGE_KEY,
    MOTION_STORAGE_KEY,
    DEFAULT_THEME,
    DEFAULT_MOTION_MODE,
    MOTION_MODES,
    THEMES,
    METRIC_MS,
    CHART_MS,
    PARTICLE_COUNTS,
    ORB_COUNTS,
    normalizeTheme,
    readStoredTheme,
    writeStoredTheme,
    normalizeMotionPrefs,
    readMotionPrefs,
    writeMotionPrefs,
    detectEnvironment,
    effectiveMotion,
    currentTheme,
    applyTheme,
    applyMotionAttrs,
    bindThemeControl,
    createMotionRuntime,
    motion: defaultMotion,
  };

  if (typeof document !== 'undefined') {
    applyTheme(readStoredTheme(), {persist: false, emit: false, motion: defaultMotion});
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => bindThemeControl());
    else bindThemeControl();
  }

  if (typeof window !== 'undefined') window.hopworldTheme = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
})();
