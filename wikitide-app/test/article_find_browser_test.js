(() => {
  let checks = 0;
  function assert(value, message) {
    if (!value) throw new Error(message);
    checks++;
  }
  document.body.innerHTML = `
    <nav>导航专用关键词</nav>
    <article class="article">
      <h1>标题专用关键词</h1>
      <div class="article-actions">操作专用关键词</div>
      <div class="prose">
        <p>中文<strong>关键词</strong>和中文关键词</p>
        <p>Flutter flutter FLUTTER</p>
        <p>跨<strong>格式</strong>查找</p>
        <pre><code>const a = "a+b [x]"; Flutter</code></pre>
        <p>前段</p><p>后段</p>
        <p hidden>隐藏专用关键词</p>
        <p style="display:none">不可见专用关键词</p>
        <button>按钮专用关键词</button>
        <details><summary>折叠内容</summary><p>折叠目标</p></details>
        <p>İ <em>Unicode</em> 🙂</p>
        <p>&lt;img src=x onerror=alert(1)&gt;</p>
      </div>
    </article>
    <aside>目录专用关键词</aside>`;
  const api = window.wikiTideArticleFind;
  const prose = document.querySelector('.prose');
  const original = prose.innerHTML;
  const marks = () => document.querySelectorAll('[data-wikitide-find]');

  for (const word of ['导航专用', '标题专用', '操作专用', '目录专用', '隐藏专用', '不可见专用', '按钮专用']) {
    assert(api.search(word).total === 0, `scope/visibility: ${word}`);
  }
  let result = api.search('中文关键词');
  assert(result.total === 2 && result.current === 1, 'Chinese matches across inline tags');
  assert(marks().length === 3, 'one logical match may have multiple highlight fragments');
  assert(document.querySelector('strong').textContent === '关键词', 'formatting preserved');
  assert(api.move(-1).current === 2, 'previous wraps to last');
  assert(api.move(1).current === 1, 'next wraps to first');
  api.clear();
  assert(prose.innerHTML === original, 'closing restores the original markup');

  result = api.search('flutter');
  assert(result.total === 4, 'case insensitive search includes code blocks');
  assert(api.search('跨格式查找').total === 1, 'inline formatting does not break a phrase');
  assert(api.search('前段后段').total === 0, 'separate paragraphs do not join into a match');
  assert(api.search('a+b [x]').total === 1, 'regex syntax is treated literally');
  assert(api.search('<img src=x onerror=alert(1)>').total === 1, 'HTML is searched as text');
  assert(!document.querySelector('img'), 'query cannot inject HTML');
  assert(api.search('Unicode').total === 1, 'Unicode case mapping preserves text offsets');
  assert(marks()[0].textContent === 'Unicode', 'highlight has the correct offsets');
  assert(api.search('🙂').total === 1, 'emoji is found without breaking surrogate pairs');
  assert(api.search('折叠目标').total === 1, 'collapsed text can be found');
  assert(document.querySelector('details').open, 'navigation reveals collapsed text');
  assert(marks()[0].getBoundingClientRect().height > 0, 'active match is visible');
  assert(marks()[0].style.backgroundColor === 'rgb(212, 137, 50)', 'active match is distinct');

  let closed = false;
  window.WikiTideFind = {postMessage: message => { closed = message === 'close'; }};
  document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  assert(closed, 'Escape from the WebView requests closing the native bar');
  closed = false;
  api.clear();
  document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  assert(!closed, 'closed find does not intercept Escape');
  result = api.search('');
  assert(result.total === 0 && result.current === 0 && marks().length === 0, 'empty search clears highlights');
  assert(api.search('missing').total === 0, 'no matches is a valid state');
  api.search('Flutter');
  window.dispatchEvent(new PageTransitionEvent('pagehide', {persisted: true}));
  assert(marks().length === 0, 'back-forward cache does not retain highlights after leaving');

  document.body.innerHTML = '<article class="article"><div class="prose">另一篇文章</div></article>';
  assert(api.search('另一篇').total === 1, 'new article searches only its own body');
  api.clear();
  document.body.innerHTML = '<main>不是文章</main>';
  assert(api.search('文章').available === false, 'non-article pages are unavailable');
  assert(marks().length === 0, 'no highlights remain after leaving an article');
  return `${checks} article find browser checks passed`;
})();
