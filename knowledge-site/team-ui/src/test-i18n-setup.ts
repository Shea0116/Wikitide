import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach } from 'vitest';
const runtime = readFileSync(resolve(process.cwd(), '../static/i18n.js'), 'utf8');
beforeEach(() => {
  document.documentElement.lang = 'zh-CN';
  window.eval(runtime);
});
