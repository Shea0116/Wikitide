"""Private project containers reference existing resources without copying ownership."""
import json
import time

from fastapi import HTTPException

from agent_context import compact_json
from agent_memory import bump_epoch, epoch, identifier, public_note, valid_reference


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
        if db.execute('SELECT count(*) FROM agent_projects WHERE user_id=?',(user_id,)).fetchone()[0]>=200:
            raise HTTPException(429,'项目最多 200 个。')
        pid,now=identifier(),int(time.time())
        db.execute('INSERT INTO agent_projects(id,user_id,title,goal,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                   (pid,user_id,data.title.strip(),data.goal.strip(),now,now))
        return own_project(db,user_id,pid)

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
