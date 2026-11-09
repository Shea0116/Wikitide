"""Account-scoped reading, annotations and spaced review. Never updates note bodies."""
import time
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from storage import can_view, connect, visible_where
from explore_search import split_article


def register_personal(app, db_path, api):
    database, current_user, page = api['database'], api['current_user'], api['page']
    with connect(db_path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS reading_items (
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
          folder TEXT NOT NULL DEFAULT '', later INTEGER NOT NULL DEFAULT 0,
          completed INTEGER NOT NULL DEFAULT 0, ordinal INTEGER NOT NULL DEFAULT 0,
          updated_at INTEGER NOT NULL, PRIMARY KEY(user_id,note_id));
        CREATE TABLE IF NOT EXISTS annotations (
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
          quote TEXT NOT NULL, comment TEXT NOT NULL, body_sha TEXT NOT NULL, created_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_annotations_owner ON annotations(user_id,note_id);
        CREATE TABLE IF NOT EXISTS review_cards (
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
          question TEXT NOT NULL, answer TEXT NOT NULL, anchor TEXT NOT NULL DEFAULT '',
          body_sha TEXT NOT NULL, due_at INTEGER NOT NULL, interval_days INTEGER NOT NULL DEFAULT 0,
          mistakes INTEGER NOT NULL DEFAULT 0, reviews INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1);
        CREATE INDEX IF NOT EXISTS ix_review_due ON review_cards(user_id,due_at);
        ''')
        if 'last_grade' not in {row['name'] for row in db.execute('PRAGMA table_info(review_cards)')}:
            db.execute("ALTER TABLE review_cards ADD COLUMN last_grade TEXT NOT NULL DEFAULT ''")

    def account(db, request, form=None):
        user, csrf = current_user(db, request)
        if not user:
            raise HTTPException(401, '请先登录')
        if form is not None:
            api['require_csrf'](form, csrf)
        return user, csrf

    def note_for(db, user, note_id):
        note = db.execute('SELECT * FROM notes WHERE id=?', (note_id,)).fetchone()
        if note is None or not can_view(db, note, user):
            raise HTTPException(404, '笔记不可用')
        return note

    def text(form, key, maximum, required=False):
        value = form.get(key, '').strip()
        if len(value) > maximum or (required and not value):
            raise HTTPException(400, '内容为空或超过长度限制')
        return value

    @app.get('/library')
    def library(request: Request, tab: str = 'all', q: str = '', folder: str = '', page_number: int = 1):
        if tab not in {'all', 'bookmarks', 'recent', 'later', 'completed', 'annotations'} or not 1 <= page_number <= 10000 or len(q) > 100 or len(folder) > 80:
            raise HTTPException(400, '筛选条件无效')
        with database(db_path) as db:
            user, csrf = current_user(db, request)
            if not user:
                return RedirectResponse('/login?next=/library', 303)
            access, params = visible_where(user)
            conditions = [access, '(r.note_id IS NOT NULL OR i.note_id IS NOT NULL OR EXISTS(SELECT 1 FROM annotations a WHERE a.note_id=notes.id AND a.user_id=?))']
            params = (*params, user['id'])
            filters = {'bookmarks': 'r.bookmarked=1', 'recent': 'r.read_at>0', 'later': 'i.later=1', 'completed': 'i.completed=1',
                       'annotations': 'EXISTS(SELECT 1 FROM annotations a WHERE a.note_id=notes.id AND a.user_id=i.user_id)'}
            if tab in filters:
                conditions.append(filters[tab])
            if q:
                conditions.append('(instr(lower(notes.title),lower(?))>0 OR instr(lower(notes.summary),lower(?))>0)')
                params += (q, q)
            if folder:
                conditions.append('i.folder=?'); params += (folder,)
            joined = 'FROM notes LEFT JOIN reading r ON r.note_id=notes.id AND r.user_id=? LEFT JOIN reading_items i ON i.note_id=notes.id AND i.user_id=?'
            where = ' AND '.join(conditions)
            args = (user['id'], user['id'], *params)
            total = db.execute(f'SELECT count(*) {joined} WHERE {where}', args).fetchone()[0]
            rows = db.execute(f'''SELECT notes.id,notes.title,notes.category,r.position,r.bookmarked,i.folder,i.completed,i.later,i.ordinal
                {joined} WHERE {where} ORDER BY {'coalesce(i.ordinal,0),' if folder else ''}
                max(coalesce(r.read_at,0),coalesce(i.updated_at,0)) DESC,notes.id DESC LIMIT 20 OFFSET ?''', (*args, (page_number - 1) * 20)).fetchall()
            folders = db.execute(f'''SELECT DISTINCT i.folder FROM reading_items i JOIN notes ON notes.id=i.note_id
                WHERE i.user_id=? AND i.folder!='' AND {access} ORDER BY i.folder''', (user['id'], *visible_where(user)[1])).fetchall()
            annotations=[]
            if tab=='annotations':
                annotation_where=access;annotation_args=(user['id'],*visible_where(user)[1])
                if q:
                    annotation_where+=' AND (instr(lower(notes.title),lower(?))>0 OR instr(lower(a.quote),lower(?))>0 OR instr(lower(a.comment),lower(?))>0)'
                    annotation_args+=(q,q,q)
                if folder:
                    annotation_where+=' AND EXISTS(SELECT 1 FROM reading_items ri WHERE ri.user_id=a.user_id AND ri.note_id=notes.id AND ri.folder=?)'
                    annotation_args+=(folder,)
                annotation_join=f'FROM annotations a JOIN notes ON notes.id=a.note_id WHERE a.user_id=? AND {annotation_where}'
                annotations=db.execute(f'SELECT a.*,notes.title,notes.body_sha AS current_sha {annotation_join} ORDER BY a.id DESC LIMIT 20 OFFSET ?',(*annotation_args,(page_number-1)*20)).fetchall()
                total=db.execute(f'SELECT count(*) {annotation_join}',annotation_args).fetchone()[0]
            return page(request, 'library.html', user=user, csrf=csrf, categories=api['categories'](db, user), notes=rows,
                        folders=folders, annotations=annotations, tab=tab, q=q, folder=folder, total=total, page_number=page_number,
                        previous='?' + urlencode(dict(tab=tab,q=q,folder=folder,page_number=page_number-1)),
                        following='?' + urlencode(dict(tab=tab,q=q,folder=folder,page_number=page_number+1)), has_more=total > page_number*20)

    @app.post('/n/{note_id}/organize')
    async def organize(request: Request, note_id: int):
        form = api['form_values'](await request.body())
        with database(db_path) as db:
            user, _ = account(db, request, form); note_for(db, user, note_id)
            folder = text(form, 'folder', 80)
            try: ordinal = int(form.get('ordinal', '0'))
            except ValueError: raise HTTPException(400, '学习顺序无效')
            if not 0 <= ordinal <= 10000: raise HTTPException(400, '学习顺序无效')
            db.execute('''INSERT INTO reading_items VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id,note_id) DO UPDATE SET
                folder=excluded.folder,later=excluded.later,completed=excluded.completed,ordinal=excluded.ordinal,updated_at=excluded.updated_at''',
                       (user['id'], note_id, folder, int(form.get('later')=='1'), int(form.get('completed')=='1'), ordinal, int(time.time())))
        return RedirectResponse('/library', 303)

    @app.get('/api/reading/{note_id}')
    def reading_details(request: Request, note_id: int):
        with database(db_path) as db:
            user, _ = account(db, request); note = note_for(db, user, note_id)
            item = db.execute('SELECT * FROM reading_items WHERE user_id=? AND note_id=?', (user['id'],note_id)).fetchone()
            return dict(item=dict(item) if item else {}, annotations=[dict(row, stale=row['body_sha']!=note['body_sha']) for row in db.execute(
                'SELECT * FROM annotations WHERE user_id=? AND note_id=? ORDER BY id DESC', (user['id'],note_id))])

    @app.post('/n/{note_id}/annotate')
    async def annotate(request: Request, note_id: int):
        form = api['form_values'](await request.body())
        with database(db_path) as db:
            user, _ = account(db, request, form); note = note_for(db, user, note_id)
            if form.get('body_sha') != note['body_sha']: raise HTTPException(409, '原文已更新，请刷新后重新选择文字')
            quote, comment = text(form,'quote',2000,True), text(form,'comment',4000)
            if db.execute('SELECT count(*) FROM annotations WHERE user_id=?', (user['id'],)).fetchone()[0] >= 5000:
                raise HTTPException(409, '批注已达 5000 条，请先整理')
            db.execute('INSERT INTO annotations(user_id,note_id,quote,comment,body_sha,created_at) VALUES(?,?,?,?,?,?)',
                       (user['id'],note_id,quote,comment,note['body_sha'],int(time.time())))
        return RedirectResponse(f'/n/{note_id}#personal-reading',303)

    @app.post('/annotations/{annotation_id}/delete')
    async def delete_annotation(request: Request, annotation_id: int):
        form = api['form_values'](await request.body())
        with database(db_path) as db:
            user, _ = account(db,request,form)
            db.execute('DELETE FROM annotations WHERE id=? AND user_id=?',(annotation_id,user['id']))
        return RedirectResponse('/library?tab=annotations',303)

    @app.get('/learn')
    def learn(request: Request, note_id: int | None = None, filter: str = 'due', page_number: int = 1):
        if filter not in {'due','all','mistakes'} or not 1 <= page_number <= 10000: raise HTTPException(400)
        with database(db_path) as db:
            user, csrf = current_user(db,request)
            if not user: return RedirectResponse('/login?next=/learn',303)
            note = note_for(db,user,note_id) if note_id else None
            suggestions = []
            if note:
                for chunk in split_article(note['body'])[:12]:
                    if len(chunk['text'].strip()) < 20: continue
                    suggestions.append(dict(question=f"如何理解“{chunk['section'] or note['title']}”？", answer=chunk['text'][:2000], anchor=chunk['anchor']))
            access, params = visible_where(user)
            condition = {'due':' AND c.due_at<=?', 'mistakes':' AND c.mistakes>0', 'all':''}[filter]
            args = (user['id'], *params, *((int(time.time()),) if filter=='due' else ()))
            joined = f'FROM review_cards c JOIN notes ON notes.id=c.note_id WHERE c.user_id=? AND {access}{condition}'
            total = db.execute(f'SELECT count(*) {joined}',args).fetchone()[0]
            cards = db.execute(f'SELECT c.*,notes.title,notes.body_sha AS current_sha {joined} ORDER BY c.due_at,c.id LIMIT 20 OFFSET ?',(*args,(page_number-1)*20)).fetchall()
            checkpoints = []
            if getattr(app.state, 'learning_enabled', False):
                import learning_service
                for row in db.execute('SELECT id FROM learning_sessions WHERE user_id=? ORDER BY updated_at DESC LIMIT 12', (user['id'],)).fetchall():
                    try:
                        checkpoints.append(learning_service.present(db, user['id'], row['id']))
                    except HTTPException:
                        continue
            return page(request,'learn.html',user=user,csrf=csrf,categories=api['categories'](db,user),note=note,suggestions=suggestions,
                        cards=cards,checkpoints=checkpoints,filter=filter,total=total,page_number=page_number,has_more=total>page_number*20)

    @app.post('/learn/cards')
    async def create_card(request: Request):
        form = api['form_values'](await request.body())
        with database(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            user, _ = account(db,request,form)
            try: note_id=int(form.get('note_id',''))
            except ValueError: raise HTTPException(400)
            note = note_for(db,user,note_id)
            if form.get('body_sha') != note['body_sha']: raise HTTPException(409,'原文已更新，请重新生成题目')
            question, answer, anchor = text(form,'question',500,True),text(form,'answer',5000,True),text(form,'anchor',500)
            if db.execute('SELECT 1 FROM review_cards WHERE user_id=? AND note_id=? AND question=? AND answer=? AND body_sha=?',(user['id'],note_id,question,answer,note['body_sha'])).fetchone():
                return RedirectResponse('/learn?filter=all',303)
            if db.execute('SELECT count(*) FROM review_cards WHERE user_id=?',(user['id'],)).fetchone()[0]>=5000: raise HTTPException(409,'复习卡片已达上限')
            db.execute('''INSERT INTO review_cards(user_id,note_id,question,answer,anchor,body_sha,due_at) VALUES(?,?,?,?,?,?,?)''',
                       (user['id'],note_id,question,answer,anchor,note['body_sha'],int(time.time())))
        return RedirectResponse('/learn?filter=all',303)

    @app.post('/learn/cards/{card_id}/review')
    async def review(request: Request, card_id: int):
        form=api['form_values'](await request.body())
        with database(db_path) as db:
            db.execute('BEGIN IMMEDIATE'); user,_=account(db,request,form)
            card=db.execute('SELECT * FROM review_cards WHERE id=? AND user_id=?',(card_id,user['id'])).fetchone()
            if not card: raise HTTPException(404)
            note=note_for(db,user,card['note_id'])
            if card['body_sha'] != note['body_sha']: raise HTTPException(409,'原文已更新，请对照原文更新卡片后复习')
            if form.get('version') != str(card['version']): raise HTTPException(409,'这张卡片已复习，请刷新')
            grade=form.get('grade')
            if grade not in {'again','hard','good','easy'}: raise HTTPException(400)
            days={'again':0,'hard':max(1,card['interval_days']), 'good':max(3,card['interval_days']*2), 'easy':max(7,card['interval_days']*3)}[grade]
            days=min(days,365)
            db.execute('''UPDATE review_cards SET due_at=?,interval_days=?,mistakes=mistakes+?,reviews=reviews+1,last_grade=?,version=version+1 WHERE id=?''',
                       (int(time.time())+(days*86400 if days else 600),days,int(grade=='again'),grade,card_id))
        return RedirectResponse('/learn',303)

    @app.post('/learn/cards/{card_id}/delete')
    async def delete_card(request:Request,card_id:int):
        form=api['form_values'](await request.body())
        with database(db_path) as db:
            user,_=account(db,request,form)
            card=db.execute('SELECT version FROM review_cards WHERE id=? AND user_id=?',(card_id,user['id'])).fetchone()
            if not card: raise HTTPException(404)
            if str(card['version'])!=form.get('version'): raise HTTPException(409,'卡片已更新，请刷新后删除')
            db.execute('DELETE FROM review_cards WHERE id=? AND user_id=? AND version=?',(card_id,user['id'],card['version']))
        return RedirectResponse('/learn?filter=all',303)

    @app.post('/learn/cards/{card_id}/update')
    async def update_card(request: Request, card_id: int):
        form=api['form_values'](await request.body())
        with database(db_path) as db:
            db.execute('BEGIN IMMEDIATE'); user,_=account(db,request,form)
            card=db.execute('SELECT * FROM review_cards WHERE id=? AND user_id=?',(card_id,user['id'])).fetchone()
            if not card: raise HTTPException(404)
            note=note_for(db,user,card['note_id'])
            if form.get('version')!=str(card['version']) or form.get('body_sha')!=note['body_sha']: raise HTTPException(409,'卡片或原文已更新，请刷新')
            db.execute("UPDATE review_cards SET question=?,answer=?,body_sha=?,due_at=?,interval_days=0,last_grade='',version=version+1 WHERE id=?",
                       (text(form,'question',500,True),text(form,'answer',5000,True),note['body_sha'],int(time.time()),card_id))
        return RedirectResponse('/learn',303)
