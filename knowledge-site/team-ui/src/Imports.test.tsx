import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Imports } from './Imports';
import { DiffView, type Diff } from './VersionDiff';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.history.replaceState(null, '', '/'); });
const job = { id: 1, filename: 'sample.md', size: 80, status: 'ready', title: '示例', body: '原始正文', html: '<p>原始正文</p>',
  warnings: [], error: '', version: 2, attempts: 1, result_url: '', updated_at: 1 };
function mockApi(personal = true) {
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'PUT') return new Response(JSON.stringify({ detail: '版本已变化，输入保留' }), { status: 409 });
    if (url === '/api/imports/1') return new Response(JSON.stringify(job));
    return new Response(JSON.stringify({ jobs: [job], teams: [{ id: 8, name: '工程团队' }], personal_allowed: personal, storage: 80, total: 1 }));
  });
  vi.stubGlobal('fetch', fetcher); return fetcher;
}
describe('import workflows', () => {
  it('lets a new account create its own destination space before committing', async () => {
    let created = false;
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/teams' && init?.method === 'POST') {
        created = true;
        return new Response(JSON.stringify({ id: 42 }), { status: 201 });
      }
      if (url === '/api/imports/1') return new Response(JSON.stringify(job));
      return new Response(JSON.stringify({ jobs: [job], teams: created ? [{ id: 42, name: '我的笔记' }] : [], personal_allowed: false, storage: 80, total: 1 }));
    });
    vi.stubGlobal('fetch', fetcher);
    render(<Imports csrf="csrf" />);
    fireEvent.click(await screen.findByText('sample.md'));
    const commit = await screen.findByRole('button', { name: '确认创建新文档' }) as HTMLButtonElement;
    expect(commit.disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '创建我的笔记空间' }));
    await waitFor(() => expect((screen.getByLabelText('目标空间') as HTMLSelectElement).value).toBe('42'));
    expect(commit.disabled).toBe(false);
    expect(fetcher.mock.calls.some(([url, init]) => url === '/api/teams' && init?.headers && (init.headers as Record<string,string>)['X-CSRF-Token'] === 'csrf')).toBe(true);
  });
  it('defaults PDF uploads to original notes and keeps other formats as Markdown', async () => {
    const fetcher = mockApi();
    render(<Imports csrf="csrf" />);
    expect((screen.getByRole('radio', { name: /保留为 PDF 笔记/ }) as HTMLInputElement).checked).toBe(true);
    const picker = screen.getByLabelText('选择导入文件');
    fireEvent.change(picker, { target: { files: [new File(['pdf'], 'scan.pdf'), new File(['# Text'], 'text.md')] } });
    await waitFor(() => expect(fetcher.mock.calls.some(([url, init]) => url.includes('filename=text.md') && init?.method === 'POST')).toBe(true));
    expect(fetcher.mock.calls.some(([url]) => url.includes('filename=scan.pdf&note_format=pdf&ocr=false'))).toBe(true);
    expect(fetcher.mock.calls.some(([url]) => url.includes('filename=text.md&note_format=markdown'))).toBe(true);
  });
  it('lets a scanned PDF save a title without extracted text and creates a separate conversion', async () => {
    const pdfJob = { ...job, filename: 'scan.pdf', body: '', note_format: 'pdf' };
    const converted = { ...job, id: 2, filename: 'scan.pdf', note_format: 'markdown', body: 'Extracted text' };
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/imports/1/convert') return new Response(JSON.stringify(converted), { status: 202 });
      if (url === '/api/imports/2') return new Response(JSON.stringify(converted));
      if (url === '/api/imports/1' && init?.method === 'PUT') return new Response(JSON.stringify({ ...pdfJob, title: '扫描讲义', version: 3 }));
      if (url === '/api/imports/1') return new Response(JSON.stringify(pdfJob));
      return new Response(JSON.stringify({ jobs: [pdfJob], teams: [], personal_allowed: true, storage: 80, total: 1 }));
    });
    vi.stubGlobal('fetch', fetcher);
    render(<Imports csrf="csrf" />);
    fireEvent.click(await screen.findByText('scan.pdf'));
    await screen.findByTitle('PDF 原文阅读');
    expect(screen.queryByLabelText('解析正文')).toBeNull();
    expect(screen.getByLabelText('存放目录')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('文档标题'), { target: { value: '扫描讲义' } });
    const save = screen.getByRole('button', { name: '保存修正' }) as HTMLButtonElement;
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await screen.findByText('修正已保存，可查看阅读预览并确认入库。');
    fireEvent.click(screen.getByRole('button', { name: '提取文字 / 转 Markdown' }));
    await waitFor(() => expect((screen.getByLabelText('解析正文') as HTMLTextAreaElement).value).toBe('Extracted text'));
    expect(fetcher.mock.calls.some(([url, init]) => url === '/api/imports/1/convert' && init?.method === 'POST')).toBe(true);
    expect(screen.getByRole('link', { name: '下载提取文字' }).getAttribute('href')).toBe('/api/imports/2/export?format=txt');
  });
  it('keeps corrections after a stale save and prevents committing unsaved input', async () => {
    mockApi(); render(<Imports csrf="csrf" />);
    fireEvent.click(await screen.findByText('sample.md'));
    const editor = await screen.findByLabelText('解析正文');
    fireEvent.change(editor, { target: { value: '我编辑后的内容' } });
    expect(screen.getByText(/下载 Markdown/).getAttribute('href')).toBeNull();
    expect((screen.getByRole('button', { name: '确认创建新文档' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '保存修正' }));
    await screen.findByRole('alert');
    expect((screen.getByLabelText('解析正文') as HTMLTextAreaElement).value).toBe('我编辑后的内容');
  });
  it('requires separate public confirmation and preselects an authorized team deep link', async () => {
    mockApi(); render(<Imports csrf="csrf" />);
    fireEvent.click(await screen.findByText('sample.md')); await screen.findByLabelText('Markdown 路径');
    fireEvent.change(screen.getByLabelText('Markdown 路径'), { target: { value: 'Frontend/导入.md' } });
    const commit = screen.getByRole('button', { name: '确认创建新文档' }) as HTMLButtonElement;
    expect(commit.disabled).toBe(true);
    fireEvent.click(screen.getByRole('checkbox',{name:/确认此文档可公开访问/}));
    expect(commit.disabled).toBe(false);
    cleanup(); window.history.replaceState(null, '', '/imports?team=8');
    render(<Imports csrf="csrf" />); fireEvent.click(await screen.findByText('sample.md'));
    await waitFor(() => expect((screen.getByLabelText('目标空间') as HTMLSelectElement).value).toBe('8'));
    expect(screen.queryByLabelText('Markdown 路径')).toBeNull();
  });
});
it('renders malicious-looking diff text literally and switches to unified rows', () => {
  const diff: Diff = { old_label: 'v1', new_label: 'v2', added: 1, deleted: 1, identical: false, newline_changed: false, patch: '',
    hunks: [{ header: '@@ -1 +1 @@', split: [{ kind: 'replace', old_no: 1, new_no: 1, old: [{ text: '<script>x()</script>', changed: true }], new: [{ text: '安全文字', changed: true }] }],
      unified: [{ kind: 'delete', old_no: 1, new_no: null, text: '<script>x()</script>', sign: '-' }, { kind: 'insert', old_no: null, new_no: 1, text: '安全文字', sign: '+' }] }] };
  const { container } = render(<DiffView diff={diff} />);
  expect(container.querySelector('script')).toBeNull();
  expect(screen.getByText('<script>x()</script>')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '统一' }));
  expect(container.querySelector('.diff-unified')).toBeTruthy();
  expect(screen.getByRole('button', { name: '统一' }).getAttribute('aria-pressed')).toBe('true');
});
