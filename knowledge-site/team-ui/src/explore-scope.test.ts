import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {afterEach, expect, it, vi} from 'vitest';

const script = readFileSync(resolve(process.cwd(), '../static/explore.js'), 'utf8');
const ok = (payload: unknown) => ({ok: true, json: async () => payload});

afterEach(() => {
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
  Reflect.deleteProperty(window, 'exploreTrial');
});

it('sends the selected scope and clears private content when returning to public search', async () => {
  window.history.replaceState(null, '', '/explore?scope=selected&note_id=2');
  document.body.innerHTML = `<div id="explore" data-csrf="csrf" data-authenticated="true" data-artifacts-enabled="true"
      data-ai-ready="true" data-web-ready="true" data-scope="selected" data-note-id="2" data-note-title="私密学习笔记">
    <div id="explore-notice"></div><span id="explore-scope-badge"></span><span id="explore-scope-label"></span>
    <button id="explore-all"></button><button id="explore-new"></button>
    <details id="explore-selection"><form id="explore-note-search"><input id="explore-note-query"></form>
      <div id="explore-note-options"></div><div id="explore-picked-notes"></div><p id="explore-selection-status"></p>
      <button id="explore-apply-selection"></button></details>
    <form id="explore-search"><input id="explore-query"></form><div class="explore-suggestions"></div>
    <div id="explore-results"></div><span id="explore-result-count"></span><p id="explore-search-mode"></p>
    <div id="explore-messages"><p>欢迎</p></div><h2 id="explore-conversation-title"></h2>
    <form id="explore-ask"><textarea id="explore-question"></textarea><input id="explore-fast" type="radio">
      <input id="explore-thinking" type="radio"><input id="explore-research" type="checkbox"><input id="explore-web" type="checkbox"><button type="submit">发送</button><button id="explore-background" type="button">后台研究</button></form>
    <p id="explore-mode-help"></p><p id="explore-selected-help"></p><p id="explore-public-help"></p>
    <section id="explore-reader" hidden><h2 id="explore-reader-title"></h2><a id="explore-open-original"></a>
      <button id="explore-close-reader"></button><article id="explore-article"></article></section>
    <div id="explore-history-list"></div>
  </div>`;
  Element.prototype.scrollIntoView = vi.fn();
  Reflect.set(window, 'exploreTrial', {available: true, refresh: async () => {}});
  const source = {kind: 'account_note', number: 1, note_id: 2, title: '私密学习笔记', section: '闭包',
    path: 'Developer/example.md', url: '/n/2', start_line: 1, end_line: 5};
  const result = {session_id: 'selected-session', answer: '私密回答 [1]', html: '<p>私密回答</p>',
    sources: [source], message_id: 2, user_message_id: 1, context: {}};
  const hit = {...source, category: '私密', reason: '关键词匹配', excerpt: '所选私密摘录'};
  let lateSearch: ((value: unknown) => void) | undefined;
  let delaySearch = false;
  let lateDraft: ((value: unknown) => void) | undefined;
  const request = vi.fn().mockImplementation((url: string) => {
    if (url.startsWith('/api/explore/search')) {
      if (delaySearch) return new Promise(resolve => {lateSearch = resolve;});
      return Promise.resolve(ok({results: [hit], mode: 'keyword'}));
    }
    if (url.startsWith('/api/explore/articles/')) return Promise.resolve(ok({title: '私密学习笔记', html: '<p>私密原文</p>', url: '/n/2'}));
    if (url.startsWith('/api/explore/notes?')) return Promise.resolve(ok({notes: [
      {id: 2, title: '私密学习笔记', path: 'Developer/example.md'}, {id: 7, title: '公开资料', path: 'Frontend/public.md'}]}));
    if (url === '/api/explore/ask/stream') return Promise.resolve({ok: true, body: null,
      text: async () => 'event: done\ndata: ' + JSON.stringify(result) + '\n\n'});
    if (url === '/api/agent/v1/runs') return Promise.resolve(ok({id: 'research-job'}));
    if (url === '/api/explore/messages/2/draft') return new Promise(resolve => {lateDraft = resolve;});
    return Promise.resolve(ok({sessions: []}));
  });
  vi.stubGlobal('fetch', request);
  window.eval(script);
  await vi.waitFor(() => expect(document.getElementById('explore-article')!.textContent).toContain('私密原文'));
  expect((document.getElementById('explore-web') as HTMLInputElement).disabled).toBe(true);
  expect(request.mock.calls.some(([url]) => url.includes('scope=selected&note_ids=2'))).toBe(true);
  (document.getElementById('explore-question') as HTMLTextAreaElement).value = '解释闭包';
  document.getElementById('explore-ask')!.dispatchEvent(new Event('submit', {cancelable: true}));
  await vi.waitFor(() => expect(document.getElementById('explore-messages')!.textContent).toContain('私密回答'));
  const ask = request.mock.calls.find(([url]) => url === '/api/explore/ask/stream');
  expect(JSON.parse(ask![1].body)).toMatchObject({scope: 'selected', note_id: 2, web_search: false});
  await vi.waitFor(() => expect((document.getElementById('explore-question') as HTMLTextAreaElement).disabled).toBe(false));
  const save = [...document.querySelectorAll<HTMLButtonElement>('#explore-messages button')].find(button => button.textContent === '保存这条回答为草稿')!;
  save.click(); save.click();
  expect(request.mock.calls.filter(([url]) => url === '/api/explore/messages/2/draft')).toHaveLength(1);
  delaySearch = true;
  document.getElementById('explore-search')!.dispatchEvent(new Event('submit', {cancelable: true}));
  document.getElementById('explore-all')!.click();
  expect(window.location.search).toBe('');
  expect(document.getElementById('explore-results')!.textContent).toBe('');
  expect(document.getElementById('explore-article')!.textContent).toBe('');
  expect(document.getElementById('explore-messages')!.textContent).not.toContain('私密回答');
  expect(document.getElementById('explore-reader')!.hidden).toBe(true);
  lateDraft!(ok({draft_id: 'old-private-draft'}));
  await new Promise(resolve => setTimeout(resolve, 0));
  expect(document.querySelector('#explore-messages a[href*="old-private-draft"]')).toBeNull();
  expect((document.getElementById('explore-web') as HTMLInputElement).disabled).toBe(false);
  lateSearch!(ok({results: [hit], mode: 'keyword'}));
  await new Promise(resolve => setTimeout(resolve, 0));
  expect(document.getElementById('explore-results')!.textContent).toBe('');
  document.getElementById('explore-note-search')!.dispatchEvent(new Event('submit', {cancelable: true}));
  await vi.waitFor(() => expect(document.querySelector('#explore-note-options input[value="7"]')).not.toBeNull());
  (document.querySelector('#explore-note-options input[value="7"]') as HTMLInputElement).click();
  document.getElementById('explore-apply-selection')!.click();
  expect(new URLSearchParams(window.location.search).getAll('note_ids')).toEqual(['2', '7']);
  expect(document.getElementById('explore-scope-label')!.textContent).toContain('2');
  (document.getElementById('explore-research') as HTMLInputElement).checked = true;
  (document.getElementById('explore-question') as HTMLTextAreaElement).value = '比较两篇资料';
  document.getElementById('explore-ask')!.dispatchEvent(new Event('submit', {cancelable: true}));
  await vi.waitFor(() => expect(request.mock.calls.filter(([url]) => url === '/api/explore/ask/stream')).toHaveLength(2));
  const second = request.mock.calls.filter(([url]) => url === '/api/explore/ask/stream')[1];
  expect(JSON.parse(second[1].body)).toMatchObject({scope: 'selected', note_id: null, note_ids: [2, 7], research: true});
  await vi.waitFor(() => expect((document.getElementById('explore-question') as HTMLTextAreaElement).disabled).toBe(false));
  (document.getElementById('explore-question') as HTMLTextAreaElement).value = '后台比较两篇资料';
  document.getElementById('explore-background')!.click();
  await vi.waitFor(() => expect(document.querySelector('#explore-notice a')?.getAttribute('href')).toBe('/agent/tasks/research-job'));
  document.getElementById('explore-background')!.click();
  await vi.waitFor(() => expect(request.mock.calls.filter(([url]) => url === '/api/agent/v1/runs')).toHaveLength(2));
  const jobs = request.mock.calls.filter(([url]) => url === '/api/agent/v1/runs').map(call => JSON.parse(call[1].body));
  expect(jobs[0]).toMatchObject({kind: 'knowledge', scope: 'selected', note_id: null, note_ids: [2, 7], memory_mode: 'off'});
  expect(jobs[0].idempotency_key).toBe(jobs[1].idempotency_key);
  const saveSecond = [...document.querySelectorAll<HTMLButtonElement>('#explore-messages button')].find(button => button.textContent === '保存这条回答为草稿')!;
  saveSecond.click(); lateDraft!(ok({draft_id: 'discussion-copy'}));
  await vi.waitFor(() => expect(document.querySelector('#explore-messages a[href*="discussion-copy"]')?.getAttribute('href')).toBe('/explore/write?draft_id=discussion-copy'));
});
