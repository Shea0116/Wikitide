import { ui } from "./i18n";
import { EditorState } from '@codemirror/state';
import { EditorView, GutterMarker, ViewPlugin, gutter } from '@codemirror/view';
import { codeFolding, foldEffect, foldedRanges, forceParsing, syntaxTree, unfoldAll, unfoldEffect } from '@codemirror/language';

type Section = { from: number; to: number; level: number };

// Use the Markdown syntax tree: hashes in fenced code, HTML, lists and quotes
// must not accidentally become document sections.
export function headingSection(state: EditorState, position: number): Section | null {
  const line = state.doc.lineAt(position);
  for (let node = syntaxTree(state).resolveInner(line.to, -1); node; node = node.parent!) {
    const match = /^(?:ATX|Setext)Heading([1-6])$/.exec(node.name);
    if (!match || node.parent?.name !== 'Document') continue;
    if (state.doc.lineAt(node.from).from !== line.from) return null;
    const level = Number(match[1]);
    let last = node;
    for (let next = node.nextSibling; next; next = next.nextSibling) {
      const heading = /^(?:ATX|Setext)Heading([1-6])$/.exec(next.name);
      if (heading && Number(heading[1]) <= level) break;
      last = next;
    }
    return last.to > node.to ? { from: node.to, to: last.to, level } : null;
  }
  return null;
}

function isFolded(state: EditorState, section: Section) {
  let folded = false;
  foldedRanges(state).between(section.from, section.from, (from, to) => {
    if (from === section.from && to === section.to) folded = true;
  });
  return folded;
}

export function toggleHeading(view: EditorView, section: Section) {
  const open = isFolded(view.state, section);
  const inside = view.state.selection.ranges.some(range => range.to > section.from && range.from < section.to);
  view.dispatch({
    effects: (open ? unfoldEffect : foldEffect).of(section),
    ...(!open && inside ? { selection: { anchor: section.from } } : {}),
  });
}

class HeadingMarker extends GutterMarker {
  constructor(readonly section: Section, readonly folded: boolean) { super(); }
  eq(other: HeadingMarker) {
    return this.section.from === other.section.from && this.section.to === other.section.to &&
      this.section.level === other.section.level && this.folded === other.folded;
  }
  toDOM(view: EditorView) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'heading-fold-toggle';
    button.textContent = this.folded ? '▸' : '▾';
    button.title = ui.t("{0} H{1} 章节", [this.folded ? ui.t("展开") : ui.t("折叠"), this.section.level]);
    button.setAttribute('aria-label', button.title);
    button.setAttribute('aria-expanded', String(!this.folded));
    button.onclick = event => { event.preventDefault(); toggleHeading(view, this.section); };
    return button;
  }
}

export function collapseHeadingLevel(view: EditorView, level: number) {
  if (!forceParsing(view, view.state.doc.length, 250)) return false;
  const sections: Section[] = [];
  const state = view.state;
  for (let node = syntaxTree(state).topNode.firstChild; node; node = node.nextSibling) {
    const section = headingSection(state, node.from);
    if (section?.level === level) sections.push(section);
  }
  if (!sections.length) return false;
  const effects = [];
  // A level action resets existing folds, so folding H3 always leaves H2 visible.
  foldedRanges(state).between(0, state.doc.length, (from, to) => { effects.push(unfoldEffect.of({ from, to })); });
  effects.push(...sections.map(section => foldEffect.of(section)));
  const containing = sections.find(section => state.selection.main.head > section.from && state.selection.main.head < section.to);
  view.dispatch({ effects, ...(containing ? { selection: { anchor: containing.from } } : {}) });
  return true;
}

export { unfoldAll };

export function headingFolding() {
  const marker = (view: EditorView, position: number) => {
    if (view.state.doc.lineAt(position).from !== position) return null;
    const section = headingSection(view.state, position);
    return section ? new HeadingMarker(section, isFolded(view.state, section)) : null;
  };
  return [
    codeFolding({
      placeholderDOM(_view, onclick) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'cm-foldPlaceholder';
        button.textContent = ui.t("… 展开章节");
        button.setAttribute('aria-label', ui.t("展开已折叠的章节"));
        button.onclick = onclick;
        return button;
      },
    }),
    gutter({
      class: 'cm-heading-folds',
      lineMarker: (view, line) => marker(view, line.from),
      // Live preview replaces headings with block widgets. Native foldGutter
      // only draws text-line markers, so explicitly support these widgets too.
      widgetMarker: (view, _widget, block) => marker(view, block.from),
      lineMarkerChange: update => update.docChanged ||
        foldedRanges(update.startState) !== foldedRanges(update.state) ||
        syntaxTree(update.startState) !== syntaxTree(update.state),
    }),
    ViewPlugin.define(view => {
      const exposeControls = () => view.dom.querySelectorAll('.cm-gutters').forEach(element => element.removeAttribute('aria-hidden'));
      queueMicrotask(exposeControls);
      return { update: exposeControls };
    }),
  ];
}
