import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

declare global {
  interface Window { wikiTideUpdates?: { open(): Promise<void> }; }
}
const script = readFileSync(resolve(process.cwd(), '../static/updates.js'), 'utf8');
const bridge = readFileSync(resolve(process.cwd(), '../static/native-state.js'), 'utf8');
const markup = readFileSync(resolve(process.cwd(), '../templates/_updates_dialog.html'), 'utf8');
const release = {id:'20261023.1', sequence:1, date:'2026-10-23', title:'测试更新', summary:'摘要', highlights:['标题折叠','<img src=x onerror=alert(1)>']};
const originalShow = HTMLDialogElement.prototype.showModal;
const originalClose = HTMLDialogElement.prototype.close;
let payload: {latest:typeof release; account_id:number|null; unread:boolean};
let request: ReturnType<typeof vi.fn>;

function clearPage() {
  window.dispatchEvent(new Event('pagehide'));
  delete window.wikiTideUpdates;
  delete window.wikiTideNative;
  document.body.innerHTML = '';
  delete document.body.dataset.userId;
}
async function start(account = '') {
  document.body.dataset.userId = account;
  document.body.dataset.orderCsrf = 'test-csrf';
  document.body.innerHTML = `<a href="/updates" data-updates-open>更新说明</a>${markup}`;
  window.eval(script);
  await vi.advanceTimersByTimeAsync(0);
}
const open = () => document.querySelector('[data-release-dialog]')!.hasAttribute('open');
const dismiss = () => (document.querySelector('[data-release-dismiss]') as HTMLButtonElement).click();

beforeEach(() => {
  vi.useFakeTimers();
  const memory = new Map<string,string>();
  vi.stubGlobal('localStorage', {
    getItem: (key:string) => memory.get(key) ?? null,
    setItem: (key:string,value:string) => memory.set(key,String(value)),
    clear: () => memory.clear(),
  });
  localStorage.clear();
  window.history.replaceState({}, '', '/');
  Object.defineProperty(document,'hidden',{configurable:true,value:false});
  HTMLDialogElement.prototype.showModal = function() { this.setAttribute('open',''); };
  HTMLDialogElement.prototype.close = function() { this.removeAttribute('open'); };
  payload = {latest:structuredClone(release), account_id:null, unread:true};
  request = vi.fn(async () => ({ok:true,json:async () => payload}));
  vi.stubGlobal('fetch',request);
});
afterEach(() => {
  clearPage();vi.restoreAllMocks();vi.unstubAllGlobals();vi.useRealTimers();
  if(originalShow) HTMLDialogElement.prototype.showModal=originalShow; else Reflect.deleteProperty(HTMLDialogElement.prototype,'showModal');
  if(originalClose) HTMLDialogElement.prototype.close=originalClose; else Reflect.deleteProperty(HTMLDialogElement.prototype,'close');
});

describe('release announcement', () => {
  it('shows once per browser release, escapes content and opens again manually', async () => {
    await start(); expect(open()).toBe(true);
    expect(document.querySelector('[data-release-highlights] img')).toBeNull();
    dismiss(); expect(open()).toBe(false);
    clearPage(); await start(); expect(open()).toBe(false);
    await window.wikiTideUpdates!.open(); expect(open()).toBe(true);
    expect(request.mock.calls.filter(call => call[0] === '/api/updates/seen')).toHaveLength(0);
  });

  it('announces a new version but suppresses a rollback', async () => {
    await start(); dismiss(); clearPage();
    payload.latest = {...release,id:'20261023.2',sequence:2};
    await start(); expect(open()).toBe(true); dismiss(); clearPage();
    payload.latest = release;
    await start(); expect(open()).toBe(false);
  });

  it('uses account receipts and CSRF without dismissing another account', async () => {
    payload.account_id=1; await start('1'); dismiss(); await vi.advanceTimersByTimeAsync(0);
    expect(request).toHaveBeenCalledWith('/api/updates/seen',expect.objectContaining({method:'POST',headers:expect.objectContaining({'X-CSRF-Token':'test-csrf'}),body:JSON.stringify({release_id:release.id})}));
    clearPage(); payload.account_id=2; await start('2'); expect(open()).toBe(true);
    window.dispatchEvent(new StorageEvent('storage',{key:'wikitide:updates:user:1',newValue:'1'}));
    expect(open()).toBe(true);
    window.dispatchEvent(new StorageEvent('storage',{key:'wikitide:updates:user:2',newValue:'1'}));
    expect(open()).toBe(false);
  });

  it('honors a server receipt on a second device and avoids stale account pages', async () => {
    payload.account_id=1; payload.unread=false; await start('1'); expect(open()).toBe(false);
    clearPage(); payload.unread=true; payload.account_id=2; await start('1'); expect(open()).toBe(false);
  });

  it('waits for editing and another modal without changing the draft', async () => {
    await start(); dismiss(); clearPage(); localStorage.clear();
    document.body.innerHTML = `<textarea>unsaved text</textarea><dialog id="other" open></dialog>${markup}`;
    window.eval(bridge);
    let dirty=true;
    window.wikiTideNative!.register('test',()=>({dirty,busy:false}));
    window.eval(script); await vi.advanceTimersByTimeAsync(0); expect(open()).toBe(false);
    dirty=false; await vi.advanceTimersByTimeAsync(1600); expect(open()).toBe(false);
    document.querySelector('#other')!.removeAttribute('open');
    await vi.advanceTimersByTimeAsync(1600); expect(open()).toBe(true);
    expect(document.querySelector('textarea')!.value).toBe('unsaved text');
  });

  it('keeps local acknowledgement when offline and retries server sync on the next page', async () => {
    payload.account_id=1;
    request.mockImplementation(async (url:string) => {if(url.endsWith('/seen')) throw new Error('offline');return {ok:true,json:async()=>payload};});
    await start('1'); dismiss(); await vi.advanceTimersByTimeAsync(0);
    clearPage(); await start('1'); expect(open()).toBe(false);
    expect(request.mock.calls.filter(call => call[0] === '/api/updates/seen')).toHaveLength(2);
  });

  it('defers login forms and marks the full history page read without another modal', async () => {
    window.history.replaceState({},'','/login'); await start(); expect(open()).toBe(false);
    clearPage(); window.history.replaceState({},'','/updates'); payload.account_id=1;
    await start('1'); expect(open()).toBe(false);
    expect(request).toHaveBeenCalledWith('/api/updates/seen',expect.anything());
  });
});
