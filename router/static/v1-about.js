(() => {
  const UNCONFIGURED = '暂未配置';
  const SUPPORT_CHANNELS = Object.freeze([
    Object.freeze({
      id: 'wechat',
      title: '微信',
      payload: 'wxp://f2f0GMSCwBWM6Esa1EBmJLXz3gQZkWKmQ0XdgXpmCiGnxxM',
      description: '使用微信扫描二维码自愿支持',
    }),
    Object.freeze({
      id: 'usdt',
      title: 'USDT',
      payload: '0x67c110DCDBE2174AA32c84D26D8C337294086838',
      description: 'USDT (ERC20)，仅支持 Ethereum 网络',
      showPayload: true,
    }),
    Object.freeze({
      id: 'alipay',
      title: '支付宝',
      payload: 'https://qr.alipay.com/fkx18579lgdnd6ogigc3qc1',
      description: '使用支付宝扫描二维码自愿支持',
    }),
  ]);

  function textOf(value) {
    return value == null ? '' : String(value);
  }

  function configuredPayload(value) {
    const payload = textOf(value);
    return payload ? payload : null;
  }

  function encodeQr(payload, cellSize, margin) {
    const text = configuredPayload(payload);
    if (!text || typeof qrcode !== 'function') return null;
    const size = Number(cellSize) > 0 ? Number(cellSize) : 4;
    const pad = Number(margin) >= 0 ? Number(margin) : 2;
    const previousEncoding = qrcode.stringToBytes;
    try {
      if (qrcode.stringToBytesFuncs && qrcode.stringToBytesFuncs['UTF-8']) {
        qrcode.stringToBytes = qrcode.stringToBytesFuncs['UTF-8'];
      }
      const qr = qrcode(0, 'M');
      qr.addData(text);
      qr.make();
      const moduleCount = qr.getModuleCount();
      const modules = [];
      for (let row = 0; row < moduleCount; row += 1) {
        const line = [];
        for (let col = 0; col < moduleCount; col += 1) line.push(!!qr.isDark(row, col));
        modules.push(line);
      }
      return {
        payload: text,
        moduleCount,
        modules,
        svg: qr.createSvgTag(size, pad),
      };
    } catch (error) {
      return null;
    } finally {
      qrcode.stringToBytes = previousEncoding;
    }
  }

  function channelView(channel) {
    const payload = configuredPayload(channel && channel.payload);
    const encoded = payload ? encodeQr(payload, 3, 2) : null;
    return {
      id: channel.id,
      title: channel.title,
      description: channel.description || '',
      payload,
      showPayload: !!(channel && channel.showPayload && payload),
      configured: !!encoded,
      placeholder: encoded ? '' : UNCONFIGURED,
      svg: encoded ? encoded.svg : '',
    };
  }

  function applySvgSize(svg, pixels) {
    if (!svg) return;
    svg.setAttribute('width', String(pixels));
    svg.setAttribute('height', String(pixels));
    svg.setAttribute('role', 'img');
  }

  function renderSupportCards(container) {
    if (!container) return [];
    const views = SUPPORT_CHANNELS.map(channelView);
    container.replaceChildren(...views.map(view => {
      const card = document.createElement('article');
      card.className = 'v1-about-channel';
      card.dataset.channel = view.id;
      const title = document.createElement('h3');
      title.className = 'v1-about-channel-title';
      title.textContent = view.title;
      const qr = document.createElement('button');
      qr.type = 'button';
      qr.className = 'v1-about-qr';
      if (view.configured) {
        qr.dataset.aboutQr = view.payload;
        qr.dataset.aboutQrLabel = view.title;
        qr.setAttribute('aria-label', '放大' + view.title + '二维码');
        qr.innerHTML = view.svg;
        applySvgSize(qr.querySelector('svg'), 132);
        qr.querySelector('svg')?.setAttribute('aria-hidden', 'true');
      } else {
        qr.disabled = true;
        qr.classList.add('is-empty');
        qr.textContent = view.placeholder;
        qr.setAttribute('aria-label', view.title + view.placeholder);
      }
      const detail = document.createElement('p');
      detail.className = 'v1-about-channel-detail';
      detail.textContent = view.description;
      card.append(title, qr, detail);
      if (view.showPayload) {
        const address = document.createElement('p');
        address.className = 'v1-about-address';
        address.textContent = view.payload;
        card.append(address);
      }
      return card;
    }));
    return views;
  }

  function bindSupportUi(root) {
    const doc = root || (typeof document !== 'undefined' ? document : null);
    if (!doc) return;
    const container = doc.getElementById('v1-about-support-cards');
    if (container) renderSupportCards(container);
    const dialog = doc.getElementById('v1-about-qr-dialog');
    const preview = doc.getElementById('v1-about-qr-preview');
    const heading = doc.getElementById('v1-about-qr-title');
    const close = doc.getElementById('v1-about-qr-close');
    if (container && dialog && preview) {
      container.addEventListener('click', event => {
        const trigger = event.target.closest('[data-about-qr]');
        if (!trigger) return;
        event.preventDefault();
        const encoded = encodeQr(trigger.dataset.aboutQr, 6, 2);
        if (!encoded) return;
        if (heading) heading.textContent = trigger.dataset.aboutQrLabel || '二维码';
        preview.innerHTML = encoded.svg;
        const svg = preview.querySelector('svg');
        applySvgSize(svg, 240);
        if (svg) svg.setAttribute('aria-label', (trigger.dataset.aboutQrLabel || '') + '二维码');
        if (typeof dialog.showModal === 'function') dialog.showModal();
      });
    }
    if (close && dialog) close.addEventListener('click', () => dialog.close());
  }

  const api = {
    UNCONFIGURED,
    SUPPORT_CHANNELS,
    configuredPayload,
    encodeQr,
    channelView,
    renderSupportCards,
    bindSupportUi,
  };
  if (typeof module === 'object' && module.exports) module.exports = api;
  const globalRoot = typeof window !== 'undefined' ? window : globalThis;
  globalRoot.hopworldAbout = api;
  if (typeof document !== 'undefined') bindSupportUi(document);
})();
