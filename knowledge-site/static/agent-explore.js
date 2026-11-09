(() => {
  'use strict';
  const panel = document.getElementById('agent-explore-tools');
  if (!panel) return;
  const $ = name => document.getElementById(`agent-${name}`);
  const prefix = '/api/agent/v1';
  let projectId = new URL(location.href).searchParams.get('project_id') || null;
  let sessionId = null, busy = false, pending = false, dirty = false, sourceId = null, revision = 0;
  const lifetime = new AbortController();
  const unregister = window.wikiTideNative?.register('agent-remember', () => ({dirty, busy: pending}));
  window.wikiTideAgentExplore = {requestContext: () => ({project_id: projectId, memory_mode: $('explore-memory').checked ? 'account' : 'off'})};
  function text(tag, content) {const result = document.createElement(tag); result.textContent = content; return result;}
  async function api(path, options = {}) {
    const response = await fetch(prefix + path, {credentials: 'same-origin', signal: lifetime.signal, ...options,
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': panel.dataset.csrf}});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? window.WikitideI18n.t(data.detail) : window.WikitideI18n.t("记忆操作未完成，请稍后重试。"));
    return data;
  }
  function controls() {
    $('explore-project').disabled = busy || pending || Boolean(sessionId);
    $('explore-memory').disabled = busy || pending;
    $('remember-save').disabled = pending || busy;
    $('remember-cancel').disabled = pending;
    $('remember-content').disabled = pending;
    $('remember-scope').disabled = pending;
    $('remember-kind').disabled = pending;
    document.querySelectorAll('.agent-remember-button').forEach(button => {button.disabled = busy || pending || !button.closest('[data-message-id]');});
  }
  function remember(box) {
    if (busy || pending || !box.dataset.messageId) return;
    sourceId = box.dataset.messageId; $('remember-content').value = box.dataset.content || '';
    $('remember-scope').value = projectId ? 'project' : 'account';
    $('remember-scope').querySelector('[value="project"]').disabled = !projectId;
    $('remember-status').textContent = ''; dirty = true; $('remember-dialog').showModal();
  }
  function decorate(box) {
    if (box.querySelector('.agent-remember-button')) {controls(); return;}
    const action = text('button', window.WikitideI18n.t("记住这条要求")); action.type = 'button'; action.className = 'agent-remember-button';
    action.addEventListener('click', () => remember(box)); box.append(action); controls();
  }
  window.addEventListener('explore-user-message', event => {
    if (event.detail.scope !== 'selected') decorate(event.detail.box);
  }, {signal: lifetime.signal});
  document.querySelectorAll('.explore-message-user').forEach(decorate);
  window.addEventListener('explore-session-change', event => {
    sessionId = event.detail.sessionId;
    if (event.detail.projectId !== undefined) projectId = event.detail.projectId;
    $('explore-project').value = projectId || '';
    $('explore-status').textContent = sessionId ? window.WikitideI18n.t("会话的所属项目已固定，可在项目工作台调整关联。") : window.WikitideI18n.t("新会话会使用当前选择的项目。");
    if (!sessionId) $('explore-used').replaceChildren(); controls();
  }, {signal: lifetime.signal});
  window.addEventListener('explore-busy-change', event => {busy = event.detail.asking; controls();}, {signal: lifetime.signal});
  window.addEventListener('explore-before-session-change', event => {
    if (pending || dirty) {event.preventDefault(); $('remember-status').textContent = pending ? window.WikitideI18n.t("正在保存记忆，请稍后切换。") : window.WikitideI18n.t("请先确认或取消当前记忆。");}
  }, {signal: lifetime.signal});
  $('explore-project').addEventListener('change', () => {projectId = $('explore-project').value || null;});
  $('explore-memory').addEventListener('change', () => {$('explore-status').textContent = $('explore-memory').checked ? window.WikitideI18n.t("后续提问将按需使用有效记忆。") : window.WikitideI18n.t("后续提问不再检索账户与项目记忆；原会话消息仍然保留。");});
  $('remember-cancel').addEventListener('click', () => {if (!pending) {dirty = false; $('remember-dialog').close();}});
  $('remember-dialog').addEventListener('cancel', event => {if (pending) event.preventDefault(); else dirty = false;});
  $('remember-form').addEventListener('submit', async event => {
    event.preventDefault(); if (pending || busy || !sourceId) return;
    if ($('remember-scope').value === 'project' && !projectId) {$('remember-status').textContent = window.WikitideI18n.t("请先选择项目。"); return;}
    pending = true; controls();
    try {
      const data = await api('/memories', {method: 'POST', body: JSON.stringify({scope: $('remember-scope').value, project_id: $('remember-scope').value === 'project' ? projectId : null, kind: $('remember-kind').value, content: $('remember-content').value.trim(), source: {kind: 'message', id: String(sourceId)}})});
      dirty = false; $('remember-dialog').close(); $('explore-status').textContent = data.status === 'candidate' ? window.WikitideI18n.t("已保存候选记忆，请到记忆中心确认。") : window.WikitideI18n.t("已记住，可以在记忆中心修改或遗忘。");
    } catch (error) {if (error.name !== 'AbortError') $('remember-status').textContent = error.message;}
    finally {pending = false; controls();}
  });
  window.addEventListener('explore-answer-context', async event => {
    const ticket = ++revision, context = event.detail, target = $('explore-used'); target.replaceChildren();
    const refs = context.memory_context || [], conflicts = context.memory_conflicts || [];
    target.append(text('p', context.memory_mode === 'off' ? window.WikitideI18n.t("本轮关闭长期记忆。") : refs.length ? window.WikitideI18n.t("本轮纳入 {0} 条有效记忆，按相关性和上下文预算选取。", [refs.length]) : window.WikitideI18n.t("本轮未纳入长期记忆。")));
    if (conflicts.length) target.append(text('p', window.WikitideI18n.t("有 {0} 项记忆存在冲突，未用于本次回答。请到记忆中心核对相同冲突标识的内容。", [conflicts.length])));
    if (!refs.length) return;
    const list = text('ul', ''); target.append(list);
    for (const ref of refs) list.append(text('li', window.WikitideI18n.t("记忆 {0} · 第 {1} 版", [ref.id, ref.revision])));
    try {
      const lookup = new Map(); let cursor = null;
      do {const data = await api(`/memories?limit=100${cursor ? `&cursor=${cursor}` : ''}`); data.memories.forEach(item => lookup.set(item.id, item)); cursor = data.next_cursor;} while (cursor && ticket === revision);
      if (ticket !== revision) return;
      list.replaceChildren();
      for (const ref of refs) {
        const item = lookup.get(ref.id), detail = text('details', ''), row = text('li', '');
        detail.append(text('summary', item && item.revision === ref.revision ? item.content : window.WikitideI18n.t("记忆 {0}（已变化或不可访问）", [ref.id])));
        detail.append(text('p', window.WikitideI18n.t("记忆 ID：{0} · 使用第 {1} 版", [ref.id, ref.revision])));
        if (item) detail.append(text('p', window.WikitideI18n.t("来源：{0} · {1}", [item.source?.kind === 'manual' ? window.WikitideI18n.t("用户手动确认") : item.source?.kind === 'message' ? window.WikitideI18n.t("消息 {0}", [item.source.id]) : window.WikitideI18n.t("笔记 {0}", [item.source?.note_id]), item.source_valid ? window.WikitideI18n.t("有效") : window.WikitideI18n.t("已失效")])));
        row.append(detail); list.append(row);
      }
    } catch (_) { /* Stable IDs remain available if details cannot be read. */ }
  }, {signal: lifetime.signal});
  window.addEventListener('beforeunload', event => {if (dirty || pending) {event.preventDefault(); event.returnValue = '';}}, {signal: lifetime.signal});
  window.addEventListener('pagehide', event => {if (!event.persisted) {revision++; lifetime.abort(); unregister?.(); Reflect.deleteProperty(window, 'wikiTideAgentExplore');}}, {signal: lifetime.signal});
  (async () => {
    try {
      let cursor = null;
      do {
        const data = await api(`/projects?limit=100${cursor ? `&cursor=${cursor}` : ''}`);
        for (const item of data.projects) {const option = text('option', window.WikitideI18n.t("{0}{1}", [item.title, item.status === 'archived' ? window.WikitideI18n.t("（归档）") : ''])); option.value = item.id; $('explore-project').append(option);}
        cursor = data.next_cursor;
      } while (cursor);
      $('explore-project').value = projectId || '';
    } catch (error) {if (error.name !== 'AbortError') $('explore-status').textContent = error.message;}
    controls();
  })();
})();
