import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { SharedEditor } from './SharedEditor';

const runtime = readFileSync(resolve(process.cwd(), '../static/i18n.js'), 'utf8');
const english = JSON.parse(readFileSync(resolve(process.cwd(), '../static/i18n/en.json'), 'utf8'));
const englishScript = readFileSync(resolve(process.cwd(), '../static/i18n/en.js'), 'utf8');
function start() {
  document.documentElement.lang='en'; window.eval(englishScript); window.eval(runtime);
}
afterEach(() => {cleanup();vi.restoreAllMocks();document.documentElement.lang='zh-CN';});

it('interpolates translated labels without translating user values or treating them as markup', () => {
  start();
  const value='<img src=x onerror=alert(1)>我的阅读';
  const text=window.WikitideI18n!.t('删除文稿：{0}',[value]);
  expect(text).toContain(value);
  expect(text).toMatch(/Delete/);
  const host=document.createElement('p');host.textContent=text;expect(host.querySelector('img')).toBeNull();
  expect(window.WikitideI18n!.t('unknown {0}',['{1}'])).toBe('unknown {1}');
});

it('localizes activity and validation messages without translating document titles', () => {
  start();
  expect(window.WikitideI18n!.t('创建文档「登录」')).toBe('Created document “登录”');
  expect(window.WikitideI18n!.t('title 不能为空')).toBe('title cannot be empty');
  expect(window.WikitideI18n!.t('。')).toBe('.');
});

it('localizes static modal markup and keeps accessible labels', () => {
  start();
  const host=document.createElement('div');host.innerHTML=window.WikitideI18n!.html('<button title="缩小（−）">适应窗口</button>');
  expect(host.textContent).toBe(english['适应窗口']);
  expect(host.querySelector('button')!.title).toBe(english['缩小（−）']);
});

it('renders English editor controls while retaining the original editable source', async () => {
  start();
  Element.prototype.scrollIntoView=vi.fn();
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,json:async()=>({blocks:[]})}));
  const body='# 登录\n\n搜索笔记';
  render(<SharedEditor value={body} onChange={()=>{}} disabled={false} teamId={2} csrf="csrf" />);
  expect(screen.getByRole('button',{name:english['实时预览']})).toBeTruthy();
  expect(document.querySelector('textarea')!.value).toBe(body);
  fireEvent.click(screen.getByRole('button',{name:english['Markdown 源码']}));
  expect(document.querySelector('textarea')!.value).toBe(body);
  vi.unstubAllGlobals();
});

it('preserves the current search and reading fragment in language links', () => {
  start();history.replaceState({},'','/search?q=React&lang=en#result-2');
  document.body.innerHTML='<a data-language-switch href="/language/zh-CN">中文</a>';
  document.dispatchEvent(new Event('DOMContentLoaded'));
  const link=document.querySelector('a')!;
  link.addEventListener('click',event=>event.preventDefault());link.click();
  expect(new URL(link.href).searchParams.get('next')).toBe('/search?q=React#result-2');
});
