(() => {
  'use strict';
  const root = document.getElementById('knowledge-task');
  if (!root) return;
  const $ = key => document.getElementById(`knowledge-task-${key}`);
  const t = (text, args) => window.WikitideI18n.t(text, args);
  const names = {pending: t("等待执行"), queued: t("等待执行"), running: t("执行中"), waiting_user: t("等待确认"), succeeded: t("已完成"), failed: t("执行失败"), cancelled: t("已取消")};
  const stepName = key => key === 'retrieve' ? t("检索初始资料") : key.startsWith('decision_') ? t("规划补充资料") : key.startsWith('lookup_') ? t("补查资料") : key === 'knowledge_answer' ? t("整理研究结果") : key === 'assess' ? t("核对作答要点") : t("处理任务");
  let timer, busy = false, alive = true, loadRevision = 0;
  async function request(path = '', body = null) {
    const response = await fetch(`/api/agent/v1/runs/${encodeURIComponent(root.dataset.id)}${path}`, {
      credentials: 'same-origin', method: body ? 'POST' : 'GET',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf}, ...(body ? {body: JSON.stringify(body)} : {})});
    const data = await response.json();
    if (!response.ok) throw new Error(t(data.detail || "任务读取失败，请重试。"));
    return data;
  }
  async function load() {
    const revision = ++loadRevision;
    clearTimeout(timer);
    try {
      const data = await request();
      if (!alive || revision !== loadRevision) return;
      $('status').textContent = `${names[data.status] || data.status}${data.error_code === 'provider_uncertain' ? ' · ' + t("上次生成结果未知，重试可能再次计费") : ''}`;
      if (data.result_available === false) $('status').textContent = t("资料或访问权限已变化，请重新创建任务。");
      $('cancel').disabled = busy || !['queued', 'running', 'waiting_user'].includes(data.status);
      $('resume').hidden = data.status !== 'waiting_user'; $('resume').disabled = busy;
      $('steps').replaceChildren();
      for (const step of data.steps) {
        const item = document.createElement('li');
        item.textContent = `${stepName(step.step_key)} · ${names[step.status] || t("等待执行")}`; $('steps').append(item);
      }
      $('result').replaceChildren(); $('links').replaceChildren();
      if ($('draft')) $('draft').hidden = true;
      if (data.result?.knowledge_id) {
        const result = await request('/knowledge');
        if (!alive || revision !== loadRevision) return;
        $('result').innerHTML = result.html;
        if ($('draft')) {$('draft').hidden = false; $('draft').disabled = busy;}
      }
      if (data.result?.learning_session_id) {
        const link = document.createElement('a'); link.textContent = t("查看作答反馈");
        link.href = `/learn/practice/${encodeURIComponent(data.result.learning_session_id)}`; $('links').append(link);
      }
      if (['queued', 'running'].includes(data.status)) timer = setTimeout(load, 1500);
    } catch (error) {if (alive && revision === loadRevision) {$('result').replaceChildren(); $('status').textContent = error.message; timer = setTimeout(load, 5000);}}
  }
  async function mutate(path, body) {
    if (busy) return;
    busy = true; $('cancel').disabled = $('resume').disabled = true;
    let failure;
    try {await request(path, body);}
    catch (error) {failure = error.message;}
    finally {busy = false; await load(); if (failure && alive) $('status').textContent = failure;}
  }
  $('refresh').addEventListener('click', load);
  $('draft')?.addEventListener('click', async () => {
    if (busy) return;
    const revision = loadRevision;
    busy = true; $('draft').disabled = true;
    try {
      const data = await request('/draft', {});
      if (!alive || revision !== loadRevision) return;
      const link = document.createElement('a'); link.textContent = t("打开研究草稿");
      link.href = `/explore/write?draft_id=${encodeURIComponent(data.draft_id)}`; $('links').replaceChildren(link);
      $('status').textContent = t("研究草稿已保存，可编辑后审阅入库。");
    } catch (error) {if (alive && revision === loadRevision) $('status').textContent = error.message;}
    finally {busy = false; if (alive) $('draft').disabled = false;}
  });
  $('cancel').addEventListener('click', () => mutate('/cancel', {}));
  $('resume').addEventListener('click', () => {
    if (window.confirm(t("恢复任务可能重试上次结果未知的模型调用，并再次产生费用。确定恢复吗？"))) mutate('/resume', {retry_uncertain: true});
  });
  window.addEventListener('pagehide', () => {alive = false; clearTimeout(timer);}, {once: true});
  load();
})();
