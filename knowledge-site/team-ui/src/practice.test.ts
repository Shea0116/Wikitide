import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {afterEach, expect, it, vi} from 'vitest';

const script = readFileSync(resolve(process.cwd(), '../static/practice.js'), 'utf8');
const ok = (payload: unknown) => ({ok: true, json: async () => payload});
const checkpoint = (changes = {}) => ({id: 'session', card_id: 1, question: '闭包', version: 1,
  status: 'answering', answer_draft: '', hint_used: false, attempt: null, ...changes});
const input = () => document.getElementById('practice-answer') as HTMLTextAreaElement;
const flush = async () => {for (let index = 0; index < 12; index++) await Promise.resolve();};
function mount(fetcher: ReturnType<typeof vi.fn>) {
  vi.useFakeTimers();
  document.body.innerHTML = `<div id="practice" data-id="session" data-csrf="csrf">
    <form id="practice-answer-form"><textarea id="practice-answer"></textarea><input id="practice-hint" type="checkbox">
      <button id="practice-save" type="button"></button><button id="practice-submit"></button></form>
    <button id="practice-reload"></button><p id="practice-status"></p><a id="practice-job"></a>
    <section id="practice-review"><p id="practice-reference"></p><p id="practice-verdict"></p><p id="practice-feedback"></p><blockquote id="practice-quote"></blockquote><ul id="practice-points"></ul></section>
    <form id="practice-correction"><select id="practice-correction-verdict"><option value="correct"></option></select>
      <textarea id="practice-correction-comment"></textarea><button></button><p id="practice-correction-saved"></p></form><form id="practice-again"></form></div>`;
  vi.stubGlobal('fetch', fetcher);
  window.eval(script);
}
function edit(value: string) {input().value = value; input().dispatchEvent(new Event('input'));}
afterEach(() => {
  window.dispatchEvent(new Event('pagehide'));
  vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); document.body.innerHTML = '';
});

it('keeps new input during an in-flight save and saves it at the returned version', async () => {
  let finish!: (value: unknown) => void;
  const fetcher = vi.fn().mockResolvedValueOnce(ok(checkpoint()))
    .mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}))
    .mockResolvedValueOnce(ok(checkpoint({version: 3, answer_draft: 'newer'})));
  mount(fetcher); await flush();
  edit('first'); await vi.advanceTimersByTimeAsync(900);
  edit('newer'); finish(ok(checkpoint({version: 2, answer_draft: 'first'}))); await flush();
  expect(input().value).toBe('newer');
  await vi.advanceTimersByTimeAsync(900);
  expect(JSON.parse(fetcher.mock.calls[2][1].body)).toEqual({expected_version: 2, answer: 'newer', hint_used: false});
});

it('preserves local input and stops autosaving after a version conflict', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok(checkpoint()))
    .mockResolvedValueOnce({ok: false, json: async () => ({detail: '学习记录已变化，当前输入请先保留并重新读取。'})});
  mount(fetcher); await flush();
  edit('local work'); await vi.advanceTimersByTimeAsync(900);
  expect(input().value).toBe('local work');
  expect((document.getElementById('practice-submit') as HTMLButtonElement).disabled).toBe(true);
  edit('keep writing'); await vi.advanceTimersByTimeAsync(3000);
  expect(fetcher).toHaveBeenCalledTimes(2);
  vi.spyOn(window, 'confirm').mockReturnValue(false);
  document.getElementById('practice-reload')!.click(); await flush();
  expect(input().value).toBe('keep writing');
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it('waits for draft persistence before submitting exactly the latest answer', async () => {
  let finish!: (value: unknown) => void;
  const fetcher = vi.fn().mockResolvedValueOnce(ok(checkpoint()))
    .mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}))
    .mockResolvedValueOnce(ok(checkpoint({version: 3, answer_draft: 'latest'})))
    .mockResolvedValueOnce(ok(checkpoint({version: 4, status: 'submitted', answer_draft: 'latest',
      reference_answer: 'reference', attempt: {answer: 'latest', feedback: null}, job: {id: 'job', status: 'queued'}})));
  mount(fetcher); await flush();
  edit('first'); await vi.advanceTimersByTimeAsync(900); edit('latest');
  document.getElementById('practice-answer-form')!.dispatchEvent(new Event('submit', {cancelable: true}));
  expect(fetcher).toHaveBeenCalledTimes(2);
  finish(ok(checkpoint({version: 2, answer_draft: 'first'}))); await vi.advanceTimersByTimeAsync(0);
  expect(fetcher).toHaveBeenCalledTimes(4);
  expect(fetcher.mock.calls[3][0]).toBe('/api/learn/sessions/session/attempts');
  expect(JSON.parse(fetcher.mock.calls[3][1].body)).toEqual({expected_version: 3, answer: 'latest', hint_used: false});
  expect(input().disabled).toBe(true);
  expect(document.getElementById('practice-reference')!.textContent).toBe('reference');
  expect(document.getElementById('practice-job')!.getAttribute('href')).toBe('/agent/tasks/job');
});

it('renders feedback as text and records corrections separately', async () => {
  const data = checkpoint({version: 3, status: 'reviewed', answer_draft: 'original answer', reference_answer: 'reference',
    attempt: {answer: 'original answer', feedback: {verdict: 'partial', feedback: '<img src=x onerror=alert(1)>', matched_points: ['scope'], missing_points: ['return']}},
    job: {id: 'job', status: 'succeeded'}});
  const fetcher = vi.fn().mockResolvedValueOnce(ok(data)).mockResolvedValueOnce(ok({...data, version: 4, correction: {verdict: 'correct', comment: 'checked'}}));
  mount(fetcher); await flush();
  expect(document.getElementById('practice-feedback')!.querySelector('img')).toBeNull();
  expect(document.getElementById('practice-feedback')!.textContent).toContain('<img');
  (document.getElementById('practice-correction-comment') as HTMLTextAreaElement).value = 'checked';
  document.getElementById('practice-correction')!.dispatchEvent(new Event('submit', {cancelable: true})); await flush();
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({expected_version: 3, verdict: 'correct', comment: 'checked'});
  expect(input().value).toBe('original answer');
  expect(document.getElementById('practice-correction-saved')!.textContent).toContain('checked');
});

it('preserves typing that starts while an explicit reload is in flight', async () => {
  let finish!: (value: unknown) => void;
  const fetcher = vi.fn().mockResolvedValueOnce(ok(checkpoint()))
    .mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}))
    .mockResolvedValueOnce(ok(checkpoint({version: 3, answer_draft: 'new local input'})));
  mount(fetcher); await flush();
  document.getElementById('practice-reload')!.click(); edit('new local input');
  finish(ok(checkpoint({version: 2, answer_draft: 'remote old draft'}))); await flush();
  expect(input().value).toBe('new local input');
  await vi.advanceTimersByTimeAsync(900);
  expect(JSON.parse(fetcher.mock.calls[2][1].body)).toMatchObject({expected_version: 2, answer: 'new local input'});
});

it('keeps an unsaved answer distinct from an answer submitted on another device', async () => {
  let finish!: (value: unknown) => void;
  const fetcher = vi.fn().mockResolvedValueOnce(ok(checkpoint()))
    .mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
  mount(fetcher); await flush();
  document.getElementById('practice-reload')!.click(); edit('unsaved local answer');
  finish(ok(checkpoint({version: 2, status: 'submitted', answer_draft: 'other device answer',
    attempt: {answer: 'other device answer', feedback: null}}))); await flush();
  expect(input().value).toBe('unsaved local answer');
  expect((document.getElementById('practice-submit') as HTMLButtonElement).disabled).toBe(true);
  expect(document.getElementById('practice-status')!.textContent).toContain('先保留');
});

it('shows the exact answer quote and contradictions as text', async () => {
  const answer = '<img src=x onerror=alert(1)>';
  const data = checkpoint({version: 3, status: 'reviewed', answer_draft: answer, attempt: {answer,
    feedback: {verdict: 'needs_review', answer_quote: answer, reference_status: 'usable', feedback: '请重新核对。',
      matched_points: [], missing_points: [], contradictions: ['将结论说反了']}}});
  mount(vi.fn().mockResolvedValueOnce(ok(data))); await flush();
  expect(document.getElementById('practice-quote')!.textContent).toBe(answer);
  expect(document.getElementById('practice-quote')!.querySelector('img')).toBeNull();
  expect(document.getElementById('practice-points')!.textContent).toContain('需要修正：将结论说反了');
});
