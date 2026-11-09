import { afterEach, describe, expect, it, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { EditorView } from '@codemirror/view';
import { readFileSync } from 'node:fs';
import { mountEditor } from './editor';
const features = readFileSync('../static/features.js', 'utf8');

describe('browser drafts and the visible editor', () => {
  let view: EditorView | undefined;
  let cleanup: (() => void)[] = [];
  afterEach(() => {
    view?.destroy(); view = undefined;
    cleanup.forEach(remove => remove()); cleanup = [];
    document.body.innerHTML = ''; localStorage.clear();
    vi.restoreAllMocks(); vi.unstubAllGlobals();
  });

  async function open(record?: { body: string; baseSha: string }, lateMount = false) {
    const snapshots = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => snapshots.get(key) ?? null,
      setItem: (key: string, value: string) => snapshots.set(key, value),
      removeItem: (key: string) => snapshots.delete(key), clear: () => snapshots.clear(),
    });
    // jsdom does not implement layout measurements used by CodeMirror's selection layer.
    const rangePrototype = Range.prototype as Range & { getClientRects?: () => DOMRectList };
    const previous = rangePrototype.getClientRects;
    rangePrototype.getClientRects = () => [] as unknown as DOMRectList;
    cleanup.push(() => { rangePrototype.getClientRects = previous!; });
    const indexedDB = new IDBFactory();
    vi.stubGlobal('indexedDB', indexedDB);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ blocks: [] }) }));
    await new Promise<void>((resolve, reject) => {
      const request = indexedDB.open('wikitide-drafts', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('drafts', { keyPath: 'key' });
      request.onerror = () => reject(request.error);
      request.onsuccess = () => {
        const db = request.result;
        const transaction = db.transaction('drafts', 'readwrite');
        if (record) transaction.objectStore('drafts').put({ key: 'test:1', userId: 'test', savedAt: Date.now(), ...record });
        transaction.oncomplete = () => { db.close(); resolve(); };
      };
    });
    document.body.dataset.userId = 'test';
    document.body.innerHTML = `<div class="draft-recovery" hidden><span data-draft-description></span><button data-restore-draft>恢复</button><button data-discard-draft>丢弃</button></div>
      <form data-editor data-note-id="1" data-base-sha="current"><input name="csrf" value="test"><input name="base_sha" value="current">
      <span data-draft-status></span><div class="editor-columns"><textarea name="body"># 最新正文</textarea><section class="preview-panel" hidden></section></div>
      <button type="button" data-preview-toggle></button><button type="button" data-download-draft></button><button type="submit">保存</button></form>`;
    // features.js owns page-lifetime listeners; remove them between isolated page fixtures.
    for (const target of [window, document]) {
      const original = target.addEventListener.bind(target);
      vi.spyOn(target, 'addEventListener').mockImplementation((type: string, listener: EventListenerOrEventListenerObject, options?: boolean | AddEventListenerOptions) => {
        original(type, listener, options);
        cleanup.push(() => target.removeEventListener(type, listener, options));
      });
    }
    const form = document.querySelector<HTMLFormElement>('form')!;
    window.eval(features);
    if (!lateMount) view = mountEditor(form)!;
    await vi.waitFor(() => expect(form.dataset.editorLock).not.toBe('loading'));
    if (lateMount) view = mountEditor(form)!;
    return form;
  }

  function expectEditable(editable: boolean) {
    expect(view!.state.readOnly).toBe(!editable);
    expect(view!.contentDOM.getAttribute('contenteditable')).toBe(String(editable));
    expect(view!.contentDOM.getAttribute('aria-readonly')).toBe(String(!editable));
  }

  it.each([false, true])('resumes a same-base draft across either script load order (late mount: %s)', async lateMount => {
    const body = '# 草稿\n\n中文 🌊\n\n```python\nprint("保留")\n```';
    const form = await open({ body, baseSha: 'current' }, lateMount);
    expectEditable(true);
    expect(view!.state.doc.toString()).toBe(body);
    expect(document.querySelector<HTMLElement>('.draft-recovery')!.hidden).toBe(true);
    expect(form.querySelector('[data-draft-status]')!.textContent).toContain('可继续编辑');
    // An actual CodeMirror keyboard command, unlike dispatch(), honors readOnly.
    view!.dispatch({ selection: { anchor: body.length } });
    view!.contentDOM.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace', code: 'Backspace', bubbles: true, cancelable: true }));
    expect(view!.state.doc.toString()).toBe(body.slice(0, -1));
    expect(form.querySelector('textarea')!.value).toBe(body.slice(0, -1));
  });

  it('explains a stale draft lock and restores its original conflict base before autosaving', async () => {
    const body = '# 较早的草稿';
    const form = await open({ body, baseSha: 'original' });
    expectEditable(false);
    expect(form.dataset.editorLock).toBe('draft');
    expect(form.querySelector('[data-draft-status]')!.textContent).toContain('编辑已暂停');
    view!.contentDOM.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace', code: 'Backspace', bubbles: true, cancelable: true }));
    expect(view!.state.doc.toString()).toBe('# 最新正文');
    document.querySelector<HTMLButtonElement>('[data-restore-draft]')!.click();
    expectEditable(true);
    expect(view!.state.doc.toString()).toBe(body);
    expect(form.dataset.baseSha).toBe('original');
    expect(form.querySelector<HTMLInputElement>('[name=base_sha]')!.value).toBe('original');
    // Exercise the emergency snapshot as well as the visible value.
    window.dispatchEvent(new Event('pagehide'));
    expect(JSON.parse(localStorage.getItem('wikitide-emergency:test:1')!)).toMatchObject({ body, baseSha: 'original' });
  });

  it('unlocks a clean page and only discards a stale draft on explicit choice', async () => {
    const form = await open({ body: '# 旧草稿', baseSha: 'original' });
    document.querySelector<HTMLButtonElement>('[data-discard-draft]')!.click();
    await vi.waitFor(() => expectEditable(true));
    expect(view!.state.doc.toString()).toBe('# 最新正文');
    expect(form.dataset.baseSha).toBe('current');
  });

  it('allows editing when no draft exists', async () => {
    await open(); expectEditable(true);
    expect(view!.state.doc.toString()).toBe('# 最新正文');
  });
});
