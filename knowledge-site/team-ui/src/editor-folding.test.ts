import { afterEach, describe, expect, it, vi } from 'vitest';
import { EditorState } from '@codemirror/state';
import { ensureSyntaxTree, foldedRanges } from '@codemirror/language';
import { editorMarkdown, liveField, rendered, mode, mountEditor } from './editor';
import { collapseHeadingLevel, headingFolding, headingSection, toggleHeading, unfoldAll } from './editor-folding';

const source = '# 目录\n\n## <span style="color:#DC143C;">**问题一**</span>\n\n甲段\n\n### 子问题\n\n乙段\n\n```md\n## 代码里的标题\n```\n\n## 问题二\n\n丙段';
function stateFor(doc = source) {
  const state = EditorState.create({ doc, extensions: [headingFolding(), editorMarkdown(), liveField] });
  ensureSyntaxTree(state, state.doc.length, 1000);
  return state;
}
function ranges(state: EditorState) {
  const values: { from: number; to: number }[] = [];
  foldedRanges(state).between(0, state.doc.length, (from, to) => { values.push({ from, to }); });
  return values;
}

describe('heading section boundaries', () => {
  it('includes lower-level sections, stops before peers, and preserves inline color markup', () => {
    const state = stateFor();
    const h2 = headingSection(state, source.indexOf('## <'))!;
    const h3 = headingSection(state, source.indexOf('###'))!;
    expect(h2.level).toBe(2);
    expect(state.sliceDoc(h2.from, h2.to)).toContain('乙段');
    expect(state.sliceDoc(h2.from, h2.to)).not.toContain('问题二');
    expect(h3.to).toBe(h2.to);
    expect(headingSection(state, source.indexOf('## 代码'))).toBeNull();
    expect(state.doc.toString()).toBe(source);
  });
  it('supports setext headings and ignores quoted/list headings and empty sections', () => {
    const state = stateFor('标题\n===\n\n正文\n\n> ## 引用\n> 内容\n\n- ## 列表\n  内容\n\n## 空标题');
    expect(headingSection(state, 0)?.level).toBe(1);
    expect(headingSection(state, state.doc.toString().indexOf('> ##'))).toBeNull();
    expect(headingSection(state, state.doc.toString().indexOf('- ##'))).toBeNull();
    expect(headingSection(state, state.doc.toString().indexOf('## 空'))).toBeNull();
  });
});

describe('folded live editor integrity', () => {
  afterEach(() => { document.body.innerHTML = ''; vi.unstubAllGlobals(); });
  function mount() {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ blocks: [] }) }));
    document.body.innerHTML = '<form data-editor data-note-id="1"><input name="csrf" value="test"><textarea name="body"></textarea><div data-heading-tools hidden><select data-fold-level><option value="2">H2</option></select><button type="button" data-fold-headings>折叠</button><button type="button" data-unfold-headings>展开</button><span data-fold-status></span></div></form>';
    const form = document.querySelector('form')!;
    form.querySelector('textarea')!.value = source;
    const view = mountEditor(form)!;
    ensureSyntaxTree(view.state, view.state.doc.length, 1000);
    return { form, view };
  }
  it('folds nested content without editing or autosaving and restores preview decorations on unfold', async () => {
    const { form, view } = mount();
    const input = vi.fn(); form.querySelector('textarea')!.addEventListener('input', input);
    const from = source.indexOf('甲段');
    view.dispatch({ effects: rendered.of([{ from, to: from + 2, html: '<p>甲段</p>' }]) });
    expect(view.state.field(liveField).decorations.size).toBe(1);
    const section = headingSection(view.state, source.indexOf('## <'))!;
    toggleHeading(view, section);
    expect(ranges(view.state)).toEqual([{ from: section.from, to: section.to }]);
    expect(view.state.field(liveField).decorations.size).toBe(0);
    view.dispatch({ effects: mode.of(false) });
    expect(ranges(view.state)).toHaveLength(1);
    view.dispatch({ effects: mode.of(true) });
    unfoldAll(view);
    expect(view.state.field(liveField).decorations.size).toBe(1);
    expect(input).not.toHaveBeenCalled();
    expect(form.querySelector('textarea')!.value).toBe(source);
    await Promise.resolve(); await Promise.resolve(); view.destroy();
  });
  it('reveals a folded search/selection target and unfolds before restoring drafts', async () => {
    const { form, view } = mount();
    collapseHeadingLevel(view, 2);
    expect(ranges(view.state)).toHaveLength(2);
    view.dispatch({ selection: { anchor: source.indexOf('乙段') } });
    expect(ranges(view.state)).toHaveLength(1);
    const textarea = form.querySelector('textarea')!;
    textarea.value = '## 恢复\n\n草稿';
    textarea.dispatchEvent(new Event('editor-replace'));
    expect(ranges(view.state)).toHaveLength(0);
    expect(view.state.doc.toString()).toBe(textarea.value);
    await Promise.resolve(); await Promise.resolve(); view.destroy();
  });
  it('switches folding levels, moves hidden caret to the heading, and offers accessible controls', async () => {
    const { form, view } = mount();
    view.dispatch({ selection: { anchor: source.indexOf('乙段') } });
    form.querySelector<HTMLButtonElement>('[data-fold-headings]')!.click();
    expect(ranges(view.state)).toHaveLength(2);
    expect(view.state.selection.main.head).toBe(headingSection(view.state, source.indexOf('## <'))!.from);
    expect(collapseHeadingLevel(view, 3)).toBe(true);
    expect(ranges(view.state)).toHaveLength(1);
    expect(ranges(view.state)[0].from).toBe(headingSection(view.state, source.indexOf('###'))!.from);
    form.querySelector<HTMLButtonElement>('[data-unfold-headings]')!.click();
    expect(ranges(view.state)).toHaveLength(0);
    expect(form.querySelector('[data-fold-status]')!.textContent).toContain('已展开');
    await Promise.resolve(); await Promise.resolve(); view.destroy();
  });
});
