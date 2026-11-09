import { ui } from "./i18n";
import { useEffect, useRef, useState } from 'react';
type Draft = { id?: number; title: string; body: string; version: number };
export function TeamDraftTools({ draft, username, teamId, csrf, disabled, restore }: {
  draft: Draft; username: string; teamId: number; csrf: string; disabled: boolean; restore: (draft: Draft) => void;
}) {
  const key=`wikitide-team-draft:${username}:${teamId}:${draft.id || 'new'}`;
  const endpoint=`/api/drafts/team/${draft.id}?team_id=${teamId}`;
  const [local,setLocal]=useState<Draft|null>(()=>{try{return JSON.parse(localStorage.getItem(key)||'null');}catch{return null;}});
  const [cloud,setCloud]=useState<{title:string;body:string;base:string;version:number}|null>(null);
  const [version,setVersion]=useState<number|null>(null),[message,setMessage]=useState('');
  const [comparison,setComparison]=useState<{base:string|null;local:string;server:string}|null>(null);
  const previous=useRef(draft);
  const latest=useRef(draft);latest.current=draft;
  useEffect(()=>{
    const emergency=()=>{if(document.body.dataset.loggingOut)return;try{localStorage.setItem(key,JSON.stringify(latest.current));}catch{/* Show the existing unsaved-input warning. */}};
    window.addEventListener('pagehide',emergency);window.addEventListener('beforeunload',emergency);
    return()=>{window.removeEventListener('pagehide',emergency);window.removeEventListener('beforeunload',emergency);};
  },[key]);
  useEffect(()=>{
    if(previous.current===draft)return;
    previous.current=draft;
    const timer=setTimeout(()=>{
      if(document.body.dataset.loggingOut)return;
      try{localStorage.setItem(key,JSON.stringify(draft));setLocal(draft);setMessage(ui.t("草稿已保存到此浏览器。"));}
      catch{setMessage(ui.t("浏览器草稿不可用，请保存云草稿或下载。"));}
    },400);
    return()=>clearTimeout(timer);
  },[draft,key]);
  async function call(url:string,method='GET',data?:unknown){
    const response=await fetch(url,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});
    const value=await response.json();if(!response.ok)throw new Error(value.detail||ui.t("请求失败"));return value;
  }
  async function load(){try{const result=await call(endpoint);setCloud(result.draft);setVersion(result.version??result.draft?.version??0);setMessage(result.draft?ui.t("云草稿已读取，可核对后恢复。"):ui.t("尚无云草稿。"));}catch(error){setMessage(String(error));}}
  useEffect(()=>{ if(draft.id)void load(); },[]);
  const recover=(value:Draft)=>{if(window.confirm(ui.t("用这份草稿替换当前输入？请先保存需要保留的修改。")))restore(value);};
  return <details className="version-panel"><summary>{ui.t("草稿恢复与三方对照")}</summary><div className="editor-actions">
    {local&&<button type="button" disabled={disabled} onClick={()=>recover(local)}>{ui.t("恢复浏览器草稿 v")}{local.version}</button>}
    {local&&<button type="button" disabled={disabled} onClick={()=>{localStorage.removeItem(key);setLocal(null);}}>{ui.t("丢弃浏览器草稿")}</button>}
    {draft.id&&<><button type="button" disabled={disabled} onClick={()=>void load()}>{ui.t("读取云草稿")}</button><button type="button" disabled={disabled||version===null} onClick={async()=>{try{const result=await call(endpoint,'PUT',{title:draft.title,body:draft.body,base:String(draft.version),version});setVersion(result.version);setCloud({title:draft.title,body:draft.body,base:String(draft.version),version:result.version});setMessage(ui.t("云草稿已保存，文档尚未提交。"));}catch(error){setMessage(String(error));}}}>{ui.t("保存云草稿")}</button>
    {cloud&&<button type="button" disabled={disabled} onClick={()=>recover({...draft,title:cloud.title,body:cloud.body,version:Number(cloud.base)})}>{ui.t("恢复云草稿")}</button>}
    {cloud&&<button type="button" disabled={disabled} onClick={async()=>{if(!window.confirm(ui.t("删除已读取的云草稿？当前输入保留。")))return;try{const result=await call(endpoint,'DELETE',{version});setCloud(null);setVersion(result.version);setMessage(ui.t("云草稿已删除。"));}catch(error){setMessage(String(error));}}}>{ui.t("删除云草稿")}</button>}
    <button type="button" disabled={disabled} onClick={async()=>{try{setComparison(await call(`/api/drafts/team/${draft.id}/compare?team_id=${teamId}`,'POST',{base:String(draft.version),body:draft.body}));}catch(error){setMessage(String(error));}}}>{ui.t("三方对照")}</button></>}
    <button type="button" onClick={()=>{const url=URL.createObjectURL(new Blob([draft.body],{type:'text/markdown;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=ui.t("团队草稿.md");a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}}>{ui.t("下载草稿")}</button>
  </div><p role="status">{message}</p>{comparison&&<div className="three-way">{(['base','local','server'] as const).map((key,index)=><section key={key}><h4>{[ui.t("共同旧版本"),ui.t("当前输入"),ui.t("网站最新版本")][index]}</h4><pre>{comparison[key]??ui.t("共同旧版本不可用，请人工核对。")}</pre></section>)}</div>}</details>;
}
