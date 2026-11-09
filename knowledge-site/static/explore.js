(() => {
  'use strict';
  const root = document.getElementById('explore');
  if (!root) return;
  const $ = id => document.getElementById(`explore-${id}`);
  const authenticated = root.dataset.authenticated === 'true';
  const aiReady = root.dataset.aiReady === 'true';
  const webReady = root.dataset.webReady === 'true';
  let noteId = Number(root.dataset.noteId) || null;
  let noteTitle = root.dataset.noteTitle || '';
  let scope = root.dataset.scope || 'public';
  let selectedNotes = JSON.parse(root.dataset.selectedNotes || '[]');
  if (!selectedNotes.length && scope === 'selected' && noteId) selectedNotes = [{id: noteId, title: noteTitle}];
  const pickedNotes = new Map(selectedNotes.map(note => [note.id, note]));
  let pickerRevision = 0;
  let backgroundRequest = '', backgroundKey = '';
  let sessionId = null;
  let projectKind = '';
  let asking = false;
  let searchRevision = 0;
  let readerRevision = 0;
  let sessionRevision = 0;
  function notifySession(projectId = undefined) {
    const url = new URL(location.href);
    if (sessionId) url.searchParams.set('session_id', sessionId);
    else url.searchParams.delete('session_id');
    history.replaceState(null, '', url);
    window.dispatchEvent(new CustomEvent('explore-session-change', {detail: {sessionId, projectId}}));
  }
  let searchController;
  const welcome = $('messages').firstElementChild.cloneNode(true);

  function notice(message = '', error = false) {
    $('notice').textContent = message;
    $('notice').hidden = !message;
    $('notice').classList.toggle('is-error', error);
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {credentials: 'same-origin', ...options,
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf, ...options.headers}});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? window.WikitideI18n.t(data.detail) : window.WikitideI18n.t("请求未完成，请稍后重试。"));
    return data;
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function unavailableDuringAnswer() {
    if (!asking) return false;
    notice(window.WikitideI18n.t("正在回答当前问题，请等回答完成后再切换。"));
    return true;
  }

  function updateScope() {
    if (scope === 'selected') $('web').checked = false;
    $('scope-badge').textContent = window.WikitideI18n.t(scope === 'selected' ? '所选笔记' : '公开技术笔记');
    $('selected-help').hidden = scope !== 'selected';
    $('public-help').hidden = scope === 'selected';
    root.querySelector('.explore-suggestions').hidden = scope === 'selected';
    const label = scope === 'selected' && selectedNotes.length > 1 ? window.WikitideI18n.t("范围：所选 {0} 篇笔记", [selectedNotes.length]) : noteId ? window.WikitideI18n.t("围绕：{0}", [noteTitle]) : window.WikitideI18n.t("范围：多篇公开笔记");
    $('scope-label').textContent = label + ($('web').checked ? window.WikitideI18n.t(" ＋ 联网资料") : '');
    $('all').hidden = !noteId && scope !== 'selected';
    const url = new URL(location.href);
    url.searchParams.delete('note_ids');
    if (noteId) url.searchParams.set('note_id', String(noteId));
    else url.searchParams.delete('note_id');
    if (scope === 'selected') url.searchParams.set('scope', scope);
    else url.searchParams.delete('scope');
    if (scope === 'selected' && selectedNotes.length > 1) selectedNotes.forEach(note => url.searchParams.append('note_ids', String(note.id)));
    const projectLink = $('learning-project');
    if (projectLink) {
      const target = new URL('/learn/projects', location.origin);
      if (scope === 'selected') selectedNotes.forEach(note => target.searchParams.append('note_ids', String(note.id)));
      projectLink.href = target.pathname + target.search;
    }
    history.replaceState(null, '', url);
    controls();
  }

  function clearResearch() {
    searchRevision++; readerRevision++;
    searchController?.abort();
    $('results').replaceChildren();
    $('results').removeAttribute('aria-busy');
    $('result-count').textContent = window.WikitideI18n.t('等待搜索');
    $('reader').hidden = true;
    $('article').replaceChildren();
    $('reader-title').textContent = window.WikitideI18n.t('原文阅读');
    $('open-original').href = '/browse';
  }

  function keepLearningThread() {
    if (projectKind === 'learning' && sessionId) {notice(window.WikitideI18n.t("学习项目只有一条问答，请从项目继续。"), true); return true;}
    return false;
  }
  function resetSession(id = noteId, title = noteTitle, nextScope = scope, notes = nextScope === 'selected' ? selectedNotes : []) {
    if (unavailableDuringAnswer()) return false;
    if (!window.dispatchEvent(new CustomEvent('explore-before-session-change', {cancelable: true}))) return false;
    sessionRevision++;
    sessionId = null;
    projectKind = '';
    noteId = id; noteTitle = title;
    clearResearch();
    scope = nextScope;
    selectedNotes = notes;
    $('messages').replaceChildren(welcome.cloneNode(true));
    notifySession();
    updateScope(); notice();
    return true;
  }

  function renderPickedNotes() {
    if (!$('picked-notes')) return;
    $('picked-notes').replaceChildren();
    for (const note of pickedNotes.values()) {
      const remove = element('button', '', `${note.title} ×`); remove.type = 'button';
      remove.addEventListener('click', () => {pickedNotes.delete(note.id); renderPickedNotes();
        $('note-options').querySelectorAll('input').forEach(input => {if (Number(input.value) === note.id) input.checked = false;});});
      $('picked-notes').append(remove);
    }
    $('selection-status').textContent = window.WikitideI18n.t("已选择 {0}/12 篇笔记", [pickedNotes.size]);
  }

  if ($('selection')) {
    renderPickedNotes();
    $('note-search').addEventListener('submit', async event => {
      event.preventDefault(); const revision = ++pickerRevision;
      try {
        const data = await api(`/api/explore/notes?q=${encodeURIComponent($('note-query').value.trim())}`);
        if (revision !== pickerRevision) return;
        $('note-options').replaceChildren();
        for (const note of data.notes) {
          const label = element('label'); const input = element('input'); input.type = 'checkbox'; input.value = String(note.id); input.checked = pickedNotes.has(note.id);
          input.disabled = asking;
          input.addEventListener('change', () => {
            if (input.checked && pickedNotes.size >= 12) {input.checked = false; $('selection-status').textContent = window.WikitideI18n.t("每次请选择 1～12 篇笔记。"); return;}
            if (input.checked) pickedNotes.set(note.id, note); else pickedNotes.delete(note.id);
            renderPickedNotes();
          });
          label.append(input, document.createTextNode(`${note.title} · ${note.path}`)); $('note-options').append(label);
        }
      } catch (error) {if (revision === pickerRevision) $('selection-status').textContent = error.message;}
    });
    $('apply-selection').addEventListener('click', () => {
      if (keepLearningThread()) return;
      const notes = [...pickedNotes.values()].sort((a, b) => a.id - b.id);
      if (!notes.length) {$('selection-status').textContent = window.WikitideI18n.t("请选择一篇笔记。"); return;}
      if (resetSession(notes.length === 1 ? notes[0].id : null, notes.length === 1 ? notes[0].title : '', 'selected', notes)) $('selection').open = false;
    });
  }

  function message(role, text, html = '', sources = [], messageId = null, retrieval = null) {
    const box = element('div', `explore-message${role === 'user' ? ' explore-message-user' : ''}`);
    box.append(element('p', 'explore-message-label', role === 'user' ? window.WikitideI18n.t("你") : window.WikitideI18n.t("知识求索 · DeepSeek")));
    if (role === 'user') {
      box.append(element('p', '', text));
      box.dataset.content = text;
      if (messageId) box.dataset.messageId = String(messageId);
      window.dispatchEvent(new CustomEvent('explore-user-message', {detail: {box, text, messageId, scope}}));
    }
    else {
      const content = element('div', 'prose');
      // This HTML is rendered and allow-list sanitized by our server, never by the model/browser.
      content.innerHTML = html;
      box.append(content);
      if (retrieval) {
        const details = element('details', 'explore-retrieval');
        details.append(element('summary', '', window.WikitideI18n.t("资料查找过程")));
        details.append(element('p', '', window.WikitideI18n.t("检索 {0} 次，读取 {1} 次，模型决策 {2} 次", [retrieval.search_calls, retrieval.read_calls, retrieval.model_calls])));
        if (retrieval.stop_reason !== 'finished') details.append(element('p', '', window.WikitideI18n.t("补查已停止，回答依据当前找到的资料。")));
        box.append(details);
      }
      if (authenticated && messageId) {
        if (root.dataset.artifactsEnabled === 'true' && sources.length) {
          const save = element('button', '', window.WikitideI18n.t("保存这条回答为草稿")); save.type = 'button';
          const saved = element('span', ''); saved.setAttribute('role', 'status');
          save.addEventListener('click', async () => {
            if (save.disabled) return;
            const revision = sessionRevision; save.disabled = true;
            try {
              const data = await api(`/api/explore/messages/${messageId}/draft`, {method: 'POST', body: '{}'});
              if (revision !== sessionRevision || !box.isConnected) return;
              const link = element('a', '', window.WikitideI18n.t("打开讨论草稿"));
              link.href = `/explore/write?draft_id=${encodeURIComponent(data.draft_id)}`; saved.replaceChildren(link);
            } catch (error) {if (revision === sessionRevision && box.isConnected) saved.textContent = error.message;}
            finally {save.disabled = false;}
          });
          box.append(save, saved);
        }
        const feedback = element('div','explore-feedback');
        const status = element('span',''); status.setAttribute('role','status');
        for (const [rating,label] of [['helpful',window.WikitideI18n.t("有帮助")],['unsupported',window.WikitideI18n.t("引用不支持结论")],['outdated',window.WikitideI18n.t("资料过时")],['incorrect',window.WikitideI18n.t("结论有误")]]) {
          const button=element('button','',label);button.type='button';
          button.onclick=async()=>{button.disabled=true;try{await api(`/api/explore/messages/${messageId}/feedback`,{method:'POST',body:JSON.stringify({rating})});status.textContent=window.WikitideI18n.t("感谢反馈，已记录。");}catch(error){status.textContent=error.message;}finally{button.disabled=false;}};
          feedback.append(button);
        }
        feedback.append(status);box.append(feedback);
      }
      if (sources.length) {
        const details = element('details', 'explore-sources');
        details.append(element('summary', '', window.WikitideI18n.t("{0} 条资料来源", [sources.length])));
        for (const source of sources) {
          const online = source.kind === 'web';
          const link = element('a', '', window.WikitideI18n.t("[{0}] {1} · {2} · {3}", [source.number, online ? window.WikitideI18n.t("网页") : window.WikitideI18n.t("笔记"), source.title, source.section]));
          link.href = source.url;
          link.title = online ? window.WikitideI18n.t("网页摘要 · 检索于 {0}", [source.retrieved_at]) : window.WikitideI18n.t("{0} · 第 {1}–{2} 行", [source.path, source.start_line, source.end_line]);
          if (online) {link.target = '_blank'; link.rel = 'noopener noreferrer';}
          details.append(link);
        }
        box.append(details);
      }
    }
    $('messages').append(box);
    $('messages').scrollTop = $('messages').scrollHeight;
    return box;
  }

  async function search() {
    const query = $('query').value.trim();
    if (!query) return;
    const revision = ++searchRevision;
    if (searchController) searchController.abort();
    searchController = new AbortController();
    $('results').setAttribute('aria-busy', 'true');
    $('result-count').textContent = window.WikitideI18n.t("正在寻找…");
    notice();
    try {
      const selected = scope === 'selected' ? '&scope=selected&' + selectedNotes.map(note => `note_ids=${note.id}`).join('&') : '';
      const data = await api(`/api/explore/search?q=${encodeURIComponent(query)}${selected}`, {signal: searchController.signal});
      if (revision !== searchRevision) return;
      const documents = new Map();
      for (const hit of data.results) if (!documents.has(hit.note_id)) documents.set(hit.note_id, hit);
      $('results').replaceChildren();
      $('result-count').textContent = window.WikitideI18n.t("{0} 篇相关文章", [documents.size]);
      $('search-mode').textContent = data.mode === 'hybrid' ? window.WikitideI18n.t("结合关键词与语义，按相关程度排列。") : window.WikitideI18n.t("当前使用关键词检索；中文问句也可尝试拆成几个关键词。");
      if (!documents.size) {
        const empty = element('div', 'explore-empty');
        empty.append(element('p', '', window.WikitideI18n.t("暂时没有找到足够相关的文章。")), element('small', '', window.WikitideI18n.t("试试技术名称、错误信息，或更短的关键词。")));
        $('results').append(empty);
      }
      for (const hit of documents.values()) {
        const card = element('article', 'explore-result');
        card.append(element('small', '', `${hit.category} · ${hit.reason}`));
        const heading = element('h3');
        const link = element('a', '', hit.title); link.href = hit.url;
        link.addEventListener('click', event => {event.preventDefault(); openArticle(hit.note_id, hit.url);});
        heading.append(link); card.append(heading);
        card.append(element('small', '', hit.section), element('p', '', hit.excerpt.slice(0, 240)));
        const actions = element('div', 'explore-result-actions');
        const read = element('button', '', window.WikitideI18n.t("阅读原文")); read.type = 'button';
        read.addEventListener('click', () => openArticle(hit.note_id, hit.url));
        const ask = element('button', '', window.WikitideI18n.t("围绕本文提问")); ask.type = 'button';
        ask.addEventListener('click', () => {
          if (keepLearningThread()) return;
          if (!resetSession(hit.note_id, hit.title, scope, scope === 'selected' ? [{id: hit.note_id, title: hit.title}] : [])) return;
          if (!$('question').value.trim()) $('question').value = query;
          if (window.exploreTrial.available && aiReady) $('question').focus();
          else notice(aiReady ? window.WikitideI18n.t("三次免费体验已用完，请登录后继续提问。") : window.WikitideI18n.t("管理员配置 DeepSeek 后，即可围绕这篇文章提问。"));
        });
        actions.append(read, ask); card.append(actions); $('results').append(card);
      }
      if (!$('question').value.trim()) $('question').value = query;
    } catch (error) {
      if (error.name !== 'AbortError' && revision === searchRevision) {
        $('result-count').textContent = window.WikitideI18n.t("搜索未完成"); notice(error.message, true);
      }
    } finally {
      if (revision === searchRevision) $('results').removeAttribute('aria-busy');
    }
  }

  async function openArticle(id, sourceUrl = '') {
    const revision = ++readerRevision;
    try {
      const data = await api(`/api/explore/articles/${id}${scope === 'selected' ? '?scope=selected' : ''}`);
      if (revision !== readerRevision) return;
      $('reader-title').textContent = data.title;
      $('open-original').href = sourceUrl || data.url;
      $('article').innerHTML = data.html;
      const used = new Set();
      for (const [index, heading] of [...$('article').querySelectorAll('h1,h2,h3,h4,h5,h6')].entries()) {
        const base = heading.textContent.trim().toLowerCase().replace(/[^\p{L}\p{N}\p{M}\s_-]/gu, '').replace(/\s+/g, '-') || `section-${index + 1}`;
        let slug = base, suffix = 2;
        while (used.has(slug)) slug = `${base}-${suffix++}`;
        used.add(slug); heading.id = `explore-reader-${slug}`;
      }
      $('reader').hidden = false;
      $('article').scrollTop = 0;
      let anchor = '';
      try {anchor = decodeURIComponent(new URL(sourceUrl || data.url, location.origin).hash.slice(1));} catch (_) { /* no anchor */ }
      if (anchor) {
        const target = document.getElementById(`explore-reader-${anchor}`);
        if (target) $('article').scrollTop = target.offsetTop - $('article').offsetTop;
      }
      $('reader').scrollIntoView({block: 'start', behavior: 'auto'});
    } catch (error) {if (revision === readerRevision) notice(error.message, true);}
  }

  async function loadHistory() {
    if (!authenticated) return;
    try {
      const data = await api('/api/explore/sessions');
      $('history-list').replaceChildren();
      if (!data.sessions.length) $('history-list').append(element('p', 'explore-help', window.WikitideI18n.t("还没有保存的会话。")));
      for (const session of data.sessions) {
        const row = element('div', 'explore-history-row');
        const open = element('button', '', session.title); open.type = 'button';
        open.addEventListener('click', async () => {
          await openSession(session.id);
        });
        const remove = element('button', '', window.WikitideI18n.t("删除")); remove.type = 'button';
        remove.setAttribute('aria-label', window.WikitideI18n.t("删除会话：{0}", [session.title]));
        remove.addEventListener('click', async () => {
          if (unavailableDuringAnswer()) return;
          if (sessionId === session.id && !window.dispatchEvent(new CustomEvent('explore-before-session-change', {cancelable: true}))) return;
          remove.disabled = true;
          try {
            await api(`/api/explore/sessions/${session.id}`, {method: 'DELETE'});
            if (sessionId === session.id) resetSession();
            await loadHistory();
          } catch (error) {notice(error.message, true); remove.disabled = false;}
        });
        row.append(open, remove); $('history-list').append(row);
      }
    } catch (error) {notice(error.message, true);}
  }

  async function openSession(id) {
    if (unavailableDuringAnswer()) return;
    if (!window.dispatchEvent(new CustomEvent('explore-before-session-change', {cancelable: true}))) return;
    const revision = ++sessionRevision;
    try {
      const detail = await api(`/api/explore/sessions/${encodeURIComponent(id)}`);
      if (asking || revision !== sessionRevision) return;
      clearResearch();
      sessionId = detail.id; noteId = detail.note_id; noteTitle = detail.note_title;
      projectKind = detail.project_kind || '';
      scope = detail.scope || 'public';
      selectedNotes = detail.notes || (noteId ? [{id: noteId, title: noteTitle}] : []);
      pickedNotes.clear(); selectedNotes.forEach(note => pickedNotes.set(note.id, note)); renderPickedNotes();
      const lastAnswer = detail.messages.filter(item => item.role === 'assistant').at(-1);
      if ($('research')) $('research').checked = Boolean(lastAnswer?.retrieval);
      $('web').checked = webReady && Boolean(lastAnswer?.sources?.some(source => source.kind === 'web'));
      updateScope(); $('messages').replaceChildren();
      for (const item of detail.messages) message(item.role, item.content, item.html, item.sources, item.id, item.retrieval);
      notifySession(detail.project_id || null);
      notice(); $('conversation-title').scrollIntoView({block: 'start', behavior: 'auto'});
    } catch (error) {notice(error.message, true);}
  }

  async function streamAnswer(payload, onEvent) {
    const response = await fetch('/api/explore/ask/stream', {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf}, body: JSON.stringify(payload)});
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(typeof error.detail === 'string' ? error.detail : window.WikitideI18n.t("请求未完成，请稍后重试。"));
    }
    const decoder = new TextDecoder();
    let buffer = '', result = null;
    function consume(text) {
      buffer += text;
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
        const lines = frame.split('\n');
        const kind = lines.find(line => line.startsWith('event:'))?.slice(6).trim();
        const data = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
        if (!kind || !data) continue;
        const event = JSON.parse(data);
        if (kind === 'error') throw new Error(window.WikitideI18n.t(event.detail) || window.WikitideI18n.t("回答未完成，问题已保留。"));
        if (kind === 'done') result = event;
        else onEvent(kind, event);
      }
    }
    if (response.body?.getReader) {
      const reader = response.body.getReader();
      try {
        while (true) {
          const part = await reader.read();
          if (part.done) {consume(decoder.decode()); break;}
          consume(decoder.decode(part.value, {stream: true}));
          if (result) break;
        }
      } finally {await reader.cancel().catch(() => {});}
    } else consume(await response.text());
    if (!result) throw new Error(window.WikitideI18n.t("回答连接中断，问题已保留，可以重试。"));
    return result;
  }

  function controls() {
    const disabled = asking || !aiReady || !window.exploreTrial.available;
    $('ask').querySelectorAll('button,textarea,input').forEach(node => {node.disabled = disabled;});
    $('web').disabled = disabled || !webReady || scope === 'selected';
    if ($('selection')) $('selection').querySelectorAll('button,input').forEach(node => {node.disabled = asking;});
  }
  window.addEventListener('explore-trial-change', controls);
  controls();

  $('background')?.addEventListener('click', async () => {
    const topic = $('question').value.trim();
    if (!topic || asking) return;
    const projectId = window.wikiTideAgentExplore?.requestContext().project_id || null;
    const body = {kind: 'knowledge', topic, scope, note_id: noteId,
      note_ids: scope === 'selected' && !noteId ? selectedNotes.map(note => note.id) : [],
      ...(projectId ? {project_id: projectId} : {}),
      memory_mode: 'off', max_tokens: 48000, max_search_calls: 3};
    const signature = JSON.stringify(body);
    if (signature !== backgroundRequest) {backgroundRequest = signature; backgroundKey = crypto.randomUUID();}
    asking = true; controls();
    window.dispatchEvent(new CustomEvent('explore-busy-change', {detail: {asking}}));
    try {
      const job = await api('/api/agent/v1/runs', {method: 'POST', body: JSON.stringify({...body, idempotency_key: backgroundKey})});
      notice(window.WikitideI18n.t("后台任务已创建，可以关闭页面后回来查看。"));
      const link = element('a', '', window.WikitideI18n.t("打开任务"));
      link.href = `/agent/tasks/${encodeURIComponent(job.id)}`; $('notice').append(document.createTextNode(' '), link);
    } catch (error) {notice(error.message, true);}
    finally {asking = false; controls(); window.dispatchEvent(new CustomEvent('explore-busy-change', {detail: {asking}}));}
  });

  function updateMode() {
    $('mode-help').textContent = $('thinking').checked ? window.WikitideI18n.t("深入思考：适合复杂问题，等待时间较长。") : window.WikitideI18n.t("快速响应：简明作答，边生成边显示。");
    updateScope();
  }
  for (const id of ['fast', 'thinking', 'web']) $(id).addEventListener('change', updateMode);

  $('search').addEventListener('submit', event => {event.preventDefault(); search();});
  root.querySelectorAll('[data-query]').forEach(button => button.addEventListener('click', () => {
    $('query').value = button.dataset.query; search();
  }));
  $('new').addEventListener('click', () => {if (!keepLearningThread()) resetSession();});
  $('all').addEventListener('click', () => {if (!keepLearningThread()) resetSession(null, '', 'public');});
  $('close-reader').addEventListener('click', () => {$('reader').hidden = true; readerRevision++;});
  $('messages').addEventListener('click', event => {
    const link = event.target.closest('a');
    if (!link) return;
    const url = new URL(link.href, location.origin);
    const match = /^\/n\/(\d+)$/.exec(url.pathname);
    if (url.origin === location.origin && match) {event.preventDefault(); openArticle(Number(match[1]), link.href);}
  });
  $('question').addEventListener('keydown', event => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.isComposing) {
      event.preventDefault(); $('ask').requestSubmit();
    }
  });
  $('ask').addEventListener('submit', async event => {
    event.preventDefault();
    if (asking || !window.exploreTrial.available || !aiReady) return;
    const question = $('question').value.trim();
    if (!question) return;
    asking = true; notice();
    window.dispatchEvent(new CustomEvent('explore-busy-change', {detail: {asking}}));
    sessionRevision++;
    $('messages').querySelector('.explore-welcome')?.remove();
    const submitted = message('user', question);
    const pending = element('p', 'explore-pending', $('thinking').checked ? window.WikitideI18n.t("正在结合原文深入分析，请稍候…") : window.WikitideI18n.t("正在查找资料并组织回答…"));
    pending.setAttribute('role', 'status'); $('messages').append(pending);
    $('messages').scrollTop = $('messages').scrollHeight;
    $('ask').querySelectorAll('button,textarea,input').forEach(node => {node.disabled = true;});
    let draft = null, draftText = '';
    try {
      const data = await streamAnswer({
        question, session_id: sessionId, note_id: noteId, scope, note_ids: scope === 'selected' && !noteId ? selectedNotes.map(note => note.id) : [], research: Boolean($('research')?.checked), thinking: $('thinking').checked,
        web_search: $('web').checked,
        ...(window.wikiTideAgentExplore?.requestContext() || {}),
      }, (kind, event) => {
        if (kind === 'status') pending.textContent = event.message;
        if (kind === 'delta') {
          if (!draft) {
            draft = message('assistant', '', '');
            draft.querySelector('.explore-message-label').textContent = window.WikitideI18n.t("知识求索 · 正在生成，完成后核对引用");
            draft.querySelector('.prose').classList.add('explore-stream-text');
          }
          draftText += event.text;
          // Streaming drafts are always plain text; final HTML comes from the server sanitizer.
          draft.querySelector('.prose').textContent = draftText;
          $('messages').scrollTop = $('messages').scrollHeight;
        }
      });
      sessionId = data.session_id;
      if (data.user_message_id) {
        submitted.dataset.messageId = String(data.user_message_id);
        window.dispatchEvent(new CustomEvent('explore-user-message', {detail: {box: submitted, text: question, messageId: data.user_message_id, scope}}));
      }
      notifySession(data.context?.project_id);
      window.dispatchEvent(new CustomEvent('explore-answer-context', {detail: data.context || {}}));
      draft?.remove();
      pending.remove(); message('assistant', data.answer, data.html, data.sources, data.message_id, data.retrieval);
      if (data.context?.degraded) notice(window.WikitideI18n.t("部分上下文暂未纳入，建议在“本次会话要求与摘要”中固定重要要求。"));
      $('question').value = '';
      await loadHistory();
    } catch (error) {
      draft?.remove(); pending.remove(); submitted.remove(); notice(error.message, true);
      if (!$('messages').children.length) $('messages').append(welcome.cloneNode(true));
    } finally {
      asking = false;
      window.dispatchEvent(new CustomEvent('explore-busy-change', {detail: {asking}}));
      await window.exploreTrial.refresh();
      controls();
      if (!$('question').disabled) $('question').focus();
    }
  });
  updateScope();
  loadHistory();
  const initialSession = new URL(location.href).searchParams.get('session_id');
  if (initialSession && authenticated) openSession(initialSession);
  else if (noteId) {
    $('query').value = noteTitle;
    search();
    openArticle(noteId);
  }
})();
