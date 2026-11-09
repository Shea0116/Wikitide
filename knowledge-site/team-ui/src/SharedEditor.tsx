import { ui } from "./i18n";
import { useEffect, useRef } from 'react';
import { mountEditor, type Block } from './editor';
import type { EditorView } from '@codemirror/view';

export function SharedEditor({ value, onChange, disabled, teamId, csrf }: {
  value: string; onChange: (value: string) => void; disabled: boolean; teamId: number; csrf: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const editorView = useRef<EditorView | undefined>(undefined);
  const callback = useRef(onChange); callback.current = onChange;
  useEffect(() => {
    const container = host.current!;
    const textarea = container.querySelector('textarea')!;
    const input = () => callback.current(textarea.value);
    textarea.addEventListener('input', input);
    const view = mountEditor(container, { preview: async (body, signal) => {
      const response = await fetch(`/api/teams/${teamId}/preview`, { method: 'POST', signal,
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify({ body, blocks: true }) });
      if (!response.ok) throw new Error('preview');
      return (await response.json() as { blocks: Block[] }).blocks;
    } });
    editorView.current = view;
    return () => { textarea.removeEventListener('input', input); view?.destroy(); };
  }, [teamId, csrf]);
  useEffect(() => {
    const textarea = host.current!.querySelector('textarea')!;
    if (editorView.current?.state.doc.toString() !== value) { textarea.value = value; textarea.dispatchEvent(new Event('editor-replace')); }
    textarea.readOnly = disabled; textarea.dispatchEvent(new Event('editor-state'));
  }, [value, disabled]);
  return <div ref={host} className="shared-editor" data-style-nonce={document.body.dataset.cmNonce || ''}>
    <input type="hidden" name="csrf" value={csrf} />
    <div className="editor-modes" role="group" aria-label={ui.t("编辑模式")}><button type="button" data-editor-mode="live" aria-pressed="true">{ui.t("实时预览")}</button><button type="button" data-editor-mode="source" aria-pressed="false">{ui.t("Markdown 源码")}</button></div>
    <div className="heading-tools" data-heading-tools><label>{ui.t("标题层级 ")}<select data-fold-level defaultValue="2">{[1,2,3,4,5,6].map(level=><option key={level} value={level}>H{level}</option>)}</select></label><button type="button" data-fold-headings>{ui.t("折叠该级章节")}</button><button type="button" data-unfold-headings>{ui.t("全部展开")}</button><span role="status" data-fold-status /></div>
    <textarea aria-label={ui.t("Markdown 正文")} name="body" defaultValue={value} onChange={event=>callback.current(event.target.value)} />
    <p role="status" data-editor-render-status hidden />
  </div>;
}
