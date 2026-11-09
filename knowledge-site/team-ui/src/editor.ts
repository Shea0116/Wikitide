import { ui } from "./i18n";
import { Compartment, EditorSelection, EditorState, StateEffect, StateField } from "@codemirror/state";
import { Decoration, DecorationSet, EditorView, ViewPlugin, WidgetType, drawSelection, keymap, lineNumbers } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { markdown, markdownKeymap } from "@codemirror/lang-markdown";
import { HighlightStyle, LanguageDescription, bracketMatching, foldedRanges, indentOnInput, syntaxHighlighting, syntaxTree } from "@codemirror/language";
import { languages } from "@codemirror/language-data";
import { searchKeymap, highlightSelectionMatches } from "@codemirror/search";
import { highlightTree, tags } from "@lezer/highlight";
import "./editor.css";
import "./editor-syntax.css";
import { collapseHeadingLevel, headingFolding, unfoldAll } from './editor-folding';

export type Block = { from: number; to: number; html: string };
type LiveState = { blocks: Block[]; live: boolean; focused: boolean; decorations: DecorationSet };
export const rendered = StateEffect.define<Block[]>();
export const mode = StateEffect.define<boolean>();
export const focus = StateEffect.define<boolean>();

class PreviewBlock extends WidgetType {
  constructor(readonly block: Block) { super(); }
  eq(other: PreviewBlock) { return other.block.html === this.block.html && other.block.from === this.block.from; }
  toDOM(view: EditorView) {
    const element = document.createElement("div");
    element.className = "live-block prose";
    // HTML comes only from the authenticated, sanitized Markdown renderer.
    element.innerHTML = this.block.html;
    void highlightPreviewCode(element);
    element.addEventListener("mousedown", event => {
      if (event.button !== 0 || view.state.readOnly) return;
      // Let modified link clicks explicitly open a separate reading tab.
      const anchor = (event.target as HTMLElement).closest("a");
      if (anchor && (event.metaKey || event.ctrlKey)) return;
      event.preventDefault();
      const source = view.state.sliceDoc(this.block.from, this.block.to);
      // Preserve the clicked text location when Markdown markers disappear.
      const caret = document.caretRangeFromPoint?.(event.clientX, event.clientY);
      let position = this.block.from;
      if (caret && element.contains(caret.startContainer) && caret.startContainer.nodeType === Node.TEXT_NODE) {
        const text = caret.startContainer.textContent || "";
        const index = source.indexOf(text);
        if (text && index >= 0) position += index + caret.startOffset;
      }
      view.dispatch({ selection: event.shiftKey
        ? EditorSelection.range(view.state.selection.main.anchor, Math.min(position, this.block.to))
        : EditorSelection.cursor(Math.min(position, this.block.to)) });
      view.focus();
    });
    element.addEventListener("click", event => {
      if (!(event.metaKey || event.ctrlKey)) event.preventDefault();
    });
    element.querySelectorAll("a").forEach(anchor => { anchor.target = "_blank"; });
    return element;
  }
  ignoreEvent() { return true; }
}

function decorations(state: EditorState, value: LiveState): DecorationSet {
  if (!value.live) return Decoration.none;
  const ranges = value.blocks.filter(block => block.to > block.from && block.to <= state.doc.length)
    .filter(block => {
      let hidden = false;
      foldedRanges(state).between(block.from, block.to, (from, to) => {
        if (from < block.to && to > block.from) hidden = true;
      });
      return !hidden;
    })
    .filter(block => !value.focused || state.readOnly || !state.selection.ranges.some(range =>
      range.from <= block.to && range.to >= block.from))
    .map(block => Decoration.replace({ widget: new PreviewBlock(block), block: true }).range(block.from, block.to));
  return Decoration.set(ranges, true);
}

export const liveField = StateField.define<LiveState>({
  create() { return { blocks: [], live: true, focused: false, decorations: Decoration.none }; },
  update(value, transaction) {
    let { blocks, live, focused } = value;
    if (transaction.docChanged) {
      // Keep unaffected blocks in place while a fresh rendering is in flight.
      blocks = blocks.filter(block => {
        let changed = false;
        transaction.changes.iterChangedRanges((from, to) => {
          if (from <= block.to && to >= block.from) changed = true;
        });
        return !changed;
      }).map(block => ({ ...block, from: transaction.changes.mapPos(block.from, 1), to: transaction.changes.mapPos(block.to, -1) }));
    }
    for (const effect of transaction.effects) {
      if (effect.is(rendered)) blocks = effect.value;
      if (effect.is(mode)) live = effect.value;
      if (effect.is(focus)) focused = effect.value;
    }
    const next = { blocks, live, focused, decorations: Decoration.none };
    next.decorations = decorations(transaction.state, next);
    return next;
  },
  provide: field => EditorView.decorations.from(field, value => value.decorations),
});

export const markdownStyle = HighlightStyle.define([
  { tag: tags.heading1, class: "md-heading md-h1" },
  { tag: tags.heading2, class: "md-heading md-h2" },
  { tag: tags.heading3, class: "md-heading md-h3" },
  { tag: [tags.heading4, tags.heading5, tags.heading6], class: "md-heading md-h4" },
  { tag: tags.strong, fontWeight: "700" },
  { tag: tags.emphasis, fontStyle: "italic" },
  { tag: tags.strikethrough, textDecoration: "line-through" },
  { tag: tags.link, class: "md-link" },
  { tag: [tags.url, tags.processingInstruction, tags.meta], class: "md-marker" },
  { tag: [tags.monospace, tags.special(tags.string)], class: "md-code" },
  { tag: tags.comment, class: "syntax-c" },
  { tag: tags.keyword, class: "syntax-k" },
  { tag: [tags.typeName, tags.className, tags.namespace, tags.standard(tags.variableName)], class: "syntax-kt" },
  { tag: [tags.string, tags.regexp], class: "syntax-s" },
  { tag: [tags.number, tags.bool], class: "syntax-m" },
  { tag: [tags.null, tags.atom, tags.escape, tags.annotation], class: "syntax-no" },
  { tag: [tags.function(tags.variableName), tags.function(tags.propertyName)], class: "syntax-nf" },
  { tag: tags.tagName, class: "syntax-nt" },
  { tag: tags.attributeName, class: "syntax-na" },
  { tag: tags.operator, class: "syntax-o" },
  { tag: tags.punctuation, class: "syntax-p" },
]);

export async function highlightPreviewCode(element: HTMLElement) {
  await Promise.all(Array.from(element.querySelectorAll<HTMLElement>('pre code')).map(async code => {
    // Production uses Pygments; keep those tokens and reuse the same palette.
    if (code.querySelector('[class^="syntax-"]')) return;
    const language = Array.from(code.classList).find(name => name.startsWith('language-'))?.slice(9);
    const description = language && LanguageDescription.matchLanguageName(languages, language, true);
    if (!description) return; // Unknown/unlabelled code remains readable plain text.
    try {
      const support = await description.load();
      const source = code.textContent || '';
      const fragment = document.createDocumentFragment();
      let cursor = 0;
      highlightTree(support.language.parser.parse(source), markdownStyle, (from, to, classes) => {
        fragment.append(document.createTextNode(source.slice(cursor, from)));
        const span = document.createElement('span'); span.className = classes;
        span.textContent = source.slice(from, to); fragment.append(span); cursor = to;
      });
      fragment.append(document.createTextNode(source.slice(cursor)));
      code.replaceChildren(fragment);
    } catch { /* A failed language download must not hide or change the code. */ }
  }));
}

export const editorMarkdown = () => markdown({ codeLanguages: languages });

const codeLineStyle = ViewPlugin.fromClass(class {
  decorations: DecorationSet;
  constructor(view: EditorView) { this.decorations = this.build(view); }
  update(update: { view: EditorView; state: EditorState; startState: EditorState; docChanged: boolean; viewportChanged: boolean }) {
    if (update.docChanged || update.viewportChanged || syntaxTree(update.state) !== syntaxTree(update.startState)) this.decorations = this.build(update.view);
  }
  build(view: EditorView) {
    const positions = new Set<number>();
    syntaxTree(view.state).iterate({ enter(node) {
      if (!['FencedCode', 'CodeBlock'].includes(node.name)) return;
      for (const range of view.visibleRanges) {
        if (range.to < node.from || range.from > node.to) continue;
        let line = view.state.doc.lineAt(Math.max(range.from, node.from));
        const end = Math.min(range.to, node.to);
        while (line.from <= end) {
          positions.add(line.from);
          if (line.number === view.state.doc.lines) break;
          line = view.state.doc.line(line.number + 1);
        }
      }
      return false;
    } });
    return Decoration.set(Array.from(positions, from => Decoration.line({ class: 'md-code-line' }).range(from)), true);
  }
}, { decorations: value => value.decorations });

export function mountEditor(form: HTMLElement, options: { preview?: (body: string, signal: AbortSignal) => Promise<Block[]> } = {}) {
  const textarea = form.querySelector<HTMLTextAreaElement>("textarea[name=body]");
  if (!textarea) return;
  const host = document.createElement("div");
  host.className = "live-editor";
  textarea.before(host);
  const readonly = new Compartment();
  const editableState = () => [
    EditorState.readOnly.of(textarea.readOnly),
    EditorView.editable.of(!textarea.readOnly),
    EditorView.contentAttributes.of({ "aria-readonly": String(textarea.readOnly) }),
  ];
  const gutter = new Compartment();
  let timer: ReturnType<typeof setTimeout>;
  let controller: AbortController | undefined;
  let sequence = 0;
  let destroyed = false;
  const renderStatus = form.querySelector<HTMLElement>("[data-editor-render-status]");
  const csrf = form.querySelector<HTMLInputElement>("[name=csrf]")!.value;
  const count = () => {
    const output = form.querySelector("[data-editor-count]");
    if (output) output.textContent = ui.t("{0} 字符", [Array.from(textarea.value.replace(/\s/g, "")).length.toLocaleString()]);
  };
  const view = new EditorView({
    parent: host,
    state: EditorState.create({
      doc: textarea.value,
      extensions: [
        headingFolding(), liveField, editorMarkdown(), codeLineStyle, history(), drawSelection(), bracketMatching(), indentOnInput(),
        EditorView.cspNonce.of(form.dataset.styleNonce || ""),
        ViewPlugin.define(() => ({ destroy() { destroyed = true; clearTimeout(timer); controller?.abort(); } })),
        syntaxHighlighting(markdownStyle), highlightSelectionMatches(), EditorView.lineWrapping,
        readonly.of(editableState()), gutter.of([]),
        EditorView.contentAttributes.of({ "aria-label": ui.t("笔记正文"), "aria-multiline": "true", spellcheck: "false" }),
        keymap.of([
          { key: "Mod-s", run: () => { form.closest('form')?.requestSubmit(); return true; } },
          ...markdownKeymap, ...defaultKeymap, ...historyKeymap, ...searchKeymap,
        ]),
        EditorView.domEventHandlers({
          mousedown: () => {
            if (view.state.readOnly) form.dispatchEvent(new Event("editor-locked"));
          },
          focus: () => { view.dispatch({ effects: focus.of(true) }); },
          blur: () => { view.dispatch({ effects: focus.of(false) }); },
        }),
        EditorView.updateListener.of(update => {
          if (!update.docChanged) return;
          textarea.value = update.state.doc.toString();
          textarea.dispatchEvent(new Event("input", { bubbles: true }));
          count(); scheduleRender();
        }),
      ],
    }),
  });
  const render = async () => {
    const body = view.state.doc.toString();
    const request = ++sequence;
    controller?.abort(); controller = new AbortController();
    try {
      if (options.preview) {
        const blocks = await options.preview(body, controller.signal);
        if (!destroyed && request === sequence && body === view.state.doc.toString()) view.dispatch({ effects: rendered.of(blocks) });
        return;
      }
      const response = await fetch(`/n/${form.dataset.noteId}/preview`, {
        method: "POST", body: new URLSearchParams({ csrf, body, blocks: "1" }), signal: controller.signal,
      });
      if (!response.ok) throw new Error("preview");
      const result = await response.json() as { blocks: Block[] };
      if (destroyed || request !== sequence || body !== view.state.doc.toString()) return;
      view.dispatch({ effects: rendered.of(result.blocks) });
      if (renderStatus) renderStatus.hidden = true;
    } catch (error) {
      if (destroyed || (error as Error).name === "AbortError" || request !== sequence || body !== view.state.doc.toString()) return;
      if (renderStatus) {
        renderStatus.textContent = ui.t("实时排版暂时不可用，仍可编辑和保存 Markdown。下次输入时会重试。");
        renderStatus.hidden = false;
      }
    }
  };
  function scheduleRender() {
    clearTimeout(timer);
    // Immediately invalidate a response for older input, including during IME.
    sequence++; controller?.abort();
    timer = setTimeout(render, 400);
  }
  textarea.hidden = true;
  textarea.required = false; // Validation and focus belong to the visible editor.
  textarea.addEventListener("editor-replace", () => {
    unfoldAll(view);
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: textarea.value } });
  });
  textarea.addEventListener("editor-state", () => {
    view.dispatch({ effects: readonly.reconfigure(editableState()) });
  });
  textarea.addEventListener("editor-focus", () => view.focus());
  form.querySelector('[data-fold-headings]')?.addEventListener('click', () => {
    const level = Number(form.querySelector<HTMLSelectElement>('[data-fold-level]')?.value || 2);
    const status = form.querySelector<HTMLElement>('[data-fold-status]');
    if (status) status.textContent = collapseHeadingLevel(view, level)
      ? ui.t("已折叠 H{0} 章节，正文未改变。", [level])
      : ui.t("没有可折叠的 H{0} 章节，或长文仍在解析，请稍后重试。", [level]);
  });
  form.querySelector('[data-unfold-headings]')?.addEventListener('click', () => {
    unfoldAll(view);
    const status = form.querySelector<HTMLElement>('[data-fold-status]');
    if (status) status.textContent = ui.t("已展开全部章节。");
  });
  form.querySelectorAll<HTMLButtonElement>("[data-editor-mode]").forEach(button => {
    button.addEventListener("click", () => {
      const live = button.dataset.editorMode === "live";
      view.dispatch({ effects: [mode.of(live), gutter.reconfigure(live ? [] : lineNumbers())] });
      host.classList.toggle("source-mode", !live);
      form.querySelectorAll<HTMLButtonElement>("[data-editor-mode]").forEach(item =>
        item.setAttribute("aria-pressed", String(item === button)));
      const hint = form.querySelector<HTMLElement>("[data-live-hint]");
      if (hint) hint.textContent = live ? ui.t("点击正文即可编辑，离开当前段落后自动呈现排版。⌘ / Ctrl + Z 撤销。") : ui.t("直接编辑 Markdown 原文。⌘ / Ctrl + F 查找，⌘ / Ctrl + S 保存。");
    });
  });
  form.querySelectorAll<HTMLElement>(".editor-modes, [data-heading-tools], [data-live-hint], [data-editor-footer]").forEach(item => { item.hidden = false; });
  count(); void render();
  return view;
}

const form = document.querySelector<HTMLFormElement>("form[data-editor]");
if (form) {
  try { mountEditor(form); }
  catch (error) {
    // A loading failure must leave the original form usable.
    form.querySelector(".live-editor")?.remove();
    const textarea = form.querySelector<HTMLTextAreaElement>("textarea[name=body]");
    if (textarea) { textarea.hidden = false; textarea.required = true; }
    console.error("Unable to initialize live editor", error);
  }
}
