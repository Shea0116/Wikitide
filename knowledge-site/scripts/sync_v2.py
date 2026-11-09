"""Versioned note/metadata/linked-asset sync with persistent conflicts."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import posixpath
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from sync_local import atomic_write, digest, read_state, safe_file, save_state
from prepare_content import walk_catalog, SKIP_DIRS, SKIP_FILES, safe_path, PRIVATE_ROOTS

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from html_compat import sanitize_html
from markdown_it import MarkdownIt
from note_assets import enable_embeds, parse_note


def meta(record):
    return {key:record[key] for key in ('title','summary','category')}


def sha(body, metadata):
    return digest(json.dumps([digest(body),metadata['title'],metadata['summary'],metadata['category']],ensure_ascii=False))


def catalog_records(root):
    records={}
    # Files determine the directory; the catalog only supplies optional metadata.
    for directory in ('Backend','Frontend'):
        for current,dirs,files in os.walk(root/directory):
            dirs[:]=[name for name in dirs if not name.startswith('.') and name not in SKIP_DIRS
                     and name.lower()!='static' and not (Path(current)/name).is_symlink()]
            for name in files:
                file=Path(current)/name
                if name in SKIP_FILES or file.suffix!='.md' or not safe_path(root,file): continue
                relative=file.relative_to(root).as_posix()
                records.setdefault(relative,dict(path=relative,title=file.stem,summary='',category=' / '.join(file.relative_to(root).parts[:-1]),catalogued=False))
    if (root/'categories.json').is_file():
        catalog=json.loads((root/'categories.json').read_text(encoding='utf-8'))
        def identity(name):
            info=(root/name).stat()
            return (name.casefold(),info.st_dev,info.st_ino)
        discovered={identity(name):name for name in records}
        annotated=set()
        for row in walk_catalog(root,catalog,[],[],skip_missing=True):
            key=identity(row['path'])
            if key in discovered and key not in annotated:
                record=records.pop(discovered[key])
                record.update(row,title=Path(row['path']).stem,catalogued=True)
                records[row['path']]=record
                annotated.add(key)
    return records


def asset_names(path,body,root=None):
    targets=set()
    def collect(tag,attr,original):
        parsed=urlsplit(original)
        if parsed.scheme or parsed.netloc or not parsed.path: return original
        name=posixpath.normpath(posixpath.join(posixpath.dirname(path),unquote(parsed.path)))
        if name.split('/',1)[0]==path.split('/',1)[0] and Path(name).suffix.lower() in {'.png','.jpg','.jpeg','.gif','.webp','.svg','.pdf'}:
            targets.add(name)
        return original
    parser=enable_embeds(MarkdownIt('default',{'html':True}))
    tokens=parse_note(parser,body,path,root)
    sanitize_html(parser.renderer.render(tokens,parser.options,{}),collect)
    return targets


def binary_sha(file):
    return hashlib.sha256(file.read_bytes()).hexdigest() if file.is_file() else ''


def file_identity(file):
    if not file.is_file(): return None
    stat=file.stat()
    return [stat.st_dev,stat.st_ino,getattr(stat,'st_birthtime_ns',None)]


def rename_files(root, records, state, explicit):
    """Inspect private files only to relocate already tracked notes, never to import them."""
    result = dict(records)
    if any(not safe_file(root, name).exists() for name in state) or explicit:
        for dirname in PRIVATE_ROOTS:
            for current, dirs, files in os.walk(root / dirname):
                dirs[:] = [name for name in dirs if name not in SKIP_DIRS and not name.startswith('.')
                           and name.lower() != 'static' and not (Path(current) / name).is_symlink()]
                for name in files:
                    file = Path(current) / name
                    if file.suffix != '.md' or name in SKIP_FILES or not safe_path(root, file):
                        continue
                    relative = file.relative_to(root).as_posix()
                    result[relative] = {'path': relative, 'catalogued': False}
        for file in root.glob('*.md'):
            if file.name not in SKIP_FILES | {'WIKI.md'} and safe_path(root, file):
                result[file.name] = {'path': file.name, 'catalogued': False}
    return result


def rename_candidates(root,records,notes,state,explicit,ambiguities=None):
    candidates=dict(explicit)
    # Identity handles edits accompanying a rename; identical content migrates old checkpoints.
    missing=[name for name in notes if name in state and not safe_file(root,name).exists()]
    matches={}
    identities = {name: file_identity(safe_file(root, name)) for name in records}
    hashes = {}
    for old in missing:
        if old in explicit: continue
        checkpoint=state[old]
        identity_matches, body_matches = [], []
        for new in records:
            if new==old or new in explicit.values(): continue
            public_target = new.split('/', 1)[0] in {'Frontend', 'Backend'}
            if (notes[old]['visibility'] == 'public') != public_target: continue
            file=safe_file(root,new)
            if checkpoint.get('file_identity') is not None and checkpoint['file_identity'] == identities[new]:
                identity_matches.append(new)
            if new not in hashes:
                hashes[new] = digest(file.read_text(encoding='utf-8'))
            if checkpoint.get('body_sha') == hashes[new]:
                body_matches.append(new)
        options = identity_matches or body_matches
        identity=checkpoint.get('file_identity')
        # Some Linux filesystems recycle inode numbers immediately. Without a
        # birth timestamp, an inode match must not hide duplicate body matches.
        if len(body_matches)>1 and (not identity or len(identity)<3 or identity[2] is None):
            options=body_matches
        if len(options)==1: matches[old]=options[0]
        elif len(options)>1 and ambiguities is not None:
            ambiguities[old]=options
    for old,new in matches.items():
        if list(matches.values()).count(new)==1: candidates.setdefault(old,new)
        elif ambiguities is not None: ambiguities[old]=[new]
    if len(set(candidates.values()))!=len(candidates): raise ValueError('Ambiguous rename targets')
    return candidates


def write_binary(file,raw,expected):
    file.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.kb-asset-',dir=file.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        if binary_sha(file)!=expected: raise RuntimeError('Local attachment changed; retry next run')
        os.replace(name,file)
    finally:
        if os.path.exists(name): os.unlink(name)


def sync_enhanced(config,api,snapshot):
    root=Path(config['source_root']).resolve()
    state_path=Path(config['state_path']).with_suffix('.v2.json').resolve()
    conflict_root=Path(config['conflict_root']).resolve()
    state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.is_file() else {'notes':{},'assets':{}}
    if not isinstance(state,dict) or not isinstance(state.get('notes'),dict) or not isinstance(state.get('assets'),dict):
        raise ValueError('Invalid enhanced sync state')
    legacy=read_state(Path(config['state_path']))
    counts={'downloaded':0,'uploaded':0,'conflicts':0}
    run=api.call('/api/sync/run',{'status':'running'})['id']
    conflicts={row['path']:row for row in snapshot['conflicts']}
    notes={row['path']:row for row in snapshot['notes']}
    asset_errors=[]
    rename_issues=[]
    synchronized={}

    def remember(name,server,body,catalog):
        state['notes'][name]={'sha':server['sha'],'body_sha':digest(body),'body':body,
                              'metadata':meta(server),'catalog':catalog,'id':server['id'],
                              'file_identity':file_identity(safe_file(root,name))}
        synchronized[name]=dict(server)

    def report(name,server,body,old):
        reply=api.call('/api/sync/v2/conflict',{'path':name,'local_body':body,'server_sha':server['sha'],'base_body':old.get('body','') if old else ''})
        if not reply.get('conflict'):
            conflict_file=safe_file(conflict_root,name+'.server.md')
            conflict_root.mkdir(parents=True,exist_ok=True,mode=0o700)
            os.chmod(conflict_root,0o700)
            atomic_write(conflict_file,server['body'])
            os.chmod(conflict_file,0o600)
        counts['conflicts']+=1

    try:
        records=catalog_records(root)
        relocations=rename_files(root,records,state['notes'],config.get('renames',{}))
        # Recover a committed server move after a crash before the local checkpoint write.
        for move in snapshot.get('moves',[]):
            source,target=move['from_path'],move['path']
            old=state['notes'].get(source)
            listing=notes.get(target)
            if (source not in notes and old and listing and target in relocations
                    and not safe_file(root,source).exists() and old['sha']==move['base_sha']
                    and listing['sha']==move['sha']):
                body=safe_file(root,target).read_text(encoding='utf-8')
                if digest(body)==listing['body_sha']:
                    state['notes'].pop(source)
                    remember(target,listing,body,meta(records[target]) if target in records and records[target].get('catalogued') else None)
        explicit=config.get('renames',{})
        if not isinstance(explicit,dict): raise ValueError('Invalid rename mapping')
        if explicit and 'rename' not in snapshot.get('capabilities',[]):
            raise RuntimeError('Server must be upgraded before renaming notes')
        blocked=set()
        if 'rename' in snapshot.get('capabilities',[]):
            ambiguities={}
            candidates=rename_candidates(root,relocations,notes,state['notes'],explicit,ambiguities)
            for source,targets in ambiguities.items():
                blocked.update([source,*targets]); counts['conflicts']+=1
                rename_issues.append('改名候选不唯一，请明确旧路径和新路径：'+source)
            for old_name,new_name in candidates.items():
                old=state['notes'].get(old_name)
                destination=state['notes'].get(new_name)
                listing=notes.get(old_name)
                target=notes.get(new_name)
                if (not listing and not old and target and destination
                        and target['sha']==destination['sha'] and not safe_file(root,old_name).exists()
                        and new_name in relocations and digest(safe_file(root,new_name).read_text(encoding='utf-8'))==destination['body_sha']):
                    continue
                if not old or not listing or new_name not in relocations or safe_file(root,old_name).exists():
                    raise ValueError('Rename does not match a missing tracked note')
                needs_v2 = listing['visibility'] != 'public' or Path(old_name).parent != Path(new_name).parent
                if needs_v2 and 'rename-v2' not in snapshot.get('capabilities',[]):
                    blocked.update((old_name,new_name)); counts['conflicts']+=1
                    rename_issues.append('服务器需升级才能同步改名或移动：'+old_name+' → '+new_name)
                    continue
                body=safe_file(root,new_name).read_text(encoding='utf-8')
                if (listing['sha']!=old['sha'] or (target and (not destination or target['sha']!=destination['sha']
                        or digest(body)!=destination['body_sha']))):
                    blocked.update((old_name,new_name)); counts['conflicts']+=1
                    rename_issues.append('改名两端存在新修改，已保留双方：'+old_name+' → '+new_name); continue
                result=api.call('/api/sync/v2/rename',{'from_path':old_name,'path':new_name,
                    'base_sha':old['sha'],'target_sha':target['sha'] if target else '', 'body':body})
                if result.get('conflict') or result.get('unsupported'):
                    blocked.update((old_name,new_name)); counts['conflicts']+=1
                    rename_issues.append('改名版本或权限需要核对：'+old_name+' → '+new_name); continue
                notes.pop(old_name); notes[new_name]=result
                conflicts.pop(old_name,None); conflicts.pop(new_name,None)
                state['notes'].pop(old_name,None)
                remember(new_name,result,body,meta(records[new_name]) if new_name in records and records[new_name].get('catalogued') else None)
                counts['uploaded']+=1
        # Public discoveries may be added; private discoveries require explicit website creation.
        for name,row in records.items():
            if name in notes or name in blocked: continue
            file=safe_file(root,name)
            body=file.read_text(encoding='utf-8')
            result=api.call('/api/sync/v2/note',{'path':name,'base_sha':'','body':body,**meta(row)})
            if result.get('conflict'): counts['conflicts']+=1; continue
            server={**row,'id':result['id'],'sha':result['sha'],'body_sha':digest(body),'visibility':'public'}
            notes[name]=server; remember(name,server,body,meta(row)); counts['uploaded']+=1

        for name,listing in notes.items():
            if name in blocked: continue
            file=safe_file(root,name)
            server=api.call(f"/api/sync/v2/note/{listing['id']}")
            if server.get('unsupported'): continue
            old=state['notes'].get(name)
            local=file.read_text(encoding='utf-8') if file.is_file() else ''
            local_digest=digest(local) if file.is_file() else ''
            observed_catalog=meta(records[name]) if name in records and records[name].get('catalogued') else None
            metadata=observed_catalog or meta(server)
            # An unchanged local catalog must not roll back metadata edited on the website.
            if old and observed_catalog==old.get('catalog'):
                metadata=old['metadata']
            conflict=conflicts.get(name)
            if conflict:
                if conflict['status']=='resolved' and local_digest==conflict['local_sha'] and server['sha']==conflict['resolved_sha']:
                    atomic_write(file,server['body'],expected_sha=local_digest)
                    result=api.call('/api/sync/v2/applied',{'path':name,'sha':server['sha']})
                    remember(name,server,server['body'],observed_catalog)
                    counts['downloaded']+=1
                    if result.get('conflict'): counts['conflicts']+=1
                else:
                    if local_digest!=conflict['local_sha'] or (conflict['status']=='resolved' and server['sha']!=conflict['resolved_sha']):
                        report(name,server,local,old)
                    else: counts['conflicts']+=1
                continue
            if not file.is_file():
                if not old and listing.get('web_created'):
                    atomic_write(file,server['body'],expected_sha='')
                    api.call('/api/sync/v2/applied',{'path':name,'sha':server['sha']})
                    remember(name,server,server['body'],observed_catalog); counts['downloaded']+=1
                elif old:
                    report(name,server,'',old)
                continue
            local_record=sha(local,metadata)
            if local_record==server['sha']:
                remember(name,server,local,observed_catalog)
                api.call('/api/sync/v2/applied',{'path':name,'sha':server['sha']})
            elif not old:
                if legacy.get(name)==server['body_sha']:
                    result=api.call('/api/sync/v2/note',{'path':name,'base_sha':server['sha'],'body':local,**metadata})
                    if result.get('conflict'): report(name,server,local,old); continue
                    server.update(metadata); server['sha']=result['sha']; server['body_sha']=digest(local)
                    remember(name,server,local,observed_catalog); counts['uploaded']+=1
                elif local_digest==server['body_sha']:
                    # First enhanced run starts with server metadata; future catalog edits are tracked.
                    remember(name,server,local,observed_catalog)
                else:
                    report(name,server,local,old); continue
            elif server['sha']==old['sha']:
                result=api.call('/api/sync/v2/note',{'path':name,'base_sha':old['sha'],'body':local,**metadata})
                if result.get('conflict'): report(name,server,local,old); continue
                server.update(metadata); server['sha']=result['sha']; server['body_sha']=digest(local)
                remember(name,server,local,observed_catalog); counts['uploaded']+=1
            elif local_record==old['sha']:
                atomic_write(file,server['body'],expected_sha=local_digest)
                api.call('/api/sync/v2/applied',{'path':name,'sha':server['sha']})
                remember(name,server,server['body'],observed_catalog); counts['downloaded']+=1
            else:
                report(name,server,local,old); continue

            # Only attachments actually referenced by a synchronized note are eligible.
            local=file.read_text(encoding='utf-8')
            remote_assets={row['path']:row['sha'] for row in api.call(f"/api/sync/v2/assets/{server['id']}")['assets']}
            for asset in asset_names(name,local,root)|remote_assets.keys():
                asset_file=safe_file(root,asset)
                if asset_file.is_file() and asset_file.stat().st_size>4*1024*1024:
                    asset_errors.append(asset+'（超过 4MB）'); continue
                current=binary_sha(asset_file); remote=remote_assets.get(asset,''); base=state['assets'].get(asset)
                if current==remote:
                    state['assets'][asset]=current; continue
                if current and (remote==base or not remote):
                    result=api.call(f"/api/sync/v2/asset/{server['id']}",{'path':asset,'base_sha':remote,'data':base64.b64encode(asset_file.read_bytes()).decode()})
                    if not result.get('conflict'): state['assets'][asset]=result['sha']; continue
                elif remote and (current==base or (not current and base is None)):
                    result=api.call(f"/api/sync/v2/asset/{server['id']}?path={quote(asset,safe='')}")
                    raw=base64.b64decode(result['data'],validate=True)
                    if hashlib.sha256(raw).hexdigest()!=result['sha']: raise RuntimeError('Attachment verification failed')
                    write_binary(asset_file,raw,current); state['assets'][asset]=result['sha']; continue
                asset_errors.append(asset)
                if remote:
                    result=api.call(f"/api/sync/v2/asset/{server['id']}?path={quote(asset,safe='')}")
                    backup=safe_file(conflict_root,asset+'.server'+Path(asset).suffix)
                    write_binary(backup,base64.b64decode(result['data'],validate=True),binary_sha(backup)); os.chmod(backup,0o600)

        from sync_catalog import update_public_catalog
        written=update_public_catalog(root,synchronized)
        if written is not None:
            for name,record in written.items():
                if name in synchronized:
                    state['notes'][name]['catalog']=meta(record)
        save_state(state_path,state)
        counts['conflicts']+=len(set(asset_errors))
        api.call('/api/sync/run',{'id':run,'status':'success',**counts,
                 'message':'；'.join(rename_issues + (['附件需人工处理：'+'、'.join(sorted(set(asset_errors)))] if asset_errors else []))[:500]})
        if rename_issues:
            print('同步提醒：'+'；'.join(rename_issues))
        return counts
    except Exception as error:
        # The saved checkpoints make partial runs retryable; avoid putting tokens or private bodies in logs.
        save_state(state_path,state)
        message = '同步中断，已保留完成进度和本地版本'
        if isinstance(error,PermissionError): message = '本地文件或同步状态目录没有读写权限'
        elif isinstance(error,FileNotFoundError): message = '本地笔记或分类引用的文件不存在'
        elif isinstance(error,UnicodeError): message = '笔记不是有效的 UTF-8 文本'
        elif isinstance(error,ValueError): message = '分类、路径或同步数据校验失败'
        elif isinstance(error,TimeoutError): message = '连接服务器超时，等待下次重试'
        elif isinstance(error,RuntimeError):
            code = re.search(r'Sync request failed \((\d{3})\)',str(error))
            if code: message = '服务器拒绝同步请求（HTTP '+code.group(1)+'）'
            elif 'Local' in str(error): message = '同步期间本地文件再次变化，已停止写入，请重试'
        try: api.call('/api/sync/run',{'id':run,'status':'failed',**counts,'message':message})
        except Exception: pass
        raise
