(() => {
  'use strict';
  const root = document.getElementById('proposal');
  if (!root) return;
  const $ = key => document.getElementById(`proposal-${key}`);
  const t = (text, args) => window.WikitideI18n.t(text, args);
  const prefix = `/api/explore/drafts/${encodeURIComponent(root.dataset.id)}`;
  let targets = [], proposal = null, busy = false, alive = true, revision = 0, saved = false;
  async function api(path, method = 'GET', body) {
    const response = await fetch(path, {credentials: 'same-origin', method,
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf},
      ...(body ? {body: JSON.stringify(body)} : {})});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(t(data.detail || "修改建议读取失败，请重试。"));
    return data;
  }
  function controls() {
    $('target').disabled = busy || saved || !targets.length;
    $('mode').disabled = $('query').disabled = busy || saved;
    $('search').querySelector('button').disabled = busy || saved;
    $('preview').disabled = busy || saved || !targets.length;
    $('confirm').disabled = busy || saved || !proposal;
    $('apply').disabled = busy || saved || !proposal || !$('confirm').checked;
  }
  function invalidate() {
    revision++; proposal = null; $('confirm').checked = false;
    $('review').hidden = true; $('diff').textContent = ''; controls();
  }
  function applied(data) {
    saved = true; $('review').hidden = true; $('diff').textContent = '';
    $('saved').href = `/n/${encodeURIComponent(data.note_id)}`; $('saved').hidden = false;
    $('status').textContent = t("修改已保存，版本历史与同步记录已更新。"); controls();
  }
  async function search(event) {
    event?.preventDefault(); if (busy || saved) return;
    invalidate(); targets = []; $('target').replaceChildren(); const ticket = revision; busy = true; controls();
    try {
      const data = await api(`${prefix}/targets?q=${encodeURIComponent($('query').value.trim())}`);
      if (!alive || ticket !== revision) return;
      targets = data.targets; $('target').replaceChildren();
      for (const item of targets) {
        const option = document.createElement('option'); option.value = String(item.id);
        option.textContent = `${item.path} · ${item.visibility === 'private' ? t("私密") : t("公开")}${item.cited ? ' · ' + t("草稿引用的笔记") : ''}`;
        $('target').append(option);
      }
      $('status').textContent = targets.length ? t("选择目标后生成差异，尚未修改笔记。") : t("没有匹配的可编辑笔记，可以返回草稿继续整理或导出。");
    } catch (error) {if (alive) $('status').textContent = error.message;}
    finally {busy = false; if (alive) controls();}
  }
  $('search').addEventListener('submit', search);
  $('target').addEventListener('change', invalidate); $('mode').addEventListener('change', invalidate);
  $('confirm').addEventListener('change', controls);
  $('preview-form').addEventListener('submit', async event => {
    event.preventDefault(); if (busy || saved) return;
    const target = targets.find(item => String(item.id) === $('target').value); if (!target) return;
    invalidate(); const ticket = revision; busy = true; controls();
    try {
      const data = await api(`${prefix}/proposals`, 'POST', {draft_version: Number(root.dataset.version),
        note_id: target.id, target_version: target.version, mode: $('mode').value});
      if (!alive || ticket !== revision) return;
      if (data.status === 'applied') {applied(data); return;}
      proposal = data; $('diff').replaceChildren();
      for (const line of data.diff.match(/[^\n]*\n|[^\n]+$/g) || []) {
        const span = document.createElement('span'); span.textContent = line;
        span.className = /^(---|\+\+\+|@@)/.test(line) ? 'diff-heading' : line.startsWith('+') ? 'diff-added' : line.startsWith('-') ? 'diff-removed' : '';
        $('diff').append(span);
      }
      $('review').hidden = false;
      $('target-summary').textContent = `${data.target.path} · ${data.target.visibility === 'private' ? t("私密") : t("公开")} · ${data.mode === 'append' ? t("追加到末尾") : t("替换全文")}`;
      $('status').textContent = t("请核对完整差异；草稿或原文变化后需要重新审阅。");
      if (data.conflict) {proposal = null; $('status').textContent = t("目标笔记已变化，请刷新目标并重新审阅差异。");}
    } catch (error) {if (alive) $('status').textContent = error.message;}
    finally {busy = false; if (alive) controls();}
  });
  $('apply-form').addEventListener('submit', async event => {
    event.preventDefault(); if (busy || saved || !proposal || !$('confirm').checked) return;
    busy = true; controls();
    try {const data = await api(`/api/explore/proposals/${encodeURIComponent(proposal.id)}/apply`, 'POST', {confirmed: true}); if (alive) applied(data);}
    catch (error) {if (alive) {$('status').textContent = error.message; $('confirm').checked = false;}}
    finally {busy = false; if (alive) controls();}
  });
  window.addEventListener('pagehide', () => {alive = false; revision++;}, {once: true});
  search();
})();
