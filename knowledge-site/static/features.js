(() => {
  const userId = document.body.dataset.userId;
  let database;
  function openDrafts() {
    if (!database) database = new Promise((resolve, reject) => {
      const request = indexedDB.open('wikitide-drafts', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('drafts', { keyPath: 'key' });
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    return database;
  }
  async function draftTransaction(mode, action) {
    const db = await openDrafts();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction('drafts', mode);
      const request = action(transaction.objectStore('drafts'));
      transaction.oncomplete = () => resolve(request?.result);
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
  }
  document.querySelectorAll('[data-confirm]').forEach(form => form.addEventListener('submit', event => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  }));
  document.querySelector('[data-logout]')?.addEventListener('submit', async event => {
    event.preventDefault();
    document.body.dataset.loggingOut='1';
    try {
      Object.keys(localStorage).filter(key => key.startsWith(`wikitide-emergency:${userId}:`) || key.startsWith(`wikitide-team-draft:${document.body.dataset.accountName}:`)).forEach(key => localStorage.removeItem(key));
    } catch (_error) { /* Storage may be disabled by browser policy. */ }
    try {
      const records = await draftTransaction('readonly', store => store.getAll());
      await Promise.all(records.filter(record => record.userId === userId).map(record =>
        draftTransaction('readwrite', store => store.delete(record.key))));
    } catch (_error) { /* A disabled store cannot contain drafts from this session. */ }
    event.target.submit();
  });

  const form = document.querySelector('[data-editor]');
  if (form) {
    const editor = form.querySelector('textarea[name=body]');
    const status = form.querySelector('[data-draft-status]');
    const recovery = document.querySelector('.draft-recovery');
    const preview = form.querySelector('.preview-panel');
    const key = `${userId}:${form.dataset.noteId}`;
    const emergencyKey = `wikitide-emergency:${key}`;
    let initial = editor.value, dirty = !!form.dataset.serverRejected, pending = false;
    let storedDraft, timer, previewTimer, previewSequence = 0, previewController;
    let saveQueue = Promise.resolve(), submitting = false;
    window.wikiTideNative?.register('note-editor', () => ({ dirty: dirty || pending, busy: submitting || editor.readOnly }));
    const csrf = form.querySelector('[name=csrf]').value;
    const setReadOnly = (value, reason = '') => {
      form.dataset.editorLock = value ? reason : '';
      editor.readOnly = value;
      editor.dispatchEvent(new Event('editor-state'));
    };
    setReadOnly(true, 'loading');
    status.textContent = window.WikitideI18n.t("正在读取浏览器草稿…");
    const emergencyDraft = () => {
      if (!dirty || pending || submitting || document.body.dataset.loggingOut) return;
      try { localStorage.setItem(emergencyKey,JSON.stringify({key,userId,body:editor.value,baseSha:form.dataset.baseSha,savedAt:Date.now()})); }
      catch (_error) { /* beforeunload still asks the user to protect unsaved content. */ }
    };
    const saveDraft = () => {
      if (document.body.dataset.loggingOut) return Promise.resolve();
      if (pending && !dirty) return Promise.resolve();
      const value = { key, userId, body: editor.value, baseSha: form.dataset.baseSha, savedAt: Date.now() };
      saveQueue = saveQueue.catch(() => {}).then(() => draftTransaction('readwrite', store => store.put(value)));
      return saveQueue.then(() => {
        status.textContent = dirty ? window.WikitideI18n.t("草稿已保存到此浏览器，正文尚未提交") : window.WikitideI18n.t("未修改");
      }).catch(() => { status.textContent = window.WikitideI18n.t("草稿保存失败，请下载草稿或保存正文"); });
    };
    const renderPreview = async () => {
      if (preview.hidden) return;
      const sequence = ++previewSequence;
      previewController?.abort(); previewController = new AbortController();
      try {
        const response = await fetch(`/n/${form.dataset.noteId}/preview`, {
          method: 'POST', body: new URLSearchParams({ csrf, body: editor.value }), signal: previewController.signal,
        });
        if (!response.ok) throw new Error('preview');
        const html = await response.text();
        if (sequence === previewSequence) preview.innerHTML = html;
      } catch (error) { if (error.name !== 'AbortError') preview.textContent = window.WikitideI18n.t("预览暂时不可用，编辑内容仍然保留。"); }
    };
    const restoreDraft = (focusEditor = false) => {
      if (!storedDraft) return;
      const stale = storedDraft.baseSha !== form.dataset.baseSha;
      // Keep the original base before CodeMirror's replacement emits an input event.
      form.querySelector('[name=base_sha]').value = storedDraft.baseSha;
      form.dataset.baseSha = storedDraft.baseSha;
      editor.value = storedDraft.body; dirty = true; pending = false; recovery.hidden = true;
      editor.dispatchEvent(new Event('editor-replace'));
      setReadOnly(false);
      status.textContent = stale ? window.WikitideI18n.t("草稿已恢复，正式正文有更新，请检查后保存") : window.WikitideI18n.t("草稿已恢复，可继续编辑，正文尚未提交");
      renderPreview();
      if (focusEditor) editor.dispatchEvent(new Event('editor-focus'));
    };
    draftTransaction('readonly', store => store.get(key)).catch(() => null).then(record => {
      try {
        const emergency=JSON.parse(localStorage.getItem(emergencyKey)||'null');
        if (emergency && typeof emergency.body==='string' && (!record || emergency.savedAt>record.savedAt)) record=emergency;
      } catch (_error) { /* Ignore an invalid fallback snapshot. */ }
      storedDraft = record;
      if (record && record.body !== editor.value && !dirty) {
        if (record.baseSha === form.dataset.baseSha) { restoreDraft(); return; }
        pending = true; recovery.hidden = false;
        recovery.querySelector('[data-draft-description]').textContent =
          window.WikitideI18n.t("保存于 {0}。正式正文已更新，请先恢复草稿进行合并，或丢弃草稿后编辑最新正文。", [new Date(record.savedAt).toLocaleString()]);
        status.textContent = window.WikitideI18n.t("编辑已暂停：请先处理上方草稿，避免覆盖已有内容");
      } else if (dirty) saveDraft();
      else status.textContent = window.WikitideI18n.t("未修改");
      setReadOnly(pending, 'draft');
    }).catch(() => { setReadOnly(pending); status.textContent = window.WikitideI18n.t("浏览器草稿存储不可用，请及时保存或下载"); });
    recovery.querySelector('[data-restore-draft]').addEventListener('click', () => restoreDraft(true));
    form.addEventListener('editor-locked', () => {
      if (!pending) return;
      status.textContent = window.WikitideI18n.t("编辑已暂停：请先恢复草稿，或丢弃草稿后编辑最新正文");
      recovery.scrollIntoView({ block: 'center', behavior: 'smooth' });
      recovery.querySelector('[data-restore-draft]').focus({ preventScroll: true });
    });
    recovery.querySelector('[data-discard-draft]').addEventListener('click', async () => {
      try {
        await draftTransaction('readwrite', store => store.delete(key));
        try {
          const emergency=JSON.parse(localStorage.getItem(emergencyKey)||'null');
          if (emergency?.body===storedDraft?.body) localStorage.removeItem(emergencyKey);
        } catch (_error) { /* A disabled fallback store must not block discarding an IndexedDB draft. */ }
        pending = false; recovery.hidden = true; setReadOnly(false); status.textContent = window.WikitideI18n.t("未修改，可编辑最新正文");
        editor.dispatchEvent(new Event('editor-focus'));
      }
      catch (_error) { status.textContent = window.WikitideI18n.t("清理失败，请重试"); }
    });
    editor.addEventListener('input', () => {
      if (pending) { pending = false; recovery.hidden = true; }
      dirty = editor.value !== initial || !!form.dataset.serverRejected;
      status.textContent = window.WikitideI18n.t("正在编辑…"); clearTimeout(timer); timer = setTimeout(saveDraft, 1000);
      clearTimeout(previewTimer); previewTimer = setTimeout(renderPreview, 600);
    });
    form.querySelector('[data-preview-toggle]').addEventListener('click', event => {
      preview.hidden = !preview.hidden;
      form.querySelector('.editor-columns').classList.toggle('has-preview', !preview.hidden);
      event.currentTarget.setAttribute('aria-expanded', String(!preview.hidden));
      event.currentTarget.textContent = preview.hidden ? window.WikitideI18n.t("阅读预览") : window.WikitideI18n.t("关闭预览"); renderPreview();
    });
    form.querySelector('[data-download-draft]').addEventListener('click', () => {
      const url = URL.createObjectURL(new Blob([editor.value], { type: 'text/markdown;charset=utf-8' }));
      const link = document.createElement('a'); link.href = url; link.download = window.WikitideI18n.t("笔记草稿.md"); link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    window.addEventListener('beforeunload', event => {
      if ((dirty || pending) && !submitting) { emergencyDraft(); event.preventDefault(); event.returnValue = ''; }
    });
    window.addEventListener('pagehide',emergencyDraft);
    document.addEventListener('visibilitychange', () => { if (document.hidden && dirty) { emergencyDraft(); saveDraft(); } });
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (submitting) return;
      if (pending) { status.textContent = window.WikitideI18n.t("请先恢复或丢弃已有草稿"); return; }
      if (!editor.value) { status.textContent = window.WikitideI18n.t("请输入正文后再保存"); editor.dispatchEvent(new Event('editor-focus')); return; }
      const review = document.querySelector('[data-conflict-reviewed]');
      if (review && !review.checked) { status.textContent = window.WikitideI18n.t("请先对照最新正文，并勾选检查确认"); return; }
      await saveDraft();
      submitting = true; const button = form.querySelector('button[type=submit]');
      button.disabled = true; setReadOnly(true, 'saving'); status.textContent = window.WikitideI18n.t("正在保存正文…");
      try {
        const response = await fetch(form.action, { method: 'POST', body: new URLSearchParams(new FormData(form)) });
        if (response.ok && response.redirected && new URL(response.url).pathname === `/n/${form.dataset.noteId}`) {
          clearTimeout(timer); await saveQueue.catch(() => {});
          await draftTransaction('readwrite', store => {
            const request = store.get(key);
            request.onsuccess = () => {
              if (request.result?.body === editor.value) store.delete(key);
            };
            return request;
          }).catch(() => {});
          try {
            const emergency=JSON.parse(localStorage.getItem(emergencyKey)||'null');
            if (emergency?.body===editor.value) localStorage.removeItem(emergencyKey);
          } catch (_error) { /* Draft cleanup must not block a successful save. */ }
          dirty = false; location.assign(response.url); return;
        }
        if (response.status === 409) {
          const html = new DOMParser().parseFromString(await response.text(), 'text/html');
          const latest = html.querySelector('[name=base_sha]')?.value;
          const panel = html.querySelector('.version-panel');
          if (latest && panel) {
            document.querySelector('[data-conflict-reviewed]')?.closest('.version-panel')?.remove(); form.before(panel);
            form.dataset.baseSha = latest; form.dataset.serverRejected = '1';
            form.querySelector('[name=base_sha]').value = latest;
          }
          status.textContent = window.WikitideI18n.t("正文已更新，你的输入已保留。请对照最新正文合并。");
        } else { status.textContent = window.WikitideI18n.t("保存失败，输入仍在编辑框中，请重试或下载草稿"); }
      } catch (_error) { status.textContent = window.WikitideI18n.t("网络异常，输入仍在编辑框中，请重试或下载草稿"); }
      submitting = false; button.disabled = false; setReadOnly(false);
    });
  }

  const article = document.querySelector('[data-reading-note]');
  if (article && userId) {
    const position = Number(article.dataset.position) || 0;
    const fraction = () => {
      const start = article.getBoundingClientRect().top + window.scrollY;
      const total = Math.max(1, article.offsetHeight - window.innerHeight + 150);
      return Math.max(0, Math.min(1, (window.scrollY - start + 150) / total));
    };
    if (new URLSearchParams(location.search).get('resume') && !location.hash && position > 0) {
      const resume = () => {
        const start = article.getBoundingClientRect().top + window.scrollY;
        window.scrollTo({ top: Math.max(0,start + position * Math.max(1,article.offsetHeight-window.innerHeight+150)-150), behavior: 'instant' });
      };
      if (document.readyState === 'complete') resume(); else window.addEventListener('load', resume, { once: true });
    }
    let timer, last = -1;
    const savePosition = () => {
      const current = fraction(); if (Math.abs(current-last)<0.002) return; last=current;
      fetch(`/n/${article.dataset.readingNote}/reading`, { method: 'POST', keepalive: true,
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': article.dataset.csrf },
        body: JSON.stringify({ position: current }),
      }).catch(() => { last=-1; });
    };
    window.addEventListener('scroll', () => { clearTimeout(timer); timer=setTimeout(savePosition,1200); }, { passive:true });
    document.addEventListener('visibilitychange', () => { if (document.hidden) savePosition(); });
    window.addEventListener('pagehide', savePosition);
    window.addEventListener('load', () => { setTimeout(savePosition,100); }, { once:true });
  }
  const summary = document.querySelector('[data-sync-summary]');
  if (summary) {
    const update = async () => {
      if (document.hidden) return;
      try {
        const response=await fetch('/admin/sync/status'); if (!response.ok) throw new Error('status');
        const state=await response.json();
        const last=state.last_success ? new Date(state.last_success*1000).toLocaleString() : window.WikitideI18n.t("暂无记录");
        const stale=state.last_success && Date.now()/1000-state.last_success>900;
        summary.replaceChildren();
        [window.WikitideI18n.t("最后成功同步：{0}{1}", [last, stale ? window.WikitideI18n.t("（长时间未收到同步反馈）") : '']),
          window.WikitideI18n.t("等待同步 {0} 篇 · 待处理或待确认冲突 {1} 篇", [state.pending, state.conflicts]),
          state.latest ? window.WikitideI18n.t("最近运行：{0} {1}", [({running:window.WikitideI18n.t("正在同步"),success:window.WikitideI18n.t("已完成"),failed:window.WikitideI18n.t("失败")})[state.latest.status], state.latest.message]) : window.WikitideI18n.t("尚未收到电脑的同步报告"),
        ].forEach(text => { const p=document.createElement('p'); p.textContent=text; summary.append(p); });
      } catch (_error) { summary.setAttribute('aria-label',window.WikitideI18n.t("暂时无法刷新同步状态")); }
    };
    update(); setInterval(update,15000);
  }
})();
