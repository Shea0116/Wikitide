import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {afterEach, expect, it, vi} from 'vitest';

const script = readFileSync(resolve(process.cwd(), '../static/knowledge-task.js'), 'utf8');
const ok = (payload: unknown) => ({ok: true, json: async () => payload});
const flush = async () => {for (let index = 0; index < 12; index++) await Promise.resolve();};
function mount(fetcher: ReturnType<typeof vi.fn>) {
  vi.useFakeTimers();
  document.body.innerHTML = `<div id="knowledge-task" data-id="job" data-csrf="csrf"><p id="knowledge-task-status"></p>
    <button id="knowledge-task-refresh"></button><button id="knowledge-task-cancel"></button><button id="knowledge-task-resume"></button>
    <ul id="knowledge-task-steps"></ul><article id="knowledge-task-result"></article><button id="knowledge-task-draft" hidden></button><div id="knowledge-task-links"></div></div>`;
  vi.stubGlobal('fetch', fetcher); window.eval(script);
}
afterEach(() => {window.dispatchEvent(new Event('pagehide')); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); document.body.innerHTML = '';});

it('loads research results and clears them if their sources become unavailable', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({status: 'succeeded', steps: [{step_key: 'knowledge_answer', status: 'succeeded'}], result: {knowledge_id: 'job'}}))
    .mockResolvedValueOnce(ok({html: '<p>cited research</p>'}))
    .mockResolvedValueOnce(ok({status: 'succeeded', steps: [], result: {}, result_available: false}));
  mount(fetcher); await flush();
  expect(document.getElementById('knowledge-task-result')!.textContent).toContain('cited research');
  expect(document.getElementById('knowledge-task-steps')!.textContent).not.toContain('knowledge_answer');
  document.getElementById('knowledge-task-refresh')!.click(); await flush();
  expect(document.getElementById('knowledge-task-result')!.textContent).toBe('');
  expect(document.getElementById('knowledge-task-status')!.textContent).toContain('权限');
});

it('requires confirmation for uncertain retry and keeps mutation errors visible', async () => {
  const pending = {status: 'waiting_user', error_code: 'provider_uncertain', steps: [], result: {}};
  const fetcher = vi.fn().mockResolvedValueOnce(ok(pending))
    .mockResolvedValueOnce({ok: false, json: async () => ({detail: 'retry rejected'})}).mockResolvedValueOnce(ok(pending));
  mount(fetcher); await flush();
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  document.getElementById('knowledge-task-resume')!.click(); await flush();
  expect(fetcher).toHaveBeenCalledTimes(1);
  confirm.mockReturnValue(true);
  document.getElementById('knowledge-task-resume')!.click(); await flush();
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({retry_uncertain: true});
  expect(document.getElementById('knowledge-task-status')!.textContent).toBe('retry rejected');
});

it('ignores a late research response after a refresh has invalidated the result', async () => {
  let finish!: (value: unknown) => void;
  const fetcher = vi.fn().mockResolvedValueOnce(ok({status: 'succeeded', steps: [], result: {knowledge_id: 'job'}}))
    .mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}))
    .mockResolvedValueOnce(ok({status: 'succeeded', steps: [], result: {}, result_available: false}));
  mount(fetcher); await flush();
  document.getElementById('knowledge-task-refresh')!.click(); await flush();
  finish(ok({html: '<p>late private result</p>'})); await flush();
  expect(document.getElementById('knowledge-task-result')!.textContent).toBe('');
  expect(document.getElementById('knowledge-task-status')!.textContent).toContain('权限');
});

it('transfers research only after clicking save and links to the returned draft', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok({status: 'succeeded', steps: [], result: {knowledge_id: 'job'}}))
    .mockResolvedValueOnce(ok({html: '<p>research</p>'})).mockResolvedValueOnce(ok({draft_id: 'draft-a'}));
  mount(fetcher); await flush();
  expect(fetcher).toHaveBeenCalledTimes(2);
  document.getElementById('knowledge-task-draft')!.click(); await flush();
  expect(fetcher.mock.calls[2][0]).toBe('/api/agent/v1/runs/job/draft');
  expect(fetcher.mock.calls[2][1].headers['X-CSRF-Token']).toBe('csrf');
  expect(document.querySelector('#knowledge-task-links a')!.getAttribute('href')).toBe('/explore/write?draft_id=draft-a');
});
