"""Private project containers reference existing resources without copying ownership.

A learning project is the personal loop's work unit: one selected-note scope (at most 12),
one explore thread, practice sessions, and write-back drafts. Container projects keep the
older handoff that starts a fresh session.
"""
import json
import time

from fastapi import HTTPException

from agent_context import compact_json
from agent_memory import bump_epoch, epoch, identifier, public_note, valid_reference
from knowledge_scope import selected_note, selected_notes, selection_ids, selection_version


def scope_ids(db, pid):
    return [row[0] for row in db.execute('SELECT note_id FROM agent_project_scope WHERE project_id=? ORDER BY position,note_id', (pid,))]


def thread_id(db, pid):
    row = db.execute('SELECT session_id FROM agent_project_threads WHERE project_id=?', (pid,)).fetchone()
    return row[0] if row else None


def own_project(db,user_id,pid):
    row = db.execute('SELECT * FROM agent_projects WHERE id=? AND user_id=?',(pid,user_id)).fetchone()
    if not row: raise HTTPException(404,'项目不存在。')
    return dict(row)


def authorized_resource(db,user_id,kind,rid):
    if kind=='note':
        note = public_note(db,rid)
        if note: return dict(note)
    else:
        table = {'session':'explore_sessions','draft':'explore_drafts'}.get(kind)
        if table:
            row=db.execute(f'SELECT * FROM {table} WHERE id=? AND user_id=?',(rid,user_id)).fetchone()
            if row: return dict(row)
    raise HTTPException(404,'资源不存在或不属于当前范围。')


class ProjectService:
    def __init__(self,database,path): self.database,self.path=database,path

    def create(self,db,user_id,data):
        if not data.title.strip(): raise HTTPException(400,'请填写项目名称。')
        note_ids=list(getattr(data,'note_ids',[]) or [])
        if data.kind!='learning' and note_ids: raise HTTPException(400,'只有学习项目可以在创建时绑定笔记。')
        if db.execute('SELECT count(*) FROM agent_projects WHERE user_id=?',(user_id,)).fetchone()[0]>=200:
            raise HTTPException(429,'项目最多 200 个。')
        pid,now=identifier(),int(time.time())
        db.execute('INSERT INTO agent_projects(id,user_id,title,goal,kind,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                   (pid,user_id,data.title.strip(),data.goal.strip(),data.kind,now,now))
        if note_ids:
            ids=selection_ids(note_ids=note_ids)
            selected_notes(db,user_id,ids)
            self.write_scope(db,pid,ids)
        return self.detail(db,user_id,pid)

    def detail(self,db,user_id,pid):
        project=own_project(db,user_id,pid); resources=[]
        for link in db.execute('SELECT * FROM agent_project_links WHERE project_id=? ORDER BY created_at,resource_id',(pid,)):
            try:
                resource=authorized_resource(db,user_id,link['resource_kind'],link['resource_id'])
                resources.append({'resource_kind':link['resource_kind'],'resource_id':link['resource_id'],
                                  'title':resource['title'],'available':True,
                                  'stage':resource.get('stage'),'version':resource.get('version')})
            except HTTPException:
                resources.append({'resource_kind':link['resource_kind'],'resource_id':link['resource_id'],'available':False,'title':'资料已变化或不可访问'})
        project['resources']=resources
        project['learning']=self.learning_state(db,user_id,project) if project.get('kind')=='learning' else None
        return project

    def edit(self,db,user_id,pid,data):
        row=own_project(db,user_id,pid)
        if row['version']!=data.expected_version: raise HTTPException(409,'项目已在其他窗口更新。')
        if not data.title.strip(): raise HTTPException(400,'项目名称不能为空。')
        db.execute('''UPDATE agent_projects SET title=?,goal=?,progress=?,next_action=?,status=?,version=version+1,updated_at=? WHERE id=?''',
            (data.title.strip(),data.goal.strip(),data.progress.strip(),data.next_action.strip(),data.status,int(time.time()),pid))
        bump_epoch(db,user_id)
        return self.detail(db,user_id,pid)

    def link(self,db,user_id,pid,data,remove=False):
        own_project(db,user_id,pid)
        if remove:
            db.execute('DELETE FROM agent_project_links WHERE project_id=? AND resource_kind=? AND resource_id=?',(pid,data.resource_kind,data.resource_id))
            if data.resource_kind=='session':
                db.execute('UPDATE agent_session_links SET project_id=NULL WHERE session_id=? AND project_id=?',(data.resource_id,pid))
            if data.resource_kind=='draft':
                db.execute('UPDATE agent_draft_context SET project_id=NULL WHERE draft_id=? AND project_id=?',(data.resource_id,pid))
        else:
            resource=authorized_resource(db,user_id,data.resource_kind,data.resource_id)
            if data.resource_kind in ('session','draft'):
                table,key = ('agent_session_links','session_id') if data.resource_kind=='session' else ('agent_draft_context','draft_id')
                existing=db.execute(f'SELECT project_id FROM {table} WHERE {key}=?',(data.resource_id,)).fetchone()
                if existing and existing['project_id'] not in (None,pid): raise HTTPException(409,'资源已属于其他项目，请先移出。')
                db.execute(f'INSERT INTO {table}({key},project_id) VALUES(?,?) ON CONFLICT({key}) DO UPDATE SET project_id=excluded.project_id',(data.resource_id,pid))
            if db.execute('SELECT count(*) FROM agent_project_links WHERE project_id=?',(pid,)).fetchone()[0]>=500:
                raise HTTPException(429,'项目资源最多 500 项。')
            db.execute('INSERT OR IGNORE INTO agent_project_links VALUES(?,?,?,?)',(pid,data.resource_kind,data.resource_id,int(time.time())))
        db.execute('UPDATE agent_projects SET version=version+1,updated_at=? WHERE id=?',(int(time.time()),pid))
        bump_epoch(db,user_id)
        return self.detail(db,user_id,pid)

    def continue_session(self,db,user_id,pid):
        project=own_project(db,user_id,pid)
        if project.get('kind')=='learning': return self.open_thread(db,user_id,pid)
        if project['status']!='active': raise HTTPException(409,'请先恢复归档项目。')
        previous=db.execute('''SELECT s.* FROM explore_sessions s JOIN agent_session_links l ON l.session_id=s.id
            WHERE l.project_id=? AND s.user_id=? ORDER BY s.updated_at DESC,s.rowid DESC LIMIT 1''',(pid,user_id)).fetchone()
        handoff,refs={},[]
        pinned='[]'
        if previous:
            prior_link=db.execute('SELECT pinned_json FROM agent_session_links WHERE session_id=?',(previous['id'],)).fetchone()
            if prior_link:pinned=prior_link['pinned_json']
            snapshot=db.execute('''SELECT c.* FROM agent_session_links l JOIN agent_context_snapshots c ON c.id=l.current_snapshot_id
                WHERE l.session_id=? AND c.status='valid' ''',(previous['id'],)).fetchone()
            if snapshot and snapshot['context_epoch']==epoch(db,user_id):
                candidates=json.loads(snapshot['sources_json'])
                if all(valid_reference(db,ref) for ref in candidates):
                    handoff={'previous_session_id':previous['id'],'summary':json.loads(snapshot['summary_json'])};refs=candidates
        sid,now=identifier(),int(time.time())
        db.execute('''INSERT INTO explore_sessions(id,user_id,title,note_id,created_at,updated_at) VALUES(?,?,?,NULL,?,?)''',
                   (sid,user_id,project['title'][:48],now,now))
        db.execute('INSERT INTO agent_session_links(session_id,project_id,pinned_json) VALUES(?,?,?)',(sid,pid,pinned))
        db.execute('INSERT INTO agent_project_links VALUES(?,?,?,?)',(pid,'session',sid,now))
        db.execute('INSERT INTO agent_session_handoffs VALUES(?,?,?,?)',(sid,compact_json(handoff),compact_json(refs),epoch(db,user_id)))
        return {'session_id':sid,'project_id':pid,'url':'/explore?session_id='+sid}

    def delete(self,db,user_id,pid,memories):
        own_project(db,user_id,pid)
        for row in db.execute("SELECT id,revision FROM agent_memory_items WHERE project_id=? AND status!='deleted'",(pid,)).fetchall():
            memories.mutate(db,user_id,row['id'],row['revision'],delete=True)
        db.execute("UPDATE agent_runs SET cancel_requested=1,status='cancelled',updated_at=? WHERE project_id=? AND status IN ('queued','waiting_user')",(int(time.time()),pid))
        db.execute('DELETE FROM agent_projects WHERE id=?',(pid,));bump_epoch(db,user_id)
        return {'ok':True}

    def write_scope(self,db,pid,ids):
        current=scope_ids(db,pid)
        if current!=ids:
            thread=thread_id(db,pid)
            if thread and db.execute('SELECT 1 FROM explore_messages WHERE session_id=?',(thread,)).fetchone():
                raise HTTPException(409,'问答已经开始，资料范围不能再更换。')
            if db.execute("SELECT 1 FROM agent_runs WHERE project_id=? AND kind='knowledge' AND status NOT IN ('succeeded','failed','cancelled')",(pid,)).fetchone():
                raise HTTPException(409,'研究任务进行中，请先完成或取消后再调整资料。')
            removed=[note_id for note_id in current if note_id not in ids]
            if removed and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='learning_sessions'").fetchone():
                marks=','.join('?'*len(removed))
                active=db.execute(f'''SELECT 1 FROM learning_sessions s JOIN review_cards c ON c.id=s.card_id
                    WHERE s.project_id=? AND s.status IN ('answering','submitted') AND c.note_id IN ({marks})''',(pid,*removed)).fetchone()
                if active: raise HTTPException(409,'有未完成的练习使用这些笔记，请先完成或放弃后再调整资料。')
        db.execute('DELETE FROM agent_project_scope WHERE project_id=?',(pid,))
        db.executemany('INSERT INTO agent_project_scope(project_id,note_id,position) VALUES(?,?,?)',[(pid,note_id,position) for position,note_id in enumerate(ids)])
        thread=thread_id(db,pid)
        if thread:
            db.execute('DELETE FROM explore_session_notes WHERE session_id=?',(thread,))
            db.executemany('INSERT INTO explore_session_notes(session_id,note_id) VALUES(?,?)',[(thread,note_id) for note_id in ids])
            db.execute('UPDATE explore_sessions SET note_id=? WHERE id=?',(ids[0] if len(ids)==1 else None,thread))

    def bind_scope(self,db,user_id,pid,note_ids):
        project=own_project(db,user_id,pid)
        if project.get('kind')!='learning': raise HTTPException(409,'只有学习项目可以绑定所选笔记。')
        if project['status']!='active': raise HTTPException(409,'请先恢复归档项目。')
        ids=selection_ids(note_ids=note_ids) if note_ids else []
        if ids: selected_notes(db,user_id,ids)
        self.write_scope(db,pid,ids)
        db.execute('UPDATE agent_projects SET version=version+1,updated_at=? WHERE id=?',(int(time.time()),pid))
        return self.detail(db,user_id,pid)

    def prepare_explore(self,db,user_id,project_id,scope,note_id,note_ids,session_id):
        project=own_project(db,user_id,project_id)
        if project.get('kind')!='learning': return None
        if project['status']!='active': raise HTTPException(409,'请先恢复归档项目。')
        if scope!='selected': raise HTTPException(409,'学习项目只围绕已选笔记提问。')
        ids=selection_ids(note_id,note_ids)
        bound=scope_ids(db,project_id)
        thread=thread_id(db,project_id)
        if thread and session_id!=thread: raise HTTPException(409,'学习项目只有一条问答，请从项目继续。')
        if not thread and session_id: raise HTTPException(409,'学习项目只有一条问答，请从项目继续。')
        if bound and ids!=bound: raise HTTPException(409,'提问必须使用学习项目已绑定的笔记。')
        if not bound: self.write_scope(db,project_id,ids)
        return ids

    def adopt_thread(self,db,project_id,session_id):
        project=db.execute('SELECT kind FROM agent_projects WHERE id=?',(project_id,)).fetchone()
        if not project or project['kind']!='learning': return
        existing=thread_id(db,project_id)
        if existing==session_id: return
        if existing: raise HTTPException(409,'学习项目只有一条问答，请从项目继续。')
        db.execute('INSERT INTO agent_project_threads(project_id,session_id) VALUES(?,?)',(project_id,session_id))

    def thread_payload(self,pid,sid):
        return {'session_id':sid,'project_id':pid,'url':'/explore?session_id='+sid+'&project_id='+pid}

    def open_thread(self,db,user_id,pid):
        project=own_project(db,user_id,pid)
        if project.get('kind')!='learning': raise HTTPException(409,'请从学习项目打开问答。')
        if project['status']!='active': raise HTTPException(409,'请先恢复归档项目。')
        ids=scope_ids(db,pid)
        if not ids: raise HTTPException(409,'请先为项目选择 1～12 篇笔记。')
        selected_notes(db,user_id,ids)
        existing=thread_id(db,pid)
        if existing:
            session=db.execute('SELECT id FROM explore_sessions WHERE id=? AND user_id=?',(existing,user_id)).fetchone()
            if session is None:
                db.execute('DELETE FROM agent_project_threads WHERE project_id=?',(pid,))
            else:
                bound=[row[0] for row in db.execute('SELECT note_id FROM explore_session_notes WHERE session_id=? ORDER BY note_id',(existing,))]
                if sorted(bound)!=list(ids): raise HTTPException(409,'项目问答与资料范围不一致，请重新打开。')
                return self.thread_payload(pid,existing)
        sid,now=identifier(),int(time.time())
        db.execute('''INSERT INTO explore_sessions(id,user_id,title,note_id,created_at,updated_at) VALUES(?,?,?,?,?,?)''',
                   (sid,user_id,project['title'][:48],ids[0] if len(ids)==1 else None,now,now))
        db.execute("UPDATE explore_sessions SET scope='selected' WHERE id=?",(sid,))
        db.executemany('INSERT INTO explore_session_notes(session_id,note_id) VALUES(?,?)',[(sid,note_id) for note_id in ids])
        db.execute('INSERT INTO agent_session_links(session_id,project_id) VALUES(?,?)',(sid,pid))
        db.execute('INSERT OR IGNORE INTO agent_project_links(project_id,resource_kind,resource_id,created_at) VALUES(?,?,?,?)',(pid,'session',sid,now))
        db.execute('INSERT INTO agent_project_threads(project_id,session_id) VALUES(?,?)',(pid,sid))
        return self.thread_payload(pid,sid)

    def bind_knowledge_request(self,db,user_id,external):
        project=own_project(db,user_id,external['project_id'])
        if project.get('kind')!='learning': return
        if project['status']!='active': raise HTTPException(409,'请先恢复归档项目。')
        if external.get('scope')!='selected': raise HTTPException(409,'学习项目的研究只使用所选笔记。')
        bound=scope_ids(db,project['id'])
        if external.get('note_id') is None and not external.get('note_ids'):
            if not bound: raise HTTPException(409,'请先为项目选择 1～12 篇笔记。')
            requested=bound
        else:
            requested=selection_ids(external.get('note_id'),external.get('note_ids') or None)
            if bound and requested!=bound: raise HTTPException(409,'研究必须使用学习项目已绑定的笔记。')
            if not bound:
                selected_notes(db,user_id,requested)
                self.write_scope(db,project['id'],requested)
                bound=requested
        external['scope']='selected'
        external['note_ids']=bound
        external['note_id']=None

    def start_research(self,runtime,db,user_id,pid,data):
        project=own_project(db,user_id,pid)
        if project.get('kind')!='learning': raise HTTPException(409,'请在学习项目中开始研究。')
        if project['status']!='active': raise HTTPException(409,'请先恢复归档项目。')
        ids=scope_ids(db,pid)
        if not ids: raise HTTPException(409,'请先为项目选择 1～12 篇笔记。')
        topic=data.topic.strip()
        if not topic: raise HTTPException(400,'主题不能为空。')
        selected_notes(db,user_id,ids)
        from agent_models import RunInput
        request=RunInput(kind='knowledge',project_id=pid,topic=topic,requirements=data.requirements.strip(),
                         scope='selected',note_ids=ids,memory_mode='off',max_tokens=48000,max_search_calls=3,
                         idempotency_key=data.idempotency_key)
        return runtime.create(db,user_id,request)

    def retry_research(self,runtime,db,user_id,pid,rid,data):
        project=own_project(db,user_id,pid)
        if project.get('kind')!='learning': raise HTTPException(404,'研究任务不存在。')
        if project['status']!='active': raise HTTPException(409,'请先恢复归档项目。')
        run=runtime.owned(db,user_id,rid)
        if run['project_id']!=pid or run['kind']!='knowledge': raise HTTPException(404,'研究任务不存在。')
        if run['status']=='waiting_user': raise HTTPException(409,'这次研究正在等待确认，请使用恢复任务。')
        if run['status'] not in {'failed','cancelled'}: raise HTTPException(409,'只有失败或已取消的研究可以重试。')
        saved=json.loads(run['input_json'])
        ids=scope_ids(db,pid)
        if not ids: raise HTTPException(409,'请先为项目选择 1～12 篇笔记。')
        tokens=saved.get('max_tokens') or 48000
        searches=saved.get('max_search_calls') or 3
        if type(tokens) is not int or not 1000<=tokens<=64000: tokens=48000
        if type(searches) is not int or not 1<=searches<=3: searches=3
        from agent_models import RunInput
        request=RunInput(kind='knowledge',project_id=pid,topic=saved['topic'],requirements=saved.get('requirements') or '',
                         scope='selected',note_ids=ids,memory_mode='off',max_tokens=tokens,
                         max_search_calls=searches,idempotency_key=data.idempotency_key)
        created=runtime.create(db,user_id,request)
        kept=db.execute('SELECT status,error_code FROM agent_runs WHERE id=?',(rid,)).fetchone()
        return {'failed_run_id':rid,'failed_status':kept['status'],'failed_error':kept['error_code'],'run':created}

    def learning_state(self,db,user_id,project):
        pid=project['id']
        ids=scope_ids(db,pid)
        scope=[]
        for note_id in ids:
            note=selected_note(db,user_id,note_id)
            if note is None:
                scope.append({'note_id':note_id,'available':False,'title':'资料已变化或不可访问'})
            else:
                scope.append({'note_id':note['id'],'title':note['title'],'available':True,'body_sha':note['body_sha'],'url':'/n/'+str(note['id'])})
        state={'scope':scope,'scope_version':selection_version(ids) if ids else '','thread':None,'practice':[],'cards':[],
               'drafts':[],'proposals':[],'jobs':[],'active_jobs':[],'last_failure':None,'latest_research':None}
        sid=thread_id(db,pid)
        if sid and db.execute('SELECT 1 FROM explore_sessions WHERE id=? AND user_id=?',(sid,user_id)).fetchone():
            count=db.execute('SELECT count(*) FROM explore_messages WHERE session_id=?',(sid,)).fetchone()[0]
            citations,changed=[],False
            latest=db.execute("SELECT sources FROM explore_messages WHERE session_id=? AND role='assistant' ORDER BY id DESC LIMIT 1",(sid,)).fetchone()
            if latest:
                from explore import sources_valid
                sources=json.loads(latest['sources'])
                if sources and sources_valid(db,sources,user_id=user_id):
                    citations=[{key:source.get(key) for key in ('number','title','url','note_id')} for source in sources]
                elif sources:
                    changed=True
            state['thread']={'session_id':sid,'url':'/explore?session_id='+sid+'&project_id='+pid,'message_count':count,
                             'citations':citations,'source_changed':changed}
        if ids and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_cards'").fetchone():
            marks=','.join('?'*len(ids))
            for card in db.execute(f'''SELECT c.id,c.question,c.note_id,c.version,c.body_sha,n.title,n.body_sha AS current_sha
                FROM review_cards c JOIN notes n ON n.id=c.note_id WHERE c.user_id=? AND c.note_id IN ({marks})
                ORDER BY c.due_at,c.id LIMIT 20''',(user_id,*ids)):
                state['cards'].append({'id':card['id'],'question':card['question'],'note_id':card['note_id'],'title':card['title'],
                                       'version':card['version'],'source_changed':card['body_sha']!=card['current_sha']})
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='learning_sessions'").fetchone():
            import learning_service
            for row in db.execute('SELECT id FROM learning_sessions WHERE user_id=? AND project_id=? ORDER BY updated_at DESC LIMIT 12',(user_id,pid)):
                try: state['practice'].append(learning_service.present(db,user_id,row['id']))
                except HTTPException: state['practice'].append({'id':row['id'],'status':'unavailable','source_changed':True})
        for draft in db.execute('''SELECT d.id,d.title,d.stage,d.updated_at FROM explore_drafts d
            JOIN agent_draft_context c ON c.draft_id=d.id WHERE c.project_id=? AND d.user_id=? ORDER BY d.updated_at DESC LIMIT 20''',(pid,user_id)):
            state['drafts'].append({'id':draft['id'],'title':draft['title'],'stage':draft['stage'],'updated_at':draft['updated_at'],
                                    'url':'/explore/write?draft_id='+draft['id']})
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_note_proposals'").fetchone():
            for proposal in db.execute('''SELECT p.id,p.status,p.note_id,p.draft_id,p.mode,p.created_at FROM knowledge_note_proposals p
                JOIN agent_draft_context c ON c.draft_id=p.draft_id WHERE c.project_id=? AND p.user_id=? ORDER BY p.created_at DESC LIMIT 20''',(pid,user_id)):
                state['proposals'].append({'id':proposal['id'],'status':proposal['status'],'note_id':proposal['note_id'],
                                           'draft_id':proposal['draft_id'],'mode':proposal['mode'],'url':'/explore/drafts/'+proposal['draft_id']+'/review'})
        from explore import sources_valid
        jobs=[]
        for run in db.execute('''SELECT * FROM agent_runs WHERE user_id=? AND project_id=? AND kind IN ('knowledge','assessment')
            ORDER BY updated_at DESC,rowid DESC LIMIT 20''',(user_id,pid)):
            view={'id':run['id'],'kind':run['kind'],'status':run['status'],'error_code':run['error_code'],'updated_at':run['updated_at'],
                  'topic':json.loads(run['input_json']).get('topic') or '','source_changed':False,'url':'/agent/tasks/'+run['id']}
            refs=json.loads(run['input_json']).get('source_refs') or []
            if refs and not sources_valid(db,refs,user_id=user_id):
                view.update(topic='资料任务',source_changed=True)
                if not view['error_code']: view['error_code']='source_changed'
            jobs.append(view)
        state['jobs']=jobs
        state['active_jobs']=[job for job in jobs if job['status'] in {'queued','running','waiting_user'}]
        state['last_failure']=next((job for job in jobs if job['status'] in {'failed','cancelled'}),None)
        succeeded=next((job for job in jobs if job['kind']=='knowledge' and job['status']=='succeeded'),None)
        if succeeded and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_job_results'").fetchone():
            saved=db.execute('SELECT sources_json FROM knowledge_job_results WHERE run_id=?',(succeeded['id'],)).fetchone()
            citations=[]
            changed=succeeded['source_changed']
            if saved and not changed:
                sources=json.loads(saved['sources_json'])
                if sources_valid(db,sources,user_id=user_id):
                    citations=[{key:source.get(key) for key in ('number','title','url','note_id')} for source in sources]
                else:
                    changed=True
            state['latest_research']={'run_id':succeeded['id'],'url':succeeded['url'],'source_changed':changed,'citations':[] if changed else citations}
        return state

    def context(self,db,user_id,pid,session_id=None,memory_mode='account'):
        if not pid: return None,[]
        row=own_project(db,user_id,pid)
        result={key:row[key] for key in ('id','title','goal','progress','next_action','version')}
        refs=[]
        if session_id:
            handoff=db.execute('SELECT * FROM agent_session_handoffs WHERE session_id=?',(session_id,)).fetchone()
            if handoff and handoff['context_epoch']==epoch(db,user_id):
                candidates=json.loads(handoff['sources_json'])
                if all(valid_reference(db,ref) and not(memory_mode=='off' and ref.get('kind')=='memory') for ref in candidates):
                    result['handoff']=json.loads(handoff['payload_json']);refs=candidates
        return result,refs
