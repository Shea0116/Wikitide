import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';

const script = readFileSync(resolve(process.cwd(), '../static/agent-workspace.js'), 'utf8');
const template = readFileSync(resolve(process.cwd(), '../templates/agent_workspace.html'), 'utf8');
const bridge = readFileSync(resolve(process.cwd(), '../static/native-state.js'), 'utf8');
const flush = async () => {await new Promise(resolve => setTimeout(resolve, 0));};
const ok = (data: unknown) => ({ok: true, json: async () => data});
const field = (name: string) => document.getElementById(`agent-${name}`) as HTMLInputElement;
const click = (name: string) => field(name).click();
const submit = (name: string) => field(name).dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
const change = (name: string, value: string) => {field(name).value = value; field(name).dispatchEvent(new Event('input', {bubbles: true}));};
const project = {id: 'project-a', title: '<img src=x onerror=alert(1)>', goal: '学习 React', progress: '', next_action: '', status: 'active', version: 4, resources: []};
const memory = {id:'memory-a', scope:'account', project_id:null, kind:'preference', content:'先给结论', status:'active', revision:3, source:{kind:'manual'}, source_valid:true, expired:false, expires_at:null, conflict_key:'tone'};

beforeEach(() => {
  history.replaceState(null, '', '/agent/projects');
  document.body.innerHTML = template.split('{% block content %}')[1].split('{% endblock %}')[0];
  field('workspace').dataset.projectId = ''; field('workspace').dataset.csrf = 'fixture-csrf';
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  window.dispatchEvent(new Event('pagehide')); document.body.innerHTML = '';
  vi.unstubAllGlobals(); vi.restoreAllMocks(); Reflect.deleteProperty(window, 'wikiTideNative');
});

it('renders project titles as text and creates a project using CSRF', async () => {
  const request = vi.fn().mockResolvedValueOnce(ok({projects:[project], next_cursor:null})).mockResolvedValueOnce({ok:false, status:409, json:async()=>({detail:'保留输入'})});
  vi.stubGlobal('fetch', request); window.eval(script); await flush();
  expect(field('project-list').textContent).toContain('<img'); expect(field('project-list').querySelector('img')).toBeNull();
  change('new-title', '面试复习'); change('new-goal', '掌握闭包'); submit('project-create'); await flush();
  expect(request.mock.calls[1]).toMatchObject(['/api/agent/v1/projects', {method:'POST', headers:{'X-CSRF-Token':'fixture-csrf'}, body:JSON.stringify({title:'面试复习', goal:'掌握闭包'})}]);
  expect(field('new-title').value).toBe('面试复习');
});

it('searches projects by title or goal and can filter archived projects', async () => {
  const request=vi.fn().mockResolvedValue(ok({projects:[],next_cursor:null}));
  vi.stubGlobal('fetch',request);window.eval(script);await flush();
  change('project-query','React');change('project-status','archived');submit('project-filter');await flush();
  expect(request.mock.calls[1][0]).toBe('/api/agent/v1/projects?limit=20&q=React&status=archived');
});

it('preserves project edits on revision conflict and protects native restart', async () => {
  field('workspace').dataset.projectId = 'project-a';
  const request = vi.fn().mockResolvedValueOnce(ok(project)).mockResolvedValueOnce({ok:false, status:409, json:async()=>({detail:'项目已在其他窗口更新。'})});
  vi.stubGlobal('fetch', request); window.eval(bridge); window.eval(script); await flush();
  change('edit-goal', '本地修改'); submit('project-edit'); await flush();
  expect(field('edit-goal').value).toBe('本地修改'); expect(field('notice').textContent).toContain('当前输入已保留');
  expect(window.wikiTideNative!.state().canRestart).toBe(false);
  expect(JSON.parse(request.mock.calls[1][1].body)).toMatchObject({expected_version:4, goal:'本地修改'});
});

it('uses memory revisions for edits and forgets only after explicit confirmation', async () => {
  history.replaceState(null, '', '/agent/memories');
  const request = vi.fn().mockResolvedValueOnce(ok({projects:[],next_cursor:null})).mockResolvedValueOnce(ok({memories:[memory],next_cursor:null})).mockResolvedValueOnce({ok:false, status:409, json:async()=>({detail:'记忆已修改'})});
  vi.stubGlobal('fetch', request); window.eval(script); await flush(); await flush();
  const edit = [...field('memory-list').querySelectorAll('button')].find(button=>button.textContent==='编辑')!; edit.click(); await flush();
  change('memory-content', '我的本地编辑'); submit('memory-form'); await flush();
  expect(JSON.parse(request.mock.calls[2][1].body)).toMatchObject({expected_revision:3, content:'我的本地编辑'});
  expect(field('memory-content').value).toBe('我的本地编辑');
  vi.mocked(window.confirm).mockReturnValue(false);
  [...field('memory-list').querySelectorAll('button')].find(button=>button.textContent==='忘记')!.click(); await flush();
  expect(request).toHaveBeenCalledTimes(3);
});

it('requires a project for project-scoped memory and activates a confirmed candidate', async () => {
  history.replaceState(null, '', '/agent/memories');
  const request = vi.fn().mockResolvedValueOnce(ok({projects:[],next_cursor:null})).mockResolvedValueOnce(ok({memories:[{...memory,status:'candidate'}],next_cursor:null})).mockResolvedValueOnce(ok({...memory,revision:4})).mockResolvedValueOnce(ok({memories:[{...memory,revision:4}],next_cursor:null}));
  vi.stubGlobal('fetch', request); window.eval(script); await flush(); await flush();
  change('memory-scope', 'project'); change('memory-content', '项目事实'); submit('memory-form'); await flush();
  expect(request).toHaveBeenCalledTimes(2); expect(field('notice').textContent).toContain('选择一个项目');
  [...field('memory-list').querySelectorAll('button')].find(button=>button.textContent==='确认并启用')!.click(); await flush();
  expect(request.mock.calls[2][0]).toBe('/api/agent/v1/memory-candidates/memory-a/activate');
  expect(JSON.parse(request.mock.calls[2][1].body)).toEqual({expected_revision:3});
});

it('displays durable budget and requires retry-cost confirmation before resume', async () => {
  history.replaceState(null, '', '/agent/projects?panel=runs');
  const run = {id:'run-a',kind:'writing',topic:'解释闭包',status:'waiting_user',error_code:'provider_uncertain',budget:{used_tokens:500,max_tokens:16000,search_calls:1,max_search_calls:3},steps:[{step_key:'write',status:'waiting_user'}],result:{draft_id:'draft-a'}};
  const request = vi.fn().mockResolvedValueOnce(ok({projects:[],next_cursor:null})).mockResolvedValueOnce(ok({runs:[run],next_cursor:null})).mockResolvedValueOnce(ok(run)).mockResolvedValueOnce(ok(run)).mockResolvedValueOnce(ok(run)).mockResolvedValueOnce(ok({runs:[run],next_cursor:null}));
  vi.stubGlobal('fetch', request); window.eval(script); await flush(); await flush();
  field('run-list').querySelector('button')!.click(); await flush();
  expect(field('run-budget-status').textContent).toContain('500 / 16000');
  expect(field('run-result').querySelector('a')!.getAttribute('href')).toBe('/explore/write?draft_id=draft-a');
  vi.mocked(window.confirm).mockReturnValue(false); click('run-resume'); await flush(); expect(request).toHaveBeenCalledTimes(3);
  vi.mocked(window.confirm).mockReturnValue(true); click('run-resume'); await flush();
  expect(window.confirm).toHaveBeenLastCalledWith(expect.stringContaining('再次产生费用'));
  expect(request.mock.calls[3][0]).toBe('/api/agent/v1/runs/run-a/resume'); expect(JSON.parse(request.mock.calls[3][1].body)).toEqual({retry_uncertain:true});
});

it('links modern research tasks to their persistent result page', async () => {
  history.replaceState(null, '', '/agent/projects?panel=runs');
  const run = {id: 'research-a', kind: 'knowledge', topic: '研究闭包', status: 'succeeded', budget: {}, steps: [], result: {knowledge_id: 'research-a'}};
  const request = vi.fn().mockResolvedValueOnce(ok({projects: [], next_cursor: null}))
    .mockResolvedValueOnce(ok({runs: [run], next_cursor: null})).mockResolvedValueOnce(ok(run));
  vi.stubGlobal('fetch', request); window.eval(script); await flush(); await flush();
  expect(field('run-list').textContent).toContain('后台研究');
  field('run-list').querySelector('button')!.click(); await flush();
  expect(field('run-result').querySelector('a')!.getAttribute('href')).toBe('/agent/tasks/research-a');
});
