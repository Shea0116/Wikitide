/// Installed only in the desktop WebView. Text stays in the current document;
/// queries are passed as JSON strings and never interpreted as HTML or code.
const articleFindScript = r'''
(() => {
  if (window.wikiTideArticleFind) {
    window.wikiTideArticleFind.clear();
    return;
  }
  let matches = [];
  let current = -1;
  let opened = false;
  const marker = 'data-wikitide-find';
  const root = () => document.querySelector('article.article .prose');
  const status = () => ({available: !!root(), total: matches.length, current: current + 1});

  function clear() {
    opened = false;
    const parents = new Set();
    for (const mark of document.querySelectorAll(`[${marker}]`)) {
      const parent = mark.parentNode;
      parents.add(parent);
      mark.replaceWith(...mark.childNodes);
    }
    for (const parent of parents) parent.normalize();
    matches = [];
    current = -1;
    return status();
  }

  function paint(index, active) {
    for (const mark of matches[index] || []) {
      mark.style.setProperty('background-color', active ? '#d48932' : '#f6df93', 'important');
      mark.style.setProperty('color', '#28251f', 'important');
      mark.style.setProperty('border-radius', '2px', 'important');
      mark.style.setProperty('box-shadow', active ? '0 0 0 2px #a67c32' : 'none', 'important');
    }
  }

  function move(delta) {
    if (!matches.length) return status();
    paint(current, false);
    current = (current + delta + matches.length) % matches.length;
    paint(current, true);
    // A matching paragraph can be inside a collapsed Markdown <details>.
    for (let parent = matches[current][0].parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === 'DETAILS') parent.open = true;
    }
    matches[current][0].scrollIntoView({block: 'center', inline: 'nearest', behavior: 'auto'});
    return status();
  }

  function search(query) {
    clear();
    opened = true;
    const article = root();
    if (!article || !query) return status();
    const pattern = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'giu');
    const runs = [];
    let nodes = [];
    let text = '';
    function flush() {
      if (text) runs.push({text, nodes});
      text = '';
      nodes = [];
    }
    function visit(element) {
      if (element.nodeType === Node.TEXT_NODE) {
        nodes.push({node: element, start: text.length, end: text.length + element.length});
        text += element.data;
        return;
      }
      if (element.nodeType !== Node.ELEMENT_NODE) return;
      if (element.matches('script, style, noscript, textarea, input, select, button, svg, [hidden], [aria-hidden="true"], [contenteditable]:not([contenteditable="false"])')) {
        flush();
        return;
      }
      const style = getComputedStyle(element);
      if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') {
        flush();
        return;
      }
      const boundary = !['inline', 'contents'].includes(style.display) || element.tagName === 'BR';
      if (boundary) flush();
      for (const child of element.childNodes) visit(child);
      if (boundary) flush();
    }
    visit(article);
    flush();
    const operations = [];
    for (const run of runs) {
      pattern.lastIndex = 0;
      let firstNode = 0;
      for (const hit of run.text.matchAll(pattern)) {
        const index = matches.length;
        matches.push([]);
        const end = hit.index + hit[0].length;
        while (firstNode < run.nodes.length && run.nodes[firstNode].end <= hit.index) firstNode++;
        for (let i = firstNode; i < run.nodes.length && run.nodes[i].start < end; i++) {
          const part = run.nodes[i];
          operations.push({node: part.node, start: Math.max(0, hit.index - part.start),
            end: Math.min(part.node.length, end - part.start), index});
        }
      }
    }
    // Work backwards so splitting a text node leaves earlier offsets intact.
    for (const op of operations.reverse()) {
      op.node.splitText(op.end);
      const textNode = op.node.splitText(op.start);
      const mark = document.createElement('span');
      mark.setAttribute(marker, String(op.index));
      textNode.replaceWith(mark);
      mark.append(textNode);
      matches[op.index].unshift(mark);
    }
    for (let i = 0; i < matches.length; i++) paint(i, false);
    return move(1);
  }
  window.wikiTideArticleFind = {search, move, clear};
  window.addEventListener('pagehide', clear);
  document.addEventListener('keydown', event => {
    if (opened && event.key === 'Escape' && !event.isComposing) {
      event.preventDefault();
      window.WikiTideFind?.postMessage('close');
    }
  });
})();
''';
