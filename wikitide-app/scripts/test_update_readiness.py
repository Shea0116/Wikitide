"""Check restart protection for the actual injected policy without a browser."""
from pathlib import Path
import subprocess

source = (Path(__file__).resolve().parents[1] / 'lib/web_client.dart').read_text()
script = source.split("const updateReadinessScript = r'''", 1)[1].split("''';", 1)[0]
tests = r'''
const assert = require('node:assert/strict');
let writing = null, editor = null;
global.window = {};
global.document = {
  getElementById: () => writing === null ? null : {textContent: writing},
  querySelector: () => editor === null ? null : {textContent: editor},
};
function ready() { return eval(POLICY); }
assert.equal(ready(), true);
for (const state of ['有未保存更改', '生成中…', '保存中…']) {
  writing = state; assert.equal(ready(), false, state);
}
for (const state of ['草稿已保存', '已存入笔记库', '等待开始']) {
  writing = state; assert.equal(ready(), true, state);
}
writing = null;
for (const state of ['正在编辑…', '草稿已保存到此浏览器，正文尚未提交', '保存失败，输入仍在编辑框中，请重试或下载草稿']) {
  editor = state; assert.equal(ready(), false, state);
}
editor = '未修改'; assert.equal(ready(), true);
window.wikiTideNative = {version: 1, state: () => ({canRestart: false})};
assert.equal(ready(), false);
window.wikiTideNative.state = () => ({canRestart: true});
editor = '旧版中文状态已变化'; assert.equal(ready(), true);
console.log('Restart protection passed for clean, unsaved, generating, saving, failed and browser-only drafts.');
'''
import json
subprocess.run(['rtk', 'proxy', 'node', '-e', 'const POLICY = ' + json.dumps(script) + ';\n' + tests], check=True)
