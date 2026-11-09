import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';

const script = readFileSync(resolve(process.cwd(), '../static/agent-context.js'), 'utf8');
const bridge = readFileSync(resolve(process.cwd(), '../static/native-state.js'), 'utf8');
const field = () => document.getElementById('agent-context-constraints') as HTMLTextAreaElement;
const save = () => document.getElementById('agent-context-save') as HTMLButtonElement;
const status = () => document.getElementById('agent-context-status')!.textContent;
const change = (sessionId: string) => window.dispatchEvent(new CustomEvent('explore-session-change', {detail: {sessionId}}));
const flush = () => new Promise(resolve => setTimeout(resolve, 0));
const data = (version = 1, constraints: string[] = []) => ({version, constraints, summary: null});
const ok = (payload: unknown) => ({ok: true, json: async () => payload});

beforeEach(() => {
  document.body.innerHTML = `<details id="agent-context-panel" data-csrf="fixture-csrf">
    <p id="agent-context-status"></p><textarea id="agent-context-constraints"></textarea>
    <button id="agent-context-save"></button><button id="agent-context-reload"></button>
    <div id="agent-context-summary"></div></details>`;
});
afterEach(() => {
  window.dispatchEvent(new Event('pagehide'));
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
  Reflect.deleteProperty(window, 'wikiTideNative');
});

it('keeps edits on conflict and requires explicit reread before replacing them', async () => {
  const request = vi.fn().mockResolvedValueOnce(ok(data()))
    .mockResolvedValueOnce({ok: false, status: 409, json: async () => ({detail: '其他窗口已经修改'})})
    .mockResolvedValueOnce(ok(data(2, ['服务端新要求'])));
  vi.stubGlobal('fetch', request); window.eval(script);
  expect(save().disabled).toBe(true);
  change('session-a'); await flush();
  field().value = '我的未保存要求'; field().dispatchEvent(new Event('input'));
  save().click(); await flush();
  expect(field().value).toBe('我的未保存要求');
  expect(save().disabled).toBe(true); expect(status()).toContain('当前输入保留');
  expect(request.mock.calls[1][1]).toMatchObject({method: 'PATCH', headers: {'X-CSRF-Token': 'fixture-csrf'},
    body: JSON.stringify({expected_version: 1, constraints: ['我的未保存要求']})});
  document.getElementById('agent-context-reload')!.click(); await flush();
  expect(field().value).toBe('服务端新要求'); expect(save().disabled).toBe(false);
});

it('ignores a stale response after another session opens', async () => {
  let finish: (value: unknown) => void = () => {};
  const stale = new Promise(resolve => {finish = resolve;});
  vi.stubGlobal('fetch', vi.fn().mockReturnValueOnce(stale).mockResolvedValueOnce(ok(data(1, ['B 会话要求']))));
  window.eval(script); change('session-a'); change('session-b'); await flush();
  finish(ok(data(1, ['A 旧要求']))); await flush();
  expect(field().value).toBe('B 会话要求');
});

it('renders summary text safely and preserves unsaved requirements across a reply', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(ok({...data(), summary: {
    goal: {text: '<img src=x onerror=alert(1)>'}, next_actions: [{text: '<script>bad()</script>'}],
  }})));
  window.eval(script); change('session-a'); await flush();
  expect(document.querySelector('img')).toBeNull(); expect(document.querySelector('script')).toBeNull();
  field().value = '保留我的输入'; field().dispatchEvent(new Event('input'));
  change('session-a'); await flush();
  expect(field().value).toBe('保留我的输入');
  const leave = new CustomEvent('explore-before-session-change', {cancelable: true});
  expect(window.dispatchEvent(leave)).toBe(false);
  expect(status()).toContain('尚未保存');
});

it('protects unsaved requirements from a native update restart', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(ok(data())));
  window.eval(bridge); window.eval(script);
  change('session-a'); await flush();
  field().value = '未保存'; field().dispatchEvent(new Event('input'));
  expect(window.wikiTideNative!.state().canRestart).toBe(false);
  document.getElementById('agent-context-reload')!.click(); await flush();
  expect(window.wikiTideNative!.state().canRestart).toBe(true);
});
