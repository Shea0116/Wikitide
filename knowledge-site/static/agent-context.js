(() => {
  'use strict';
  const panel = document.getElementById('agent-context-panel');
  if (!panel) return;
  const field = document.getElementById('agent-context-constraints');
  const status = document.getElementById('agent-context-status');
  const summary = document.getElementById('agent-context-summary');
  const save = document.getElementById('agent-context-save');
  const reload = document.getElementById('agent-context-reload');
  let sessionId = null, version = null, revision = 0, busy = false, requesting = false, conflict = false, dirty = false;
  const lifetime = new AbortController();
  const unregister = window.wikiTideNative?.register('agent-context', () => ({dirty, busy: requesting}));
  window.addEventListener('pagehide', event => {
    if (!event.persisted) {revision++; lifetime.abort(); unregister?.();}
  }, {signal: lifetime.signal});

  function controls() {
    field.disabled = !sessionId || busy || requesting;
    save.disabled = !sessionId || version === null || busy || requesting || conflict;
    reload.disabled = !sessionId || busy || requesting;
  }
  async function api(path, options = {}) {
    const response = await fetch(path, {credentials: 'same-origin', ...options,
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': panel.dataset.csrf}});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(typeof data.detail === 'string' ? window.WikitideI18n.t(data.detail) : window.WikitideI18n.t("会话要求读取失败。"));
      error.status = response.status; throw error;
    }
    return data;
  }
  function render(data) {
    version = data.version; conflict = false; dirty = false;
    field.value = data.constraints.join('\n');
    summary.replaceChildren();
    if (data.summary) {
      const heading = document.createElement('p');
      heading.textContent = window.WikitideI18n.t("当前任务：{0}", [data.summary.goal.text]); summary.append(heading);
      for (const [key, label] of [['decisions', window.WikitideI18n.t("已确认")], ['open_questions', window.WikitideI18n.t("待解决")], ['next_actions', window.WikitideI18n.t("下一步")]]) {
        if (!data.summary[key]?.length) continue;
        const line = document.createElement('p');
        line.textContent = `${label}：${data.summary[key].map(item => item.text).join('；')}`;
        summary.append(line);
      }
    }
    status.textContent = data.summary ? window.WikitideI18n.t("历史摘要已就绪，后续提问会保留任务线索。") : window.WikitideI18n.t("要求已读取；对话变长后会整理历史摘要。");
  }
  async function load() {
    const ticket = ++revision;
    if (!sessionId) {controls(); return;}
    requesting = true; controls();
    try {
      const data = await api(`/api/agent/v1/sessions/${encodeURIComponent(sessionId)}/context`);
      if (ticket === revision) render(data);
    } catch (error) {
      if (ticket === revision) {version = null; status.textContent = error.message;}
    } finally {
      if (ticket === revision) {requesting = false; controls();}
    }
  }
  window.addEventListener('explore-session-change', event => {
    if (event.detail.sessionId === sessionId && dirty) {
      status.textContent = window.WikitideI18n.t("固定要求尚未保存，当前输入已保留。"); return;
    }
    sessionId = event.detail.sessionId;
    version = null; requesting = false; conflict = false; dirty = false; revision++;
    field.value = ''; summary.replaceChildren();
    status.textContent = window.WikitideI18n.t("首条回答后，可以固定本次会话的要求。");
    controls(); if (sessionId) load();
  }, {signal: lifetime.signal});
  window.addEventListener('explore-busy-change', event => {busy = event.detail.asking; controls();}, {signal: lifetime.signal});
  window.addEventListener('explore-before-session-change', event => {
    if (dirty || requesting) {
      event.preventDefault();
      panel.open = true;
      status.textContent = requesting ? window.WikitideI18n.t("正在读取或保存固定要求，请稍后切换。") : window.WikitideI18n.t("固定要求尚未保存，请先保存，或重新读取以放弃当前输入。");
    }
  }, {signal: lifetime.signal});
  field.addEventListener('input', () => {dirty = true;});
  reload.addEventListener('click', () => {if (!requesting && !busy) load();});
  save.addEventListener('click', async () => {
    if (save.disabled) return;
    const ticket = ++revision;
    const constraints = field.value.split('\n').map(value => value.trim()).filter(Boolean);
    requesting = true; controls();
    try {
      const data = await api(`/api/agent/v1/sessions/${encodeURIComponent(sessionId)}/constraints`, {
        method: 'PATCH', body: JSON.stringify({expected_version: version, constraints}),
      });
      if (ticket === revision) {render(data); status.textContent = window.WikitideI18n.t("固定要求已保存。");}
    } catch (error) {
      if (ticket === revision) {
        conflict = error.status === 409;
        status.textContent = window.WikitideI18n.t("{0}{1}", [error.message, conflict ? window.WikitideI18n.t(" 当前输入保留，请复制后重新读取。") : '']);
      }
    } finally {
      if (ticket === revision) {requesting = false; controls();}
    }
  });
  controls();
})();
