import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
const bridge = readFileSync(resolve(process.cwd(),'../static/native-state.js'),'utf8');
afterEach(()=>{document.body.innerHTML='';delete window.wikiTideNative;});
describe('native status contract',()=>{
  it('blocks restart until editors register, and uses state rather than visible wording',()=>{
    document.body.innerHTML='<div id="team-root"></div><p>任意文案</p>';
    window.eval(bridge);
    expect(window.wikiTideNative!.state().canRestart).toBe(false);
    let dirty=true;const unregister=window.wikiTideNative!.register('team',()=>({dirty,busy:false}));
    expect(window.wikiTideNative!.state().canRestart).toBe(false);
    dirty=false;expect(window.wikiTideNative!.state().canRestart).toBe(true);
    unregister();expect(window.wikiTideNative!.state().canRestart).toBe(false);
  });
  it('treats provider failures as busy and waits for writing and import modules',()=>{
    document.body.innerHTML='<form id="writing-start"></form>';
    window.eval(bridge);
    expect(window.wikiTideNative!.state().canRestart).toBe(false);
    window.wikiTideNative!.register('writing',()=>{throw new Error('not ready');});
    expect(window.wikiTideNative!.state().canRestart).toBe(false);
  });
});
