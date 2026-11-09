"""Browser-scoped exploration trials, isolated from real accounts and their data."""
import hashlib
import secrets
import time

from fastapi import HTTPException

COOKIE = 'kb_explore_guest'
LIMIT = 3
LIFETIME = 365 * 86400
EXHAUSTED = '三次免费体验已用完，请登录后继续使用知识求索。'


def initialize_trials(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS explore_guests (
        id TEXT PRIMARY KEY, csrf TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS explore_trial_leases (
        id TEXT PRIMARY KEY, guest_id TEXT NOT NULL REFERENCES explore_guests(id) ON DELETE CASCADE,
        expires_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_explore_trial_guest ON explore_trial_leases(guest_id);
    ''')
    # Mirror only exploration storage. Existing account tables need no migration.
    names = ('sessions', 'messages', 'requests', 'drafts')
    for name in names:
        row = db.execute('SELECT sql FROM sqlite_master WHERE type=\'table\' AND name=?', ('explore_' + name,)).fetchone()
        sql = row['sql']
        for target in names:
            sql = sql.replace('explore_' + target, 'explore_guest_' + target)
        sql = sql.replace('CREATE TABLE ', 'CREATE TABLE IF NOT EXISTS ', 1)
        sql = sql.replace('user_id INTEGER', 'user_id TEXT').replace('REFERENCES users(id)', 'REFERENCES explore_guests(id)')
        db.execute(sql)
    for name in ('sessions', 'drafts', 'requests'):
        db.execute(f'CREATE INDEX IF NOT EXISTS ix_explore_guest_{name}_user ON explore_guest_{name}(user_id)')


def is_guest(user):
    return user is not None and 'guest' in user.keys()


def explore_table(user, name):
    assert name in {'sessions', 'messages', 'requests', 'drafts'}
    return ('explore_guest_' if is_guest(user) else 'explore_') + name


def guest_account(db, request):
    token = request.cookies.get(COOKIE, '')
    if len(token) != 43:
        return None, None
    guest_id = hashlib.sha256(token.encode()).hexdigest()
    row = db.execute('SELECT * FROM explore_guests WHERE id=?', (guest_id,)).fetchone()
    return ({'id': guest_id, 'guest': True, 'role': 'guest'}, row['csrf']) if row else (None, None)


def trial_page(db, request, app, render):
    guest, csrf = guest_account(db, request)
    token = None
    if guest is None:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        guest = {'id': hashlib.sha256(token.encode()).hexdigest(), 'guest': True}
        db.execute('INSERT INTO explore_guests(id,csrf,created_at) VALUES(?,?,?)',
                   (guest['id'], csrf, int(time.time())))
    response = render(csrf, trial_status(db, guest))
    response.headers['Cache-Control'] = 'no-store'
    if token:
        response.set_cookie(COOKIE, token, max_age=LIFETIME, httponly=True,
                            secure=app.state.cookie_secure, samesite='lax')
    return response


def trial_status(db, user):
    if not is_guest(user):
        return None
    used = db.execute('SELECT used FROM explore_guests WHERE id=?', (user['id'],)).fetchone()[0]
    pending = db.execute('SELECT count(*) FROM explore_trial_leases WHERE guest_id=? AND expires_at>?',
                         (user['id'], int(time.time()))).fetchone()[0]
    return {'limit': LIMIT, 'remaining': max(0, LIMIT-used-pending)}


def check_trial(db, user):
    status = trial_status(db, user)
    if status is not None and status['remaining'] == 0:
        raise HTTPException(429, EXHAUSTED)


def reserve_trial(db, user):
    if not is_guest(user):
        return None
    # Caller holds BEGIN IMMEDIATE, so concurrent requests cannot overspend.
    db.execute('DELETE FROM explore_trial_leases WHERE expires_at<=?', (int(time.time()),))
    check_trial(db, user)
    lease = secrets.token_urlsafe(24)
    db.execute('INSERT INTO explore_trial_leases VALUES(?,?,?)', (lease, user['id'], int(time.time())+900))
    return lease


def finish_trial(db, lease, success=False):
    if lease is None:
        return
    row = db.execute('DELETE FROM explore_trial_leases WHERE id=? RETURNING guest_id,expires_at', (lease,)).fetchone()
    if success and (row is None or row['expires_at'] <= int(time.time())):
        raise HTTPException(409, '本次体验请求已过期，请重新尝试。')
    if row and success:
        db.execute('UPDATE explore_guests SET used=used+1 WHERE id=?', (row['guest_id'],))
