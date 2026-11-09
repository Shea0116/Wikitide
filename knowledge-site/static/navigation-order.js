(() => {
  'use strict';
  const user = document.body.dataset.userId;
  const csrf = document.body.dataset.orderCsrf;
  const roots = [...document.querySelectorAll('[data-order-root]')];
  if (!user || !csrf) return;
  const groupId = (kind, parent) => JSON.stringify([kind, parent]);
  let groups = new Map(), ready = false, fetching = false, saving = false, gesture = null;
  let suppressClick = false;
  const notices = roots.map(root => {
    const status = document.createElement('p');
    status.className = 'order-status'; status.role = 'status'; status.ariaLive = 'polite';
    root.append(status); return status;
  });
  const announce = message => notices.forEach(node => { if (node.textContent !== message) node.textContent = message; });
  const listOf = item => item.parentElement;
  const siblings = item => [...listOf(item).children].filter(child =>
    child.dataset.orderKind === item.dataset.orderKind && child.dataset.orderKey);
  const info = item => ({kind: item.dataset.orderKind, parent: listOf(item).dataset.orderParent});
  const restore = (list, children) => children.forEach(child => list.append(child));
  function applyOrders() {
    const focused = document.activeElement;
    document.querySelectorAll('[data-order-parent]').forEach(list => {
      for (const kind of ['category', 'note']) {
        const record = groups.get(groupId(kind, list.dataset.orderParent));
        if (!record) continue;
        const rank = new Map(record.keys.map((key, index) => [key, index]));
        const children = [...list.children];
        const sorted = children.filter(child => child.dataset.orderKind === kind)
          .sort((a, b) => (rank.get(a.dataset.orderKey) ?? Infinity) - (rank.get(b.dataset.orderKey) ?? Infinity));
        let index = 0;
        const ordered = children.map(child => child.dataset.orderKind === kind ? sorted[index++] : child);
        if (ordered.some((child, i) => child !== children[i])) ordered.forEach(child => list.append(child));
      }
    });
    const rootOrder = groups.get(groupId('category', ''));
    if (rootOrder) {
      const rank = new Map(rootOrder.keys.map((key, index) => [key, index]));
      const parents = new Set([...document.querySelectorAll('[data-order-category-key]')].map(link => link.parentElement));
      parents.forEach(parent => {
        const children = [...parent.children];
        const links = children.filter(child => child.dataset.orderCategoryKey)
          .sort((a, b) => (rank.get(a.dataset.orderCategoryKey) ?? Infinity) - (rank.get(b.dataset.orderCategoryKey) ?? Infinity));
        let index = 0;
        const ordered = children.map(child => child.dataset.orderCategoryKey ? links[index++] : child);
        if (ordered.some((child, i) => child !== children[i])) ordered.forEach(child => parent.append(child));
      });
    }
    if (focused?.classList.contains('order-handle') && focused.isConnected) focused.focus({preventScroll: true});
  }
  function signalOthers() {
    try { localStorage.setItem(`wikitide-order:${user}`, String(Date.now())); } catch (_) { /* Focus and polling also synchronize. */ }
  }
  async function refresh({initial = false} = {}) {
    if (fetching || saving || gesture || document.hidden) return;
    fetching = true;
    try {
      const response = await fetch('/api/navigation-order', {cache: 'no-store', credentials: 'same-origin'});
      if (!response.ok) throw new Error(response.status === 401 ? window.WikitideI18n.t("登录已过期，请重新登录。") : window.WikitideI18n.t("暂时无法加载顺序，请稍后重试。"));
      const data = await response.json();
      const incoming = new Map(data.groups.map(group => [groupId(group.kind, group.parent), group]));
      const changed = ready && JSON.stringify([...groups]) !== JSON.stringify([...incoming]);
      groups = incoming; ready = true; applyOrders();
      if (changed && !initial && document.body.classList.contains('browse-page')) { location.reload(); return; }
      announce(changed ? window.WikitideI18n.t("已同步最新顺序。") : window.WikitideI18n.t("长按拖动按钮排序，仅同步当前账号。"));
    } catch (error) { announce(error.message); }
    finally { fetching = false; }
  }
  async function save(item, original) {
    const list = listOf(item), {kind, parent} = info(item);
    const record = groups.get(groupId(kind, parent));
    const keys = siblings(item).map(child => child.dataset.orderKey);
    if (!record || JSON.stringify(keys) === JSON.stringify(original.filter(child => child.dataset.orderKind === kind).map(child => child.dataset.orderKey))) {
      announce(window.WikitideI18n.t("顺序未改变。")); return;
    }
    saving = true; document.body.classList.add('order-saving'); announce(window.WikitideI18n.t("正在保存顺序…"));
    let succeeded = false;
    try {
      const response = await fetch('/api/navigation-order', {
        method: 'PUT', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
        body: JSON.stringify({kind, parent, keys, version: record.version})
      });
      if (response.status === 409) {
        const latest = await response.json(); groups.set(groupId(kind, parent), latest); applyOrders();
        announce(window.WikitideI18n.t("其他页面已调整此目录，已加载最新顺序，请重新拖动。"));
      } else if (!response.ok) {
        restore(list, original);
        announce(response.status === 401 ? window.WikitideI18n.t("登录已过期，请重新登录。") : window.WikitideI18n.t("保存失败，已恢复原顺序；请刷新后重试。"));
      } else {
        const saved = await response.json(); groups.set(groupId(kind, parent), saved); applyOrders();
        succeeded = true; announce(window.WikitideI18n.t("顺序已保存，并同步到当前账号的其他页面和设备。")); signalOthers();
      }
    } catch (_) {
      restore(list, original); announce(window.WikitideI18n.t("网络异常，暂未确认保存；请刷新核对顺序。"));
    } finally {
      saving = false; document.body.classList.remove('order-saving');
    }
    if (succeeded && document.body.classList.contains('browse-page')) location.reload();
  }
  const canStart = () => ready && !fetching && !saving && !gesture;
  function finish(cancelled = false) {
    if (!gesture) return;
    const active = gesture; gesture = null;
    clearTimeout(active.timer); cancelAnimationFrame(active.frame);
    if (!active.started) return;
    suppressClick = true;
    // Suppress only the synthetic click immediately following the pointer release.
    setTimeout(() => { suppressClick = false; }, 0);
    active.item.classList.remove('order-dragging'); active.handle.ariaPressed = 'false';
    active.ghost.remove(); document.body.classList.remove('order-drag-active');
    if (active.root.hasPointerCapture(active.pointer)) active.root.releasePointerCapture(active.pointer);
    if (cancelled) { restore(active.list, active.original); announce(window.WikitideI18n.t("已取消排序。")); }
    else void save(active.item, active.original);
  }
  function moveAt(active) {
    const peers = siblings(active.item).filter(item => item !== active.item && item.getClientRects().length);
    const before = peers.find(peer => {
      const label = peer.querySelector(':scope > a, :scope > details > summary');
      const box = (label || peer).getBoundingClientRect();
      return active.y < box.top + box.height / 2;
    });
    if (peers.length) {
      const reference = before || peers[peers.length - 1].nextSibling;
      if (reference !== active.item && reference !== active.item.nextSibling) active.list.insertBefore(active.item, reference);
    }
    active.ghost.style.left = `${Math.max(8, Math.min(active.x + 12, innerWidth - active.ghost.offsetWidth - 8))}px`;
    active.ghost.style.top = `${Math.max(8, Math.min(active.y + 12, innerHeight - active.ghost.offsetHeight - 8))}px`;
  }
  function scrollFrame() {
    const active = gesture;
    if (!active?.started) return;
    let container = active.list;
    while (container && container !== document.body) {
      if (/(auto|scroll)/.test(getComputedStyle(container).overflowY) && container.scrollHeight > container.clientHeight) break;
      container = container.parentElement;
    }
    const area = container && container !== document.body ? container : document.scrollingElement;
    const box = area === document.scrollingElement ? {top: 0, bottom: innerHeight} : area.getBoundingClientRect();
    let delta = 0;
    if (active.y < box.top + 45) delta = -8;
    else if (active.y > box.bottom - 45) delta = 8;
    if (delta) { area.scrollTop += delta; moveAt(active); }
    active.frame = requestAnimationFrame(scrollFrame);
  }
  function begin(active) {
    if (gesture !== active) return;
    active.started = true; suppressClick = true;
    // Document listeners still track the gesture if a browser declines capture.
    try { active.root.setPointerCapture(active.pointer); } catch (_) { /* Pointer may be crossing a native surface. */ }
    active.item.classList.add('order-dragging'); active.handle.ariaPressed = 'true';
    document.body.classList.add('order-drag-active');
    const ghost = document.createElement('div'); ghost.className = 'order-ghost';
    ghost.textContent = active.handle.dataset.orderLabel; document.body.append(ghost); active.ghost = ghost;
    moveAt(active); scrollFrame(); announce(window.WikitideI18n.t("正在拖动；松开保存，Esc 取消。"));
  }
  roots.forEach(root => {
    root.querySelectorAll('[data-order-key]').forEach(item => {
      item.classList.add('order-item');
      const handle = document.createElement('button'); handle.type = 'button'; handle.className = 'order-handle';
      const label = item.querySelector(':scope > a, :scope > details > summary');
      // Links otherwise start the browser's native URL drag before our hold timer.
      item.querySelectorAll('a, img').forEach(element => { element.draggable = false; });
      const name = label?.textContent.trim() || window.WikitideI18n.t("项目");
      handle.dataset.orderLabel = name; handle.textContent = '⠿';
      handle.title = window.WikitideI18n.t("长按拖动，或按 Alt+↑ / Alt+↓ 调整顺序");
      handle.setAttribute('aria-label', window.WikitideI18n.t("调整 {0} 的顺序", [name])); handle.ariaPressed = 'false'; item.append(handle);
      handle.addEventListener('click', event => { event.preventDefault(); event.stopPropagation(); });
      handle.addEventListener('keydown', event => {
        if (!event.altKey || !['ArrowUp', 'ArrowDown'].includes(event.key)) return;
        event.preventDefault(); if (!canStart()) return;
        const peers = siblings(item), index = peers.indexOf(item);
        const other = peers[index + (event.key === 'ArrowUp' ? -1 : 1)];
        if (!other) return;
        const list = listOf(item), original = [...list.children];
        list.insertBefore(item, event.key === 'ArrowUp' ? other : other.nextSibling);
        handle.focus(); void save(item, original);
      });
    });
    root.addEventListener('pointerdown', event => {
      if (event.button !== 0 || !canStart()) return;
      const item = event.target.closest('[data-order-key]');
      if (!item || !root.contains(item)) return;
      const handle = item.querySelector(':scope > .order-handle');
      const label = item.querySelector(':scope > a, :scope > details > summary');
      if (!handle || (!handle.contains(event.target) && (!label?.contains(event.target) || event.pointerType !== 'mouse'))) return;
      const {kind, parent} = info(item);
      if (!groups.has(groupId(kind, parent))) return;
      if (handle.contains(event.target)) event.preventDefault();
      const active = {item, handle, root, list: listOf(item), original: [...listOf(item).children],
        pointer: event.pointerId, pointerType: event.pointerType, x: event.clientX, y: event.clientY,
        initialX: event.clientX, initialY: event.clientY, started: false};
      gesture = active; active.timer = setTimeout(() => begin(active), 420);
    });
    root.addEventListener('dragstart', event => {
      if (event.target.closest('[data-order-key]')) event.preventDefault();
    }, true);
    root.addEventListener('contextmenu', event => {
      if (gesture?.started || event.target.closest('.order-handle')) event.preventDefault();
    });
  });
  document.addEventListener('pointermove', event => {
    if (!gesture || event.pointerId !== gesture.pointer) return;
    gesture.x = event.clientX; gesture.y = event.clientY;
    if (!gesture.started && Math.hypot(event.clientX - gesture.initialX, event.clientY - gesture.initialY) > 10) {
      if (gesture.pointerType === 'mouse') {
        // Desktop users can drag immediately; touch users retain hold-to-drag and normal scrolling.
        clearTimeout(gesture.timer); begin(gesture);
      } else { finish(true); return; }
    }
    if (gesture.started) { event.preventDefault(); moveAt(gesture); }
  }, {passive: false});
  document.addEventListener('pointerup', event => { if (gesture?.pointer === event.pointerId) finish(); });
  document.addEventListener('pointercancel', event => { if (gesture?.pointer === event.pointerId) finish(true); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && gesture) { event.preventDefault(); finish(true); } });
  document.addEventListener('click', event => {
    if (suppressClick) { event.preventDefault(); event.stopPropagation(); }
  }, true);
  window.addEventListener('storage', event => { if (event.key === `wikitide-order:${user}`) void refresh(); });
  window.addEventListener('focus', () => void refresh());
  window.addEventListener('blur', () => finish(true));
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) finish(true); else void refresh();
  });
  setInterval(() => void refresh(), 20000);
  void refresh({initial: true});
})();
