(() => {
  const form = document.querySelector('form[data-editor]'); if (!form) return;
  const body = form.querySelector('[name=body]'), csrf = form.querySelector('[name=csrf]').value;
  const endpoint = `/api/drafts/note/${form.dataset.noteId}`;
  const panel = form.querySelector('[data-cloud-drafts]'), status = panel.querySelector('[role=status]');
  let version = null, stored = null;
  async function load() {
    const response = await fetch(endpoint); const data = await response.json();
    if (!response.ok) throw new Error(window.WikitideI18n.t(data.detail) || window.WikitideI18n.t("读取失败"));
    stored = data.draft; version = data.version ?? stored?.version ?? 0;
    status.textContent = stored ? window.WikitideI18n.t("云草稿保存于 {0}。恢复前请先保存当前修改。", [new Date(stored.updated_at*1000).toLocaleString()]) : window.WikitideI18n.t("尚无云草稿；可将当前内容保存后在其他设备恢复。");
    panel.querySelector('[data-cloud-restore]').disabled = !stored;
  }
  panel.querySelector('[data-cloud-load]').onclick = () => load().catch(error => status.textContent = error.message);
  panel.querySelector('[data-cloud-save]').onclick = async () => {
    if (version === null) { status.textContent = window.WikitideI18n.t("请先读取云草稿，核对版本后再保存。"); return; }
    const snapshot={body:body.value,base:form.dataset.baseSha,version};
    try {
      const response = await fetch(endpoint, { method: 'PUT', headers: { 'Content-Type':'application/json','X-CSRF-Token':csrf },
        body:JSON.stringify(snapshot) });
      const data=await response.json(); if(!response.ok)throw new Error(window.WikitideI18n.t(data.detail) || window.WikitideI18n.t("保存失败"));
      version=data.version; status.textContent=window.WikitideI18n.t("云草稿已保存，正式正文尚未提交。");
      stored={...snapshot,version};panel.querySelector('[data-cloud-restore]').disabled=false;
    } catch(error) {status.textContent=error.message;}
  };
  panel.querySelector('[data-cloud-restore]').onclick = () => {
    if (body.readOnly) { status.textContent=window.WikitideI18n.t("请先处理浏览器草稿或等待保存完成，再恢复云草稿。"); return; }
    if (!stored || !window.confirm(window.WikitideI18n.t("用已读取的云草稿替换当前编辑内容？当前未保存修改请先保存或下载。"))) return;
    form.dataset.baseSha=stored.base; form.querySelector('[name=base_sha]').value=stored.base;
    body.value=stored.body; body.dispatchEvent(new Event('editor-replace')); body.dispatchEvent(new Event('input',{bubbles:true}));
    status.textContent=window.WikitideI18n.t("已恢复云草稿；保存正文时仍会检查版本冲突。");
  };
  panel.querySelector('[data-cloud-delete]').onclick=async()=>{
    if(version===null||!window.confirm(window.WikitideI18n.t("删除已读取的云草稿？当前编辑内容和正式正文会保留。")))return;
    try{const response=await fetch(endpoint,{method:'DELETE',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({version})});const data=await response.json();if(!response.ok)throw new Error(window.WikitideI18n.t(data.detail)||window.WikitideI18n.t("删除失败"));version=data.version;stored=null;panel.querySelector('[data-cloud-restore]').disabled=true;status.textContent=window.WikitideI18n.t("云草稿已删除。");}catch(error){status.textContent=error.message;}
  };
  panel.querySelector('[data-cloud-compare]').onclick=async()=>{
    try {
      const response=await fetch(endpoint+'/compare',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({base:form.dataset.baseSha,body:body.value})});
      const data=await response.json(); if(!response.ok)throw new Error(window.WikitideI18n.t(data.detail) || window.WikitideI18n.t("比较失败"));
      const output=panel.querySelector('[data-three-way]'); output.replaceChildren();
      for(const [label,value] of [[window.WikitideI18n.t("共同旧版本"),data.base],[window.WikitideI18n.t("当前输入"),data.local],[window.WikitideI18n.t("网站最新版本"),data.server]]) {
        const column=document.createElement('section'),title=document.createElement('h3'),pre=document.createElement('pre');
        title.textContent=label;pre.textContent=value===null?window.WikitideI18n.t("无可用的共同旧版本，请人工核对。"):value;column.append(title,pre);output.append(column);
      }
      status.textContent=window.WikitideI18n.t("对照三份正文后，在编辑器中合并。保存时仍检查最新版本。");
    }catch(error){status.textContent=error.message;}
  };
  load().catch(error=>status.textContent=error.message);
})();
