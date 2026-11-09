"""Account-scoped team workspaces. Team documents never enter the personal sync/catalog."""
from contextlib import contextmanager
import json
import re
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from markdown_it import MarkdownIt

from html_compat import sanitize_html
from storage import connect, digest
from version_diff import make_diff

MANAGERS = {'owner', 'admin'}
WRITERS = MANAGERS | {'editor'}
ROLES = {'admin', 'editor', 'reader'}
PAGE_SIZE = 20
PARSER = MarkdownIt('default', {'html': True})


def initialize_teams(db_path):
    # Additive and idempotent: no note ownership, grants, or file paths are changed.
    with connect(db_path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            version INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS team_members (
            team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            role TEXT NOT NULL CHECK(role IN ('owner','admin','editor','reader')),
            joined_at INTEGER NOT NULL, PRIMARY KEY(team_id,user_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ix_team_owner ON team_members(team_id) WHERE role='owner';
        CREATE INDEX IF NOT EXISTS ix_team_member_user ON team_members(user_id,team_id);
        CREATE TABLE IF NOT EXISTS team_invitations (
            id INTEGER PRIMARY KEY, team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id), invited_by INTEGER NOT NULL REFERENCES users(id),
            role TEXT NOT NULL CHECK(role IN ('admin','editor','reader')),
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','declined','revoked','expired')),
            created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ix_team_pending_invitation
            ON team_invitations(team_id,user_id) WHERE status='pending';
        CREATE INDEX IF NOT EXISTS ix_team_invitation_inbox ON team_invitations(user_id,status);
        CREATE TABLE IF NOT EXISTS team_documents (
            id INTEGER PRIMARY KEY, team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            title TEXT NOT NULL, body TEXT NOT NULL, body_sha TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1, updated_by INTEGER NOT NULL REFERENCES users(id),
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_team_documents ON team_documents(team_id,updated_at DESC,id DESC);
        CREATE TABLE IF NOT EXISTS team_document_revisions (
            id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES team_documents(id) ON DELETE CASCADE,
            version INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
            actor_id INTEGER NOT NULL REFERENCES users(id), created_at INTEGER NOT NULL,
            UNIQUE(document_id,version)
        );
        CREATE TABLE IF NOT EXISTS team_events (
            id INTEGER PRIMARY KEY, team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            actor_id INTEGER NOT NULL REFERENCES users(id), action TEXT NOT NULL,
            detail TEXT NOT NULL, created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_team_events ON team_events(team_id,id DESC);
        ''')


def text(data, key, maximum, *, required=True):
    value = data.get(key, '')
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise HTTPException(400, f'{key} 内容无效或过长')
    if required and not value.strip():
        raise HTTPException(400, f'{key} 不能为空')
    return value if key == 'body' else value.strip()


def version(data):
    value = data.get('version')
    if type(value) is not int or value < 1:
        raise HTTPException(400, '请提供有效版本')
    return value


async def json_body(request):
    if request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
        raise HTTPException(415, '请提交 JSON 内容')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 256 * 1024:
            raise HTTPException(413, '提交内容过大')
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(400, 'JSON 内容无效') from exc
    if not isinstance(data, dict):
        raise HTTPException(400, 'JSON 内容必须是对象')
    return data


def create_document_record(db, team_id, user, title, body):
    """Create the document, first revision and activity in the caller's transaction."""
    now = int(time.time())
    identity = db.execute('''INSERT INTO team_documents(team_id,title,body,body_sha,updated_by,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?)''', (team_id, title, body, digest(body), user['id'], now, now)).lastrowid
    db.execute('''INSERT INTO team_document_revisions(document_id,version,title,body,actor_id,created_at)
        VALUES(?,1,?,?,?,?)''', (identity, title, body, user['id'], now))
    db.execute('''INSERT INTO team_events(team_id,actor_id,action,detail,created_at)
        VALUES(?,?,'document.created',?,?)''', (team_id, user['id'], f'创建文档「{title}」', now))
    db.execute('UPDATE teams SET version=version+1 WHERE id=?', (team_id,))
    return identity


def rendered(body, team_id=None):
    def urls(tag, attr, url):
        if team_id is not None and re.fullmatch(rf'/api/teams/{team_id}/attachments/\d+',url):
            return url
        if tag == 'a' and attr == 'href':
            try:
                parsed = urlsplit(url)
            except ValueError:
                return None
            if url.startswith('#') or (parsed.scheme in {'https', 'http', 'mailto'} and not parsed.username):
                return url
        # Team uploads/attachments need their own scoped resource API; never use /asset or content/.
        return None
    return sanitize_html(PARSER.render(body), urls)


def register_teams(app, db_path, g):
    initialize_teams(db_path)
    database, current_user = g['database'], g['current_user']

    @contextmanager
    def authorized(request, team_id=None, *, write=False, roles=None):
        with database(db_path) as db:
            if write:
                db.execute('BEGIN IMMEDIATE')
            user, csrf = current_user(db, request)
            if user is None:
                raise HTTPException(401, '请先登录')
            if write:
                g['require_csrf']({'csrf': request.headers.get('X-CSRF-Token', '')}, csrf)
            team = None
            if team_id is not None:
                team = db.execute('''SELECT t.*,m.role FROM teams t JOIN team_members m ON m.team_id=t.id
                                     WHERE t.id=? AND m.user_id=?''', (team_id, user['id'])).fetchone()
                if team is None:
                    raise HTTPException(404, '空间不存在或你已不在该空间')
                if roles is not None and team['role'] not in roles:
                    raise HTTPException(403, '当前空间角色无权执行此操作')
            yield db, user, team

    def require_version(team, data):
        if version(data) != team['version']:
            raise HTTPException(409, '空间已发生变化，请刷新后重新操作')

    def event(db, team_id, user, action, detail):
        db.execute('INSERT INTO team_events(team_id,actor_id,action,detail,created_at) VALUES(?,?,?,?,?)',
                   (team_id, user['id'], action, detail, int(time.time())))
        db.execute('UPDATE teams SET version=version+1 WHERE id=?', (team_id,))

    def team_info(db, team):
        result = dict(team)
        result['member_count'] = db.execute('SELECT count(*) FROM team_members WHERE team_id=?', (team['id'],)).fetchone()[0]
        result['document_count'] = db.execute('SELECT count(*) FROM team_documents WHERE team_id=?', (team['id'],)).fetchone()[0]
        return result

    def invitation_info(row):
        item = dict(row)
        if item['status'] == 'pending' and item['expires_at'] <= int(time.time()):
            item['status'] = 'expired'
        return item

    def document(db, team_id, document_id):
        row = db.execute('SELECT * FROM team_documents WHERE id=? AND team_id=?', (document_id, team_id)).fetchone()
        if row is None:
            raise HTTPException(404, '文档不存在')
        return row

    def document_info(db, row):
        from pdf_notes import pdf_info
        pdf = pdf_info(db, document_id=row['id'], team_id=row['team_id'])
        return {**dict(row), 'html': rendered(row['body'],team_id=row['team_id']), 'pdf': pdf, 'note_format': pdf['note_format'] if pdf else 'markdown'}

    def save_revision(db, doc, user):
        db.execute('''INSERT INTO team_document_revisions(document_id,version,title,body,actor_id,created_at)
                      VALUES(?,?,?,?,?,?)''', (doc['id'], doc['version'], doc['title'], doc['body'], user['id'], int(time.time())))

    @app.get('/teams')
    def team_page(request: Request):
        with database(db_path) as db:
            user, csrf = current_user(db, request)
            if user is None:
                return RedirectResponse('/login?next=/teams', 303)
            return g['page'](request, 'teams.html', user=user, csrf=csrf, categories=g['categories'](db, user))

    @app.get('/api/teams')
    def list_teams(request: Request):
        with authorized(request) as (db, user, _):
            rows = db.execute('''SELECT t.*,m.role FROM teams t JOIN team_members m ON m.team_id=t.id
                                 WHERE m.user_id=? ORDER BY t.id DESC''', (user['id'],)).fetchall()
            return {'teams': [team_info(db, row) for row in rows]}

    @app.post('/api/teams', status_code=201)
    async def create_team(request: Request):
        data = await json_body(request)
        name, description = text(data, 'name', 80), text(data, 'description', 500, required=False)
        with authorized(request, write=True) as (db, user, _):
            count = db.execute("SELECT count(*) FROM team_members WHERE user_id=? AND role='owner'", (user['id'],)).fetchone()[0]
            if count >= 20:
                raise HTTPException(409, '每个账户最多拥有 20 个团队空间')
            now = int(time.time())
            team_id = db.execute('INSERT INTO teams(name,description,created_at) VALUES(?,?,?)', (name, description, now)).lastrowid
            db.execute("INSERT INTO team_members VALUES(?,?,'owner',?)", (team_id, user['id'], now))
            event(db, team_id, user, 'team.created', f'创建空间「{name}」')
            return {'id': team_id}

    # Static inbox route must precede /api/teams/{team_id}.
    @app.get('/api/teams/invitations')
    def inbox(request: Request):
        with authorized(request) as (db, user, _):
            rows = db.execute('''SELECT i.id,i.team_id,i.role,i.status,i.expires_at,t.name AS team_name,u.username AS invited_by
                                 FROM team_invitations i JOIN teams t ON t.id=i.team_id JOIN users u ON u.id=i.invited_by
                                 WHERE i.user_id=? AND i.status='pending' AND i.expires_at>? ORDER BY i.id DESC LIMIT 100''',
                              (user['id'], int(time.time()))).fetchall()
            return {'invitations': [dict(row) for row in rows]}

    @app.post('/api/teams/invitations/{invitation_id}/respond')
    async def respond(request: Request, invitation_id: int):
        data = await json_body(request)
        if not isinstance(data.get('action'), str) or data['action'] not in {'accept', 'decline'}:
            raise HTTPException(400, '请选择接受或拒绝')
        with authorized(request, write=True) as (db, user, _):
            row = db.execute('SELECT * FROM team_invitations WHERE id=? AND user_id=?', (invitation_id, user['id'])).fetchone()
            if row is None:
                raise HTTPException(404, '邀请不存在')
            if row['status'] != 'pending' or row['expires_at'] <= int(time.time()):
                raise HTTPException(409, '邀请已处理、撤销或过期')
            if data['action'] == 'accept':
                inviter = db.execute('''SELECT m.role FROM team_members m JOIN users u ON u.id=m.user_id
                                         WHERE m.team_id=? AND m.user_id=? AND u.active=1''', (row['team_id'], row['invited_by'])).fetchone()
                if not inviter or inviter['role'] not in MANAGERS or (row['role'] == 'admin' and inviter['role'] != 'owner'):
                    raise HTTPException(409, '邀请人的权限已变化，请索取新邀请')
                if db.execute('SELECT 1 FROM team_members WHERE team_id=? AND user_id=?', (row['team_id'], user['id'])).fetchone():
                    raise HTTPException(409, '你已加入该空间')
                db.execute('INSERT INTO team_members VALUES(?,?,?,?)', (row['team_id'], user['id'], row['role'], int(time.time())))
            db.execute('UPDATE team_invitations SET status=? WHERE id=?', ('accepted' if data['action'] == 'accept' else 'declined', invitation_id))
            event(db, row['team_id'], user, 'invitation.' + data['action'], f"{user['username']}" + (' 加入空间' if data['action'] == 'accept' else ' 拒绝邀请'))
            return {'ok': True, 'team_id': row['team_id']}

    @app.get('/api/teams/{team_id}')
    def detail(request: Request, team_id: int):
        with authorized(request, team_id) as (db, user, team):
            members = db.execute('''SELECT m.user_id,u.username,u.active,m.role,m.joined_at FROM team_members m
                                    JOIN users u ON u.id=m.user_id WHERE m.team_id=? ORDER BY m.joined_at,m.user_id''', (team_id,)).fetchall()
            invites = []
            if team['role'] in MANAGERS:
                invites = [invitation_info(row) for row in db.execute('''SELECT i.id,i.role,i.status,i.expires_at,u.username FROM team_invitations i
                              JOIN users u ON u.id=i.user_id WHERE i.team_id=? ORDER BY i.id DESC LIMIT 50''', (team_id,))]
            return {'team': team_info(db, team), 'members': [dict(row) for row in members], 'invitations': invites, 'user_id': user['id']}

    @app.post('/api/teams/{team_id}/invitations', status_code=201)
    async def invite(request: Request, team_id: int):
        data = await json_body(request)
        username = text(data, 'username', 64)
        role = data.get('role')
        if not isinstance(role, str) or role not in ROLES:
            raise HTTPException(400, '邀请角色无效')
        with authorized(request, team_id, write=True, roles=MANAGERS) as (db, user, team):
            require_version(team, data)
            if role == 'admin' and team['role'] != 'owner':
                raise HTTPException(403, '只有所有者可以邀请管理员')
            target = db.execute('SELECT id FROM users WHERE username=? AND active=1', (username,)).fetchone()
            if target is None:
                raise HTTPException(400, '请输入已有且启用的账户名')
            if db.execute('SELECT 1 FROM team_members WHERE team_id=? AND user_id=?', (team_id, target['id'])).fetchone():
                raise HTTPException(409, '该账户已经是成员')
            now = int(time.time())
            db.execute("UPDATE team_invitations SET status='expired' WHERE team_id=? AND status='pending' AND expires_at<=?", (team_id, now))
            if db.execute("SELECT 1 FROM team_invitations WHERE team_id=? AND user_id=? AND status='pending'", (team_id, target['id'])).fetchone():
                raise HTTPException(409, '已有待处理邀请，请等待回应或先撤销')
            if db.execute("SELECT count(*) FROM team_invitations WHERE team_id=? AND status='pending'", (team_id,)).fetchone()[0] >= 50:
                raise HTTPException(409, '待处理邀请已达 50 条')
            invitation_id = db.execute('''INSERT INTO team_invitations(team_id,user_id,invited_by,role,created_at,expires_at)
                                         VALUES(?,?,?,?,?,?)''', (team_id, target['id'], user['id'], role, now, now + 7 * 86400)).lastrowid
            event(db, team_id, user, 'invitation.created', f'邀请 {username}，角色 {role}')
            return {'id': invitation_id}

    @app.post('/api/teams/{team_id}/invitations/{invitation_id}/revoke')
    async def revoke(request: Request, team_id: int, invitation_id: int):
        data = await json_body(request)
        with authorized(request, team_id, write=True, roles=MANAGERS) as (db, user, team):
            require_version(team, data)
            row = db.execute('SELECT * FROM team_invitations WHERE id=? AND team_id=?', (invitation_id, team_id)).fetchone()
            if row is None:
                raise HTTPException(404, '邀请不存在')
            if row['role'] == 'admin' and team['role'] != 'owner':
                raise HTTPException(403, '只有所有者可以撤销管理员邀请')
            if row['status'] != 'pending':
                raise HTTPException(409, '邀请已处理')
            db.execute("UPDATE team_invitations SET status='revoked' WHERE id=?", (invitation_id,))
            event(db, team_id, user, 'invitation.revoked', f'撤销邀请 #{invitation_id}')
            return {'ok': True}

    @app.post('/api/teams/{team_id}/members/{member_id}')
    async def change_member(request: Request, team_id: int, member_id: int):
        data = await json_body(request)
        if not isinstance(data.get('action'), str) or data['action'] not in {'role', 'remove'}:
            raise HTTPException(400, '成员操作无效')
        with authorized(request, team_id, write=True, roles=MANAGERS) as (db, user, team):
            require_version(team, data)
            target = db.execute('SELECT * FROM team_members WHERE team_id=? AND user_id=?', (team_id, member_id)).fetchone()
            if target is None:
                raise HTTPException(404, '成员不存在')
            if target['role'] == 'owner' or member_id == user['id']:
                raise HTTPException(403, '请使用退出或转让所有权操作')
            if team['role'] != 'owner' and target['role'] == 'admin':
                raise HTTPException(403, '管理员不能管理其他管理员')
            if data['action'] == 'role':
                role = data.get('role')
                if not isinstance(role, str) or role not in ROLES:
                    raise HTTPException(400, '成员角色无效')
                if team['role'] != 'owner' and role == 'admin':
                    raise HTTPException(403, '只有所有者可以设置管理员')
                db.execute('UPDATE team_members SET role=? WHERE team_id=? AND user_id=?', (role, team_id, member_id))
            else:
                db.execute('DELETE FROM team_members WHERE team_id=? AND user_id=?', (team_id, member_id))
            # Old invitations cannot let a removed/demoted manager grant access later.
            db.execute("UPDATE team_invitations SET status='revoked' WHERE team_id=? AND invited_by=? AND status='pending'", (team_id, member_id))
            event(db, team_id, user, 'member.' + data['action'], f"账户 #{member_id}" + (f" 的角色调整为 {data['role']}" if data['action'] == 'role' else ' 已移出空间'))
            return {'ok': True}

    @app.post('/api/teams/{team_id}/leave')
    async def leave(request: Request, team_id: int):
        data = await json_body(request)
        with authorized(request, team_id, write=True) as (db, user, team):
            require_version(team, data)
            if team['role'] == 'owner':
                raise HTTPException(409, '所有者请先转让所有权，再退出空间')
            db.execute('DELETE FROM team_members WHERE team_id=? AND user_id=?', (team_id, user['id']))
            db.execute("UPDATE team_invitations SET status='revoked' WHERE team_id=? AND invited_by=? AND status='pending'", (team_id, user['id']))
            event(db, team_id, user, 'member.left', f"{user['username']} 退出空间")
            return {'ok': True}

    @app.post('/api/teams/{team_id}/transfer')
    async def transfer(request: Request, team_id: int):
        data = await json_body(request)
        if type(data.get('user_id')) is not int:
            raise HTTPException(400, '请选择成员')
        with authorized(request, team_id, write=True, roles={'owner'}) as (db, user, team):
            require_version(team, data)
            target = db.execute('''SELECT m.* FROM team_members m JOIN users u ON u.id=m.user_id
                                    WHERE m.team_id=? AND m.user_id=? AND u.active=1''', (team_id, data['user_id'])).fetchone()
            if target is None or target['user_id'] == user['id']:
                raise HTTPException(400, '请选择另一位已加入且启用的成员')
            db.execute("UPDATE team_members SET role='admin' WHERE team_id=? AND user_id=?", (team_id, user['id']))
            db.execute("UPDATE team_members SET role='owner' WHERE team_id=? AND user_id=?", (team_id, target['user_id']))
            event(db, team_id, user, 'owner.transferred', f"所有权转让给账户 #{target['user_id']}")
            return {'ok': True}

    @app.get('/api/teams/{team_id}/documents')
    def documents(request: Request, team_id: int, q: str = '', page: int = 1):
        if len(q) > 100 or page < 1 or page > 10000:
            raise HTTPException(400, '查询条件无效')
        with authorized(request, team_id) as (db, user, _):
            escaped = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            where = "team_id=? AND (title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')"
            params = (team_id, '%' + escaped + '%', '%' + escaped + '%')
            total = db.execute('SELECT count(*) FROM team_documents WHERE ' + where, params).fetchone()[0]
            rows = db.execute('''SELECT id,title,version,updated_at,EXISTS(SELECT 1 FROM document_pdfs p WHERE p.document_id=team_documents.id AND p.note_format='pdf') AS is_pdf FROM team_documents WHERE ''' + where +
                              ' ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?', (*params, PAGE_SIZE, (page - 1) * PAGE_SIZE)).fetchall()
            return {'documents': [dict(row) for row in rows], 'total': total, 'page': page, 'page_size': PAGE_SIZE}

    @app.post('/api/teams/{team_id}/documents', status_code=201)
    async def create_document(request: Request, team_id: int):
        data = await json_body(request)
        title, body = text(data, 'title', 160), text(data, 'body', 100000)
        with authorized(request, team_id, write=True, roles=WRITERS) as (db, user, _):
            doc_id = create_document_record(db, team_id, user, title, body)
            doc = document(db, team_id, doc_id)
            return document_info(db, doc)

    @app.get('/api/teams/{team_id}/documents/{document_id}')
    def get_document(request: Request, team_id: int, document_id: int):
        with authorized(request, team_id) as (db, user, _):
            return document_info(db, document(db, team_id, document_id))

    @app.put('/api/teams/{team_id}/documents/{document_id}')
    async def edit_document(request: Request, team_id: int, document_id: int):
        data = await json_body(request)
        title, body, expected = text(data, 'title', 160), text(data, 'body', 100000), version(data)
        with authorized(request, team_id, write=True, roles=WRITERS) as (db, user, _):
            doc = document(db, team_id, document_id)
            if doc['version'] != expected:
                raise HTTPException(409, '另一位成员已修改文档；你的输入已保留，请对照最新版本后再保存')
            db.execute('''UPDATE team_documents SET title=?,body=?,body_sha=?,version=version+1,updated_by=?,updated_at=?
                          WHERE id=? AND team_id=? AND version=?''', (title, body, digest(body), user['id'], int(time.time()), document_id, team_id, expected))
            doc = document(db, team_id, document_id)
            save_revision(db, doc, user)
            event(db, team_id, user, 'document.updated', f'更新文档「{title}」至 v{doc["version"]}')
            return document_info(db, doc)

    @app.post('/api/teams/{team_id}/preview')
    async def preview(request: Request, team_id: int):
        data = await json_body(request)
        body = text(data, 'body', 100000, required=False)
        with authorized(request, team_id, write=True, roles=WRITERS):
            if data.get('blocks'):
                offsets=[0]
                for line in body.split('\n'): offsets.append(offsets[-1]+len(line.encode('utf-16-le'))//2+1)
                blocks=[]; depth=0; start=end=None
                for token in PARSER.parse(body):
                    if depth==0 and token.map: start,end=token.map
                    depth+=token.nesting
                    if depth==0 and start is not None:
                        fragment='\n'.join(body.split('\n')[start:end])
                        blocks.append(dict(from_=offsets[start],to=offsets[end]-1,html=rendered(fragment,team_id=team_id)))
                        blocks[-1]['from']=blocks[-1].pop('from_'); start=end=None
                return {'blocks':blocks}
            return {'html': rendered(body,team_id=team_id)}

    @app.get('/api/teams/{team_id}/documents/{document_id}/history')
    def history(request: Request, team_id: int, document_id: int, page: int = 1):
        if page < 1 or page > 10000:
            raise HTTPException(400, '页码无效')
        with authorized(request, team_id) as (db, user, _):
            document(db, team_id, document_id)
            rows = db.execute('''SELECT r.version,r.title,r.created_at,u.username AS actor
                                FROM team_document_revisions r JOIN users u ON u.id=r.actor_id
                                WHERE document_id=? ORDER BY r.version DESC LIMIT 20 OFFSET ?''', (document_id, (page - 1) * 20)).fetchall()
            return {'revisions': [dict(row) for row in rows]}

    @app.get('/api/teams/{team_id}/events')
    def events(request: Request, team_id: int, page: int = 1):
        if page < 1 or page > 10000:
            raise HTTPException(400, '页码无效')
        with authorized(request, team_id) as (db, user, _):
            rows = db.execute('''SELECT e.id,e.action,e.detail,e.created_at,u.username AS actor FROM team_events e
                                JOIN users u ON u.id=e.actor_id WHERE team_id=? ORDER BY e.id DESC LIMIT 20 OFFSET ?''', (team_id, (page - 1) * 20)).fetchall()
            return {'events': [dict(row) for row in rows]}

    @app.get('/api/teams/{team_id}/documents/{document_id}/diff')
    def document_diff(request: Request, team_id: int, document_id: int, base: int, head: int = 0):
        if base < 1 or head < 0:
            raise HTTPException(400, '请选择有效版本')
        with authorized(request, team_id) as (db, user, _):
            current = document(db, team_id, document_id)
            old = db.execute('SELECT * FROM team_document_revisions WHERE document_id=? AND version=?', (document_id, base)).fetchone()
            new = db.execute('SELECT * FROM team_document_revisions WHERE document_id=? AND version=?', (document_id, head)).fetchone() if head else current
            if old is None or new is None:
                raise HTTPException(404, '版本不存在')
            result = make_diff(old['body'], new['body'], f'v{base}', f'v{head}' if head else f'当前 v{current["version"]}')
            result.update(old_title=old['title'], new_title=new['title'])
            return result
