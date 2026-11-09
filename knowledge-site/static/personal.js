(() => {
  const panel = document.querySelector('[data-personal-reading]');
  if (!panel) return;
  const quote = panel.querySelector('[name=quote]');
  const message = panel.querySelector('[data-annotation-status]');
  const article = document.querySelector('article.article .prose');
  function highlightQuote(item) {
    if (!article || item.stale) return;
    const walker=document.createTreeWalker(article,NodeFilter.SHOW_TEXT,{acceptNode:node=>node.parentElement?.closest('pre,code,mark')?NodeFilter.FILTER_REJECT:NodeFilter.FILTER_ACCEPT});
    const nodes=[];let node,text='';
    while((node=walker.nextNode())){nodes.push({node,from:text.length});text+=node.textContent;}
    const from=text.indexOf(item.quote);
    if(from<0||text.indexOf(item.quote,from+1)>=0)return;
    const to=from+item.quote.length;
    for(const entry of nodes.reverse()) {
      const start=Math.max(0,from-entry.from),end=Math.min(entry.node.textContent.length,to-entry.from);
      if(end<=start)continue;
      const selected=entry.node.splitText(start);selected.splitText(end-start);
      const mark=document.createElement('mark');mark.className='personal-highlight';mark.title=item.comment||window.WikitideI18n.t("个人摘录");selected.before(mark);mark.append(selected);
    }
  }
  const capture = () => {
    const selected = window.getSelection();
    if (!selected?.rangeCount || selected.isCollapsed || !article?.contains(selected.anchorNode) || !article.contains(selected.focusNode)) return;
    const value = selected.toString().trim();
    if (value && value.length <= 2000) { quote.value = value; message.textContent = window.WikitideI18n.t("已选中文字，可以在下方添加个人批注。"); }
  };
  document.addEventListener('selectionchange', capture);
  panel.querySelector('[data-capture-quote]').addEventListener('click', () => { capture(); panel.open = true; quote.focus(); });
  fetch(`/api/reading/${panel.dataset.personalReading}`).then(response => {
    if (!response.ok) throw new Error('load'); return response.json();
  }).then(data => {
    const organize = panel.querySelector('[data-organize]');
    for (const key of ['folder','ordinal']) organize.elements[key].value = data.item[key] || (key === 'ordinal' ? '0' : '');
    for (const key of ['later','completed']) organize.elements[key].checked = !!data.item[key];
    const list = panel.querySelector('[data-annotations]');
    for (const item of data.annotations) {
      highlightQuote(item);
      const section = document.createElement('section'); section.className = 'version-panel';
      const text = document.createElement('blockquote'); text.textContent = item.quote;
      const comment = document.createElement('p'); comment.textContent = item.comment;
      section.append(text, comment);
      if (item.stale) { const stale = document.createElement('small'); stale.textContent = window.WikitideI18n.t("原文已更新，请核对摘录。"); section.append(stale); }
      list.append(section);
    }
  }).catch(() => { message.textContent = window.WikitideI18n.t("个人阅读记录加载失败，可刷新重试。"); });
})();
