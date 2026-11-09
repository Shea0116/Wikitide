(() => {
  'use strict';
  const root = document.getElementById('practice');
  if (!root) return;
  const $ = key => document.getElementById(`practice-${key}`);
  const t = (text, args) => window.WikitideI18n.t(text, args);
  const names = {correct: t("要点正确"), partial: t("部分正确"), needs_review: t("需要继续学习"), uncertain: t("需要核对")};
  let state = null, dirty = false, blocked = false, submitting = false, alive = true;
  let savePromise = null, saveTimer, pollTimer, revision = 0, loadRevision = 0;
  async function api(path = '', method = 'GET', body) {
    const response = await fetch(`/api/learn/sessions/${encodeURIComponent(root.dataset.id)}${path}`, {
      credentials: 'same-origin', method, headers: {'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf},
      ...(body ? {body: JSON.stringify(body)} : {})});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(t(data.detail || "学习记录读取失败，请重试。"));
    return data;
  }
  function controls() {
    const editable = state?.status === 'answering' && !submitting;
    $('answer').disabled = $('hint').disabled = !editable;
    $('save').disabled = $('submit').disabled = !editable || blocked || Boolean(savePromise);
  }
  function render(data, keepInput = false) {
    state = data;
    if (!keepInput) {$('answer').value = data.answer_draft; $('hint').checked = Boolean(data.hint_used);}
    const feedback = data.attempt?.feedback;
    $('review').hidden = !data.attempt;
    $('correction').hidden = !feedback;
    $('reference').textContent = data.reference_answer || '';
    $('verdict').textContent = feedback ? names[feedback.verdict] : '';
    $('feedback').textContent = feedback?.feedback || '';
    if ($('quote')) {$('quote').textContent = feedback?.answer_quote || ''; $('quote').hidden = !feedback?.answer_quote;}
    if ($('quote-label')) $('quote-label').hidden = !feedback?.answer_quote;
    $('points').replaceChildren();
    for (const [points, label] of [[feedback?.matched_points || [], "已覆盖：{0}"], [feedback?.missing_points || [], "待补充：{0}"], [feedback?.contradictions || [], "需要修正：{0}"]]) {
      for (const point of points) {
        const item = document.createElement('li'); item.textContent = t(label, [point]); $('points').append(item);
      }
    }
    $('correction-saved').textContent = data.correction ? `${names[data.correction.verdict]} · ${data.correction.comment}` : '';
    $('job').hidden = !data.job;
    if (data.job) $('job').href = `/agent/tasks/${encodeURIComponent(data.job.id)}`;
    $('again').hidden = !feedback && !['failed', 'cancelled'].includes(data.job?.status);
    $('status').textContent = data.status === 'answering' ? t("作答检查点已读取。") : feedback ? t("参考反馈已保存，可以核对或记录你的判断。") : t("作答已提交，正在等待参考反馈。");
    if (['failed', 'cancelled'].includes(data.job?.status)) $('status').textContent = t("反馈任务未完成，作答已保留，可查看任务或重新练习。");
    controls();
    clearTimeout(pollTimer);
    if (data.status === 'submitted' && !['failed', 'cancelled'].includes(data.job?.status)) pollTimer = setTimeout(load, 2000);
  }
  async function load() {
    const requestRevision = ++loadRevision, inputRevision = revision;
    try {
      const data = await api();
      if (alive && requestRevision === loadRevision) {
        const keepInput = revision !== inputRevision;
        if (keepInput && data.status !== 'answering') {
          blocked = true; $('status').textContent = t("学习记录已变化，当前输入请先保留并重新读取。"); controls(); return;
        }
        if (!keepInput) dirty = false;
        blocked = false; render(data, keepInput);
        if (keepInput && data.status === 'answering') saveTimer = setTimeout(saveDraft, 900);
      }
    }
    catch (error) {if (alive && requestRevision === loadRevision) {$('status').textContent = error.message; blocked = true; controls();}}
  }
  function saveDraft() {
    clearTimeout(saveTimer);
    if (savePromise) return savePromise;
    if (!dirty || blocked || state?.status !== 'answering') return Promise.resolve();
    const ticket = revision;
    const body = {expected_version: state.version, answer: $('answer').value, hint_used: $('hint').checked};
    savePromise = (async () => {
      try {
        const data = await api('', 'PATCH', body);
        if (!alive) return;
        dirty = revision !== ticket; render(data, true);
        $('status').textContent = t("作答已保存。");
      } catch (error) {if (alive) {blocked = true; $('status').textContent = error.message;}}
      finally {
        savePromise = null; controls();
        if (dirty && !blocked && alive && !submitting) saveTimer = setTimeout(saveDraft, 900);
      }
    })();
    controls(); return savePromise;
  }
  function edited() {dirty = true; revision++; clearTimeout(saveTimer); if (!blocked) saveTimer = setTimeout(saveDraft, 900);}
  $('answer').addEventListener('input', edited); $('hint').addEventListener('change', edited);
  $('save').addEventListener('click', saveDraft);
  $('reload').addEventListener('click', () => {
    if (savePromise || submitting) return;
    if (!dirty || window.confirm(t("重新读取会替换当前未保存输入，确定继续吗？"))) load();
  });
  $('answer-form').addEventListener('submit', async event => {
    event.preventDefault(); if (submitting || blocked || !state || !$('answer').value.trim()) return;
    submitting = true; loadRevision++; controls(); clearTimeout(saveTimer); clearTimeout(pollTimer);
    try {
      if (savePromise) await savePromise;
      if (dirty) await saveDraft();
      if (blocked) return;
      const data = await api('/attempts', 'POST', {expected_version: state.version, answer: $('answer').value, hint_used: $('hint').checked});
      if (alive) {dirty = false; render(data);}
    } catch (error) {if (alive) $('status').textContent = error.message;}
    finally {submitting = false; controls();}
  });
  $('correction').addEventListener('submit', async event => {
    event.preventDefault(); if (submitting || !state) return;
    submitting = true; loadRevision++; clearTimeout(pollTimer);
    const button = $('correction').querySelector('button'); button.disabled = true;
    try {
      const data = await api('/corrections', 'POST', {expected_version: state.version, verdict: $('correction-verdict').value, comment: $('correction-comment').value});
      if (alive) render(data);
    } catch (error) {if (alive) $('status').textContent = error.message;}
    finally {submitting = false; button.disabled = false; controls();}
  });
  window.addEventListener('beforeunload', event => {if (dirty || savePromise || submitting) {event.preventDefault(); event.returnValue = '';}});
  window.addEventListener('pagehide', () => {alive = false; clearTimeout(saveTimer); clearTimeout(pollTimer);}, {once: true});
  load();
})();
