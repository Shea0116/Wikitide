(() => {
  const dialog = document.querySelector('[data-share-dialog]');
  if (!dialog) return;
  const form = dialog.querySelector('[data-share-form]');
  const status = dialog.querySelector('[data-share-status]');
  const link = dialog.querySelector('[data-share-link]');
  const urlInput = dialog.querySelector('#share-url');
  const users = dialog.querySelector('[data-share-users]');
  let target, state, busy = false;
  const setBusy = value => {
    busy = value;
    form.querySelectorAll('input,select,button').forEach(element => { element.disabled = value; });
    dialog.querySelector('[data-share-close]').disabled = value;
  };
  const showLink = () => {
    urlInput.value = new URL(state.url, location.origin).href;
    link.hidden = !state.version || !state.active;
  };
  const render = () => {
    dialog.querySelector('[data-share-title]').textContent = state.title;
    dialog.querySelector('[data-share-scope]').textContent = target.category
      ? window.WikitideI18n.t("目录及全部子目录，当前 {0} 篇；以后加入的笔记也会自动纳入分享，移出后不再包含。", [state.count])
      : window.WikitideI18n.t("仅分享这一篇笔记及正文引用的附件。");
    form.querySelectorAll('[name=audience]').forEach(input => { input.checked = input.value === state.audience; });
    form.elements.expiry.value = 'keep';
    dialog.querySelector('[data-share-expiry]').textContent = state.expires_at
      ? window.WikitideI18n.t("{0}：{1}", [state.expired ? window.WikitideI18n.t("已到期") : window.WikitideI18n.t("到期时间"), new Date(state.expires_at * 1000).toLocaleString()]) : window.WikitideI18n.t("当前长期有效");
    users.replaceChildren();
    state.users.forEach(account => {
      const row = document.createElement('label'); row.className = 'share-user';
      const name = document.createElement('span'); name.textContent = account.username;
      const select = document.createElement('select'); select.dataset.userId = account.id;
      [['none', window.WikitideI18n.t("无本次授权")], ['view', window.WikitideI18n.t("可阅读")], ...(account.role === 'editor' ? [['edit', window.WikitideI18n.t("可编辑")]] : [])].forEach(([value, text]) => {
        const option = document.createElement('option'); option.value = value; option.textContent = text; select.append(option);
      });
      select.value = account.permission; row.append(name, select); users.append(row);
    });
    if (!state.users.length) {
      const empty = document.createElement('p'); empty.textContent = window.WikitideI18n.t("暂无其他账户。可在“我的”管理页面创建账户后授权。"); users.append(empty);
    }
    showLink();
  };
  document.querySelectorAll('[data-share-note],[data-share-category]').forEach(button => button.addEventListener('click', async () => {
    if (busy) return;
    target = button.hasAttribute('data-share-note') ? { note_id: Number(button.dataset.shareNote) } : { category: button.dataset.shareCategory };
    state = null; link.hidden = true; form.hidden = true; users.replaceChildren();
    dialog.querySelector('[data-share-title]').textContent = '';
    dialog.querySelector('[data-share-scope]').textContent = '';
    status.textContent = window.WikitideI18n.t("正在读取分享设置…"); dialog.showModal(); setBusy(true);
    try {
      const response = await fetch('/api/sharing?' + new URLSearchParams(target), { cache: 'no-store' });
      const data = await response.json();
      if (!response.ok) throw new Error(window.WikitideI18n.t(data.detail) || window.WikitideI18n.t("无法读取分享设置"));
      state = data; render(); form.hidden = false; status.textContent = '';
    } catch (error) { status.textContent = error.message || window.WikitideI18n.t("无法读取分享设置，请稍后重试"); }
    finally { setBusy(false); }
  }));
  dialog.querySelector('[data-share-close]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  form.addEventListener('change', () => { link.hidden = true; status.textContent = window.WikitideI18n.t("设置尚未保存，保存后可复制链接。"); });
  const save = async revoke => {
    if (busy || !state) return;
    const members = revoke ? [] : [...users.querySelectorAll('select')].filter(select => select.value !== 'none')
      .map(select => ({ user_id: Number(select.dataset.userId), permission: select.value }));
    const audience = revoke ? 'restricted' : form.querySelector('[name=audience]:checked').value;
    const expiry = form.elements.expiry.value;
    let expires_at = revoke || expiry === 'none' ? null : state.expires_at;
    if (!revoke && ['1','7'].includes(expiry)) expires_at = Math.floor(Date.now()/1000) + Number(expiry)*86400;
    if (!revoke && expiry === 'custom') {
      expires_at = Math.floor(new Date(form.elements.expiry_time.value).getTime()/1000);
      if (!Number.isFinite(expires_at)) { status.textContent = window.WikitideI18n.t("请选择有效的到期时间。"); return; }
    }
    link.hidden = true; setBusy(true); status.textContent = window.WikitideI18n.t("正在保存…");
    try {
      const response = await fetch('/api/sharing', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': document.body.dataset.orderCsrf },
        body: JSON.stringify({ ...target, version: state.version, audience, members, expires_at }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(window.WikitideI18n.t(data.detail) || window.WikitideI18n.t("保存失败，请重试"));
      state = data; render();
      status.textContent = revoke ? window.WikitideI18n.t("已撤销本次分享。其他已有授权仍然有效。")
        : state.active ? window.WikitideI18n.t("分享设置已保存，可以复制链接。") : window.WikitideI18n.t("设置已保存。请指定账户或开启任何人可查看，才能分享链接。");
      if (revoke) link.hidden = true;
    } catch (error) { status.textContent = error.message || window.WikitideI18n.t("网络异常，设置尚未保存，请重试"); }
    finally { setBusy(false); }
  };
  form.addEventListener('submit', event => { event.preventDefault(); save(false); });
  dialog.querySelector('[data-share-revoke]').addEventListener('click', () => save(true));
  dialog.querySelector('[data-share-copy]').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(urlInput.value); status.textContent = window.WikitideI18n.t("链接已复制"); }
    catch (_error) { urlInput.focus(); urlInput.select(); status.textContent = window.WikitideI18n.t("请长按或按 Ctrl/Cmd+C 复制选中的链接"); }
  });
})();
