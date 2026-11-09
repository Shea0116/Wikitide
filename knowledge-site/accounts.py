"""Public signup and site administration. Site roles never bypass team membership."""
from contextlib import contextmanager
import hmac
import json
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from features import sync_summary
from storage import SESSION_LIFETIME, connect, create_session, create_user, digest, password_hash

USERNAME = re.compile(r'[a-z][a-z0-9_-]{2,31}\Z')
RESERVED_NAMES = {'shea', 'admin', 'administrator', 'root', 'system'}
ACCOUNT_FIELDS = 'id,username,role,active,created_at,version'


def initialize_accounts(db_path):
    with connect(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        if 'version' not in {row['name'] for row in db.execute('PRAGMA table_info(users)')}:
            db.execute('ALTER TABLE users ADD COLUMN version INTEGER NOT NULL DEFAULT 1')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS site_settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
        );
        INSERT OR IGNORE INTO site_settings(key,value) VALUES('registration_open','1');
        CREATE TABLE IF NOT EXISTS signup_attempts (key TEXT NOT NULL, happened_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_signup_attempts ON signup_attempts(key,happened_at);
        CREATE TABLE IF NOT EXISTS site_audit (
            id INTEGER PRIMARY KEY, actor_id INTEGER REFERENCES users(id),
            action TEXT NOT NULL, target TEXT NOT NULL, detail TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        ''')


def audit(db, actor_id, action, target, detail):
    db.execute('INSERT INTO site_audit(actor_id,action,target,detail,created_at) VALUES(?,?,?,?,?)',
               (actor_id, action, target, detail, int(time.time())))


def settings(db):
    row = db.execute("SELECT * FROM site_settings WHERE key='registration_open'").fetchone()
    return {'registration_open': row['value'] == '1', 'version': row['version']}


def safe_next(value, default='/teams'):
    # Reject browser URL normalization tricks as well as external redirect targets.
    if not isinstance(value, str) or not value.startswith('/') or value.startswith('//'):
        return default
    if '\\' in value or any(ord(c) < 33 for c in value):
        return default
    try:
        parsed = urlsplit(value)
        return value if not parsed.scheme and not parsed.netloc else default
    except ValueError:
        return default


def signup_username(value):
    name = value.strip().lower()
    if not USERNAME.fullmatch(name):
        raise ValueError('用户名需为 3–32 位，以英文字母开头，可包含小写字母、数字、下划线和短横线')
    if name in RESERVED_NAMES:
        raise ValueError('该用户名为管理账户保留，请选择其他用户名')
    return name


def signup_next(value):
    target = safe_next(value)
    path = urlsplit(target).path
    if path.startswith(('/admin', '/api/')) or path in {'/login', '/register', '/logout'}:
        return '/teams'
    return target


def update_account(db, actor, target_id, *, role, active, version, password=''):
    """Caller holds BEGIN IMMEDIATE and validates CSRF. Shared by form and JSON APIs."""
    if role not in {'reader', 'editor', 'admin'} or type(active) is not bool:
        raise HTTPException(400, '账户角色或状态无效')
    if type(version) is not int or version < 1:
        raise HTTPException(400, '请提供有效账户版本')
    target = db.execute('SELECT * FROM users WHERE id=?', (target_id,)).fetchone()
    if not target:
        raise HTTPException(404, '账户不存在')
    if target['version'] != version:
        raise HTTPException(409, '账户已被其他管理员修改，请刷新后重试')
    if target_id == actor['id'] and (role != 'admin' or not active):
        raise HTTPException(400, '不能停用或降级当前管理员')
    if target['role'] == 'admin' and target['active'] and (role != 'admin' or not active):
        count = db.execute("SELECT count(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]
        if count <= 1:
            raise HTTPException(400, '至少保留一位启用的管理员')
    try:
        new_hash = password_hash(password) if password else None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    changed = role != target['role'] or active != bool(target['active']) or bool(new_hash)
    if not changed:
        return
    db.execute('UPDATE users SET role=?,active=?,version=version+1 WHERE id=?', (role, int(active), target_id))
    if new_hash:
        db.execute('UPDATE users SET password_hash=? WHERE id=?', (new_hash, target_id))
    # Disabling, credential changes and site role changes invalidate web and native sessions.
    db.execute('DELETE FROM sessions WHERE user_id=?', (target_id,))
    if not active:
        db.execute("UPDATE team_invitations SET status='revoked' WHERE status='pending' AND (user_id=? OR invited_by=?)", (target_id, target_id))
    detail = json.dumps({'role_before': target['role'], 'role_after': role,
                         'active_before': bool(target['active']), 'active_after': active,
                         'password_changed': bool(new_hash)}, ensure_ascii=False)
    audit(db, actor['id'], 'account.updated', target['username'], detail)


async def bounded_body(request, maximum=16 * 1024):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > maximum:
            raise HTTPException(413, '提交内容过大')
    return bytes(raw)


def register_accounts(app, db_path, g):
    initialize_accounts(db_path)
    database, current_user, page = g['database'], g['current_user'], g['page']

    def signup_page(request, csrf, *, error='', status=200, username='', next_path='/teams', opened=True):
        return page(request, 'register.html', status_code=status, user=None, csrf=csrf,
                    error=error, username=username, next=next_path, registration_open=opened)

    @app.get('/register')
    def register_page(request: Request, next: str = '/teams'):
        with database(db_path) as db:
            user, _ = current_user(db, request)
            if user:
                return RedirectResponse(safe_next(next), 303)
            opened = settings(db)['registration_open']
        token = secrets.token_urlsafe(24)
        response = signup_page(request, token, next_path=signup_next(next), opened=opened)
        response.set_cookie('kb_signup_csrf', token, max_age=600, httponly=True,
                            secure=app.state.cookie_secure, samesite='lax')
        return response

    @app.post('/register')
    async def signup(request: Request):
        if request.headers.get('content-type', '').split(';')[0].strip() != 'application/x-www-form-urlencoded':
            raise HTTPException(415, '请使用注册表单')
        form = g['form_values'](await bounded_body(request))
        g['require_csrf'](form, request.cookies.get('kb_signup_csrf'))
        next_path = signup_next(form.get('next', '/teams'))
        with database(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            user, _ = current_user(db, request)
            if user:
                return RedirectResponse(next_path, 303)
            if not settings(db)['registration_open']:
                return signup_page(request, form['csrf'], opened=False, status=403)
            # Use the actual peer, never trust an arbitrary X-Forwarded-For header.
            key = digest('signup:' + (request.client.host if request.client else 'unknown'))
            now = int(time.time())
            db.execute('DELETE FROM signup_attempts WHERE happened_at<?', (now - 86400,))
            recent = db.execute('SELECT count(*) FROM signup_attempts WHERE key=? AND happened_at>?', (key, now - 900)).fetchone()[0]
            daily = db.execute('SELECT count(*) FROM signup_attempts WHERE key=?', (key,)).fetchone()[0]
            if recent >= 10 or daily >= 30:
                response = signup_page(request, form['csrf'], error='注册尝试过多，请稍后再试', status=429)
                response.headers['Retry-After'] = '900'
                return response
            db.execute('INSERT INTO signup_attempts VALUES(?,?)', (key, now))
            name = form.get('username', '')[:64]
            try:
                name = signup_username(form.get('username', ''))
                password = form.get('password', '')
                if not 12 <= len(password) <= 128:
                    raise ValueError('密码需为 12–128 个字符')
                if password != form.get('password_confirm', ''):
                    raise ValueError('两次输入的密码不一致')
                if db.execute('SELECT 1 FROM users WHERE lower(username)=?', (name,)).fetchone():
                    raise ValueError('该用户名暂不可用，请选择其他用户名')
                # Never accept role/active fields supplied by a public client.
                create_user(db, name, password, 'reader')
            except (ValueError, sqlite3.IntegrityError) as exc:
                message = str(exc) if isinstance(exc, ValueError) else '该用户名暂不可用，请选择其他用户名'
                return signup_page(request, form['csrf'], error=message, status=400, username=name, next_path=next_path)
            user_id = db.execute('SELECT id FROM users WHERE username=?', (name,)).fetchone()[0]
            audit(db, user_id, 'account.registered', name, '公开注册；初始角色为普通读者')
            token, _ = create_session(db, user_id)
        response = RedirectResponse(next_path, 303)
        response.set_cookie('kb_session', token, max_age=SESSION_LIFETIME, httponly=True,
                            secure=app.state.cookie_secure, samesite='lax')
        response.delete_cookie('kb_signup_csrf')
        return response

    @contextmanager
    def admin(request, write=False):
        with database(db_path) as db:
            if write:
                db.execute('BEGIN IMMEDIATE')
            user, csrf = current_user(db, request)
            g['require_admin'](user)
            if write:
                supplied = request.headers.get('X-CSRF-Token', '')
                if not csrf or not hmac.compare_digest(supplied, csrf):
                    raise HTTPException(403, '表单已过期，请刷新页面')
            yield db, user, csrf

    async def payload(request):
        if request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
            raise HTTPException(415, '请提交 JSON 内容')
        try:
            data = json.loads(await bounded_body(request))
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(400, 'JSON 内容无效') from exc
        if not isinstance(data, dict):
            raise HTTPException(400, 'JSON 内容必须是对象')
        return data

    @app.get('/admin')
    def dashboard(request: Request):
        with admin(request) as (_, user, csrf):
            if any(key in request.query_params for key in ('q', 'category', 'page_number')):
                return RedirectResponse('/admin/permissions?' + str(request.url.query), 303)
            return page(request, 'admin_dashboard.html', user=user, csrf=csrf)

    @app.get('/api/admin/overview')
    def overview(request: Request):
        with admin(request) as (db, user, _):
            counts = {key: db.execute(sql).fetchone()[0] for key, sql in {
                'users': 'SELECT count(*) FROM users',
                'active_users': 'SELECT count(*) FROM users WHERE active=1',
                'new_users': f'SELECT count(*) FROM users WHERE created_at>={int(time.time()) - 7 * 86400}',
                'teams': 'SELECT count(*) FROM teams',
                'team_documents': 'SELECT count(*) FROM team_documents',
                'public_notes': "SELECT count(*) FROM notes WHERE visibility='public'",
                'private_notes': "SELECT count(*) FROM notes WHERE visibility='private'",
            }.items()}
            return {'counts': counts, 'settings': settings(db), 'sync': sync_summary(db),
                    'user': {'id': user['id'], 'username': user['username']}}

    @app.get('/api/admin/users')
    def users(request: Request, q: str = '', role: str = '', state: str = '', page: int = 1):
        if len(q) > 64 or role not in {'', 'admin', 'editor', 'reader'} or state not in {'', 'active', 'disabled'} or not 1 <= page <= 10000:
            raise HTTPException(400, '筛选条件无效')
        with admin(request) as (db, _, _):
            where, args = ['1=1'], []
            if q:
                where.append("username LIKE ? ESCAPE '\\'")
                args.append('%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
            if role:
                where.append('role=?')
                args.append(role)
            if state:
                where.append('active=?')
                args.append(int(state == 'active'))
            clause = ' AND '.join(where)
            total = db.execute('SELECT count(*) FROM users WHERE ' + clause, args).fetchone()[0]
            rows = db.execute(f'SELECT {ACCOUNT_FIELDS} FROM users WHERE {clause} ORDER BY id DESC LIMIT 20 OFFSET ?', (*args, (page - 1) * 20)).fetchall()
            return {'users': [dict(r) for r in rows], 'total': total, 'page': page}

    @app.patch('/api/admin/users/{user_id}')
    async def save_user(request: Request, user_id: int):
        data = await payload(request)
        with admin(request, True) as (db, actor, _):
            if not isinstance(data.get('role'), str):
                raise HTTPException(400, '角色无效')
            update_account(db, actor, user_id, role=data['role'], active=data.get('active'), version=data.get('version'))
        return {'ok': True}

    @app.post('/api/admin/settings')
    async def save_settings(request: Request):
        data = await payload(request)
        if type(data.get('registration_open')) is not bool or type(data.get('version')) is not int:
            raise HTTPException(400, '注册设置无效')
        with admin(request, True) as (db, user, _):
            current = settings(db)
            if data['version'] != current['version']:
                raise HTTPException(409, '设置已被其他管理员修改，请刷新后重试')
            db.execute("UPDATE site_settings SET value=?,version=version+1 WHERE key='registration_open'", ('1' if data['registration_open'] else '0',))
            audit(db, user['id'], 'registration.changed', 'registration_open', '开放注册' if data['registration_open'] else '暂停注册')
            return settings(db)

    @app.get('/api/admin/audit')
    def audit_log(request: Request, page: int = 1):
        if not 1 <= page <= 10000:
            raise HTTPException(400, '页码无效')
        with admin(request) as (db, _, _):
            total = db.execute('SELECT count(*) FROM site_audit').fetchone()[0]
            rows = db.execute('''SELECT a.id,a.action,a.target,a.detail,a.created_at,u.username AS actor
                                 FROM site_audit a LEFT JOIN users u ON u.id=a.actor_id
                                 ORDER BY a.id DESC LIMIT 20 OFFSET ?''', ((page - 1) * 20,)).fetchall()
            return {'events': [dict(row) for row in rows], 'total': total, 'page': page}
