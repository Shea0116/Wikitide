(() => {
  'use strict';
  const root = document.getElementById('learning-project');
  if (!root) return;
  const $ = id => document.getElementById(id);
  const t = (text, args) => window.WikitideI18n.t(text, args);
  const notice = $('learning-notice');
  let pending = false;
  function noteIds(value) {
    const ids = value.split(/[\s,，]+/).filter(Boolean).map(item => Number(item));
    if (ids.some(item => !Number.isInteger(item) || item <= 0) || ids.length > 12) throw new Error(t("每次请选择 1～12 篇笔记。"));
    return ids;
  }
  function show(message, error = false) {
    notice.hidden = !message;
    notice.textContent = message || '';
    notice.classList.toggle('is-error', error);
  }
  async function api(path, body) {
    const response = await fetch('/api/agent/v1' + path, {
      credentials: 'same-origin', method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf},
      body: JSON.stringify(body)});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? t(data.detail) : t("请求未完成，请稍后重试"));
    return data;
  }
  async function task(work, reload = true) {
    if (pending) return;
    pending = true;
    show('');
    try {await work();}
    catch (error) {show(error.message, true); pending = false; return;}
    if (reload) location.reload();
  }
  $('learning-create')?.addEventListener('submit', event => {
    event.preventDefault();
    task(async () => {
      const title = $('learning-title').value.trim();
      if (!title) throw new Error(t("请填写项目名称。"));
      const created = await api('/projects', {title, goal: $('learning-goal').value.trim(), kind: 'learning', note_ids: noteIds($('learning-notes').value)});
      location.assign('/learn/projects/' + encodeURIComponent(created.id));
    }, false);
  });
  $('learning-scope')?.addEventListener('submit', event => {
    event.preventDefault();
    task(async () => {
      await api(`/projects/${encodeURIComponent(root.dataset.projectId)}/scope`, {note_ids: noteIds($('learning-scope-notes').value)});
    });
  });
  $('learning-open')?.addEventListener('click', () => task(async () => {
    const opened = await api(`/projects/${encodeURIComponent(root.dataset.projectId)}/open`, {});
    location.assign(opened.url);
  }, false));
  $('learning-research')?.addEventListener('submit', event => {
    event.preventDefault();
    task(async () => {
      const topic = $('learning-topic').value.trim();
      if (!topic) throw new Error(t("主题不能为空。"));
      await api(`/projects/${encodeURIComponent(root.dataset.projectId)}/research`, {
        topic, requirements: $('learning-requirements').value.trim(), idempotency_key: crypto.randomUUID()});
    });
  });
  $('learning-retry')?.addEventListener('click', () => task(async () => {
    await api(`/projects/${encodeURIComponent(root.dataset.projectId)}/runs/${encodeURIComponent($('learning-retry').dataset.run)}/retry`, {idempotency_key: crypto.randomUUID()});
  }));
  root.querySelectorAll('[data-resume]').forEach(button => button.addEventListener('click', () => {
    if (button.dataset.uncertain === 'true' && !window.confirm(t("恢复任务可能重试上次结果未知的模型调用，并再次产生费用。确定恢复吗？"))) return;
    task(async () => {await api(`/runs/${encodeURIComponent(button.dataset.resume)}/resume`, {retry_uncertain: true});});
  }));
})();
