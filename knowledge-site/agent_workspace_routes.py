"""Shared workspace services, separate cookie/CSRF and native bearer adapters."""
import json
import time
from fastapi import HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from agent_models import (ProjectInput, ProjectEdit, ResourceInput, MemoryInput, MemoryEdit, RevisionInput,
                          ScopeInput, ResearchInput, RetryInput)


def register_workspace(app,path,g):
    def actor(request,mutation=False):
        if not app.state.agent_memory.enabled: raise HTTPException(404,'Agent 工作台尚未启用。')
        with g['database'](path) as db:
            user,csrf=g['current_user'](db,request)
            if not user: raise HTTPException(401,'请先登录。')
            if mutation and not request.url.path.startswith('/api/client/'):
                g['require_csrf']({'csrf':request.headers.get('X-CSRF-Token','')},csrf)
            return user['id']

    async def payload(request,schema):
        raw=await request.body()
        if len(raw)>32768: raise HTTPException(413,'请求内容过长。')
        try: return schema.model_validate_json(raw)
        except ValidationError as exc: raise HTTPException(400,'请求格式无效，请检查字段和内容长度。') from exc

    def mount(prefix):
        @app.get(prefix+'/projects')
        def projects(request:Request,cursor:int=0,limit:int=20,q:str='',status:str|None=None,kind:str|None=None):
            uid=actor(request)
            if not 1<=limit<=100 or cursor<0: raise HTTPException(400,'分页参数无效。')
            if len(q)>120 or status not in (None,'','active','archived') or kind not in (None,'','container','learning'):
                raise HTTPException(400,'项目筛选参数无效。')
            with g['database'](path) as db:
                rows=db.execute('''SELECT rowid AS cursor,* FROM agent_projects WHERE user_id=?
                    AND (?=0 OR rowid<?) AND (?='' OR instr(lower(title||' '||goal),lower(?))>0)
                    AND (?='' OR status=?) AND (?='' OR kind=?) ORDER BY rowid DESC LIMIT ?''',
                    (uid,cursor,cursor,q.strip(),q.strip(),status or '',status or '',kind or '',kind or '',limit+1)).fetchall()
                return {'projects':[dict(r) for r in rows[:limit]],'next_cursor':rows[limit-1]['cursor'] if len(rows)>limit else None}
        @app.post(prefix+'/projects')
        async def create_project(request:Request):
            uid=actor(request,True);data=await payload(request,ProjectInput)
            with g['database'](path) as db:
                db.execute('BEGIN IMMEDIATE');return app.state.agent_projects.create(db,uid,data)
        @app.get(prefix+'/projects/{pid}')
        def project(request:Request,pid:str):
            uid=actor(request)
            with g['database'](path) as db: return app.state.agent_projects.detail(db,uid,pid)
        @app.patch(prefix+'/projects/{pid}')
        async def edit_project(request:Request,pid:str):
            uid=actor(request,True);data=await payload(request,ProjectEdit)
            with g['database'](path) as db:
                db.execute('BEGIN IMMEDIATE');return app.state.agent_projects.edit(db,uid,pid,data)
        @app.delete(prefix+'/projects/{pid}')
        def delete_project(request:Request,pid:str):
            uid=actor(request,True)
            with g['database'](path) as db:
                db.execute('BEGIN IMMEDIATE');return app.state.agent_projects.delete(db,uid,pid,app.state.agent_memory)
        @app.post(prefix+'/projects/{pid}/resources')
        @app.delete(prefix+'/projects/{pid}/resources')
        async def resource(request:Request,pid:str):
            uid=actor(request,True);data=await payload(request,ResourceInput)
            with g['database'](path) as db:
                db.execute('BEGIN IMMEDIATE');return app.state.agent_projects.link(db,uid,pid,data,request.method=='DELETE')
        @app.post(prefix+'/projects/{pid}/continue')
        def continue_project(request:Request,pid:str):
            uid=actor(request,True)
            with g['database'](path) as db:
                db.execute('BEGIN IMMEDIATE');return app.state.agent_projects.continue_session(db,uid,pid)
        @app.post(prefix+'/projects/{pid}/scope')
        async def project_scope(request:Request,pid:str):
            uid=actor(request,True);data=await payload(request,ScopeInput)
            with g['database'](path) as db:
                db.execute('BEGIN IMMEDIATE');return app.state.agent_projects.bind_scope(db,uid,pid,data.note_ids)
        @app.post(prefix+'/projects/{pid}/open')
        def open_project(request:Request,pid:str):
            uid=actor(request,True)
            with g['database'](path) as db:
                db.execute('BEGIN IMMEDIATE');return app.state.agent_projects.open_thread(db,uid,pid)
        @app.post(prefix+'/projects/{pid}/research')
        async def project_research(request:Request,pid:str):
            uid=actor(request,True)
            if not app.state.agent_runtime_enabled: raise HTTPException(404,'后台任务尚未启用。')
            data=await payload(request,ResearchInput)
            with g['database'](path) as db:
                db.execute('BEGIN IMMEDIATE')
                return app.state.agent_projects.start_research(app.state.agent_runtime,db,uid,pid,data)
        @app.post(prefix+'/projects/{pid}/runs/{rid}/retry')
        async def retry_project_run(request:Request,pid:str,rid:str):
            uid=actor(request,True)
            if not app.state.agent_runtime_enabled: raise HTTPException(404,'后台任务尚未启用。')
            data=await payload(request,RetryInput)
            with g['database'](path) as db:
                db.execute('BEGIN IMMEDIATE')
                return app.state.agent_projects.retry_research(app.state.agent_runtime,db,uid,pid,rid,data)
        @app.get(prefix+'/memories')
        def memories(request:Request,project_id:str|None=None,status:str|None=None,cursor:int=0,limit:int=20):
            uid=actor(request)
            if not 1<=limit<=100 or cursor<0: raise HTTPException(400,'分页参数无效。')
            with g['database'](path) as db: return app.state.agent_memory.list(db,uid,project_id,status,cursor,limit)
        @app.post(prefix+'/memories')
        async def create_memory(request:Request):
            uid=actor(request,True);data=await payload(request,MemoryInput)
            with g['database'](path) as db:
                db.execute('BEGIN IMMEDIATE');return app.state.agent_memory.create(db,uid,data)
        @app.patch(prefix+'/memories/{mid}')
        async def edit_memory(request:Request,mid:str):
            uid=actor(request,True);data=await payload(request,MemoryEdit)
            with g['database'](path) as db:
                db.execute('BEGIN IMMEDIATE');return app.state.agent_memory.mutate(db,uid,mid,data.expected_revision,data)
        @app.delete(prefix+'/memories/{mid}')
        @app.post(prefix+'/memory-candidates/{mid}/activate')
        async def memory_state(request:Request,mid:str):
            uid=actor(request,True);data=await payload(request,RevisionInput)
            with g['database'](path) as db:
                db.execute('BEGIN IMMEDIATE');return app.state.agent_memory.mutate(db,uid,mid,data.expected_revision,delete=request.method=='DELETE',activate=request.method=='POST')
        @app.get(prefix+'/memory-export')
        def export(request:Request):
            uid=actor(request)
            with g['database'](path) as db:
                rows=db.execute("SELECT * FROM agent_memory_items WHERE user_id=? AND status!='deleted' ORDER BY rowid",(uid,)).fetchall()
                return {'schema_version':1,'exported_at':int(time.time()),'memories':[app.state.agent_memory.present(db,dict(r)) for r in rows]}

    mount('/api/agent/v1');mount('/api/client/v1/agent')
    app.state.agent_actor=actor

    @app.get('/agent/projects')
    @app.get('/agent/projects/{pid}')
    @app.get('/agent/memories')
    def workspace_page(request:Request,pid:str|None=None):
        actor(request)
        with g['database'](path) as db:
            user,csrf=g['current_user'](db,request)
            if pid:
                from agent_projects import own_project
                if own_project(db,user['id'],pid).get('kind')=='learning':
                    return RedirectResponse('/learn/projects/'+pid,303)
            return g['page'](request,'agent_workspace.html',user=user,csrf=csrf,categories=g['categories'](db,user),project_id=pid or '')

    @app.get('/learn/projects')
    @app.get('/learn/projects/{pid}')
    def learning_project_page(request:Request,pid:str|None=None,note_ids:list[int]=Query(default=[])):
        uid=actor(request)
        if len(note_ids)>12 or any(note_id<=0 for note_id in note_ids):
            raise HTTPException(400,'每次请选择 1～12 篇笔记。')
        with g['database'](path) as db:
            user,csrf=g['current_user'](db,request)
            rows=db.execute("SELECT id,title,goal,status,updated_at FROM agent_projects WHERE user_id=? AND kind='learning' ORDER BY updated_at DESC LIMIT 50",(uid,)).fetchall()
            project=app.state.agent_projects.detail(db,uid,pid) if pid else None
            if project and project.get('kind')!='learning': raise HTTPException(404,'项目不存在。')
            return g['page'](request,'learning_project.html',user=user,csrf=csrf,categories=g['categories'](db,user),
                             project=project,projects=[dict(row) for row in rows],note_ids=note_ids)
