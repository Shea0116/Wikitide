(() => {
  const prose = document.querySelector('.article .prose');
  if (!prose || typeof HTMLDialogElement === 'undefined') return;

  let dialog, viewport, image, status, percentage, source, observer;
  let scale = 1, fitScale = 1, x = 0, y = 0, fitted = true;
  let width = 0, height = 0, moved = false, lastTap = 0;
  const pointers = new Map();
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const measureFit = () => Math.min(Math.max(1, viewport.clientWidth - 32) / width, Math.max(1, viewport.clientHeight - 32) / height, 1);

  function paint() {
    x = clamp(x, -Math.max(0, (width * scale - viewport.clientWidth + 32) / 2), Math.max(0, (width * scale - viewport.clientWidth + 32) / 2));
    y = clamp(y, -Math.max(0, (height * scale - viewport.clientHeight + 32) / 2), Math.max(0, (height * scale - viewport.clientHeight + 32) / 2));
    image.style.transform = `translate(-50%, -50%) translate(${x}px, ${y}px) scale(${scale})`;
    percentage.value = `${Math.round(scale * 100)}%`;
    viewport.classList.toggle('is-zoomed', scale > fitScale + 0.001);
    dialog.querySelector('[data-zoom-out]').disabled = scale <= Math.min(fitScale, 0.1) + 0.001;
    dialog.querySelector('[data-zoom-in]').disabled = scale >= 5;
  }

  function fit() {
    if (!width || !height) return;
    fitScale = measureFit();
    scale = fitScale; x = 0; y = 0; fitted = true; paint();
  }

  function zoom(next, clientX, clientY) {
    if (!width || !height) return;
    next = clamp(next, Math.min(fitScale, 0.1), 5);
    const rect = viewport.getBoundingClientRect();
    const px = (clientX ?? rect.left + rect.width / 2) - rect.left - rect.width / 2;
    const py = (clientY ?? rect.top + rect.height / 2) - rect.top - rect.height / 2;
    x = px - (px - x) * next / scale; y = py - (py - y) * next / scale;
    scale = next; fitted = false; paint();
  }

  function build() {
    dialog = document.createElement('dialog');
    dialog.className = 'image-preview';
    dialog.setAttribute('aria-labelledby', 'image-preview-title');
    dialog.innerHTML = window.WikitideI18n.html("\n      <header class=\"image-preview-toolbar\">\n        <span id=\"image-preview-title\" class=\"image-preview-title\">图片预览</span>\n        <div class=\"image-preview-controls\" aria-label=\"图片缩放\">\n          <button type=\"button\" data-zoom-out aria-label=\"缩小图片\" title=\"缩小（−）\">−</button>\n          <output class=\"image-preview-percentage\" aria-label=\"缩放比例\">100%</output>\n          <button type=\"button\" data-zoom-in aria-label=\"放大图片\" title=\"放大（+）\">+</button>\n          <button type=\"button\" data-fit>适应窗口</button>\n          <button type=\"button\" data-original title=\"按原始像素显示\">100%</button>\n        </div>\n        <button type=\"button\" class=\"image-preview-close\" data-close aria-label=\"关闭图片预览\" title=\"关闭（Esc）\" autofocus>×</button>\n      </header>\n      <div class=\"image-preview-viewport\">\n        <img class=\"image-preview-image\" alt=\"\" draggable=\"false\" hidden>\n        <p class=\"image-preview-status\" role=\"status\">正在加载图片…</p>\n      </div>\n      <p class=\"image-preview-hint\">滚轮或双指缩放 · 拖动查看细节 · Esc 关闭</p>");
    document.body.append(dialog);
    viewport = dialog.querySelector('.image-preview-viewport');
    image = dialog.querySelector('.image-preview-image');
    status = dialog.querySelector('.image-preview-status');
    percentage = dialog.querySelector('output');
    const close = () => dialog.close();
    dialog.querySelector('[data-close]').addEventListener('click', close);
    dialog.querySelector('[data-fit]').addEventListener('click', fit);
    dialog.querySelector('[data-original]').addEventListener('click', () => zoom(1));
    dialog.querySelector('[data-zoom-in]').addEventListener('click', () => zoom(scale * 1.25));
    dialog.querySelector('[data-zoom-out]').addEventListener('click', () => zoom(scale / 1.25));
    dialog.addEventListener('click', event => { if (event.target === dialog) close(); });
    dialog.addEventListener('close', () => {
      document.documentElement.classList.remove('image-preview-open');
      pointers.clear(); observer?.disconnect();
      viewport.classList.remove('is-dragging');
      image.removeAttribute('src'); image.hidden = true;
      source?.focus({ preventScroll: true });
    });
    dialog.addEventListener('keydown', event => {
      event.stopPropagation();
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key === '+' || event.key === '=') { event.preventDefault(); zoom(scale * 1.25); }
      if (event.key === '-') { event.preventDefault(); zoom(scale / 1.25); }
      if (event.key === '0') { event.preventDefault(); fit(); }
    });
    image.addEventListener('load', () => {
      if (!dialog.open) return;
      width = image.naturalWidth; height = image.naturalHeight;
      image.style.width = `${width}px`; image.style.height = `${height}px`;
      image.hidden = false; status.hidden = true; fit();
    });
    image.addEventListener('error', () => {
      if (!dialog.open) return;
      status.hidden = false; status.textContent = window.WikitideI18n.t("图片加载失败，请关闭后重试。");
    });
    viewport.addEventListener('wheel', event => {
      event.preventDefault();
      zoom(scale * Math.exp(-clamp(event.deltaY, -100, 100) * 0.002), event.clientX, event.clientY);
    }, { passive: false });
    viewport.addEventListener('dblclick', event => {
      event.preventDefault();
      if (scale > fitScale + 0.001) fit(); else zoom(Math.max(1, scale * 2), event.clientX, event.clientY);
    });
    viewport.addEventListener('pointerdown', event => {
      if (event.pointerType === 'mouse' && event.button !== 0) return;
      if (pointers.size === 0) moved = false;
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY, startX: event.clientX, startY: event.clientY, target: event.target });
      viewport.setPointerCapture(event.pointerId);
    });
    viewport.addEventListener('pointermove', event => {
      const previous = pointers.get(event.pointerId);
      if (!previous) return;
      const points = [...pointers.values()];
      if (points.length === 2) {
        const other = points.find(point => point !== previous);
        const before = Math.hypot(previous.x - other.x, previous.y - other.y);
        const after = Math.hypot(event.clientX - other.x, event.clientY - other.y);
        if (before > 0) zoom(scale * after / before, (event.clientX + other.x) / 2, (event.clientY + other.y) / 2);
        moved = true;
      } else if (points.length === 1) {
        x += event.clientX - previous.x; y += event.clientY - previous.y;
        if (Math.hypot(event.clientX - previous.startX, event.clientY - previous.startY) > 4) moved = true;
        if (moved) viewport.classList.add('is-dragging');
        paint();
      }
      previous.x = event.clientX; previous.y = event.clientY;
    });
    function endPointer(event) {
      const pointer = pointers.get(event.pointerId);
      pointers.delete(event.pointerId);
      if (pointers.size) return;
      viewport.classList.remove('is-dragging');
      if (event.type !== 'pointerup' || !pointer || moved) return;
      if (pointer.target === viewport) { close(); return; }
      if (event.pointerType === 'touch') {
        if (Date.now() - lastTap < 300) {
          if (scale > fitScale + 0.001) fit(); else zoom(Math.max(1, fitScale * 2), event.clientX, event.clientY);
          lastTap = 0;
        }
        else lastTap = Date.now();
      }
    }
    viewport.addEventListener('pointerup', endPointer);
    viewport.addEventListener('pointercancel', endPointer);
    if (typeof ResizeObserver !== 'undefined') observer = new ResizeObserver(() => {
      if (!dialog.open || !width) return;
      fitScale = measureFit();
      if (fitted) fit(); else paint();
    });
  }

  function open(target) {
    const url = target.currentSrc || target.src;
    if (!url) return;
    if (!dialog) build();
    source = target; width = 0; height = 0; x = 0; y = 0; lastTap = 0; pointers.clear();
    status.textContent = window.WikitideI18n.t("正在加载图片…"); status.hidden = false; image.hidden = true;
    percentage.value = '—';
    dialog.querySelector('[data-zoom-out]').disabled = true;
    dialog.querySelector('[data-zoom-in]').disabled = true;
    dialog.querySelector('.image-preview-title').textContent = target.alt || window.WikitideI18n.t("图片预览");
    image.alt = target.alt || window.WikitideI18n.t("放大的正文图片");
    dialog.showModal(); document.documentElement.classList.add('image-preview-open');
    observer?.observe(viewport); image.src = url;
  }

  prose.querySelectorAll('img').forEach(target => {
    target.classList.add('image-preview-trigger');
    target.tabIndex = 0; target.setAttribute('role', 'button'); target.setAttribute('aria-haspopup', 'dialog');
    target.setAttribute('aria-label', window.WikitideI18n.t("{0}，点击放大预览", [target.alt || window.WikitideI18n.t("正文图片")]));
  });
  prose.addEventListener('click', event => {
    const target = event.target.closest('img');
    if (target && prose.contains(target)) { event.preventDefault(); open(target); }
  });
  prose.addEventListener('keydown', event => {
    if (event.target.tagName === 'IMG' && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault(); open(event.target);
    }
  });
})();
