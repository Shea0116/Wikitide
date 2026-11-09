(() => {
  'use strict';
  const root = document.querySelector('.writing');
  if (!root) return;
  const $ = name => document.getElementById(`writing-${name}`);
  const authenticated = root.dataset.authenticated === 'true';
  const aiReady = root.dataset.aiReady === 'true';
  const webReady = root.dataset.webReady === 'true';
  const workspaceEnabled = root.dataset.workspaceEnabled === 'true';
  const scopeHelp = $('scope-help')?.textContent || '';
  let projectId = new URL(location.href).searchParams.get('project_id') || null, associationDirty = false, linking = false;
  let draft = null, dirty = false, busy = false, saving = false, saveTask = null, controller = null, loadingRevision = 0;
  window.wikiTideNative?.register('writing', () => ({ dirty, busy: busy || saving }));

  function notice(text = '', error = false) {
    $('notice').textContent = text;
    $('notice').hidden = !text;
    $('notice').classList.toggle('is-error', error);
  }
  async function api(path, options = {}) {
    const response = await fetch(path, {credentials:'same-origin', ...options,
      headers:{'Content-Type':'application/json', 'X-CSRF-Token':root.dataset.csrf, ...options.headers}});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? window.WikitideI18n.t(data.detail) : window.WikitideI18n.t("操作未完成，请稍后重试。"));
    return data;
  }
  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function controls() {
    const locked = busy || saving || linking;
    if (workspaceEnabled) {
      $('project').disabled = locked || Boolean(draft?.research_origin);
      $('memory').disabled = locked || Boolean(draft?.research_origin);
      $('link-project').hidden = !draft || Boolean(draft?.research_origin);
      $('link-project').disabled = locked || !draft;
    }
    const saved = Boolean(draft?.saved_note_id);
    for (const name of ['topic','requirements','length']) $(name).disabled = locked || !window.exploreTrial.available || !aiReady || Boolean(draft);
    $('web').disabled = locked || !window.exploreTrial.available || !aiReady || !webReady || Boolean(draft);
    $('new').disabled = locked;
    $('research').disabled = locked || !window.exploreTrial.available || !aiReady || saved || Boolean(draft?.research_origin);
    $('research').textContent = draft?.discussion_origin ? window.WikitideI18n.t("讨论资料已固定") : draft?.research_origin ? window.WikitideI18n.t("研究资料已固定") : draft ? window.WikitideI18n.t("重新检索并生成提纲 ↗") : window.WikitideI18n.t("检索资料并生成提纲 ↗");
    if ($('scope-help')) $('scope-help').textContent = draft?.discussion_origin ? window.WikitideI18n.t("这份草稿复制了所选回答，保留源讨论和资料依赖；继续修改时会将草稿和所列资料交给模型。") : draft?.research_origin ? window.WikitideI18n.t("这份草稿沿用研究任务的固定资料；按要求修改时会将草稿和所列资料交给模型。") : scopeHelp;
    for (const name of ['title','outline','body','instruction']) $(name).disabled = locked || !draft || saved;
    $('save').disabled = locked || !draft || saved;
    $('compose').disabled = locked || !window.exploreTrial.available || !aiReady || !draft || saved || !$('outline').value.trim();
    $('revise').disabled = locked || !window.exploreTrial.available || !aiReady || !draft?.body || saved;
    $('export').disabled = locked || !draft?.body;
    if ($('publish')) $('publish').disabled = locked || !draft?.body || saved;
    if ($('review-note')) {
      $('review-note').hidden = !draft?.body || saved;
      $('review-note').href = draft ? `/explore/drafts/${encodeURIComponent(draft.id)}/review` : '#';
      $('review-note').setAttribute('aria-disabled', String(locked));
    }
    $('show-preview').disabled = locked || !draft;
    $('show-markdown').disabled = locked || !draft;
    $('document').hidden = !draft;
    $('empty').hidden = Boolean(draft) || busy;
    $('progress').hidden = !busy;
    $('saved-note').hidden = !saved;
    if (saved) $('saved-note').href = `/n/${draft.saved_note_id}`;
    $('save-status').textContent = saving ? window.WikitideI18n.t("保存中…") : busy ? window.WikitideI18n.t("生成中…") : !draft ? window.WikitideI18n.t("等待开始") : saved ? window.WikitideI18n.t("已存入笔记库") : dirty ? window.WikitideI18n.t("有未保存更改") : window.WikitideI18n.t("草稿已保存");
  }
  function sources(items) {
    $('sources').replaceChildren();
    $('source-count').textContent = items.length ? window.WikitideI18n.t("{0} 条来源", [items.length]) : window.WikitideI18n.t("等待检索");
    if (!items.length) $('sources').append(element('p','explore-help',window.WikitideI18n.t("生成提纲后，资料会出现在这里。")));
    for (const source of items) {
      const row = element('div','writing-source');
      const online = source.kind === 'web';
      const link = element('a','',`[${source.number}] ${source.title}`);
      link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
      row.append(link, element('small','',online ? window.WikitideI18n.t("网页摘要 · {0} · {1}", [source.section, source.retrieved_at.slice(0,10)]) : window.WikitideI18n.t("笔记 · {0}", [source.section])));
      $('sources').append(row);
    }
  }
  function show(data) {
    draft = data; dirty = false;
    if (workspaceEnabled) {
      if (Object.prototype.hasOwnProperty.call(data, 'project_id')) projectId = data.project_id;
      $('project').value = projectId || '';
      if (data.memory_mode) $('memory').checked = data.memory_mode === 'account';
      if (data.context) {
        $('used-memory').replaceChildren();
        const refs = data.context.memory_context || [];
        $('used-memory').append(element('p', 'agent-help', refs.length ? window.WikitideI18n.t("本次生成使用 {0} 条有效记忆，按相关性和上下文预算选取。", [refs.length]) : window.WikitideI18n.t("本次生成未纳入长期记忆。")));
        for (const ref of refs) $('used-memory').append(element('p', 'agent-help', window.WikitideI18n.t("记忆 {0} · 第 {1} 版（可在记忆中心核对来源）", [ref.id, ref.revision])));
        if (data.context.conflicts?.length) $('used-memory').append(element('p', 'agent-help', window.WikitideI18n.t("部分记忆存在冲突，未用于本次生成，请到记忆中心核对。")));
      }
    }
    $('topic').value = data.topic; $('requirements').value = data.requirements;
    $('length').value = data.length; $('web').checked = data.web_search;
    $('title').value = data.title; $('outline').value = data.outline; $('body').value = data.body;
    // Preview HTML is rendered and sanitized by the server, including its citation URLs.
    if (data.html) $('preview').innerHTML = data.html;
    else $('preview').replaceChildren(element('p','explore-help',window.WikitideI18n.t("确认提纲后，点击“按提纲撰写正文”。")));
    sources(data.sources); controls();
    const url = new URL(location.href); url.searchParams.set('draft', data.id); history.replaceState(null,'',url);
  }
  function canLeave() {
    if (busy || saving) {notice(window.WikitideI18n.t("文稿正在生成或保存，请完成后再切换。")); return false;}
    if (associationDirty || linking) {notice(window.WikitideI18n.t("项目关联尚未保存，请先保存关联。")); return false;}
    return !dirty || window.confirm(window.WikitideI18n.t("当前文稿有未保存修改，确定离开吗？"));
  }
  async function list() {
    if (!authenticated) return;
    try {
      const data = await api('/api/explore/drafts');
      $('history').replaceChildren();
      if (!data.drafts.length) $('history').append(element('p','explore-help',window.WikitideI18n.t("还没有文稿，从一个主题开始。")));
      for (const item of data.drafts) {
        const row = element('div','writing-history-row');
        const open = element('button','',item.title); open.type = 'button';
        open.append(element('small','',item.stale ? window.WikitideI18n.t("资料已变化 · 请重新检索") : item.saved_note_id ? window.WikitideI18n.t("已存入笔记库") : item.stage === 'draft' ? window.WikitideI18n.t("正文草稿") : item.stage === 'outline' ? window.WikitideI18n.t("写作提纲") : window.WikitideI18n.t("等待生成")));
        open.addEventListener('click',() => load(item.id));
        const remove = element('button','',window.WikitideI18n.t("删除")); remove.type = 'button';
        remove.setAttribute('aria-label',window.WikitideI18n.t("删除文稿：{0}", [item.title]));
        remove.addEventListener('click',async () => {
          if (busy || (draft?.id === item.id && !canLeave())) return;
          remove.disabled = true;
          try {
            await api(`/api/explore/drafts/${item.id}`,{method:'DELETE'});
            if (draft?.id === item.id) reset(true);
            await list();
          } catch (error) {notice(error.message,true); remove.disabled = false;}
        });
        row.append(open,remove); $('history').append(row);
      }
    } catch (error) {notice(error.message,true);}
  }
  async function load(id) {
    if (!canLeave()) return;
    const revision = ++loadingRevision;
    try {
      const data = await api(`/api/explore/drafts/${id}`);
      if (busy || revision !== loadingRevision) return;
      show(data); notice();
      if (data.busy) notice(window.WikitideI18n.t("这份文稿正在另一处生成，完成后可重新打开。"));
    } catch (error) {notice(error.message,true);}
  }
  function reset(force = false) {
    if (!force && !canLeave()) return;
    loadingRevision++; draft = null; dirty = false;
    for (const name of ['topic','requirements','title','outline','body','instruction']) $(name).value = '';
    $('length').value = 'standard'; $('web').checked = false;
    sources([]); notice(); controls();
    const url = new URL(location.href); url.searchParams.delete('draft'); url.searchParams.delete('draft_id'); history.replaceState(null,'',url);
  }
  async function save() {
    if (saveTask) return saveTask;
    if (!draft || !dirty) return;
    saving = true; controls();
    saveTask = (async () => {
      try {
        const data = await api(`/api/explore/drafts/${draft.id}`,{method:'PUT',body:JSON.stringify({
          version:draft.version,title:$('title').value,outline:$('outline').value,body:$('body').value,
        })});
        show(data);
      } finally {saving = false; saveTask = null; controls();}
    })();
    return saveTask;
  }
  async function stream(action, instruction = '') {
    await save();
    busy = true; loadingRevision++; notice(); $('stream').textContent = '';
    $('progress-status').textContent = window.WikitideI18n.t("正在准备资料…"); controls();
    controller = new AbortController();
    const currentId = draft.id;
    try {
      const response = await fetch(`/api/explore/drafts/${currentId}/generate`,{method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/json','X-CSRF-Token':root.dataset.csrf},signal:controller.signal,
        body:JSON.stringify({version:draft.version,action,instruction,
          ...(workspaceEnabled ? {project_id: projectId, memory_mode: $('memory').checked ? 'account' : 'off'} : {})})});
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(typeof data.detail === 'string' ? window.WikitideI18n.t(data.detail) : window.WikitideI18n.t("生成未完成，请重试。"));
      }
      let buffer = '', result = null;
      const decoder = new TextDecoder();
      function consume(text) {
        buffer += text;
        let boundary;
        while ((boundary = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0,boundary); buffer = buffer.slice(boundary+2);
          const lines = frame.split('\n');
          const kind = lines.find(line => line.startsWith('event:'))?.slice(6).trim();
          const raw = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
          if (!kind || !raw) continue;
          const data = JSON.parse(raw);
          if (kind === 'error') throw new Error(window.WikitideI18n.t(data.detail) || window.WikitideI18n.t("生成未完成，原文稿已保留。"));
          if (kind === 'status') $('progress-status').textContent = window.WikitideI18n.t(data.message);
          if (kind === 'delta') {$('stream').textContent += data.text; $('stream').scrollTop = $('stream').scrollHeight;}
          if (kind === 'done') result = data;
        }
      }
      if (response.body?.getReader) {
        const reader = response.body.getReader();
        try {
          while (true) {
            const part = await reader.read();
            if (part.done) {consume(decoder.decode()); break;}
            consume(decoder.decode(part.value,{stream:true}));
            if (result) break;
          }
        } finally {await reader.cancel().catch(() => {});}
      } else consume(await response.text());
      if (!result) throw new Error(window.WikitideI18n.t("连接中断，上一版文稿已保留。请重新打开查看最新状态。"));
      show(result);
      if (action !== 'outline') {showPreview(); $('instruction').value = '';}
      notice(action === 'outline' ? window.WikitideI18n.t("提纲已生成并保存，可以修改后再撰写正文。") : window.WikitideI18n.t("文稿已生成并保存，请核对来源与待核实内容。"));
      await list();
    } catch (error) {
      notice(error.name === 'AbortError' ? window.WikitideI18n.t("已停止接收生成内容，上一版文稿保留。稍后可重新打开查看服务器状态。") : error.message,true);
    } finally {busy = false; controller = null; await window.exploreTrial.refresh(); controls();}
  }
  function showPreview() {
    $('body').hidden = true; $('preview').hidden = false;
    $('show-preview').setAttribute('aria-pressed','true'); $('show-markdown').setAttribute('aria-pressed','false');
  }
  $('start').addEventListener('submit',async event => {
    event.preventDefault(); if (busy || !window.exploreTrial.available || !aiReady) return;
    if (draft?.body && !window.confirm(window.WikitideI18n.t("重新检索并生成提纲会替换当前正文，确定继续吗？"))) return;
    try {
      if (!draft) {
        saving = true; controls();
        try {
          show(await api('/api/explore/drafts',{method:'POST',body:JSON.stringify({
            topic:$('topic').value.trim(),requirements:$('requirements').value.trim(),length:$('length').value,
            web_search:$('web').checked,note_id:Number(root.dataset.noteId)||null,
            ...(workspaceEnabled ? {project_id: projectId, memory_mode: $('memory').checked ? 'account' : 'off'} : {}),
          })}));
        } finally {saving = false; controls();}
      }
      await stream('outline');
    } catch (error) {notice(error.message,true);}
  });
  $('new').addEventListener('click',() => reset());
  $('refresh')?.addEventListener('click',list);
  $('compose').addEventListener('click',async () => {
    if (busy) return;
    if (draft?.body && !window.confirm(window.WikitideI18n.t("按提纲重新撰写会替换现有正文，确定继续吗？"))) return;
    try {await stream('write');} catch (error) {notice(error.message,true);}
  });
  $('revise').addEventListener('click',async () => {
    const instruction = $('instruction').value.trim();
    if (busy || !instruction) {notice(window.WikitideI18n.t("请填写希望如何修改文稿。")); return;}
    try {await stream('revise',instruction);} catch (error) {notice(error.message,true);}
  });
  for (const name of ['title','outline','body']) $(name).addEventListener('input',() => {dirty = true; controls();});
  $('save').addEventListener('click',async () => {
    try {await save(); await list(); notice(window.WikitideI18n.t("草稿已保存。"));} catch (error) {notice(error.message,true);}
  });
  $('show-preview').addEventListener('click',async () => {
    try {await save(); showPreview();} catch (error) {notice(error.message,true);}
  });
  $('show-markdown').addEventListener('click',() => {
    $('body').hidden = false; $('preview').hidden = true;
    $('show-preview').setAttribute('aria-pressed','false'); $('show-markdown').setAttribute('aria-pressed','true');
  });
  $('stop').addEventListener('click',() => controller?.abort());
  $('export').addEventListener('click',async () => {
    try {
      await save();
      const response = await fetch(`/api/explore/drafts/${draft.id}/export`,{credentials:'same-origin'});
      if (!response.ok) {const error = await response.json(); throw new Error(error.detail || window.WikitideI18n.t("导出未完成。"));}
      const link = document.createElement('a'); link.href = URL.createObjectURL(await response.blob());
      link.download = `${draft.title.replace(/[\\/:*?"<>|]/g,'_')}.md`; link.click();
      setTimeout(() => URL.revokeObjectURL(link.href),1000);
    } catch (error) {notice(error.message,true);}
  });
  $('review-note')?.addEventListener('click', async event => {
    event.preventDefault(); if (busy || saving || linking || !draft) return;
    try {await save(); location.href = `/explore/drafts/${encodeURIComponent(draft.id)}/review`;}
    catch (error) {notice(error.message, true);}
  });
  $('publish')?.addEventListener('click',async () => {
    try {
      await save(); showPreview();
      const title = draft.title.replace(/[\\/:*?"<>|]/g,'_');
      const backend = draft.sources.some(source => source.path?.startsWith('Backend/'));
      $('path').value = `${backend ? 'Backend' : 'Frontend'}/知识求索/${title}.md`;
      $('visibility').value = 'private';
      $('visibility').querySelector('option[value="public"]').disabled = Boolean(draft.private_only);
      $('confirm-summary').textContent = window.WikitideI18n.t("《{0}》 · {1} 字符 · {2} 条检索来源", [draft.title, draft.body.length, draft.sources.length]);
      $('publish-dialog').showModal();
    } catch (error) {notice(error.message,true);}
  });
  $('cancel-publish')?.addEventListener('click',() => $('publish-dialog').close());
  $('publish-form')?.addEventListener('submit',async event => {
    event.preventDefault(); $('confirm-publish').disabled = true;
    try {
      const data = await api(`/api/explore/drafts/${draft.id}/publish`,{method:'POST',body:JSON.stringify({
        version:draft.version,path:$('path').value.trim(),visibility:$('visibility').value,confirmed:true,
      })});
      show(data.draft); $('publish-dialog').close(); notice(window.WikitideI18n.t("已创建新笔记，可打开笔记继续阅读与编辑。")); await list();
    } catch (error) {notice(error.message,true); $('publish-dialog').close();}
    finally {$('confirm-publish').disabled = false;}
  });
  window.addEventListener('beforeunload',event => {if (dirty || busy) {event.preventDefault(); event.returnValue = '';}});
  if (workspaceEnabled) {
    window.wikiTideNative?.register('writing-project', () => ({dirty: associationDirty, busy: linking}));
    $('project').addEventListener('change', () => {
      if (draft) {associationDirty = true; $('project-status').textContent = window.WikitideI18n.t("项目关联尚未保存，生成前请先保存关联。");}
      else projectId = $('project').value || null;
    });
    $('link-project').addEventListener('click', async () => {
      if (busy || saving || linking || !draft) return;
      linking = true; $('link-project').disabled = true;
      try {
        const next = $('project').value || null;
        await save();
        if (projectId && projectId !== next) await api(`/api/agent/v1/projects/${encodeURIComponent(projectId)}/resources`, {method:'DELETE', body:JSON.stringify({resource_kind:'draft',resource_id:draft.id})});
        if (next && projectId !== next) await api(`/api/agent/v1/projects/${encodeURIComponent(next)}/resources`, {method:'POST', body:JSON.stringify({resource_kind:'draft',resource_id:draft.id})});
        projectId = next; draft.project_id = next; $('project').value = next || ''; associationDirty = false; $('project-status').textContent = window.WikitideI18n.t("项目关联已保存。");
      } catch (error) {notice(error.message, true);}
      finally {linking = false; controls();}
    });
    $('start').addEventListener('submit', event => {if (associationDirty || linking) {event.preventDefault(); event.stopImmediatePropagation(); notice(window.WikitideI18n.t("请先保存项目关联。"));}}, true);
    for (const name of ['compose', 'revise', 'new']) $(name).addEventListener('click', event => {if (associationDirty || linking) {event.stopImmediatePropagation(); notice(window.WikitideI18n.t("请先保存项目关联。"));}}, true);
    window.addEventListener('beforeunload', event => {if (associationDirty || linking) {event.preventDefault(); event.returnValue = '';}});
    (async () => {
      try {
        let cursor = null;
        do {
          const data = await api(`/api/agent/v1/projects?limit=100${cursor ? `&cursor=${cursor}` : ''}`);
          for (const item of data.projects) {const option = element('option', '', item.title); option.value = item.id; $('project').append(option);}
          cursor = data.next_cursor;
        } while (cursor);
        $('project').value = projectId || '';
      } catch (error) {notice(error.message, true);}
    })();
  }
  window.addEventListener('explore-trial-change', controls);
  controls(); list();
  const initial = new URL(location.href).searchParams.get('draft_id') || new URL(location.href).searchParams.get('draft');
  if (initial) load(initial);
})();
