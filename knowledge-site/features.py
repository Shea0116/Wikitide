"""Editing, discovery and sync management for the permissioned library."""
from __future__ import annotations

import base64
import difflib
import hashlib
import json
import posixpath
import re
import sqlite3
import time
from functools import lru_cache
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from markupsafe import Markup, escape

from storage import can_edit, can_view, digest, save_note, visible_where
from note_renames import rename_note
from version_diff import make_diff


def like(value):
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def category_filter(where, params, category):
    if category:
        where += " AND (notes.category=? OR notes.category LIKE ? ESCAPE '\\')"
        params += (category, like(category) + ' / %')
    return where, params


def highlight(value, term):
    if not term:
        return escape(value)
    return Markup('').join(Markup('<mark>') + escape(part) + Markup('</mark>') if i % 2 else escape(part)
                          for i, part in enumerate(re.split('(' + re.escape(term) + ')', value, flags=re.I)))


def search_notes(db, user, term, category, page_number, scope='all', since=0):
    where, params = category_filter(*visible_where(user), category)
    if scope in {'public', 'private'}:
        where += ' AND notes.visibility=?'; params += (scope,)
    if since:
        where += ' AND notes.updated_at>=?'; params += (since,)
    if not term:
        return [], 0
    pattern = '%' + like(term) + '%'
    where += " AND (notes.title LIKE ? ESCAPE '\\' OR notes.summary LIKE ? ESCAPE '\\' OR notes.body LIKE ? ESCAPE '\\')"
    params += (pattern,) * 3
    total = db.execute(f'SELECT COUNT(*) FROM notes WHERE {where}', params).fetchone()[0]
    rows = db.execute(f"""SELECT * FROM notes WHERE {where} ORDER BY
        CASE WHEN lower(title)=lower(?) THEN 0 WHEN title LIKE ? ESCAPE '\\' THEN 1
        WHEN summary LIKE ? ESCAPE '\\' THEN 2 ELSE 3 END, updated_at DESC, id
        LIMIT 20 OFFSET ?""", (*params, term, pattern, pattern, (page_number - 1) * 20)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        text = re.sub(r'\s+', ' ', row['body'])
        match = re.search(re.escape(term), text, re.I)
        start = max(0, (match.start() if match else 0) - 65)
        fragment = ('…' if start else '') + text[start:start + 210] + ('…' if len(text) > start + 210 else '')
        item['hit_title'] = highlight(row['title'], term)
        item['snippet'] = highlight(fragment, term)
        from explore_search import split_article
        matched = next((chunk for chunk in split_article(row['body']) if term.casefold() in chunk['text'].casefold()), None)
        item['url'] = f"/n/{row['id']}" + ('#' + quote(matched['anchor']) if matched and matched['anchor'] else '')
        result.append(item)
    return result, total


def resolve_wiki(target, note_path, rows):
    target = unquote(target).strip()
    if not target:
        return None
    paths = {row['path']: row['id'] for row in rows}
    name = target if target.lower().endswith('.md') else target + '.md'
    relative = posixpath.normpath(posixpath.join(posixpath.dirname(note_path), name))
    if relative in paths:
        return paths[relative]
    if name in paths:
        return paths[name]
    matches = [row['id'] for row in rows if Path(row['path']).stem.casefold() == Path(target).stem.casefold()]
    return matches[0] if len(matches) == 1 else None


def wiki_tokens(parser, tokens, note_path, rows):
    """Rewrite only prose text, retaining code fences, inline code and fragments."""
    for token in tokens:
        if not token.children:
            continue
        children = []
        depth = 0
        for child in token.children:
            if child.type == 'link_open':
                depth += 1
            if child.type == 'text' and not depth:
                parts = re.split(r'(\[\[[^\]]+\]\])', child.content)
                for part in parts:
                    if part.startswith('[[') and part.endswith(']]'):
                        target, _, label = part[2:-2].partition('|')
                        path, separator, fragment = target.partition('#')
                        note_id = resolve_wiki(path, note_path, rows)
                        if note_id or (not path and separator):
                            href = (f'/n/{note_id}' if note_id else '') + ('#' + quote(fragment) if separator else '')
                            from markdown_it.token import Token
                            opening = Token('link_open', 'a', 1)
                            opening.attrSet('href', href)
                            text = Token('text', '', 0)
                            text.content = label or target
                            children.extend([opening, text, Token('link_close', 'a', -1)])
                            continue
                    from markdown_it.token import Token
                    text = Token('text', '', 0)
                    text.content = part
                    children.append(text)
            else:
                children.append(child)
            if child.type == 'link_close':
                depth -= 1
        token.children = children
    return tokens


@lru_cache(maxsize=1024)
def reference_targets(parser, body):
    links, wikis = [], []
    for token in parser.parse(body):
        depth = 0
        for child in token.children or []:
            if child.type == 'link_open':
                depth += 1
                links.append(child.attrGet('href') or '')
            elif child.type == 'link_close':
                depth -= 1
            elif child.type == 'text' and not depth:
                wikis.extend(match.split('|',1)[0].split('#',1)[0] for match in re.findall(r'\[\[([^\]]+)\]\]',child.content))
    return tuple(links), tuple(wikis)


def linked_note_ids(parser, body, path, rows):
    return resolved_note_ids(reference_targets(parser, body), path, rows)


def resolved_note_ids(targets, path, rows):
    ids = set()
    paths = {row['path']: row['id'] for row in rows}
    links, wikis = targets
    for target in wikis:
        note_id = resolve_wiki(target,path,rows)
        if note_id: ids.add(note_id)
    for href in links:
        parsed = urlsplit(href)
        if parsed.scheme or parsed.netloc:
            continue
        if re.fullmatch(r'/n/\d+', parsed.path):
            ids.add(int(parsed.path.rsplit('/', 1)[1]))
        else:
            target = posixpath.normpath(posixpath.join(posixpath.dirname(path), unquote(parsed.path)))
            if target in paths:
                ids.add(paths[target])
    return ids


def note_extras(db, user, note, parser):
    where, params = visible_where(user)
    rows = db.execute(f'SELECT id,path,title,category,body_sha FROM notes WHERE {where}', params).fetchall()
    indexed = {row['note_id']: row for row in db.execute(f'''SELECT refs.* FROM note_reference_index refs
        JOIN notes ON notes.id=refs.note_id WHERE {where}''', params)}
    backlinks = []
    for row in rows:
        cached = indexed.get(row['id'])
        if not cached or cached['body_sha'] != row['body_sha']:
            body = db.execute('SELECT body FROM notes WHERE id=?',(row['id'],)).fetchone()[0]
            targets = reference_targets(parser,body)
            db.execute('INSERT OR REPLACE INTO note_reference_index VALUES(?,?,?)',(row['id'],row['body_sha'],json.dumps(targets,ensure_ascii=False)))
        else:
            targets = json.loads(cached['targets'])
        if row['id'] != note['id'] and note['id'] in resolved_note_ids(targets,row['path'],rows):
            backlinks.append(row)
    related = [row for row in rows if row['id'] != note['id'] and row['category'] == note['category']][:6]
    reading = db.execute('SELECT * FROM reading WHERE user_id=? AND note_id=?', (user['id'], note['id'])).fetchone() if user else None
    return dict(backlinks=backlinks, related=related, reading=reading)


def personal_notes(db, user, bookmarks=False):
    if not user:
        return []
    where, params = visible_where(user)
    return db.execute(f"""SELECT notes.*,r.position FROM notes JOIN reading r ON r.note_id=notes.id
        WHERE {where} AND r.user_id=? AND {'r.bookmarked=1' if bookmarks else 'r.read_at>0'}
        ORDER BY r.read_at DESC LIMIT 20""", (*params, user['id'])).fetchall()


def record_sha(note):
    return digest(json.dumps([note['body_sha'],note['title'],note['summary'],note['category']], ensure_ascii=False))


def safe_note_path(name):
    if not isinstance(name, str) or len(name) > 500 or '\\' in name or any(ord(c) < 32 for c in name):
        raise HTTPException(400, '笔记路径无效')
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(p.startswith('.') or p in {'skills','tools','scripts','tests','node_modules'} for p in path.parts):
        raise HTTPException(400, '笔记路径无效')
    if path.suffix != '.md' or path.name in {'AGENTS.md','CLAUDE.md'} or any(p.lower() == 'static' for p in path.parts[:-1]):
        raise HTTPException(400, '请选择 Markdown 笔记路径')
    if path.as_posix()!=name or ':' in name:
        raise HTTPException(400, '请使用规范的相对路径')
    return path


def add_note(db, path, body, summary='', category='', visibility='private', actor='本地同步', origin='local'):
    parsed = safe_note_path(path)
    if not isinstance(body, str) or not body.strip() or not isinstance(summary, str) or len(summary) > 2000 or not isinstance(category, str) or len(category) > 240:
        raise HTTPException(400, '正文或分类无效')
    now = int(time.time())
    category = category.strip() or ' / '.join(parsed.parts[:-1]) or '私人笔记'
    cursor = db.execute('INSERT INTO notes(id,path,title,summary,category,visibility,body,body_sha,updated_at) VALUES((SELECT max(value)+1 FROM (SELECT coalesce(max(id),0) AS value FROM notes UNION ALL SELECT coalesce(max(old_id),0) FROM note_redirects)),?,?,?,?,?,?,?,?)',
                        (path,parsed.stem,summary,category,visibility,body,digest(body),now))
    note_id = cursor.lastrowid
    db.execute('INSERT INTO revisions(note_id,body,body_sha,actor,origin,created_at) VALUES(?,?,?,?,?,?)',
               (note_id,body,digest(body),actor,origin,now))
    if origin == 'web':
        db.execute("INSERT INTO edit_events(note_id,base_sha,new_sha,body,origin,status,created_at) VALUES(?,?,?,?,?,'pending',?)",
                   (note_id,'',digest(body),body,'web',now))
    return db.execute('SELECT * FROM notes WHERE id=?', (note_id,)).fetchone()


def sync_summary(db):
    latest = db.execute('SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1').fetchone()
    success = db.execute("SELECT finished_at FROM sync_runs WHERE status='success' ORDER BY id DESC LIMIT 1").fetchone()
    return dict(latest=dict(latest) if latest else None, last_success=success[0] if success else None,
                pending=db.execute("SELECT COUNT(DISTINCT note_id) FROM edit_events WHERE status='pending'").fetchone()[0],
                conflicts=db.execute("SELECT COUNT(*) FROM sync_conflicts WHERE status IN ('open','resolved')").fetchone()[0])


def admin_context(db, q, category, page_number):
    where, params = category_filter('1=1', (), category)
    if q:
        where += " AND (notes.title LIKE ? ESCAPE '\\' OR notes.path LIKE ? ESCAPE '\\')"
        params += ('%' + like(q) + '%',) * 2
    total = db.execute(f'SELECT COUNT(*) FROM notes WHERE {where}', params).fetchone()[0]
    notes = db.execute(f'SELECT * FROM notes WHERE {where} ORDER BY category,title LIMIT 50 OFFSET ?', (*params,(page_number-1)*50)).fetchall()
    conflicts = db.execute("SELECT c.*,n.title,n.path FROM sync_conflicts c JOIN notes n ON n.id=c.note_id WHERE c.status IN ('open','resolved') ORDER BY c.updated_at DESC").fetchall()
    events = db.execute("SELECT e.id,e.status,e.message,n.title,n.path FROM edit_events e JOIN notes n ON n.id=e.note_id WHERE e.status IN ('pending','conflict') ORDER BY e.id DESC LIMIT 30").fetchall()
    return dict(notes=notes,total=total,q=q,category=category,page_number=page_number,has_more=total>page_number*50,
                conflicts=conflicts,events=events,sync=sync_summary(db),
                category_options=[r[0] for r in db.execute('SELECT DISTINCT category FROM notes ORDER BY category')],
                users=db.execute('SELECT id,username,role,active,version FROM users ORDER BY username').fetchall())


def register(app, db_path, content_root, g):
    database, current_user, page = g['database'], g['current_user'], g['page']
    require_admin, require_csrf, form_values = g['require_admin'], g['require_csrf'], g['form_values']
    render, categories, require_sync = g['rendered_markdown'], g['categories'], g['require_sync']

    def authorized_note(db, request, note_id, edit=False):
        user, csrf = current_user(db, request)
        note = db.execute('SELECT * FROM notes WHERE id=?', (note_id,)).fetchone()
        if not note or not (can_edit(db,note,user) if edit else can_view(db,note,user)):
            raise HTTPException(404, '笔记不存在或无权访问')
        return note, user, csrf

    async def json_input(request):
        if len(await request.body()) > g['MAX_BODY']:
            raise HTTPException(413)
        try:
            data = await request.json()
        except ValueError:
            raise HTTPException(400, '请求格式无效')
        if not isinstance(data, dict):
            raise HTTPException(400)
        return data

    @app.post('/n/{note_id}/preview')
    async def preview(request: Request, note_id: int):
        form = form_values(await request.body())
        with database(db_path) as db:
            note,user,csrf = authorized_note(db,request,note_id,True)
            require_csrf(form,csrf)
            copy = dict(note); copy['body'] = form.get('body','')
            if form.get('blocks') == '1':
                return JSONResponse({'blocks': render(db, copy, user, editor_blocks=True)},
                                    headers={'Cache-Control': 'no-store'})
            return HTMLResponse(str(render(db,copy,user)),headers={'Cache-Control':'no-store'})

    @app.get('/n/{note_id}/history')
    def history(request: Request, note_id: int, revision: int = 0, compare: int = 0, page_number: int = 1):
        if not 1 <= page_number <= 10000:
            raise HTTPException(400)
        with database(db_path) as db:
            note,user,csrf = authorized_note(db,request,note_id,True)
            rows = db.execute('SELECT id,actor,origin,created_at,body_sha FROM revisions WHERE note_id=? ORDER BY id DESC LIMIT 31 OFFSET ?', (note_id,(page_number-1)*30)).fetchall()
            compare_options = db.execute('SELECT id,actor,created_at FROM revisions WHERE note_id=? ORDER BY id DESC LIMIT 1000',(note_id,)).fetchall()
            selected = db.execute('SELECT * FROM revisions WHERE note_id=? AND id=?', (note_id,revision)).fetchone() if revision else None
            other = db.execute('SELECT * FROM revisions WHERE note_id=? AND id=?', (note_id,compare)).fetchone() if compare else None
            if (revision and not selected) or (compare and not other):
                raise HTTPException(404)
            comparison = make_diff(selected['body'], other['body'] if other else note['body'],
                                   f'历史版本 #{selected["id"]}', f'历史版本 #{other["id"]}' if other else '当前正文') if selected else None
            diff = comparison['patch'] if comparison else ''
            return page(request,'history.html',user=user,csrf=csrf,categories=categories(db,user),note=note,
                        revisions=rows[:30],selected=selected,compare=compare,compare_options=compare_options,diff=diff,comparison=comparison,editable=True,page_number=page_number,has_more=len(rows)>30)

    @app.get('/n/{note_id}/diff.patch')
    def diff_patch(request: Request, note_id: int, revision: int, compare: int = 0):
        from fastapi.responses import Response
        with database(db_path) as db:
            note, user, csrf = authorized_note(db, request, note_id, True)
            old = db.execute('SELECT * FROM revisions WHERE note_id=? AND id=?', (note_id, revision)).fetchone()
            new = db.execute('SELECT * FROM revisions WHERE note_id=? AND id=?', (note_id, compare)).fetchone() if compare else note
            if old is None or new is None:
                raise HTTPException(404)
            patch = make_diff(old['body'], new['body'])['patch']
            return Response(patch, media_type='text/plain; charset=utf-8', headers={
                'Content-Disposition': f'attachment; filename="note-{note_id}.patch"', 'Cache-Control': 'no-store'})

    @app.post('/n/{note_id}/restore')
    async def restore(request: Request, note_id: int):
        form = form_values(await request.body())
        with database(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            note,user,csrf = authorized_note(db,request,note_id,True)
            require_csrf(form,csrf)
            if form.get('base_sha') != note['body_sha']:
                raise HTTPException(409, '正文已变化，请重新查看后恢复')
            revision = db.execute('SELECT * FROM revisions WHERE note_id=? AND id=?', (note_id,form.get('revision'))).fetchone()
            if not revision:
                raise HTTPException(404)
            save_note(db,note,revision['body'],'web',user['username']+'（恢复版本）')
        return RedirectResponse(f'/n/{note_id}',303)

    @app.post('/n/{note_id}/reading')
    async def reading(request: Request, note_id: int):
        data = await json_input(request)
        with database(db_path) as db:
            note,user,csrf = authorized_note(db,request,note_id)
            if not user:
                raise HTTPException(401)
            require_csrf({'csrf':request.headers.get('X-CSRF-Token','')},csrf)
            try:
                position = float(data.get('position',0))
                if not 0 <= position <= 1:
                    raise ValueError()
            except (ValueError,TypeError):
                raise HTTPException(400)
            db.execute('INSERT INTO reading(user_id,note_id,position,read_at) VALUES(?,?,?,?) ON CONFLICT(user_id,note_id) DO UPDATE SET position=excluded.position,read_at=excluded.read_at',
                       (user['id'],note_id,position,int(time.time())))
        return {'ok':True}

    @app.post('/n/{note_id}/bookmark')
    async def bookmark(request: Request, note_id: int):
        form = form_values(await request.body())
        with database(db_path) as db:
            note,user,csrf = authorized_note(db,request,note_id)
            if not user: raise HTTPException(401)
            require_csrf(form,csrf)
            db.execute('INSERT INTO reading(user_id,note_id,bookmarked) VALUES(?,?,1) ON CONFLICT(user_id,note_id) DO UPDATE SET bookmarked=1-reading.bookmarked', (user['id'],note_id))
        return RedirectResponse(f'/n/{note_id}',303)

    @app.get('/new')
    def new_page(request: Request):
        with database(db_path) as db:
            user,csrf = current_user(db,request); require_admin(user)
            return page(request,'new.html',user=user,csrf=csrf,categories=categories(db,user),error='',values={})

    @app.post('/new')
    async def new_note(request: Request):
        form = form_values(await request.body())
        with database(db_path) as db:
            user,csrf = current_user(db,request); require_admin(user); require_csrf(form,csrf)
            try:
                path = form.get('path','').strip(); parsed = safe_note_path(path)
                if parsed.parts[0] not in {'Backend','Frontend','Developer'}:
                    raise HTTPException(400,'新笔记请选择 Backend/、Frontend/ 或 Developer/ 路径')
                note = add_note(db,path,form.get('body',''),form.get('summary',''),form.get('category',''),
                                'public' if parsed.parts[0] in {'Backend','Frontend'} else 'private',user['username'],'web')
            except (sqlite3.IntegrityError,HTTPException) as exc:
                return page(request,'new.html',status_code=400,user=user,csrf=csrf,categories=categories(db,user),values=form,error=exc.detail if isinstance(exc,HTTPException) else '路径已经存在')
        return RedirectResponse(f"/n/{note['id']}",303)

    @app.post('/n/{note_id}/metadata')
    async def metadata(request: Request, note_id: int):
        form = form_values(await request.body())
        with database(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            note,user,csrf = authorized_note(db,request,note_id,True); require_admin(user); require_csrf(form,csrf)
            if form.get('record_sha') != record_sha(note): raise HTTPException(409,'笔记已变化，请重新打开')
            if len(form.get('summary',''))>2000 or not 0 < len(form.get('category',''))<=240: raise HTTPException(400)
            db.execute('UPDATE notes SET summary=?,category=?,updated_at=? WHERE id=?',(form['summary'],form['category'],int(time.time()),note_id))
            db.execute("INSERT INTO edit_events(note_id,base_sha,new_sha,body,origin,status,created_at) VALUES(?,?,?,?,?,'pending',?)",
                       (note_id,note['body_sha'],note['body_sha'],note['body'],'web',int(time.time())))
        return RedirectResponse(f'/n/{note_id}',303)

    @app.post('/admin/bulk')
    async def bulk(request: Request):
        form = form_values(await request.body())
        with database(db_path) as db:
            user,csrf = current_user(db,request); require_admin(user); require_csrf(form,csrf)
            target = db.execute("SELECT * FROM users WHERE id=? AND active=1 AND role!='admin'",(form.get('user_id'),)).fetchone()
            if not target: raise HTTPException(400,'请选择有效账户')
            if not form.get('category'): raise HTTPException(400,'请选择分类')
            mode = form.get('mode')
            if mode not in {'view','edit','revoke'} or (mode=='edit' and target['role']!='editor'): raise HTTPException(400,'授权类型无效')
            where,params = category_filter('1=1',(),form['category'])
            rows = db.execute(f'SELECT id FROM notes WHERE {where}',params).fetchall()
            for row in rows:
                db.execute('DELETE FROM grants WHERE user_id=? AND note_id=?',(target['id'],row['id']))
                if mode!='revoke': db.execute('INSERT INTO grants VALUES(?,?,1,?)',(target['id'],row['id'],int(mode=='edit')))
        return RedirectResponse('/admin',303)

    @app.get('/admin/sync/status')
    def status(request: Request):
        with database(db_path) as db:
            user,_ = current_user(db,request); require_admin(user)
            return JSONResponse(sync_summary(db),headers={'Cache-Control':'no-store'})

    @app.get('/admin/conflicts/{conflict_id}')
    def conflict_page(request: Request, conflict_id: int):
        with database(db_path) as db:
            user,csrf = current_user(db,request); require_admin(user)
            conflict = db.execute('SELECT * FROM sync_conflicts WHERE id=?',(conflict_id,)).fetchone()
            if not conflict: raise HTTPException(404)
            note = db.execute('SELECT * FROM notes WHERE id=?',(conflict['note_id'],)).fetchone()
            diff = '\n'.join(difflib.unified_diff(conflict['local_body'].splitlines(),note['body'].splitlines(),fromfile='本地',tofile='网站',lineterm=''))
            return page(request,'conflict.html',user=user,csrf=csrf,categories=categories(db,user),conflict=conflict,note=note,diff=diff,server_record_sha=record_sha(note))

    @app.post('/admin/conflicts/{conflict_id}')
    async def resolve(request: Request, conflict_id: int):
        form = form_values(await request.body())
        with database(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            user,csrf = current_user(db,request); require_admin(user); require_csrf(form,csrf)
            conflict = db.execute("SELECT * FROM sync_conflicts WHERE id=? AND status='open'",(conflict_id,)).fetchone()
            if not conflict: raise HTTPException(409,'冲突状态已变化')
            note = db.execute('SELECT * FROM notes WHERE id=?',(conflict['note_id'],)).fetchone()
            if form.get('server_sha')!=record_sha(note) or form.get('local_sha')!=conflict['local_sha']:
                raise HTTPException(409,'版本已变化，请重新查看对比')
            action = form.get('action')
            body = note['body'] if action=='server' else conflict['local_body'] if action=='local' else form.get('body','') if action=='merge' else ''
            if not body.strip(): raise HTTPException(400,'合并内容不能为空')
            save_note(db,note,body,'web',user['username']+'（解决冲突）')
            current = db.execute('SELECT * FROM notes WHERE id=?',(note['id'],)).fetchone()
            db.execute("UPDATE sync_conflicts SET status='resolved',resolved_body=?,resolved_sha=?,updated_at=? WHERE id=?",(body,record_sha(current),int(time.time()),conflict_id))
        return RedirectResponse('/admin',303)

    @app.post('/api/sync/run')
    async def run_report(request: Request):
        require_sync(request); data = await json_input(request)
        with database(db_path) as db:
            status = data.get('status')
            if status not in {'running','success','failed'}: raise HTTPException(400)
            if status=='running':
                cursor = db.execute("INSERT INTO sync_runs(started_at,status) VALUES(?,'running')",(int(time.time()),))
                return {'id':cursor.lastrowid}
            counts = [data.get(k,0) for k in ('uploaded','downloaded','conflicts')]
            if any(not isinstance(v,int) or v<0 for v in counts): raise HTTPException(400)
            db.execute('UPDATE sync_runs SET finished_at=?,status=?,message=?,uploaded=?,downloaded=?,conflicts=? WHERE id=?',
                       (int(time.time()),status,str(data.get('message',''))[:500],*counts,data.get('id')))
        return {'ok':True}

    @app.get('/api/sync/v2/state')
    def v2_state(request: Request):
        require_sync(request)
        with database(db_path) as db:
            notes = [dict(row) for row in db.execute('SELECT id,path,title,summary,category,visibility,body_sha FROM notes ORDER BY path')]
            for note in notes:
                note['sha'] = record_sha(note)
                first = db.execute('SELECT origin FROM revisions WHERE note_id=? ORDER BY id LIMIT 1',(note['id'],)).fetchone()
                note['web_created'] = bool(first and first[0]=='web')
            conflicts = [dict(row) for row in db.execute("SELECT c.id,n.path,c.local_sha,c.status,c.resolved_sha FROM sync_conflicts c JOIN notes n ON n.id=c.note_id WHERE c.status IN ('open','resolved')")]
            moves=[]
            for move in db.execute('SELECT * FROM note_moves ORDER BY id'):
                audit=json.loads(move['metadata_json'])
                moves.append(dict(from_path=move['from_path'],path=move['to_path'],id=move['note_id'],
                                  base_sha=audit.get('base_sha'),sha=audit.get('sha')))
            return {'notes':notes,'conflicts':conflicts,'capabilities':['rename','rename-v2'],'moves':moves}

    @app.post('/api/sync/v2/rename')
    async def v2_rename(request: Request):
        require_sync(request); data=await json_input(request)
        with database(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            note=rename_note(db,data,safe_note_path,record_sha,save_note)
            if note is None: return JSONResponse({'conflict':True},409)
            return note

    @app.get('/api/sync/v2/note/{note_id}')
    def v2_note(request: Request, note_id: int):
        require_sync(request)
        with database(db_path) as db:
            note = db.execute('SELECT * FROM notes WHERE id=?',(note_id,)).fetchone()
            if not note: raise HTTPException(404)
            item = dict(note); item['sha']=record_sha(note)
            return item

    @app.post('/api/sync/v2/note')
    async def v2_push(request: Request):
        require_sync(request); data=await json_input(request)
        path=data.get('path'); parsed=safe_note_path(path)
        body=data.get('body'); summary=data.get('summary',''); category=data.get('category','')
        if not isinstance(body,str) or not body.strip() or not isinstance(summary,str) or len(summary)>2000 or not isinstance(category,str) or not 0<len(category)<=240:
            raise HTTPException(400)
        with database(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            note=db.execute('SELECT * FROM notes WHERE path=?',(path,)).fetchone()
            if not note:
                if data.get('base_sha')!='' or parsed.parts[0] not in {'Backend','Frontend'}: raise HTTPException(409,'新增本地笔记仅限公开目录')
                note=add_note(db,path,body,summary,category,'public')
            else:
                if db.execute("SELECT 1 FROM sync_conflicts WHERE note_id=? AND status IN ('open','resolved')",(note['id'],)).fetchone():
                    return JSONResponse({'conflict':True},409)
                if data.get('base_sha')!=record_sha(note): return JSONResponse({'conflict':True},409)
                save_note(db,note,body,'local')
                db.execute('UPDATE notes SET title=?,summary=?,category=?,updated_at=? WHERE id=?',(parsed.stem,summary,category,int(time.time()),note['id']))
                note=db.execute('SELECT * FROM notes WHERE id=?',(note['id'],)).fetchone()
            return {'id':note['id'],'sha':record_sha(note)}

    @app.post('/api/sync/v2/conflict')
    async def report_conflict(request: Request):
        require_sync(request); data=await json_input(request)
        body=data.get('local_body'); base=data.get('base_body','')
        if not isinstance(body,str) or not isinstance(base,str): raise HTTPException(400)
        with database(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            note=db.execute('SELECT * FROM notes WHERE path=?',(data.get('path'),)).fetchone()
            if not note: raise HTTPException(404)
            if data.get('server_sha')!=record_sha(note): return JSONResponse({'conflict':True},409)
            now=int(time.time())
            db.execute("""INSERT INTO sync_conflicts(note_id,local_body,local_sha,server_sha,base_body,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(note_id) DO UPDATE SET local_body=excluded.local_body,
                local_sha=excluded.local_sha,server_sha=excluded.server_sha,base_body=excluded.base_body,
                status='open',resolved_body='',resolved_sha='',updated_at=excluded.updated_at""",
                (note['id'],body,digest(body),record_sha(note),base,now,now))
            db.execute("UPDATE edit_events SET status='conflict' WHERE note_id=? AND status='pending'",(note['id'],))
        return {'ok':True}

    @app.post('/api/sync/v2/applied')
    async def applied(request: Request):
        require_sync(request); data=await json_input(request)
        with database(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            note=db.execute('SELECT * FROM notes WHERE path=?',(data.get('path'),)).fetchone()
            if not note or data.get('sha')!=record_sha(note): return JSONResponse({'conflict':True},409)
            conflict=db.execute("SELECT * FROM sync_conflicts WHERE note_id=? AND status IN ('open','resolved')",(note['id'],)).fetchone()
            if conflict and (conflict['status']!='resolved' or conflict['resolved_sha']!=record_sha(note)):
                return JSONResponse({'conflict':True},409)
            db.execute("UPDATE edit_events SET status='applied' WHERE note_id=? AND status IN ('pending','conflict')",(note['id'],))
            db.execute("UPDATE sync_conflicts SET status='applied',updated_at=? WHERE note_id=?",(int(time.time()),note['id']))
        return {'ok':True}

    def asset_file(note, name):
        if name not in g['linked_assets'](note, content_root): raise HTTPException(404)
        path=content_root/name
        top = content_root / note['path'].split('/',1)[0]
        parts = Path(name).parts
        if any(content_root.joinpath(*parts[:index]).is_symlink() for index in range(1,len(parts)+1)) or not path.resolve().is_relative_to(top): raise HTTPException(400)
        return path

    @app.get('/api/sync/v2/assets/{note_id}')
    def asset_list(request: Request, note_id: int):
        require_sync(request)
        with database(db_path) as db:
            note=db.execute('SELECT * FROM notes WHERE id=?',(note_id,)).fetchone()
            if not note: raise HTTPException(404)
            result=[]
            for name in sorted(g['linked_assets'](note, content_root)):
                path=asset_file(note,name)
                if path.is_file() and path.stat().st_size<=4*1024*1024:
                    result.append({'path':name,'sha':hashlib.sha256(path.read_bytes()).hexdigest()})
            return {'assets':result}

    @app.get('/api/sync/v2/asset/{note_id}')
    def asset_get(request: Request, note_id: int, path: str):
        require_sync(request)
        with database(db_path) as db:
            note=db.execute('SELECT * FROM notes WHERE id=?',(note_id,)).fetchone()
            if not note: raise HTTPException(404)
            file=asset_file(note,path)
            if not file.is_file() or file.stat().st_size>4*1024*1024: raise HTTPException(404)
            raw=file.read_bytes()
            return {'data':base64.b64encode(raw).decode(),'sha':hashlib.sha256(raw).hexdigest()}

    @app.post('/api/sync/v2/asset/{note_id}')
    async def asset_push(request: Request, note_id: int):
        require_sync(request); data=await json_input(request)
        try: raw=base64.b64decode(data.get('data',''),validate=True)
        except (ValueError,TypeError): raise HTTPException(400)
        if len(raw)>4*1024*1024: raise HTTPException(413)
        with database(db_path) as db:
            # Serialize hash-check and replacement with other sync writes.
            db.execute('BEGIN IMMEDIATE')
            note=db.execute('SELECT * FROM notes WHERE id=?',(note_id,)).fetchone()
            if not note: raise HTTPException(404)
            file=asset_file(note,data.get('path'))
            current=hashlib.sha256(file.read_bytes()).hexdigest() if file.is_file() else ''
            if current!=data.get('base_sha'): return JSONResponse({'conflict':True},409)
            file.parent.mkdir(parents=True,exist_ok=True)
            import tempfile, os
            fd,name=tempfile.mkstemp(prefix='.asset-',dir=file.parent)
            try:
                with os.fdopen(fd,'wb') as stream: stream.write(raw)
                os.replace(name,file)
            finally:
                if os.path.exists(name): os.unlink(name)
            return {'sha':hashlib.sha256(raw).hexdigest()}
