(() => {
  const dialog = document.querySelector('[data-release-dialog]');
  if (!dialog || window.wikiTideUpdates) return;
  const events = new AbortController();
  const backdrop = document.querySelector('[data-release-backdrop]');
  const pageAccount = document.body.dataset.userId || '';
  let data, key, timer, returnFocus, stopped = false, dismissed = false;
  const isOpen = () => dialog.hasAttribute('open');
  const remembered = () => { try { return Number(localStorage.getItem(key)) || 0; } catch { return 0; } };
  const store = () => { try { localStorage.setItem(key, String(Math.max(remembered(), data.latest.sequence))); } catch {} };
  const syncRead = async () => {
    if (!data?.account_id) return;
    try {
      await fetch('/api/updates/seen', {
        method: 'POST', credentials: 'same-origin', signal: events.signal,
        headers: {'Content-Type':'application/json','X-CSRF-Token':document.body.dataset.orderCsrf || ''},
        body: JSON.stringify({release_id:data.latest.id}),
      });
    } catch { /* The local receipt prevents repeats; the next page retries sync. */ }
  };
  const close = () => {
    if (typeof dialog.close === 'function') dialog.close();
    else dialog.removeAttribute('open');
    backdrop.hidden = true;
    if (returnFocus?.isConnected) returnFocus.focus();
  };
  const dismiss = () => {
    if (!data || !isOpen()) return;
    dismissed = true; clearTimeout(timer); store(); close(); void syncRead();
  };
  const safeToShow = () => {
    const otherDialog = [...document.querySelectorAll('dialog[open],[aria-modal="true"]')].some(element =>
      element !== dialog && !element.hidden && (element.hasAttribute('open') || element.getClientRects().length));
    if (document.hidden || otherDialog) return false;
    if (document.activeElement?.matches('input,textarea,select,[contenteditable="true"]')) return false;
    if (/^\/(login|register|recover|account)(\/|$)/.test(location.pathname)) return false;
    try { if (window.wikiTideNative && !window.wikiTideNative.state().canRestart) return false; }
    catch { return false; }
    return true;
  };
  const show = () => {
    if (!data || isOpen() || stopped) return;
    clearTimeout(timer);
    returnFocus = document.activeElement;
    const release = data.latest;
    dialog.querySelector('#release-title').textContent = release.title;
    dialog.querySelector('#release-summary').textContent = release.summary;
    dialog.querySelector('[data-release-date]').textContent = `${release.date} · ${release.id}`;
    const list = dialog.querySelector('[data-release-highlights]');
    list.replaceChildren(...release.highlights.map(text => { const item = document.createElement('li'); item.textContent = text; return item; }));
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else { dialog.setAttribute('open',''); backdrop.hidden = false; }
    dialog.querySelector('[data-release-dismiss]').focus();
  };
  const maybeShow = () => {
    clearTimeout(timer);
    if (!data || stopped || dismissed || !data.unread) return;
    if (remembered() >= data.latest.sequence) { void syncRead(); return; }
    if (safeToShow()) show();
    else timer = setTimeout(maybeShow, 1500);
  };
  let pending;
  const load = () => pending ||= fetch('/api/updates', {credentials:'same-origin',cache:'no-store',signal:events.signal})
    .then(response => { if (!response.ok) throw new Error('unavailable'); return response.json(); })
    .then(value => {
      if (!value.latest || !Number.isSafeInteger(value.latest.sequence) || !Array.isArray(value.latest.highlights)) throw new Error('invalid');
      // A login in another tab must not apply the old page's CSRF to a new account.
      if (String(value.account_id || '') !== pageAccount) throw new Error('account changed');
      data = value; key = `wikitide:updates:${data.account_id ? `user:${data.account_id}` : 'guest'}`;
    }).catch(error => { pending = null; throw error; });
  document.querySelectorAll('[data-release-dismiss]').forEach(button => button.addEventListener('click', dismiss, {signal:events.signal}));
  dialog.addEventListener('cancel', event => { event.preventDefault(); dismiss(); }, {signal:events.signal});
  dialog.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); dismiss(); }
    if (event.key === 'Tab') {
      const controls = [...dialog.querySelectorAll('button,a[href]')];
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  }, {signal:events.signal});
  document.querySelectorAll('[data-updates-open]').forEach(link => link.addEventListener('click', async event => {
    event.preventDefault();
    try { await load(); show(); } catch { location.assign(link.href); }
  }, {signal:events.signal}));
  window.addEventListener('storage', event => {
    if (data && event.key === key && Number(event.newValue) >= data.latest.sequence) {
      dismissed = true; clearTimeout(timer); if (isOpen()) close();
    }
  }, {signal:events.signal});
  window.addEventListener('pagehide', () => { stopped = true; clearTimeout(timer); events.abort(); }, {once:true});
  window.wikiTideUpdates = Object.freeze({open: async () => { await load(); show(); }});
  // The history page is already the full update announcement.
  if (location.pathname !== '/updates') void load().then(maybeShow).catch(() => {});
  else void load().then(() => { store(); void syncRead(); }).catch(() => {});
})();
