"""Admin-managed note and category sharing, with isolated read-only link views."""
import secrets
import time
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse


def scope_where(share):
    if share['note_id'] is not None:
        return 'notes.id=?', (share['note_id'],)
    return "(notes.category=? OR substr(notes.category,1,length(?)+3)=?||' / ')", (share['category'],) * 3


def find_share(db, token):
    if not 20 <= len(token) <= 100:
        raise HTTPException(404, '分享不存在或已撤销')
    share = db.execute('SELECT * FROM shares WHERE token=?', (token,)).fetchone()
    if share is None or (share['expires_at'] is not None and share['expires_at'] <= int(time.time())):
        raise HTTPException(404, '分享不存在或已撤销')
    return share


def link_share(db, token):
    share = find_share(db, token)
    if share['audience'] != 'link':
        raise HTTPException(404, '分享不存在或已撤销')
    return share


def shared_note(db, token, note_id):
    share = link_share(db, token)
    where, params = scope_where(share)
    note = db.execute(f'SELECT * FROM notes WHERE {where} AND notes.id=?', (*params, note_id)).fetchone()
    if note is None:
        raise HTTPException(404, '笔记不在分享范围内')
    return note


def register_sharing(app, db_path, api):
    database, current_user = api['database'], api['current_user']
    require_admin, require_csrf = api['require_admin'], api['require_csrf']
    page, rendered_markdown = api['page'], api['rendered_markdown']

    @app.exception_handler(HTTPException)
    async def sharing_error(request, exc):
        if request.url.path.startswith('/s/') and '/asset/' not in request.url.path and exc.status_code in {403, 404}:
            return page(request, 'share_unavailable.html', status_code=exc.status_code,
                        user=None, csrf=None, categories=[], forbidden=exc.status_code == 403,
                        login_next='/login?next=' + quote(request.url.path, safe=''))
        return await http_exception_handler(request, exc)

    def target(db, note_id, category):
        if not isinstance(category, str):
            raise HTTPException(400, '目录无效')
        if note_id is not None and not category:
            note = db.execute('SELECT id,title FROM notes WHERE id=?', (note_id,)).fetchone()
            if note:
                return f'note:{note_id}', note['title'], f'/n/{note_id}'
            raise HTTPException(404, '笔记不存在')
        if note_id is None and isinstance(category, str) and 0 < len(category) <= 240:
            where, params = scope_where({'note_id': None, 'category': category})
            if db.execute(f'SELECT 1 FROM notes WHERE {where} LIMIT 1', params).fetchone():
                return f'category:{category}', category, '/browse?category=' + quote(category, safe='')
            raise HTTPException(404, '目录不存在')
        raise HTTPException(400, '请选择一篇笔记或一个目录')

    def state(db, note_id, category):
        key, title, url = target(db, note_id, category)
        share = db.execute('SELECT * FROM shares WHERE scope_key=?', (key,)).fetchone()
        members = {row['user_id']: row['can_edit'] for row in db.execute(
            'SELECT user_id,can_edit FROM share_members WHERE share_id=?', (share['id'],))} if share else {}
        users = [dict(id=row['id'], username=row['username'], role=row['role'],
                      permission=('edit' if members[row['id']] else 'view') if row['id'] in members else 'none')
                 for row in db.execute("SELECT id,username,role FROM users WHERE active=1 AND role!='admin' ORDER BY username")]
        where, params = scope_where({'note_id': note_id, 'category': category})
        count = db.execute(f'SELECT count(*) FROM notes WHERE {where}', params).fetchone()[0]
        audience = share['audience'] if share else 'restricted'
        expired = bool(share and share['expires_at'] is not None and share['expires_at'] <= int(time.time()))
        active = not expired and (audience == 'link' or any(account['permission'] != 'none' for account in users))
        return dict(title=title, count=count, audience=audience, version=share['version'] if share else 0,
                    users=users, active=active, expired=expired, expires_at=share['expires_at'] if share else None,
                    url=f"/s/{share['token']}" if share else url)

    @app.get('/admin/shares')
    def share_center(request: Request, page_number: int = 1):
        if not 1 <= page_number <= 10000: raise HTTPException(400)
        with database(db_path) as db:
            user,csrf=current_user(db,request); require_admin(user)
            rows=db.execute('''SELECT s.*,coalesce(n.title,s.category) AS title,
                (SELECT count(*) FROM share_members m WHERE m.share_id=s.id) AS members
                FROM shares s LEFT JOIN notes n ON n.id=s.note_id ORDER BY s.id DESC LIMIT 51 OFFSET ?''',((page_number-1)*50,)).fetchall()
            return page(request,'share_center.html',user=user,csrf=csrf,categories=api['categories'](db,user),shares=rows[:50],now=int(time.time()),page_number=page_number,has_more=len(rows)>50)

    @app.get('/api/sharing')
    def get_sharing(request: Request, note_id: int | None = None, category: str = ''):
        with database(db_path) as db:
            user, _ = current_user(db, request)
            require_admin(user)
            return state(db, note_id, category)

    @app.post('/api/sharing')
    async def save_sharing(request: Request):
        raw = await request.body()
        if len(raw) > 128 * 1024:
            raise HTTPException(413, '分享设置过大')
        try:
            data = await request.json()
        except ValueError as exc:
            raise HTTPException(400, '分享设置无效') from exc
        if not isinstance(data, dict):
            raise HTTPException(400, '分享设置无效')
        with database(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            user, csrf = current_user(db, request)
            require_admin(user)
            require_csrf({'csrf': request.headers.get('X-CSRF-Token', '')}, csrf)
            note_id, category = data.get('note_id'), data.get('category', '')
            if note_id is not None and (type(note_id) is not int or note_id <= 0):
                raise HTTPException(400, '笔记无效')
            key, _, _ = target(db, note_id, category)
            existing = db.execute('SELECT * FROM shares WHERE scope_key=?', (key,)).fetchone()
            version = existing['version'] if existing else 0
            if type(data.get('version')) is not int or data['version'] < 0:
                raise HTTPException(400, '分享版本无效')
            if data['version'] != version:
                raise HTTPException(409, '分享设置已变化，请关闭后重新打开再修改')
            audience = data.get('audience')
            expires_at=data.get('expires_at', existing['expires_at'] if existing else None)
            if expires_at is not None and (type(expires_at) is not int or expires_at <= int(time.time()) or expires_at > int(time.time())+366*86400):
                # Revoking an already-expired share must remain possible.
                if audience == 'restricted' and not data.get('members'): expires_at=None
                else: raise HTTPException(400,'请选择未来一年以内的到期时间')
            members = data.get('members', [])
            if not isinstance(audience, str) or audience not in {'restricted', 'link'} or not isinstance(members, list) or len(members) > 1000:
                raise HTTPException(400, '分享设置无效')
            validated = {}
            for member in members:
                if not isinstance(member, dict) or type(member.get('user_id')) is not int or member.get('permission') not in ('view', 'edit'):
                    raise HTTPException(400, '账户权限无效')
                account = db.execute("SELECT * FROM users WHERE id=? AND active=1 AND role!='admin'", (member['user_id'],)).fetchone()
                if account is None or (member['permission'] == 'edit' and account['role'] != 'editor') or account['id'] in validated:
                    raise HTTPException(400, '账户不存在、已停用或不允许编辑')
                validated[account['id']] = int(member['permission'] == 'edit')
            # Rotate on disabling/re-enabling links so a revoked URL never becomes live again.
            keep_token = existing and (existing['expires_at'] is None or existing['expires_at']>int(time.time())) and existing['audience'] == audience and (audience == 'link' or validated)
            token = existing['token'] if keep_token else secrets.token_urlsafe(32)
            db.execute('''INSERT INTO shares(scope_key,note_id,category,token,audience,version,expires_at) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(scope_key) DO UPDATE SET token=excluded.token,audience=excluded.audience,version=excluded.version,expires_at=excluded.expires_at''',
                       (key, note_id, category if note_id is None else None, token, audience, version + 1,expires_at))
            share_id = db.execute('SELECT id FROM shares WHERE scope_key=?', (key,)).fetchone()[0]
            db.execute('DELETE FROM share_members WHERE share_id=?', (share_id,))
            db.executemany('INSERT INTO share_members(share_id,user_id,can_edit) VALUES(?,?,?)',
                           [(share_id, uid, edit) for uid, edit in validated.items()])
            return state(db, note_id, category)

    def render_shared(request, db, share, note=None, page_number=1):
        where, params = scope_where(share)
        rows = db.execute(f'SELECT id,path,title,summary,category,visibility FROM notes WHERE {where} ORDER BY category,title,id', params).fetchall()
        prefix = f"/s/{share['token']}"
        html = rendered_markdown(db, note, None, app.state.content_root, visible_rows=rows,
                                 note_prefix=prefix + '/n', asset_prefix=prefix + '/asset') if note else ''
        from pdf_notes import pdf_info
        return page(request, 'shared.html', user=None, csrf=None, categories=[], share=share, note=note, html=html,
                    pdf=pdf_info(db, note_id=note['id'], share_prefix=prefix) if note else None,
                    notes=rows[(page_number - 1) * 50:page_number * 50], total=len(rows), page_number=page_number,
                    has_more=len(rows) > page_number * 50, share_prefix=prefix)

    @app.get('/s/{token}')
    def share_page(request: Request, token: str, page_number: int = 1):
        if not 1 <= page_number <= 10000:
            raise HTTPException(400, '页码无效')
        with database(db_path) as db:
            share = find_share(db, token)
            if share['audience'] == 'restricted':
                if not db.execute('''SELECT 1 FROM share_members m JOIN users u ON u.id=m.user_id
                    WHERE m.share_id=? AND u.active=1 LIMIT 1''', (share['id'],)).fetchone():
                    raise HTTPException(404, '分享不存在或已撤销')
                user, _ = current_user(db, request)
                if user is None:
                    return RedirectResponse('/login?next=' + quote(f'/s/{token}', safe=''), status_code=303)
                if user['role'] != 'admin' and not db.execute(
                    'SELECT 1 FROM share_members WHERE share_id=? AND user_id=?', (share['id'], user['id'])).fetchone():
                    raise HTTPException(403, '当前账户没有此分享的权限')
                destination = f"/n/{share['note_id']}" if share['note_id'] else '/browse?category=' + quote(share['category'], safe='')
                return RedirectResponse(destination, status_code=303)
            note = shared_note(db, token, share['note_id']) if share['note_id'] else None
            return render_shared(request, db, share, note, page_number)

    @app.get('/s/{token}/n/{note_id}')
    def share_note_page(request: Request, token: str, note_id: int):
        with database(db_path) as db:
            share = link_share(db, token)
            return render_shared(request, db, share, shared_note(db, token, note_id))
