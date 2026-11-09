import { afterEach, describe, expect, it, vi } from "vitest";
import { EditorState } from "@codemirror/state";
import { LanguageDescription, ensureSyntaxTree } from "@codemirror/language";
import { languages } from "@codemirror/language-data";
import { highlightTree } from "@lezer/highlight";
import { liveField, rendered, mode, focus, mountEditor, editorMarkdown, markdownStyle, highlightPreviewCode } from "./editor";

describe('fenced code highlighting', () => {
  it.each([
    ['python', 'import os\n# 中文注释\ndef task(name):\n    return "你好🌊"'],
    ['javascript', 'function task(name) { return "hello"; }'],
    ['go', 'package main\nfunc task() string { return "hello" }'],
    ['java', 'class Task { String run() { return "hello"; } }'],
  ])('keeps %s source intact while coloring keywords and strings', async (name, code) => {
    await LanguageDescription.matchLanguageName(languages, name, true)!.load();
    const source = '```' + name + '\n' + code + '\n```';
    const state = EditorState.create({ doc: source, extensions: [editorMarkdown()] });
    const tree = ensureSyntaxTree(state, state.doc.length, 1000)!;
    const tokens: { text: string; classes: string }[] = [];
    highlightTree(tree, markdownStyle, (from, to, classes) => tokens.push({ text: state.sliceDoc(from, to), classes }));
    expect(tokens.some(token => token.classes.includes('syntax-k'))).toBe(true);
    expect(tokens.some(token => token.classes.includes('syntax-s') && token.text.includes('hello') || token.classes.includes('syntax-s') && token.text.includes('你好'))).toBe(true);
    expect(state.doc.toString()).toBe(source);
  });
  it('colors preview fallbacks without changing code text and preserves server tokens', async () => {
    const element = document.createElement('div');
    element.innerHTML = '<pre><code class="language-python">import os\nprint("中文🌊")\n</code></pre><pre><code class="language-python"><span class="syntax-k">import</span> os</code></pre><pre><code class="language-unknown-xyz">&lt;tag&gt; unchanged</code></pre>';
    const before = Array.from(element.querySelectorAll('code'), node => node.textContent);
    const server = element.querySelectorAll('code')[1].innerHTML;
    await highlightPreviewCode(element);
    expect(element.querySelector('code .syntax-k')?.textContent).toBe('import');
    expect(Array.from(element.querySelectorAll('code'), node => node.textContent)).toEqual(before);
    expect(element.querySelectorAll('code')[1].innerHTML).toBe(server);
    expect(element.querySelectorAll('code')[2].querySelector('span')).toBeNull();
  });
});

describe("Markdown live preview source integrity", () => {
  const source = '# 标题 🌊\n\n<span style="color:#DC143C;">**结论**</span>\n\n最后一段';
  const blocks = [
    { from: 0, to: source.indexOf('\n'), html: '<h1>标题 🌊</h1>' },
    { from: source.indexOf('<span'), to: source.indexOf('\n\n最后'), html: '<p><span style="color:#DC143C;"><strong>结论</strong></span></p>' },
    { from: source.indexOf('最后'), to: source.length, html: '<p>最后一段</p>' },
  ];
  it("renders idle blocks, exposes the selection and switches modes without rewriting source", () => {
    let state = EditorState.create({ doc: source, extensions: [liveField] });
    state = state.update({ effects: rendered.of(blocks) }).state;
    expect(state.field(liveField).decorations.size).toBe(3);
    state = state.update({ effects: focus.of(true), selection: { anchor: blocks[1].from + 5 } }).state;
    expect(state.field(liveField).decorations.size).toBe(2);
    state = state.update({ effects: mode.of(false) }).state;
    expect(state.field(liveField).decorations.size).toBe(0);
    state = state.update({ effects: [mode.of(true), focus.of(false)] }).state;
    expect(state.field(liveField).decorations.size).toBe(3);
    expect(state.doc.toString()).toBe(source);
  });
  it("invalidates modified blocks and maps unaffected offsets while rendering is pending", () => {
    let state = EditorState.create({ doc: source, extensions: [liveField] });
    state = state.update({ effects: rendered.of(blocks) }).state;
    state = state.update({ changes: { from: 2, insert: '新增' } }).state;
    expect(state.field(liveField).blocks).toHaveLength(2);
    expect(state.field(liveField).blocks[0].from).toBe(blocks[1].from + 2);
    expect(state.doc.toString()).toBe(source.slice(0, 2) + '新增' + source.slice(2));
  });
  it("shows every block when drafts lock the editor", () => {
    let state = EditorState.create({ doc: source, extensions: [liveField, EditorState.readOnly.of(true)] });
    state = state.update({ effects: [rendered.of(blocks), focus.of(true)] }).state;
    expect(state.field(liveField).decorations.size).toBe(3);
  });
});

describe("editor form bridge", () => {
  afterEach(() => { document.body.innerHTML = ''; vi.unstubAllGlobals(); });
  it("mirrors input and restored drafts, retains Markdown and honors save locks", async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ blocks: [] }) }));
    document.body.innerHTML = '<form data-editor data-note-id="1"><input name="csrf" value="test"><textarea name="body" required># 原文</textarea><div class="editor-modes" hidden><button type="button" data-editor-mode="source"></button><button type="button" data-editor-mode="live"></button></div></form>';
    const form = document.querySelector('form')!;
    const textarea = form.querySelector('textarea')!;
    const input = vi.fn(); textarea.addEventListener('input', input);
    const view = mountEditor(form)!;
    expect(textarea.hidden).toBe(true);
    view.dispatch({ changes: { from: view.state.doc.length, insert: '\n\n中文输入 🌊' } });
    expect(textarea.value).toBe('# 原文\n\n中文输入 🌊');
    expect(input).toHaveBeenCalledOnce();
    form.querySelector<HTMLButtonElement>('[data-editor-mode=source]')!.click();
    expect(view.state.doc.toString()).toBe(textarea.value);
    textarea.value = '<span style="color:red;">恢复的草稿</span>';
    textarea.dispatchEvent(new Event('editor-replace'));
    expect(view.state.doc.toString()).toBe(textarea.value);
    textarea.readOnly = true; textarea.dispatchEvent(new Event('editor-state'));
    expect(view.state.readOnly).toBe(true);
    textarea.readOnly = false; textarea.dispatchEvent(new Event('editor-state'));
    expect(view.state.readOnly).toBe(false);
    // Allow the initial asynchronous render to settle before destroying the view.
    await Promise.resolve(); await Promise.resolve();
    view.destroy();
  });
});
