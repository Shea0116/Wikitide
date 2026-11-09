(() => {
  'use strict';
  const root = document.getElementById('agent-workspace');
  if (!root) return;
  const $ = name => document.getElementById(`agent-${name}`);
  const prefix = '/api/agent/v1';
  const pid = root.dataset.projectId || '';
  const params = new URL(location.href).searchParams;
  const panel = location.pathname === '/agent/memories' ? 'memories' : params.get('panel') === 'runs' ? 'runs' : 'projects';
  let project = null, editingMemory = null, pending = 0, dirtyProject = false, dirtyMemory = false;
  let projectCursor = null, memoryCursor = null, runCursor = null, memoryRevision = 0, projectRevision = 0, runRevision = 0;
  let run = null, events = null, eventSequence = 0, alive = true, runKey = null, reconnectTimer = null;
  let navigating = false;
  const memories = new Map();
  const lifetime = new AbortController();
  const unregister = window.wikiTideNative?.register('agent-workspace', () => ({dirty: dirtyProject || dirtyMemory, busy: pending > 0}));

  function node(tag, text, className = '') {
    const result = document.createElement(tag); result.textContent = text; result.className = className; return result;
  }
  function button(text, action) {
    const result = node('button', text); result.type = 'button';
    result.addEventListener('click', () => task(async () => {result.disabled = true; try {await action();} finally {result.disabled = false;}}));
    return result;
  }
  function notice(text = '', error = false) { $('notice').textContent = text; $('notice').hidden = !text; $('notice').classList.toggle('is-error', error); }
  async function api(path, options = {}) {
    const response = await fetch(prefix + path, {credentials: 'same-origin', signal: lifetime.signal, ...options,
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf, ...options.headers}});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {const error = new Error(typeof data.detail === 'string' ? window.WikitideI18n.t(data.detail) : window.WikitideI18n.t("操作未完成，请稍后重试。")); error.status = response.status; throw error;}
    return data;
  }
  async function task(action) {
    pending++;
    try {await action();} catch (error) {if (error.name !== 'AbortError') notice(error.message + (error.status === 409 ? window.WikitideI18n.t(" 当前输入已保留，请复制后重新读取。") : ''), true);}
    finally {pending--;}
  }
  function post(path, data = {}, method = 'POST') {return api(path, {method, body: JSON.stringify(data)});}
  function navigate(url) {navigating = true; dirtyProject = false; dirtyMemory = false; location.assign(url);}
  function canDiscard(dirty) {return !dirty || window.confirm(window.WikitideI18n.t("当前输入尚未保存，确定放弃并重新读取吗？"));}
  function link(text, href) {const result = node('a', text); result.href = href; return result;}
  function empty(target, text) {target.replaceChildren(node('p', text, 'agent-help'));}
  function date(value) {return value ? new Date(value * 1000).toLocaleString() : window.WikitideI18n.t("无到期时间");}
  function readExpiry() {const raw = $('memory-expiry').value; return raw ? Math.floor(new Date(raw).getTime() / 1000) : null;}
  function expiryValue(value) {if (!value) return ''; const d = new Date(value * 1000); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);}

  for (const key of ['projects', 'memories', 'runs']) {
    $(`${key}-panel`).hidden = key !== panel || Boolean(pid);
    root.querySelector(`[data-tab="${key}"]`).toggleAttribute('aria-current', key === panel);
    if (key === panel) root.querySelector(`[data-tab="${key}"]`).setAttribute('aria-current', 'page');
  }
  $('project-detail').hidden = !pid;

  async function loadProjects(more = false) {
    const query = $('project-query').value.trim(), status = $('project-status').value;
    const data = await api(`/projects?limit=20${more && projectCursor ? `&cursor=${projectCursor}` : ''}${query ? `&q=${encodeURIComponent(query)}` : ''}${status ? `&status=${encodeURIComponent(status)}` : ''}`);
    if (!more) $('project-list').replaceChildren();
    for (const item of data.projects) {
      const row = node('article', '', 'agent-list-row'), heading = node('h3', '');
      heading.append(link(item.title, `/agent/projects/${encodeURIComponent(item.id)}`));
      row.append(heading, node('p', item.goal || window.WikitideI18n.t("尚未填写目标")), node('small', item.status === 'archived' ? window.WikitideI18n.t("已归档") : window.WikitideI18n.t("进行中"), 'agent-meta'));
      $('project-list').append(row);
    }
    if (!$('project-list').children.length) empty($('project-list'), window.WikitideI18n.t("还没有项目，从一个具体目标开始。"));
    projectCursor = data.next_cursor; $('project-more').hidden = !projectCursor;
  }
  async function loadProjectOptions() {
    const items = []; let cursor = null;
    do {
      const data = await api(`/projects?limit=100${cursor ? `&cursor=${cursor}` : ''}`); items.push(...data.projects); cursor = data.next_cursor;
    } while (cursor && alive);
    for (const name of ['memory-project', 'memory-filter-project', 'run-project']) {
      const select = $(name), value = select.value;
      while (select.options.length > 1) select.remove(1);
      for (const item of items) {const option = node('option', window.WikitideI18n.t("{0}{1}", [item.title, item.status === 'archived' ? window.WikitideI18n.t("（归档）") : ''])); option.value = item.id; select.append(option);}
      select.value = value || params.get('project_id') || '';
    }
    if (params.get('project_id')) $('memory-scope').value = 'project';
    $('memory-project').disabled = $('memory-scope').value !== 'project';
  }
  function renderProject(data) {
    project = data; dirtyProject = false;
    for (const [field, key] of [['title','title'], ['goal','goal'], ['progress','progress'], ['next','next_action'], ['status','status']]) $(`edit-${field}`).value = data[key] || '';
    $('project-continue').disabled = data.status !== 'active';
    $('project-write').href = `/explore/write?project_id=${encodeURIComponent(data.id)}`;
    $('project-memory').href = `/agent/memories?project_id=${encodeURIComponent(data.id)}`;
    $('resource-list').replaceChildren();
    for (const item of data.resources || []) {
      const row = node('div', '', 'agent-list-row');
      const path = item.resource_kind === 'note' ? `/n/${encodeURIComponent(item.resource_id)}` : item.resource_kind === 'session' ? `/explore?session_id=${encodeURIComponent(item.resource_id)}` : `/explore/write?draft_id=${encodeURIComponent(item.resource_id)}`;
      row.append(item.available ? link(item.title, path) : node('span', item.title));
      row.append(node('p', window.WikitideI18n.t("{0}{1}", [({note:window.WikitideI18n.t("笔记"),session:window.WikitideI18n.t("会话"),draft:window.WikitideI18n.t("文稿")})[item.resource_kind], item.available ? '' : window.WikitideI18n.t(" · 已失效")]), 'agent-meta'));
      row.append(button(window.WikitideI18n.t("移出项目"), async () => {
        if (dirtyProject) {notice(window.WikitideI18n.t("请先保存项目修改，再调整关联资料。")); return;}
        renderProject(await post(`/projects/${encodeURIComponent(pid)}/resources`, {resource_kind: item.resource_kind, resource_id: item.resource_id}, 'DELETE'));
      }));
      $('resource-list').append(row);
    }
    if (!(data.resources || []).length) empty($('resource-list'), window.WikitideI18n.t("还没有关联资料。可以在下方关联已有资料。"));
  }
  async function loadProject() {
    const revision = ++projectRevision;
    const data = await api(`/projects/${encodeURIComponent(pid)}`);
    if (revision === projectRevision) renderProject(data);
  }
  $('project-create').addEventListener('submit', event => {event.preventDefault(); if (pending) return; task(async () => {
    const data = await post('/projects', {title: $('new-title').value.trim(), goal: $('new-goal').value.trim()}); navigate(`/agent/projects/${encodeURIComponent(data.id)}`);
  });});
  $('project-refresh').addEventListener('click', () => task(() => loadProjects()));
  $('project-filter').addEventListener('submit', event => {event.preventDefault(); task(() => loadProjects());});
  $('project-more').addEventListener('click', () => task(() => loadProjects(true)));
  $('project-edit').addEventListener('input', () => {dirtyProject = true;});
  $('project-edit').addEventListener('submit', event => {event.preventDefault(); if (!project || pending) return; task(async () => {
    renderProject(await post(`/projects/${encodeURIComponent(pid)}`, {expected_version: project.version, title: $('edit-title').value.trim(), goal: $('edit-goal').value.trim(), progress: $('edit-progress').value.trim(), next_action: $('edit-next').value.trim(), status: $('edit-status').value}, 'PATCH')); notice(window.WikitideI18n.t("项目已保存。"));
  });});
  $('project-reload').addEventListener('click', () => {if (canDiscard(dirtyProject)) task(loadProject);});
  $('project-continue').addEventListener('click', () => task(async () => {
    if (dirtyProject) {notice(window.WikitideI18n.t("请先保存目标和进度，再继续任务。")); return;}
    const data = await post(`/projects/${encodeURIComponent(pid)}/continue`); navigate(`/explore?session_id=${encodeURIComponent(data.session_id)}`);
  }));
  $('project-delete').addEventListener('click', () => task(async () => {
    if (!window.confirm(window.WikitideI18n.t("删除项目会同时遗忘项目记忆并取消未执行任务，原始笔记、会话和文稿仍可单独查看。确定删除吗？"))) return;
    await api(`/projects/${encodeURIComponent(pid)}`, {method: 'DELETE'}); navigate('/agent/projects');
  }));
  $('resource-form').addEventListener('submit', event => {event.preventDefault(); if (pending) return; task(async () => {
    if (dirtyProject) {notice(window.WikitideI18n.t("请先保存项目修改，再关联资料。")); return;}
    renderProject(await post(`/projects/${encodeURIComponent(pid)}/resources`, {resource_kind: $('resource-kind').value, resource_id: $('resource-id').value.trim()})); $('resource-id').value = ''; notice(window.WikitideI18n.t("资料已关联。"));
  });});

  function resetMemory() {
    editingMemory = null; dirtyMemory = false; $('memory-form').reset(); $('memory-form-title').textContent = window.WikitideI18n.t("新建记忆");
    for (const name of ['scope', 'project', 'kind']) $(`memory-${name}`).disabled = false;
    if (params.get('project_id')) {$('memory-scope').value = 'project'; $('memory-project').value = params.get('project_id');}
    $('memory-project').disabled = $('memory-scope').value !== 'project';
  }
  function editMemory(item) {
    if (!canDiscard(dirtyMemory)) return;
    editingMemory = item; dirtyMemory = false; $('memory-form-title').textContent = window.WikitideI18n.t("编辑记忆 · 第 {0} 版", [item.revision]);
    for (const name of ['scope', 'project', 'kind']) $(`memory-${name}`).disabled = true;
    $('memory-scope').value = item.scope; $('memory-project').value = item.project_id || ''; $('memory-kind').value = item.kind;
    $('memory-content').value = item.content; $('memory-key').value = item.conflict_key || ''; $('memory-expiry').value = expiryValue(item.expires_at); $('memory-state').value = item.status === 'disabled' ? 'disabled' : 'active';
    $('memory-form-title').scrollIntoView({block: 'start'});
  }
  function renderMemory(item) {
    memories.set(item.id, item);
    const row = node('article', '', 'agent-list-row');
    const states = {active:window.WikitideI18n.t("启用"), disabled:window.WikitideI18n.t("停用"), candidate:window.WikitideI18n.t("待确认候选")};
    row.append(node('h3', window.WikitideI18n.t("{0} · {1}", [item.scope === 'project' ? window.WikitideI18n.t("项目") : window.WikitideI18n.t("账户"), states[item.status] || item.status])), node('p', item.content));
    row.append(node('p', window.WikitideI18n.t("第 {0} 版 · {1}{2}{3}", [item.revision, item.expired ? window.WikitideI18n.t("已到期") : date(item.expires_at), !item.source_valid ? window.WikitideI18n.t(" · 来源已失效") : '', item.conflict_key ? window.WikitideI18n.t(" · 冲突标识：{0}", [item.conflict_key]) : '']), 'agent-meta'));
    const source = item.source || {}, details = node('details', ''); details.append(node('summary', window.WikitideI18n.t("来源与记忆标识")), node('p', window.WikitideI18n.t("记忆 ID：{0}", [item.id])));
    if (source.kind === 'note') details.append(link(window.WikitideI18n.t("原始笔记 #{0}", [source.note_id]), `/n/${encodeURIComponent(source.note_id)}`));
    else details.append(node('p', source.kind === 'message' ? window.WikitideI18n.t("来自消息 {0}", [source.id]) : window.WikitideI18n.t("用户手动确认")));
    row.append(details);
    const actions = node('div', '', 'agent-actions');
    if (item.status === 'candidate') actions.append(button(window.WikitideI18n.t("确认并启用"), async () => {await post(`/memory-candidates/${encodeURIComponent(item.id)}/activate`, {expected_revision: item.revision}); await loadMemories(); notice(window.WikitideI18n.t("候选记忆已确认。"));}));
    else actions.append(button(window.WikitideI18n.t("编辑"), async () => editMemory(item)), button(item.status === 'active' ? window.WikitideI18n.t("停用") : window.WikitideI18n.t("启用"), async () => {
      await post(`/memories/${encodeURIComponent(item.id)}`, {expected_revision: item.revision, content: item.content, conflict_key: item.conflict_key || '', expires_at: item.expires_at, status: item.status === 'active' ? 'disabled' : 'active'}, 'PATCH'); await loadMemories();
    }));
    actions.append(button(window.WikitideI18n.t("忘记"), async () => {
      if (!window.confirm(window.WikitideI18n.t("确定遗忘这条记忆吗？依赖它的历史上下文会一并失效。"))) return;
      const data = await post(`/memories/${encodeURIComponent(item.id)}`, {expected_revision: item.revision}, 'DELETE');
      if (editingMemory?.id === item.id) resetMemory(); await loadMemories(); notice(window.WikitideI18n.t("已遗忘，{0} 条相关回答已标记需复查。", [data.affected_answers || 0]));
    }));
    row.append(actions); $('memory-list').append(row);
  }
  async function loadMemories(more = false) {
    const revision = ++memoryRevision, query = new URLSearchParams({limit: '20'});
    if ($('memory-filter-project').value) query.set('project_id', $('memory-filter-project').value);
    if ($('memory-filter-status').value) query.set('status', $('memory-filter-status').value);
    if (more && memoryCursor) query.set('cursor', String(memoryCursor));
    const data = await api(`/memories?${query}`); if (revision !== memoryRevision) return;
    if (!more) {$('memory-list').replaceChildren(); memories.clear();}
    data.memories.forEach(renderMemory);
    if (!memories.size) empty($('memory-list'), window.WikitideI18n.t("当前筛选范围还没有记忆。"));
    memoryCursor = data.next_cursor; $('memory-more').hidden = !memoryCursor;
  }
  $('memory-scope').addEventListener('change', () => {$('memory-project').disabled = $('memory-scope').value !== 'project';});
  $('memory-form').addEventListener('input', () => {dirtyMemory = true;});
  $('memory-form').addEventListener('change', () => {dirtyMemory = true;});
  $('memory-reset').addEventListener('click', () => {if (canDiscard(dirtyMemory)) resetMemory();});
  $('memory-form').addEventListener('submit', event => {event.preventDefault(); if (pending) return; task(async () => {
    const body = {content: $('memory-content').value.trim(), conflict_key: $('memory-key').value.trim(), expires_at: readExpiry()};
    if (editingMemory) await post(`/memories/${encodeURIComponent(editingMemory.id)}`, {...body, expected_revision: editingMemory.revision, status: $('memory-state').value}, 'PATCH');
    else {
      if ($('memory-scope').value === 'project' && !$('memory-project').value) {notice(window.WikitideI18n.t("请为项目记忆选择一个项目。"), true); return;}
      const created = await post('/memories', {...body, scope: $('memory-scope').value, project_id: $('memory-scope').value === 'project' ? $('memory-project').value : null, kind: $('memory-kind').value, source: {kind: 'manual'}});
      if ($('memory-state').value === 'disabled') await post(`/memories/${encodeURIComponent(created.id)}`, {...body, expected_revision: created.revision, status: 'disabled'}, 'PATCH');
    }
    resetMemory(); await loadMemories(); notice(window.WikitideI18n.t("记忆已保存。"));
  });});
  $('memory-filter').addEventListener('submit', event => {event.preventDefault(); task(() => loadMemories());});
  $('memory-more').addEventListener('click', () => task(() => loadMemories(true)));
  $('memory-export').addEventListener('click', () => task(async () => {
    const data = await api('/memory-export'), url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'}));
    const download = link('', url); download.download = 'wikitide-memories.json'; download.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); notice(window.WikitideI18n.t("记忆已导出，请妥善保存。"));
  }));

  const statusNames = {queued:window.WikitideI18n.t("等待执行"), running:window.WikitideI18n.t("执行中"), waiting_user:window.WikitideI18n.t("等待确认"), succeeded:window.WikitideI18n.t("已完成"), completed:window.WikitideI18n.t("已完成"), failed:window.WikitideI18n.t("执行失败"), cancelled:window.WikitideI18n.t("已取消"), interrupted:window.WikitideI18n.t("执行中断")};
  const stepNames = {retrieve:window.WikitideI18n.t("检索资料"),outline:window.WikitideI18n.t("整理提纲"),write:window.WikitideI18n.t("撰写文稿"),validate:window.WikitideI18n.t("检查引用"),save_draft:window.WikitideI18n.t("保存草稿")};
  const errorNames = {provider_uncertain:window.WikitideI18n.t("上次生成结果未知，重试可能再次计费"),context_changed:window.WikitideI18n.t("项目或记忆已变化，请新建任务"),source_changed:window.WikitideI18n.t("资料已更新，请重新检索"),source_unavailable:window.WikitideI18n.t("资料已退出可用范围"),source_or_context_changed:window.WikitideI18n.t("来源或上下文已变化，旧结果不可继续使用"),project_unavailable:window.WikitideI18n.t("项目已归档或移除"),account_inactive:window.WikitideI18n.t("账户暂不可用"),draft_conflict:window.WikitideI18n.t("原文稿已变化，请重新打开"),draft_limit:window.WikitideI18n.t("草稿数量已达上限"),budget_exceeded:window.WikitideI18n.t("本次生成预算不足"),time_budget_exceeded:window.WikitideI18n.t("本次执行时间已达上限"),insufficient_evidence:window.WikitideI18n.t("没有找到足够资料"),web_unavailable:window.WikitideI18n.t("联网搜索尚未启用"),missing_citations:window.WikitideI18n.t("正文缺少有效引用"),provider_output_limit:window.WikitideI18n.t("生成超过长度预算"),validation_failed:window.WikitideI18n.t("生成内容未通过校验"),task_failed:window.WikitideI18n.t("任务未完成，请重新创建"),cancelled:window.WikitideI18n.t("任务已取消")};
  function runError(code) {return errorNames[code] || window.WikitideI18n.t("任务未完成，请重新读取状态");}
  function runStatus(value) {return statusNames[value] || value;}
  function renderRun(data) {
    run = data; $('run-detail').hidden = false; $('run-title').textContent = data.topic || window.WikitideI18n.t("任务 {0}", [data.id]);
    $('run-status').textContent = `${runStatus(data.status)}${data.error_code || data.error ? ` · ${runError(data.error_code || data.error)}` : ''}`;
    const budget = data.budget || data;
    $('run-budget-status').textContent = window.WikitideI18n.t("Token 预算占用：{0} / {1}；检索：{2} / {3}（用量未知时按预留计算）", [budget.used_tokens ?? data.tokens_used ?? '—', budget.max_tokens ?? '—', budget.search_calls ?? '—', budget.max_search_calls ?? '—']);
    $('run-cancel').disabled = !['queued', 'running', 'waiting_user'].includes(data.status);
    $('run-resume').disabled = data.status !== 'waiting_user';
    $('run-result').replaceChildren();
    let result = data.result || data.result_json || {};
    if (typeof result === 'string') {try {result = JSON.parse(result);} catch (_) {result = {text: result};}}
    const draftId = result.draft_id || data.draft_id;
    if (['knowledge', 'assessment'].includes(data.kind)) $('run-result').append(link(window.WikitideI18n.t("打开任务"), `/agent/tasks/${encodeURIComponent(data.id)}`));
    if (draftId) $('run-result').append(link(window.WikitideI18n.t("打开结果文稿并核对 ↗"), `/explore/write?draft_id=${encodeURIComponent(draftId)}`));
    if (result.text || result.summary) $('run-result').append(node('pre', result.text || (typeof result.summary === 'string' ? result.summary : JSON.stringify(result.summary, null, 2))));
    if (result.impacts || result.affected) $('run-result').append(node('pre', JSON.stringify(result.impacts || result.affected, null, 2)));
    if (result.impact || result.proposal_diff) $('run-result').append(node('pre', typeof (result.proposal_diff || result.impact) === 'string' ? (result.proposal_diff || result.impact) : JSON.stringify(result.impact, null, 2)));
    if (data.steps?.length) $('run-result').append(node('p', data.steps.map(step => window.WikitideI18n.t("{0}：{1}{2}", [stepNames[step.step_key] || window.WikitideI18n.t("处理资料"), runStatus(step.status), step.error_code ? `（${runError(step.error_code)}）` : ''])).join('；'), 'agent-meta'));
    if (['succeeded', 'completed', 'cancelled', 'failed', 'waiting_user'].includes(data.status)) {events?.close(); events = null; clearTimeout(reconnectTimer);}
  }
  function connectEvents(id, reset = true) {
    events?.close(); clearTimeout(reconnectTimer);
    if (reset) {eventSequence = 0; $('run-events').replaceChildren();}
    if (!window.EventSource) return;
    const source = new EventSource(`${prefix}/runs/${encodeURIComponent(id)}/events?after_seq=${eventSequence}`); events = source;
    const receive = event => {
      if (run?.id !== id || events !== source) return;
      let item; try {item = JSON.parse(event.data);} catch (_) {return;}
      const seq = item.seq || item.sequence || Number(event.lastEventId) || 0;
      if (seq && seq <= eventSequence) return; eventSequence = Math.max(eventSequence, seq);
      const payload = item.payload || item;
      const label = payload.message || payload.status || item.type || item.kind || item.event || event.type;
      $('run-events').append(node('li', window.WikitideI18n.t("{0}{1}{2}{3}", [seq ? `${seq}. ` : '', runStatus(label), payload.step ? window.WikitideI18n.t(" · {0}", [stepNames[payload.step] || window.WikitideI18n.t("处理资料")]) : '', payload.error_code ? ` · ${runError(payload.error_code)}` : ''])));
      const revision = ++runRevision;
      task(async () => {const data = await api(`/runs/${encodeURIComponent(id)}`); if (run?.id === id && revision === runRevision) renderRun(data);});
    };
    source.onmessage = receive;
    for (const name of ['metadata','step','progress','done','update','status','run','completed','failed']) source.addEventListener(name, receive);
    source.onerror = () => {if (events === source && run?.id === id) {
      source.close(); events = null;
      $('run-status').textContent = window.WikitideI18n.t("{0} · 正在重新连接执行记录，可随时重新读取。", [runStatus(run.status)]);
      if (alive && ['queued', 'running'].includes(run.status)) reconnectTimer = setTimeout(() => connectEvents(id, false), 1500);
    }};
  }
  async function openRun(id) {
    const revision = ++runRevision, data = await api(`/runs/${encodeURIComponent(id)}`); if (revision !== runRevision) return;
    renderRun(data); connectEvents(id);
  }
  async function loadRuns(more = false) {
    const data = await api(`/runs?limit=20${more && runCursor ? `&cursor=${runCursor}` : ''}`);
    if (!more) $('run-list').replaceChildren();
    for (const item of data.runs || []) {
      const row = node('article', '', 'agent-list-row'); row.append(button(item.topic || item.id, () => openRun(item.id)), node('p', window.WikitideI18n.t("{0} · {1}", [runStatus(item.status), {knowledge:window.WikitideI18n.t("后台研究"),assessment:window.WikitideI18n.t("参考反馈"),research:window.WikitideI18n.t("研究"),writing:window.WikitideI18n.t("写作"),impact:window.WikitideI18n.t("影响分析")}[item.kind] || item.kind]), 'agent-meta')); $('run-list').append(row);
    }
    if (!$('run-list').children.length) empty($('run-list'), window.WikitideI18n.t("还没有后台任务。"));
    runCursor = data.next_cursor; $('run-more').hidden = !runCursor;
  }
  $('run-form').addEventListener('input', () => {runKey = null;});
  $('run-form').addEventListener('change', () => {runKey = null;});
  $('run-form').addEventListener('submit', event => {event.preventDefault(); if (pending) return; task(async () => {
    if ($('run-kind').value === 'impact' && !$('run-note').value) {notice(window.WikitideI18n.t("影响分析需要填写公开笔记 ID。"), true); return;}
    runKey ||= window.crypto?.randomUUID?.() || `web-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const data = await post('/runs', {kind: $('run-kind').value, topic: $('run-topic').value.trim(), requirements: $('run-requirements').value.trim(), project_id: $('run-project').value || null, draft_id: $('run-draft').value.trim() || null, note_id: Number($('run-note').value) || null, web_search: $('run-web').checked, memory_mode: $('run-memory').checked ? 'account' : 'off', max_tokens: Number($('run-budget').value), max_search_calls: Number($('run-searches').value), idempotency_key: runKey});
    await loadRuns(); await openRun(data.id || data.run_id || data.run?.id); notice(window.WikitideI18n.t("任务已提交，可以稍后回来查看。"));
  });});
  $('run-refresh').addEventListener('click', () => task(() => loadRuns()));
  $('run-more').addEventListener('click', () => task(() => loadRuns(true)));
  $('run-reread').addEventListener('click', () => {if (run) task(() => openRun(run.id));});
  $('run-cancel').addEventListener('click', () => {if (run) task(async () => {await post(`/runs/${encodeURIComponent(run.id)}/cancel`); await openRun(run.id); await loadRuns();});});
  $('run-resume').addEventListener('click', () => {if (run) task(async () => {
    if (!window.confirm(window.WikitideI18n.t("恢复任务可能重试上次结果未知的模型调用，并再次产生费用。确定恢复吗？"))) return;
    await post(`/runs/${encodeURIComponent(run.id)}/resume`, {retry_uncertain: true}); await openRun(run.id); await loadRuns();
  });});
  window.addEventListener('beforeunload', event => {if (!navigating && (dirtyProject || dirtyMemory || pending)) {event.preventDefault(); event.returnValue = '';}}, {signal: lifetime.signal});
  window.addEventListener('pagehide', event => {if (!event.persisted) {alive = false; lifetime.abort(); events?.close(); clearTimeout(reconnectTimer); unregister?.();}}, {signal: lifetime.signal});
  root.addEventListener('click', event => {const anchor = event.target.closest('a'); if (anchor && (dirtyProject || dirtyMemory) && !canDiscard(true)) event.preventDefault();});
  task(async () => {
    if (pid) await loadProject();
    else if (panel === 'projects') await loadProjects();
    else {await loadProjectOptions(); if (panel === 'memories') await loadMemories(); else await loadRuns();}
  });
})();
