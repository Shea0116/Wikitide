(() => {
  const input = document.getElementById('site-search');
  const panel = document.getElementById('search-suggestions');
  if (!input?.form || !panel) return;
  const form = input.form;
  const list = panel.querySelector('[role=listbox]');
  const status = panel.querySelector('[role=status]');
  let timer, controller, sequence = 0, composing = false;
  let options = [], active = -1;

  const cancel = () => {
    clearTimeout(timer);
    controller?.abort();
    sequence += 1;
  };
  const select = (index) => {
    active = index;
    options.forEach((option, i) => option.setAttribute('aria-selected', String(i === active)));
    if (active < 0) input.removeAttribute('aria-activedescendant');
    else {
      input.setAttribute('aria-activedescendant', options[active].id);
      options[active].scrollIntoView({ block: 'nearest', behavior: 'instant' });
    }
  };
  const hide = () => {
    cancel();
    panel.hidden = true;
    form.classList.remove('suggestion-open');
    input.setAttribute('aria-expanded', 'false');
    select(-1);
  };
  const show = () => {
    panel.hidden = false;
    form.classList.add('suggestion-open');
    input.setAttribute('aria-expanded', 'true');
  };
  const highlightedTitle = (title, query) => {
    const element = document.createElement('strong');
    const pattern = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
    let start = 0;
    for (const match of title.matchAll(pattern)) {
      element.append(document.createTextNode(title.slice(start, match.index)));
      const mark = document.createElement('mark');
      mark.textContent = match[0];
      element.append(mark);
      start = match.index + match[0].length;
    }
    element.append(document.createTextNode(title.slice(start)));
    return element;
  };
  const addOption = (href, className) => {
    const item = document.createElement('li');
    item.setAttribute('role', 'presentation');
    const link = document.createElement('a');
    link.href = href;
    link.className = className;
    link.id = `search-option-${options.length}`;
    link.setAttribute('role', 'option');
    link.setAttribute('aria-selected', 'false');
    link.tabIndex = -1;
    link.addEventListener('pointerdown', event => event.preventDefault());
    const index = options.length;
    link.addEventListener('pointermove', () => select(index));
    item.append(link);
    list.append(item);
    options.push(link);
    return link;
  };
  const render = (rows, query) => {
    select(-1);
    options = [];
    list.replaceChildren();
    status.textContent = rows.length ? window.WikitideI18n.t("匹配的笔记 · {0} 条", [rows.length]) : window.WikitideI18n.t("暂无匹配笔记，可以查看全部搜索结果");
    rows.forEach(row => {
      const link = addOption(`/n/${row.id}`, 'suggestion-option');
      const icon = document.createElement('span');
      icon.className = 'suggestion-icon';
      icon.setAttribute('aria-hidden', 'true');
      const body = document.createElement('span');
      body.className = 'suggestion-body';
      const category = document.createElement('small');
      category.textContent = row.category;
      body.append(highlightedTitle(row.title, query), category);
      link.append(icon, body);
    });
    const all = addOption(`/search?q=${encodeURIComponent(query)}`, 'suggestion-all');
    all.textContent = window.WikitideI18n.t("查看“{0}”的全部结果 →", [query]);
    show();
  };
  const load = async (query, selectFirst = false) => {
    const request = ++sequence;
    controller = new AbortController();
    try {
      const response = await fetch(`/api/search/suggestions?q=${encodeURIComponent(query)}`, {
        credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
      });
      if (!response.ok) throw new Error('search');
      const data = await response.json();
      if (request !== sequence || input.value.trim() !== query || document.activeElement !== input || composing) return;
      render(data.suggestions, query);
      if (selectFirst) select(0);
    } catch (error) {
      if (error.name === 'AbortError' || request !== sequence || document.activeElement !== input) return;
      render([], query);
      status.textContent = window.WikitideI18n.t("联想暂时不可用，按回车仍可搜索");
    }
  };
  const schedule = (immediate = false, selectFirst = false) => {
    cancel();
    select(-1);
    const query = input.value.trim();
    if (!query || composing) { hide(); return; }
    // Clear results from the previous term before allowing a keyboard selection.
    options = [];
    list.replaceChildren();
    if (!panel.hidden) status.textContent = window.WikitideI18n.t("正在匹配…");
    if (immediate) load(query, selectFirst);
    else timer = setTimeout(() => load(query), 180);
  };
  input.addEventListener('input', () => schedule());
  input.addEventListener('focus', () => schedule());
  input.addEventListener('compositionstart', () => { composing = true; hide(); });
  input.addEventListener('compositionend', () => { composing = false; schedule(); });
  input.addEventListener('keydown', event => {
    if (event.isComposing || composing) return;
    if (event.key === 'Escape') {
      event.preventDefault(); event.stopPropagation(); hide();
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (!input.value.trim()) return;
      event.preventDefault();
      if (panel.hidden || !options.length) { schedule(true, true); return; }
      const next = active < 0 ? (event.key === 'ArrowDown' ? 0 : options.length - 1)
        : (active + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
      select(next);
    } else if (event.key === 'Enter' && !panel.hidden && active >= 0) {
      event.preventDefault(); options[active].click();
    }
  });
  input.addEventListener('blur', hide);
  form.addEventListener('submit', hide);
  document.addEventListener('pointerdown', event => { if (!form.contains(event.target)) hide(); });
  window.addEventListener('pageshow', hide);
})();
