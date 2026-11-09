import { ui } from "./i18n";
import { useEffect, useState } from 'react';
import { createApi, type Revision } from './api';
import '../../static/version-diff.css';

type Seg = { text: string; changed: boolean };
export type Diff = {
  old_label: string; new_label: string; old_title?: string; new_title?: string;
  added: number; deleted: number; identical: boolean; newline_changed: boolean; patch: string;
  hunks: { header: string; split: { kind: string; old_no: number | null; new_no: number | null; old: Seg[]; new: Seg[] }[];
    unified: { kind: string; old_no: number | null; new_no: number | null; text: string; sign: string }[] }[];
};

export function DiffView({ diff }: { diff: Diff }) {
  const [mode, setMode] = useState('split');
  return <section className="version-diff" aria-label={ui.t("文章版本差异")}>
    <div className="diff-toolbar"><strong>{diff.old_label} → {diff.new_label}</strong>
      <span className="diff-added">+{diff.added}{ui.t(" 行")}</span><span className="diff-deleted">−{diff.deleted}{ui.t(" 行")}</span>
      <div role="group" aria-label={ui.t("差异显示方式")}>{['split', 'unified'].map(value => <button type="button" key={value}
        aria-pressed={mode === value} onClick={() => setMode(value)}>{value === 'split' ? ui.t("并排") : ui.t("统一")}</button>)}</div>
    </div>
    {diff.old_title !== diff.new_title && <p>{ui.t("标题：")}{diff.old_title} → {diff.new_title}</p>}
    {diff.identical && <p className="diff-empty">{ui.t("两个版本正文完全相同。")}</p>}
    {diff.newline_changed && <p>{ui.t("文件末尾换行发生变化。")}</p>}
    <div className="diff-scroll" tabIndex={0} aria-label={ui.t("可横向滚动的代码差异")}>
      <table className={`diff-table diff-${mode}`}>{mode === 'split' && <colgroup><col style={{ width: '3.6rem' }} /><col /><col style={{ width: '3.6rem' }} /><col /></colgroup>}<thead><tr>{mode === 'split' ? <><th colSpan={2}>{diff.old_label}</th><th colSpan={2}>{diff.new_label}</th></> : <><th>{ui.t("旧行")}</th><th>{ui.t("新行")}</th><th>{ui.t("变更")}</th><th>{ui.t("正文")}</th></>}</tr></thead>
        <tbody>{diff.hunks.map((hunk, h) => <FragmentRows key={h} hunk={hunk} mode={mode} />)}</tbody></table>
    </div><p className="diff-caption">{ui.t("展示变更附近的上下文；行号对应原文。绿色与 + 表示新增，红色与 − 表示删除。")}</p>
  </section>;
}
function FragmentRows({ hunk, mode }: { hunk: Diff['hunks'][number]; mode: string }) {
  const spans = (items: Seg[]) => items.map((s, i) => <span key={i} className={s.changed ? 'diff-inline' : ''}>{s.text}</span>);
  return <><tr className="diff-hunk"><td colSpan={4}>{hunk.header}</td></tr>
    {mode === 'split' ? hunk.split.map((row, i) => <tr key={i}>
      <td className={`diff-line ${row.kind !== 'equal' && row.old_no !== null ? 'diff-delete' : ''}`}>{row.old_no}</td>
      <td className={row.kind !== 'equal' && row.old_no !== null ? 'diff-delete' : ''}><code><span className="diff-sign">{row.kind !== 'equal' && row.old_no !== null ? '−' : ' '}</span>{spans(row.old)}</code></td>
      <td className={`diff-line ${row.kind !== 'equal' && row.new_no !== null ? 'diff-insert' : ''}`}>{row.new_no}</td>
      <td className={row.kind !== 'equal' && row.new_no !== null ? 'diff-insert' : ''}><code><span className="diff-sign">{row.kind !== 'equal' && row.new_no !== null ? '+' : ' '}</span>{spans(row.new)}</code></td>
    </tr>) : hunk.unified.map((row, i) => <tr key={i} className={`diff-${row.kind}`}><td className="diff-line">{row.old_no}</td><td className="diff-line">{row.new_no}</td><td className="diff-sign">{row.sign}</td><td><code>{row.text}</code></td></tr>)}
  </>;
}

export function HistoryDiff({ csrf, teamId, documentId, revisions }: { csrf: string; teamId: number; documentId: number; revisions: Revision[] }) {
  const [base, setBase] = useState(revisions[1]?.version || revisions[0]?.version || 1);
  const [head, setHead] = useState(0);
  const [result, setResult] = useState<Diff | null>(null), [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    setResult(null); setError('');
    createApi(csrf)<Diff>(`/${teamId}/documents/${documentId}/diff?base=${base}&head=${head}`)
      .then(value => { if (live) setResult(value); }).catch(e => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [csrf, teamId, documentId, base, head]);
  return <section className="version-list"><h4>{ui.t("版本对比")}</h4><div className="diff-toolbar">
    <label>{ui.t("旧版本")}<select value={base} onChange={e => setBase(Number(e.target.value))}>{revisions.map(r => <option key={r.version} value={r.version}>v{r.version} · {r.actor}</option>)}</select></label>
    <label>{ui.t("新版本")}<select value={head} onChange={e => setHead(Number(e.target.value))}><option value={0}>{ui.t("当前正文")}</option>{revisions.map(r => <option key={r.version} value={r.version}>v{r.version} · {r.actor}</option>)}</select></label>
    {result && <button onClick={() => { const url = URL.createObjectURL(new Blob([result.patch], { type: 'text/plain;charset=utf-8' })); const a = document.createElement('a'); a.href = url; a.download = `document-${documentId}.patch`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }}>{ui.t("下载 .patch")}</button>}
  </div>{error ? <p role="alert">{error}</p> : result ? <DiffView diff={result} /> : <p role="status">{ui.t("正在比较版本…")}</p>}
    <details><summary>{ui.t("最近版本与操作者")}</summary>{revisions.map(r => <p key={r.version}>v{r.version} · {r.actor} · {new Date(r.created_at * 1000).toLocaleString(ui.locale)}</p>)}</details>
  </section>;
}
