"""Team-scoped attachments, discussions, tasks and versioned publication reviews."""
import re
import time
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from storage import connect


def register_team_workflow(app,db_path,api):
    with connect(db_path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS team_attachments(
          id INTEGER PRIMARY KEY,team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
          document_id INTEGER NOT NULL REFERENCES team_documents(id) ON DELETE CASCADE,
          name TEXT NOT NULL,mime TEXT NOT NULL,content BLOB NOT NULL,created_by INTEGER NOT NULL REFERENCES users(id),created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS team_comments(
          id INTEGER PRIMARY KEY,team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
          document_id INTEGER NOT NULL REFERENCES team_documents(id) ON DELETE CASCADE,
          user_id INTEGER NOT NULL REFERENCES users(id),body TEXT NOT NULL,quote TEXT NOT NULL,
          document_version INTEGER NOT NULL,assignee INTEGER REFERENCES users(id),done INTEGER NOT NULL DEFAULT 0,
          version INTEGER NOT NULL DEFAULT 1,created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS team_mentions(
          comment_id INTEGER NOT NULL REFERENCES team_comments(id) ON DELETE CASCADE,user_id INTEGER NOT NULL REFERENCES users(id),
          PRIMARY KEY(comment_id,user_id));
        CREATE TABLE IF NOT EXISTS team_reviews(
          id INTEGER PRIMARY KEY,team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
          document_id INTEGER NOT NULL REFERENCES team_documents(id) ON DELETE CASCADE,
          document_version INTEGER NOT NULL,status TEXT NOT NULL,submitted_by INTEGER NOT NULL REFERENCES users(id),
          reviewer INTEGER REFERENCES users(id),comment TEXT NOT NULL DEFAULT '',created_at INTEGER NOT NULL,reviewed_at INTEGER);
        CREATE INDEX IF NOT EXISTS ix_team_comments_doc ON team_comments(team_id,document_id,id);
        CREATE INDEX IF NOT EXISTS ix_team_reviews_doc ON team_reviews(team_id,document_id,id);
        ''')

    def access(db,request,tid,did=None,form=None,roles=None):
        user,csrf=api['current_user'](db,request)
        if not user: raise HTTPException(401,'请先登录')
        member=db.execute('SELECT t.*,m.role FROM teams t JOIN team_members m ON m.team_id=t.id WHERE t.id=? AND m.user_id=?',(tid,user['id'])).fetchone()
        if not member: raise HTTPException(404,'团队不可用')
        if roles and member['role'] not in roles: raise HTTPException(403,'当前角色无权执行此操作')
        if form is not None: api['require_csrf'](form,csrf)
        doc=db.execute('SELECT * FROM team_documents WHERE id=? AND team_id=?',(did,tid)).fetchone() if did else None
        if did and not doc: raise HTTPException(404)
        return user,csrf,member,doc

    def route(tid,did): return f'/teams/{tid}/documents/{did}/collaborate'
    def text(form,key,limit,required=False):
        value=str(form.get(key,'')).strip()
        if len(value)>limit or (required and not value): raise HTTPException(400,'内容为空或过长')
        return value
    def event(db,tid,user,action,detail):
        db.execute('INSERT INTO team_events(team_id,actor_id,action,detail,created_at) VALUES(?,?,?,?,?)',(tid,user['id'],action,detail,int(time.time())))

    @app.get('/teams/{tid}/documents/{did}/collaborate')
    def workspace(request:Request,tid:int,did:int,page_number:int=1):
        if not 1<=page_number<=10000: raise HTTPException(400)
        with api['database'](db_path) as db:
            user,csrf,team,doc=access(db,request,tid,did)
            comments=db.execute('''SELECT c.*,u.username,a.username AS assigned_name FROM team_comments c JOIN users u ON u.id=c.user_id
                LEFT JOIN users a ON a.id=c.assignee WHERE c.team_id=? AND c.document_id=? ORDER BY c.id DESC LIMIT 21 OFFSET ?''',(tid,did,(page_number-1)*20)).fetchall()
            reviews=db.execute('''SELECT r.*,u.username FROM team_reviews r JOIN users u ON u.id=r.submitted_by WHERE team_id=? AND document_id=? ORDER BY id DESC LIMIT 20''',(tid,did)).fetchall()
            files=db.execute('SELECT id,name,mime,length(content) AS size FROM team_attachments WHERE team_id=? AND document_id=? ORDER BY id DESC',(tid,did)).fetchall()
            members=db.execute('SELECT u.id,u.username FROM users u JOIN team_members m ON m.user_id=u.id WHERE m.team_id=? AND u.active=1',(tid,)).fetchall()
            return api['page'](request,'team_workflow.html',user=user,csrf=csrf,categories=api['categories'](db,user),team=team,doc=doc,comments=comments[:20],reviews=reviews,files=files,members=members,has_more=len(comments)>20,page_number=page_number,base=route(tid,did))

    @app.post('/teams/{tid}/documents/{did}/comments')
    async def comment(request:Request,tid:int,did:int):
        form=api['form_values'](await request.body())
        with api['database'](db_path) as db:
            db.execute('BEGIN IMMEDIATE'); user,_,_,doc=access(db,request,tid,did,form)
            if form.get('document_version')!=str(doc['version']): raise HTTPException(409,'文档已更新，请刷新后重新选择段落')
            body,quote=text(form,'body',4000,True),text(form,'quote',2000)
            assignee=None
            if form.get('assignee'):
                member=db.execute('SELECT m.user_id FROM team_members m JOIN users u ON u.id=m.user_id WHERE m.team_id=? AND m.user_id=? AND u.active=1',(tid,form['assignee'])).fetchone()
                if not member: raise HTTPException(400,'任务负责人不是当前成员')
                assignee=member[0]
            cid=db.execute('''INSERT INTO team_comments(team_id,document_id,user_id,body,quote,document_version,assignee,created_at) VALUES(?,?,?,?,?,?,?,?)''',
                           (tid,did,user['id'],body,quote,doc['version'],assignee,int(time.time()))).lastrowid
            for name in set(re.findall(r'@([\w.-]{1,64})',body)):
                target=db.execute('SELECT u.id FROM users u JOIN team_members m ON m.user_id=u.id WHERE m.team_id=? AND u.username=? AND u.active=1',(tid,name)).fetchone()
                if target: db.execute('INSERT OR IGNORE INTO team_mentions VALUES(?,?)',(cid,target[0]))
            if assignee: db.execute('INSERT OR IGNORE INTO team_mentions VALUES(?,?)',(cid,assignee))
            event(db,tid,user,'comment.created',f'在「{doc["title"]}」添加讨论')
        return RedirectResponse(route(tid,did),303)

    @app.post('/teams/{tid}/documents/{did}/comments/{cid}/toggle')
    async def toggle(request:Request,tid:int,did:int,cid:int):
        form=api['form_values'](await request.body())
        with api['database'](db_path) as db:
            db.execute('BEGIN IMMEDIATE');user,_,team,_=access(db,request,tid,did,form)
            item=db.execute('SELECT * FROM team_comments WHERE id=? AND team_id=? AND document_id=?',(cid,tid,did)).fetchone()
            if not item: raise HTTPException(404)
            if user['id'] not in {item['user_id'],item['assignee']} and team['role'] not in {'owner','admin'}: raise HTTPException(403)
            if str(item['version'])!=form.get('version'): raise HTTPException(409,'任务状态已更新，请刷新')
            db.execute('UPDATE team_comments SET done=1-done,version=version+1 WHERE id=?',(cid,))
        return RedirectResponse(route(tid,did),303)

    @app.post('/teams/{tid}/documents/{did}/attachments')
    async def upload(request:Request,tid:int,did:int):
        with api['database'](db_path) as db:
            access(db,request,tid,did,roles={'owner','admin','editor'})
        form=await request.form(max_files=1,max_fields=3)
        try:
            file=form.get('file')
            if not hasattr(file,'read'): raise HTTPException(400,'请选择附件')
            body=await file.read(4*1024*1024+1)
            filename=str(file.filename or '附件')
        finally:
            await form.close()
        with api['database'](db_path) as db:
            db.execute('BEGIN IMMEDIATE');user,_,_,_=access(db,request,tid,did,form,{'owner','admin','editor'})
            if len(body)>4*1024*1024: raise HTTPException(413,'附件不得超过 4 MB')
            mime=next((mime for prefix,mime in [(b'\x89PNG\r\n\x1a\n','image/png'),(b'\xff\xd8\xff','image/jpeg'),(b'GIF8','image/gif'),(b'%PDF-','application/pdf')] if body.startswith(prefix)),None)
            if not mime and body.startswith(b'RIFF') and body[8:12]==b'WEBP': mime='image/webp'
            if not mime: raise HTTPException(400,'支持 PNG、JPEG、GIF、WebP 和 PDF')
            from pdf_notes import team_storage_usage
            used=team_storage_usage(db, tid)
            if used[0]+len(body)>100*1024*1024 or used[1]>=500: raise HTTPException(409,'团队附件已达上限（100 MB / 500 个）')
            name=filename.replace('/','_').replace('\\','_')[:160]
            db.execute('INSERT INTO team_attachments(team_id,document_id,name,mime,content,created_by,created_at) VALUES(?,?,?,?,?,?,?)',(tid,did,name,mime,body,user['id'],int(time.time())))
        return RedirectResponse(route(tid,did),303)

    @app.get('/api/teams/{tid}/attachments/{aid}')
    def attachment(request:Request,tid:int,aid:int):
        with api['database'](db_path) as db:
            access(db,request,tid)
            file=db.execute('SELECT * FROM team_attachments WHERE id=? AND team_id=?',(aid,tid)).fetchone()
            if not file: raise HTTPException(404)
            from urllib.parse import quote
            return Response(file['content'],media_type=file['mime'],headers={'Content-Disposition':f"{'attachment' if file['mime']=='application/pdf' else 'inline'}; filename*=UTF-8''{quote(file['name'])}", 'Cache-Control':'no-store'})

    @app.post('/teams/{tid}/documents/{did}/reviews')
    async def submit_review(request:Request,tid:int,did:int):
        form=api['form_values'](await request.body())
        with api['database'](db_path) as db:
            db.execute('BEGIN IMMEDIATE');user,_,_,doc=access(db,request,tid,did,form,{'owner','admin','editor'})
            if str(doc['version'])!=form.get('version'): raise HTTPException(409,'文档已更新，请刷新后提交')
            if not db.execute("SELECT 1 FROM team_reviews WHERE document_id=? AND document_version=? AND status='pending'",(did,doc['version'])).fetchone():
                db.execute("UPDATE team_reviews SET status='superseded' WHERE document_id=? AND status='pending'",(did,))
                db.execute('INSERT INTO team_reviews(team_id,document_id,document_version,status,submitted_by,created_at) VALUES(?,?,?,?,?,?)',(tid,did,doc['version'],'pending',user['id'],int(time.time())))
                event(db,tid,user,'review.submitted',f'提交「{doc["title"]}」v{doc["version"]} 审核')
        return RedirectResponse(route(tid,did),303)

    @app.post('/teams/{tid}/documents/{did}/reviews/{rid}')
    async def review(request:Request,tid:int,did:int,rid:int):
        form=api['form_values'](await request.body())
        with api['database'](db_path) as db:
            db.execute('BEGIN IMMEDIATE');user,_,_,doc=access(db,request,tid,did,form,{'owner','admin'})
            item=db.execute('SELECT * FROM team_reviews WHERE id=? AND team_id=? AND document_id=?',(rid,tid,did)).fetchone()
            if not item: raise HTTPException(404)
            if item['status']!='pending' or doc['version']!=item['document_version']: raise HTTPException(409,'审核状态或正文已变化，请重新提审最新版本')
            action=form.get('action')
            if action not in {'approved','rejected'}: raise HTTPException(400)
            db.execute('UPDATE team_reviews SET status=?,reviewer=?,comment=?,reviewed_at=? WHERE id=?',(action,user['id'],text(form,'comment',2000),int(time.time()),rid))
            event(db,tid,user,'review.'+action,f'审核「{doc["title"]}」v{doc["version"]}')
        return RedirectResponse(route(tid,did),303)

    @app.get('/teams/{tid}/documents/{did}/published')
    def published(request:Request,tid:int,did:int):
        with api['database'](db_path) as db:
            user,csrf,team,doc=access(db,request,tid,did)
            item=db.execute("SELECT * FROM team_reviews WHERE team_id=? AND document_id=? AND status='approved' ORDER BY id DESC LIMIT 1",(tid,did)).fetchone()
            if not item: raise HTTPException(404,'尚无已发布版本')
            snapshot=db.execute('SELECT * FROM team_document_revisions WHERE document_id=? AND version=?',(did,item['document_version'])).fetchone()
            from teams import rendered
            return api['page'](request,'team_published.html',user=user,csrf=csrf,categories=api['categories'](db,user),team=team,doc=snapshot,html=rendered(snapshot['body'],team_id=tid),base=route(tid,did))

    @app.get('/team-inbox')
    def inbox(request:Request,page_number:int=1):
        if not 1<=page_number<=10000: raise HTTPException(400)
        with api['database'](db_path) as db:
            user,csrf=api['current_user'](db,request)
            if not user: return RedirectResponse('/login?next=/team-inbox',303)
            rows=db.execute('''SELECT c.*,d.title,t.name FROM team_mentions x JOIN team_comments c ON c.id=x.comment_id
                JOIN team_members m ON m.team_id=c.team_id AND m.user_id=x.user_id JOIN team_documents d ON d.id=c.document_id JOIN teams t ON t.id=c.team_id
                WHERE x.user_id=? ORDER BY c.id DESC LIMIT 21 OFFSET ?''',(user['id'],(page_number-1)*20)).fetchall()
            return api['page'](request,'team_inbox.html',user=user,csrf=csrf,categories=api['categories'](db,user),items=rows[:20],page_number=page_number,has_more=len(rows)>20)
