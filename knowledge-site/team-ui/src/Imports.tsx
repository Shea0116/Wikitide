import { ui } from "./i18n";
import { createApi } from './api';
import { useCallback, useEffect, useRef, useState } from 'react';
import './imports.css';

type Job = { id: number; filename: string; size: number; status: string; title: string; body?: string; html?: string;
  note_format?: 'pdf' | 'markdown';
  original_body?: string; assets?: { path:string;mime:string;size:number }[];
  warnings: string[]; error: string; version: number; attempts: number; result_url: string; updated_at: number };
type Listing = { jobs: Job[]; teams: { id: number; name: string }[]; personal_allowed: boolean; total: number; storage: number; ocr_available?: boolean };
const names: Record<string, string> = { queued: ui.t("等待解析"), parsing: ui.t("正在解析"), ready: ui.t("待确认入库"), failed: ui.t("解析失败"), committed: ui.t("已入库"), cancelled: ui.t("已取消") };
const safeTitle = (s: string) => s.replace(/[\\/:*?"<>|]/g, '-').replace(/^\.+/, '').trim() || ui.t("新文档");

export function Imports({ csrf }: { csrf: string }) {
  const [list, setList] = useState<Listing | null>(null), [page, setPage] = useState(1);
  const [job, setJob] = useState<Job | null>(null), [title, setTitle] = useState(''), [body, setBody] = useState('');
  const [target, setTarget] = useState(''), [path, setPath] = useState(''), [summary, setSummary] = useState('');
  const [category, setCategory] = useState('私人笔记 / Developer / 导入资料');
  const [publicConfirmed, setPublicConfirmed] = useState(false), [tab, setTab] = useState('source');
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [dragging, setDragging] = useState(false);
  const [ocr,setOcr]=useState(false);
  const [pdfMode, setPdfMode] = useState<'pdf' | 'markdown'>('pdf');
  const loadSequence = useRef(0);
  const chosen = useRef<number | null>(null), fileInput = useRef<HTMLInputElement>(null);
  const initialJob = useRef(new URLSearchParams(window.location.search).get('job'));
  const dirty = !!job && job.status === 'ready' && (title !== job.title || body !== job.body);
  const isPublic = /^(Backend|Frontend)\//.test(path);
  const pdfNote = job?.note_format === 'pdf';
  useEffect(()=>window.wikiTideNative?.register('imports',()=>({dirty,busy})),[dirty,busy]);

  const api = useCallback(async <T,>(url: string, method = 'GET', data?: unknown, file?: File): Promise<T> => {
    const response = await fetch('/api/imports' + url, { method, credentials: 'same-origin', cache: 'no-store',
      headers: method === 'GET' ? {} : { 'X-CSRF-Token': csrf, 'Content-Type': file ? 'application/octet-stream' : 'application/json' },
      body: file || (data === undefined ? undefined : JSON.stringify(data)) });
    const result = await response.json().catch(() => null);
    if (!response.ok) throw new Error(typeof result?.detail === 'string' ? result.detail : ui.t("操作未完成，请重试"));
    return result as T;
  }, [csrf]);
  const load = useCallback(async () => {
    const turn = ++loadSequence.current;
    const value = await api<Listing>(`?page=${page}`);
    if (turn !== loadSequence.current) return;
    setList(value);
    setTarget(previous => {
      const requested = previous || new URLSearchParams(window.location.search).get('team') || '';
      return (requested === '0' && value.personal_allowed) || value.teams.some(t => String(t.id) === requested)
        ? requested : value.personal_allowed ? '0' : String(value.teams[0]?.id || '');
    });
  }, [api, page]);

  useEffect(() => { let alive = true; load().catch(e => { if (alive) setError(e.message); });
    const interval = setInterval(() => { if (!document.hidden) void load().catch(() => {}); }, 3000);
    return () => { alive = false; loadSequence.current++; clearInterval(interval); };
  }, [load]);
  useEffect(() => {
    const requested = initialJob.current;
    if (!requested || !list) return;
    initialJob.current = null;
    if (/^[1-9]\d*$/.test(requested)) void select(Number(requested));
  }, [list]);
  useEffect(() => {
    if (!job || !['queued', 'parsing'].includes(job.status)) return;
    let alive = true;
    const id = job.id;
    const interval = setInterval(() => {
      if (document.hidden) return;
      void api<Job>(`/${id}`).then(value => {
        if (alive && chosen.current === id) { setJob(value); setTitle(value.title); setBody(value.body || ''); }
      }).catch(e => { if (alive) setError(e.message); });
    }, 1000);
    return () => { alive = false; clearInterval(interval); };
  }, [job?.id, job?.status, api]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  async function select(id: number) {
    if (busy || (dirty && !window.confirm(ui.t("当前修正尚未保存，确定放弃并切换吗？")))) return;
    chosen.current = id; setError(''); setJob(null);
    try {
      const value = await api<Job>(`/${id}`);
      if (chosen.current !== id) return;
      setJob(value); setTitle(value.title); setBody(value.body || '');
      setPath(`Developer/Imports/${safeTitle(value.title)}.md`); setPublicConfirmed(false); setTab(value.note_format === 'pdf' ? 'original' : 'source');
    } catch (e) { setError((e as Error).message); }
  }
  async function act(work: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError(''); setNotice('');
    try { await work(); await load(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function upload(files: FileList | File[]) {
    const batch = Array.from(files);
    if (busy) return;
    if (batch.length > 10) { setError(ui.t("每批最多选择 10 个文件")); return; }
    await act(async () => {
      const errors: string[] = []; let count = 0, duplicates = 0;
      for (const file of batch) {
        if (!/\.(md|pdf|docx|zip)$/i.test(file.name) || file.size > 10 * 1024 * 1024 || !file.size) { errors.push(ui.t("{0}：格式无效、文件为空或超过 10 MB", [file.name])); continue; }
        const isPdf = file.name.toLowerCase().endsWith('.pdf');
        try { const value = await api<Job & { duplicate?: boolean }>(`?filename=${encodeURIComponent(file.name)}&note_format=${isPdf ? pdfMode : 'markdown'}&ocr=${ocr&&isPdf&&pdfMode==='markdown'}`, 'POST', undefined, file); count++; if (value.duplicate) duplicates++; }
        catch (e) { errors.push(`${file.name}：${(e as Error).message}`); }
      }
      setNotice(ui.t("已接收 {0} 个文件{1}。点击左侧记录检查解析结果。", [count, duplicates ? ui.t("，其中 {0} 个复用已有记录", [duplicates]) : '']));
      if (errors.length) setError(errors.join('\n'));
    });
    if (fileInput.current) fileInput.current.value = '';
  }
  async function save() {
    if (!job) return;
    await act(async () => { const value = await api<Job>(`/${job.id}`, 'PUT', { title, body, version: job.version }); setJob(value); setNotice(ui.t("修正已保存，可查看阅读预览并确认入库。")); });
  }
  async function createSpace() {
    await act(async () => {
      const created = await createApi(csrf)<{ id: number }>('', 'POST', { name: ui.t("我的笔记") });
      await load(); setTarget(String(created.id));
      setNotice(ui.t("已创建自己的笔记空间，确认资料后即可入库。"));
    });
  }
  async function convert() {
    if (!job || busy || (dirty && !window.confirm(ui.t("当前修正尚未保存，确定放弃并切换吗？")))) return;
    await act(async () => {
      const queued = await api<Job>(`/${job.id}/convert`, 'POST', { ocr });
      chosen.current = queued.id;
      const value = await api<Job>(`/${queued.id}`);
      setJob(value); setTitle(value.title); setBody(value.body || ''); setTab('source');
      setPath(`Developer/Imports/${safeTitle(value.title)}.md`); setPublicConfirmed(false);
      setNotice(ui.t("已创建文字提取任务，原 PDF 笔记保留。检查并修正结果后，可下载文字或存为 Markdown 笔记。"));
    });
  }
  async function commit() {
    if (!job || dirty || !target) return;
    const targetName = target === '0' ? ui.t("{0}：{1}", [isPublic ? ui.t("公开笔记") : ui.t("私密笔记"), path]) : ui.t("团队：{0}", [list?.teams.find(t => t.id === Number(target))?.name || '']);
    if (!window.confirm(ui.t("确认将「{0}」存入{1}？\n只创建新文档，不覆盖已有文章。", [title, targetName]))) return;
    await act(async () => {
      const destinationPath = pdfNote ? `${path.slice(0, path.lastIndexOf('/'))}/${safeTitle(title)}.md` : path;
      await api(`/${job.id}/commit`, 'POST', { version: job.version, team_id: Number(target), path: destinationPath, summary, category,
        visibility: isPublic ? 'public' : 'private', confirm_public: publicConfirmed });
      setJob(await api<Job>(`/${job.id}`)); setNotice(ui.t("已入库。个人笔记沿用原有同步与检索规则，团队文档仅在所属空间内可见。"));
    });
  }
  return <section className="imports-app">
    <header className="imports-heading"><p>WIKITIDE / INGEST</p><h1>{ui.t("把资料，变成可用的知识")}</h1><p>{ui.t("上传 · 检查与修正 · 确认入库。原文件保留供下载，上传和解析不会自动公开。")}</p></header>
    <fieldset className="import-pdf-choice" disabled={busy}><legend>{ui.t("PDF 导入方式")}</legend>
      <label><input type="radio" name="pdf-mode" value="pdf" checked={pdfMode === 'pdf'} onChange={() => setPdfMode('pdf')} /><span><strong>{ui.t("保留为 PDF 笔记")}</strong><small>{ui.t("直接阅读原排版，扫描件也可保存。以后可再提取文字。")}</small></span></label>
      <label><input type="radio" name="pdf-mode" value="markdown" checked={pdfMode === 'markdown'} onChange={() => setPdfMode('markdown')} /><span><strong>{ui.t("提取文字 / 转 Markdown")}</strong><small>{ui.t("预览、修正后下载文字，或创建可编辑笔记。")}</small></span></label>
    </fieldset>
    <div className={`imports-drop ${dragging ? 'dragging' : ''}`} onDragOver={e => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={e => { e.preventDefault(); setDragging(false); void upload(e.dataTransfer.files); }}>
      <div><strong>{ui.t("拖放 Markdown、PDF、DOCX 或 Markdown 附件 ZIP 包")}</strong><p>{ui.t("每批最多 10 个，每个不超过 10 MB。DOCX 图片与 ZIP 中被正文引用的附件会一并暂存。")}</p><label><input type="checkbox" checked={ocr} disabled={busy||!list?.ocr_available} onChange={event=>setOcr(event.target.checked)} />{ui.t("扫描 PDF 使用本地 OCR")}{!list?.ocr_available?ui.t("（运行环境尚未配置）"):''}</label></div>
      <button type="button" disabled={busy} onClick={() => fileInput.current?.click()}>{busy ? ui.t("正在处理…") : ui.t("选择文件")}</button>
      <input ref={fileInput} className="sr-only" type="file" multiple accept=".md,.pdf,.docx,.zip" aria-label={ui.t("选择导入文件")} onChange={e => { if (e.target.files) void upload(e.target.files); }} />
    </div>
    {error && <div className="imports-alert" role="alert">{error}</div>}{notice && <div className="imports-notice" role="status">{notice}</div>}
    <div className="imports-layout"><aside className="imports-list" aria-label={ui.t("我的导入记录")}>
      <header><h2>{ui.t("我的导入")}</h2><small>{list?.total || 0}{ui.t(" 份 · ")}{((list?.storage || 0) / 1048576).toFixed(1)} / 50 MB</small></header>
      {!list ? <p>{ui.t("正在加载…")}</p> : !list.jobs.length ? <p>{ui.t("选择文件后，解析进度会显示在这里。")}</p> : list.jobs.map(item => <button key={item.id} disabled={busy} className={`import-card ${job?.id === item.id ? 'selected' : ''}`} onClick={() => void select(item.id)}>
        <span className="import-format">{item.note_format === 'pdf' ? ui.t("PDF 笔记") : item.filename.toLowerCase().endsWith('.pdf') ? ui.t("PDF → Markdown") : item.filename.split('.').pop()?.toUpperCase()}</span><strong>{item.filename}</strong><span className={`import-status status-${item.status}`}>{names[item.status]}</span><small>{(item.size / 1024).toFixed(1)} KB · {new Date(item.updated_at * 1000).toLocaleDateString(ui.locale)}</small>
      </button>)}
      <nav className="imports-pagination" aria-label={ui.t("导入分页")}><button disabled={busy || page === 1} onClick={() => { if (!dirty || window.confirm(ui.t("修正尚未保存，确定换页吗？"))) setPage(page - 1); }}>{ui.t("上一页")}</button><span>{page}</span><button disabled={busy || page * 20 >= (list?.total || 0)} onClick={() => { if (!dirty || window.confirm(ui.t("修正尚未保存，确定换页吗？"))) setPage(page + 1); }}>{ui.t("下一页")}</button></nav>
    </aside><main className="imports-detail">
      {!job ? <div className="imports-empty"><span>01 → 02 → 03</span><h2>{ui.t("先预览，再入库")}</h2><p>{ui.t("从左侧选择一份资料，检查解析结果并选择目标空间。")}</p><p>{ui.t("没有保存空间也可以开始，确认入库时可创建自己的空间。")}</p></div> : <>
        <header className="import-detail-head"><div><small>{names[job.status]} · v{job.version}</small><h2>{job.filename}</h2></div><div className="import-source-actions">{job.filename.toLowerCase().endsWith('.pdf') && <a href={`/api/imports/${job.id}/preview-original`} target="_blank" rel="noopener">{ui.t("打开 PDF")}</a>}<a href={`/api/imports/${job.id}/original`}>{ui.t("下载原文件")}</a></div></header>
        {['ready', 'committed'].includes(job.status) && <div className="import-source-actions">
          {pdfNote ? <button disabled={busy} onClick={() => void convert()}>{ui.t("提取文字 / 转 Markdown")}</button> : <><a href={`/api/imports/${job.id}/export?format=txt`}>{ui.t("下载提取文字")}</a><a aria-disabled={dirty} href={dirty ? undefined : `/api/imports/${job.id}/export?format=md`}>{ui.t("下载 Markdown")}{dirty ? ui.t("（请先保存）") : ''}</a></>}
        </div>}
        {!!job.warnings.length && <ul className="import-warnings">{job.warnings.map(w => <li key={w}>{ui.t(w)}</li>)}</ul>}
        {!!job.assets?.length&&<details><summary>{ui.t("已提取 ")}{job.assets.length}{ui.t(" 个附件")}</summary>{job.assets.map((item,index)=><p key={item.path}><a href={`/api/imports/${job.id}/assets/${index}`} target="_blank" rel="noopener noreferrer">{item.path}</a> · {(item.size/1024).toFixed(1)} KB</p>)}</details>}
        {['queued', 'parsing'].includes(job.status) && <div role="status"><p>{ui.t("正在准备文字预览，刷新或离开后可继续查看。")}</p><button disabled={busy} onClick={() => void act(async () => { await api(`/${job.id}/cancel`, 'POST'); setJob(await api<Job>(`/${job.id}`)); })}>{ui.t("取消解析")}</button></div>}
        {['failed', 'cancelled'].includes(job.status) && <div><p role="alert">{ui.t(job.error) || ui.t("解析已取消。")}</p><button disabled={busy || job.attempts >= 3} onClick={() => void act(async () => { await api(`/${job.id}/retry`, 'POST'); setJob(await api<Job>(`/${job.id}`)); })}>{ui.t("重新解析（最多三次）")}</button>{job.status === 'failed' && job.filename.toLowerCase().endsWith('.pdf') && <button disabled={busy} onClick={() => void convert()}>{ui.t("按当前 OCR 设置提取")}</button>}</div>}
        {['ready', 'committed'].includes(job.status) && <>
          <label>{ui.t("文档标题")}<input value={title} maxLength={160} disabled={busy || job.status === 'committed'} onChange={e => setTitle(e.target.value)} /></label>
          {pdfNote ? <><p>{ui.t("确认标题和目标后即可保存原 PDF；提取文字会创建独立任务。")}</p><iframe className="import-pdf-reader" title={ui.t("PDF 原文阅读")} src={`/api/imports/${job.id}/preview-original`} /><label>{ui.t("笔记与摘录（可选）")}<textarea value={body} maxLength={100000} disabled={busy || job.status === 'committed'} onChange={e => setBody(e.target.value)} /></label></> : <>
          <div className="imports-tabs" role="group" aria-label={ui.t("预览方式")}><button aria-pressed={tab === 'source'} onClick={() => setTab('source')}>{ui.t("Markdown 原文")}</button><button aria-pressed={tab === 'compare'} onClick={()=>setTab('compare')}>{ui.t("对照原资料")}</button><button aria-pressed={tab === 'preview'} disabled={dirty} onClick={() => setTab('preview')}>{ui.t("阅读预览")}{dirty ? ui.t("（请先保存）") : ''}</button></div>
          {tab === 'source' ? <textarea aria-label={ui.t("解析正文")} value={body} maxLength={100000} disabled={busy || job.status === 'committed'} onChange={e => setBody(e.target.value)} spellCheck={false} /> : tab==='compare'?<div className="import-comparison"><section><h3>{job.filename.toLowerCase().endsWith('.pdf')?ui.t("原 PDF"):ui.t("初始解析文本（原件可下载）")}</h3>{job.filename.toLowerCase().endsWith('.pdf')?<><p><a href={`/api/imports/${job.id}/preview-original`} target="_blank" rel="noopener">{ui.t("在独立窗口对照 PDF")}</a></p><iframe title={ui.t("原 PDF 对照")} src={`/api/imports/${job.id}/preview-original#navpanes=0&view=FitH`} /></>:<pre>{job.original_body||job.body}</pre>}</section><section><h3>{ui.t("当前修正")}</h3><textarea aria-label={ui.t("对照修正正文")} value={body} maxLength={100000} disabled={busy||job.status==='committed'} onChange={e=>setBody(e.target.value)} /></section></div>:<article className="import-preview" dangerouslySetInnerHTML={{ __html: job.html || '' }} />}
          </>}
          {job.status === 'ready' ? <><div className="imports-save"><small>{body.length.toLocaleString()}{ui.t(" / 100,000 字符 · ")}{dirty ? ui.t("有未保存修正") : ui.t("已保存")}</small><button disabled={busy || !dirty || !title.trim() || (!pdfNote && !body.trim())} onClick={() => void save()}>{ui.t("保存修正")}</button></div>
            <section className="import-destination"><h3>{ui.t("确认入库")}</h3><label>{ui.t("目标空间")}<select value={target} disabled={busy} onChange={e => setTarget(e.target.value)}><option value="">{ui.t("选择空间")}</option>{list?.personal_allowed && <option value="0">{ui.t("个人笔记库")}</option>}{list?.teams.map(t => <option key={t.id} value={t.id}>{t.name}{ui.t("（团队成员可见）")}</option>)}</select></label>
              {target === '0' && <><label>{pdfNote ? ui.t("存放目录") : ui.t("Markdown 路径")}<input value={pdfNote ? path.slice(0, path.lastIndexOf('/')) : path} maxLength={500} disabled={busy} onChange={e => { setPath(pdfNote ? `${e.target.value}/${safeTitle(title)}.md` : e.target.value); setPublicConfirmed(false); }} /></label><label>{ui.t("分类")}<input value={category} maxLength={240} disabled={busy} onChange={e => setCategory(e.target.value)} /></label><label>{ui.t("摘要")}<textarea className="import-summary" value={summary} maxLength={2000} disabled={busy} onChange={e => setSummary(e.target.value)} /></label>
                {isPublic ? <label className="import-public"><input type="checkbox" checked={publicConfirmed} onChange={e => setPublicConfirmed(e.target.checked)} />{ui.t("确认此文档可公开访问，并进入公开目录同步流程")}</label> : <p>{ui.t("默认存入 Developer/ 私密目录，仅管理员及明确授权账户可读。")}</p>}</>}
              {!list?.personal_allowed && !list?.teams.length && <div><p>{ui.t("还没有保存空间。创建后即可存入资料，新空间开始时只有你。")}</p><button type="button" disabled={busy} onClick={() => void createSpace()}>{ui.t("创建我的笔记空间")}</button></div>}
              <button className="import-primary" disabled={busy || dirty || !target || (target === '0' && isPublic && !publicConfirmed)} onClick={() => void commit()}>{ui.t("确认创建新文档")}</button><p>{ui.t("同名路径或重复正文会停止入库，避免覆盖和重复创建。")}</p>
            </section></> : <p className="imports-notice">{ui.t("已创建文档。")}<a href={job.result_url}>{ui.t("打开入库结果 →")}</a>{ui.t(" 后续修改在文档页面进行。")}</p>}
        </>}
        <footer className="imports-cleanup"><button disabled={busy} onClick={() => { if (window.confirm(ui.t("永久清除此导入记录、预览修正与原文件？已入库的文档不受影响。"))) void act(async () => { await api(`/${job.id}`, 'DELETE'); chosen.current = null; setJob(null); }); }}>{ui.t("清理导入记录")}</button><small>{ui.t("仅清理暂存资料，不删除已入库文章。")}</small></footer>
      </>}
    </main></div>
  </section>;
}
