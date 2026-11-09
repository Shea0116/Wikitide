"""Versioned per-account drafts and three-way comparison inputs."""
import time
import json
from fastapi import HTTPException, Request
from storage import connect, can_edit
from accounts import bounded_body


def register_drafts(app,db_path,api):
    with connect(db_path) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS editor_drafts(
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind TEXT NOT NULL, resource_id INTEGER NOT NULL, team_id INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL, body TEXT NOT NULL, base TEXT NOT NULL, version INTEGER NOT NULL,updated_at INTEGER NOT NULL,
            PRIMARY KEY(user_id,kind,resource_id,team_id))''')
        if 'deleted' not in {row['name'] for row in db.execute('PRAGMA table_info(editor_drafts)')}:
            db.execute('ALTER TABLE editor_drafts ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')

    async def data_for(request):
        try: data=json.loads(await bounded_body(request,4*1024*1024))
        except ValueError as exc: raise HTTPException(400,'草稿格式无效') from exc
        if not isinstance(data,dict): raise HTTPException(400,'草稿格式无效')
        return data

    def access(db,request,kind,resource_id,team_id,write=False):
        user,csrf=api['current_user'](db,request)
        if not user: raise HTTPException(401)
        if write: api['require_csrf']({'csrf':request.headers.get('X-CSRF-Token','')},csrf)
        if kind=='note' and team_id==0:
            doc=db.execute('SELECT * FROM notes WHERE id=?',(resource_id,)).fetchone()
            if not doc or not can_edit(db,doc,user): raise HTTPException(404)
            return user,dict(doc,base=doc['body_sha'])
        if kind=='team':
            member=db.execute("SELECT 1 FROM team_members WHERE team_id=? AND user_id=? AND role IN ('owner','admin','editor')",(team_id,user['id'])).fetchone()
            doc=db.execute('SELECT * FROM team_documents WHERE id=? AND team_id=?',(resource_id,team_id)).fetchone()
            if not member or not doc: raise HTTPException(404)
            return user,dict(doc,base=str(doc['version']))
        raise HTTPException(404)

    @app.get('/api/drafts/{kind}/{resource_id}')
    def get_draft(request:Request,kind:str,resource_id:int,team_id:int=0):
        with api['database'](db_path) as db:
            user,doc=access(db,request,kind,resource_id,team_id)
            draft=db.execute('SELECT * FROM editor_drafts WHERE user_id=? AND kind=? AND resource_id=? AND team_id=?',(user['id'],kind,resource_id,team_id)).fetchone()
            return dict(draft=dict(draft) if draft and not draft['deleted'] else None,version=draft['version'] if draft else 0,current_base=doc['base'])

    @app.put('/api/drafts/{kind}/{resource_id}')
    async def save_draft(request:Request,kind:str,resource_id:int,team_id:int=0):
        data=await data_for(request)
        if type(data.get('version')) is not int or not isinstance(data.get('body'),str) or len(data['body'])>1000000 or not isinstance(data.get('base'),str) or len(data['base'])>80 or not isinstance(data.get('title',''),str) or len(data.get('title',''))>160:
            raise HTTPException(400,'草稿无效')
        with api['database'](db_path) as db:
            db.execute('BEGIN IMMEDIATE'); user,_=access(db,request,kind,resource_id,team_id,True)
            key=(user['id'],kind,resource_id,team_id)
            current=db.execute('SELECT version,deleted FROM editor_drafts WHERE user_id=? AND kind=? AND resource_id=? AND team_id=?',key).fetchone()
            if data['version']!=(current[0] if current else 0): raise HTTPException(409,'云草稿已在另一设备修改，请先读取最新云草稿；当前输入仍保留')
            if (not current or current['deleted']) and db.execute('SELECT count(*) FROM editor_drafts WHERE user_id=? AND deleted=0',(user['id'],)).fetchone()[0]>=200: raise HTTPException(409,'云草稿已达 200 份，请先整理')
            version=data['version']+1
            db.execute('''INSERT OR REPLACE INTO editor_drafts VALUES(?,?,?,?,?,?,?,?,?,0)''',(*key,data.get('title',''),data['body'],data['base'],version,int(time.time())))
            return {'version':version}

    @app.post('/api/drafts/{kind}/{resource_id}/compare')
    async def compare(request:Request,kind:str,resource_id:int,team_id:int=0):
        data=await data_for(request)
        if not isinstance(data.get('base'),str) or not isinstance(data.get('body'),str) or len(data['body'])>1000000: raise HTTPException(400)
        with api['database'](db_path) as db:
            _,doc=access(db,request,kind,resource_id,team_id,True)
            old=db.execute('SELECT body FROM revisions WHERE note_id=? AND body_sha=? ORDER BY id LIMIT 1',(resource_id,data['base'])).fetchone() if kind=='note' else db.execute('SELECT body FROM team_document_revisions WHERE document_id=? AND version=?',(resource_id,data['base'])).fetchone()
            return dict(base=old[0] if old else None,local=data['body'],server=doc['body'],current_base=doc['base'])

    @app.delete('/api/drafts/{kind}/{resource_id}')
    async def delete_draft(request:Request,kind:str,resource_id:int,team_id:int=0):
        data=await data_for(request)
        with api['database'](db_path) as db:
            db.execute('BEGIN IMMEDIATE');user,_=access(db,request,kind,resource_id,team_id,True)
            key=(user['id'],kind,resource_id,team_id)
            draft=db.execute('SELECT version FROM editor_drafts WHERE user_id=? AND kind=? AND resource_id=? AND team_id=?',key).fetchone()
            if not draft or data.get('version')!=draft['version']: raise HTTPException(409,'云草稿版本已变化，请重新读取')
            db.execute("UPDATE editor_drafts SET title='',body='',base='',deleted=1,version=version+1,updated_at=? WHERE user_id=? AND kind=? AND resource_id=? AND team_id=?",(int(time.time()),*key))
            return {'version':draft['version']+1}
