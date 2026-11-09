import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {afterEach, expect, it, vi} from 'vitest';

const script = readFileSync(resolve(process.cwd(), '../static/knowledge-proposal.js'), 'utf8');
const ok = (data: unknown) => ({ok: true, json: async () => data});
const flush = async () => {for (let i = 0; i < 20; i++) await Promise.resolve();};
const target = {id: 9, title: '合成目标', path: 'Developer/target.md', visibility: 'private', version: 'a'.repeat(64), cited: true};
const preview = {id: 'preview', status: 'review', target, mode: 'append', conflict: false, diff: '-old\n+<script>bad()</script>'};
const field = (name: string) => document.getElementById(`proposal-${name}`) as HTMLInputElement;
function submit(name: string) {field(name).dispatchEvent(new Event('submit', {cancelable: true}));}
function mount(request: ReturnType<typeof vi.fn>) {
  document.body.innerHTML = `<section id="proposal" data-id="draft" data-version="2" data-csrf="csrf">
    <form id="proposal-search"><input id="proposal-query"><button></button></form>
    <form id="proposal-preview-form"><select id="proposal-target"></select><select id="proposal-mode"><option value="append"></option><option value="replace"></option></select><button id="proposal-preview"></button></form>
    <p id="proposal-status"></p><section id="proposal-review" hidden><p id="proposal-target-summary"></p><pre id="proposal-diff"></pre>
    <form id="proposal-apply-form"><input id="proposal-confirm" type="checkbox"><button id="proposal-apply"></button></form></section><a id="proposal-saved" hidden></a></section>`;
  vi.stubGlobal('fetch', request); window.eval(script);
}
afterEach(() => {window.dispatchEvent(new Event('pagehide')); document.body.innerHTML = ''; vi.unstubAllGlobals();});

it('shows a plain-text diff and applies only after an explicit confirmation', async () => {
  const request = vi.fn().mockResolvedValueOnce(ok({targets: [target]})).mockResolvedValueOnce(ok(preview))
    .mockResolvedValueOnce(ok({status: 'applied', note_id: 9}));
  mount(request); await flush(); submit('preview-form'); await flush();
  expect(JSON.parse(request.mock.calls[1][1].body)).toEqual({draft_version: 2, note_id: 9, target_version: target.version, mode: 'append'});
  expect(field('diff').textContent).toContain('<script>');
  expect(field('diff').querySelector('script')).toBeNull();
  expect(field('apply').disabled).toBe(true);
  submit('apply-form'); await flush(); expect(request).toHaveBeenCalledTimes(2);
  field('confirm').checked = true; field('confirm').dispatchEvent(new Event('change'));
  submit('apply-form'); submit('apply-form'); await flush();
  expect(request).toHaveBeenCalledTimes(3);
  expect(request.mock.calls[2][0]).toBe('/api/explore/proposals/preview/apply');
  expect(JSON.parse(request.mock.calls[2][1].body)).toEqual({confirmed: true});
  expect(field('saved').getAttribute('href')).toBe('/n/9');
  expect(field('review').hidden).toBe(true);
});

it('requires a new diff after changing the destination or update mode', async () => {
  const request = vi.fn().mockResolvedValueOnce(ok({targets: [target]})).mockResolvedValueOnce(ok(preview));
  mount(request); await flush(); submit('preview-form'); await flush();
  field('confirm').checked = true;
  field('mode').value = 'replace'; field('mode').dispatchEvent(new Event('change'));
  expect(field('confirm').checked).toBe(false);
  expect(field('review').hidden).toBe(true);
  submit('apply-form'); await flush(); expect(request).toHaveBeenCalledTimes(2);
});

it('preserves a conflicted diff and clears the confirmation after a rejected commit', async () => {
  const request = vi.fn().mockResolvedValueOnce(ok({targets: [target]})).mockResolvedValueOnce(ok(preview))
    .mockResolvedValueOnce({ok: false, json: async () => ({detail: '目标笔记已变化，请刷新目标并重新审阅差异。'})});
  mount(request); await flush(); submit('preview-form'); await flush();
  field('confirm').checked = true; submit('apply-form'); await flush();
  expect(field('diff').textContent).toBe(preview.diff);
  expect(field('confirm').checked).toBe(false);
  expect(field('status').textContent).toContain('重新审阅');
  expect(field('saved').hidden).toBe(true);
});

it('does not enable an already-conflicted preview', async () => {
  const request = vi.fn().mockResolvedValueOnce(ok({targets: [target]})).mockResolvedValueOnce(ok({...preview, conflict: true}));
  mount(request); await flush(); submit('preview-form'); await flush();
  field('confirm').checked = true; submit('apply-form'); await flush();
  expect(field('apply').disabled).toBe(true);
  expect(request).toHaveBeenCalledTimes(2);
});

it('clears old targets and the old diff when searching for another destination', async () => {
  let finish!: (value: unknown) => void;
  const request = vi.fn().mockResolvedValueOnce(ok({targets: [target]})).mockResolvedValueOnce(ok(preview))
    .mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
  mount(request); await flush(); submit('preview-form'); await flush();
  field('query').value = 'another'; submit('search'); await flush();
  expect(field('review').hidden).toBe(true);
  expect((field('target') as unknown as HTMLSelectElement).options.length).toBe(0);
  finish(ok({targets: []})); await flush();
  expect(field('preview').disabled).toBe(true);
  expect(field('status').textContent).toContain('没有匹配');
});
