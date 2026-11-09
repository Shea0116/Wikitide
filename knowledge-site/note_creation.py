"""A discoverable first-note flow that uses existing team ownership and permissions."""
import time

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from teams import WRITERS, create_document_record, text


def register_note_creation(app, db_path, g):
    def spaces(db, user):
        return db.execute('''SELECT t.id,t.name FROM teams t JOIN team_members m ON m.team_id=t.id
            WHERE m.user_id=? AND m.role IN ('owner','admin','editor') ORDER BY t.id DESC''', (user['id'],)).fetchall()

    def render(request, db, user, csrf, values=None, error='', status=200):
        targets = spaces(db, user)
        values = values if values is not None else {'team_id': '' if targets else 'new', 'space_name': g['ui']('我的笔记')}
        return g['page'](request, 'create_note.html', user=user, csrf=csrf, categories=g['categories'](db, user),
                         spaces=targets, values=values, error=error, status_code=status)

    @app.get('/create')
    def page(request: Request):
        with g['database'](db_path) as db:
            user, csrf = g['current_user'](db, request)
            if user is None:
                return RedirectResponse('/login?next=/create', 303)
            if user['role'] == 'admin':
                return RedirectResponse('/new', 303)
            return render(request, db, user, csrf)

    @app.post('/create')
    async def create(request: Request):
        raw = await request.body()
        if len(raw) > 512 * 1024:
            raise HTTPException(413, '正文过大，请缩短后再创建')
        form = g['form_values'](raw)
        with g['database'](db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            user, csrf = g['current_user'](db, request)
            if user is None:
                raise HTTPException(401, '请先登录')
            g['require_csrf'](form, csrf)
            try:
                title, body = text(form, 'title', 160), text(form, 'body', 100000)
                if form.get('team_id') == 'new':
                    name = text(form, 'space_name', 80)
                    count = db.execute("SELECT count(*) FROM team_members WHERE user_id=? AND role='owner'", (user['id'],)).fetchone()[0]
                    if count >= 20:
                        raise HTTPException(409, '每个账户最多拥有 20 个团队空间')
                    now = int(time.time())
                    team_id = db.execute('INSERT INTO teams(name,created_at) VALUES(?,?)', (name, now)).lastrowid
                    db.execute("INSERT INTO team_members VALUES(?,?,'owner',?)", (team_id, user['id'], now))
                    db.execute("INSERT INTO team_events(team_id,actor_id,action,detail,created_at) VALUES(?,?,'team.created',?,?)", (team_id, user['id'], f'创建空间「{name}」', now))
                    db.execute('UPDATE teams SET version=version+1 WHERE id=?', (team_id,))
                else:
                    selected = form.get('team_id', '')
                    if not 1 <= len(selected) <= 19 or not selected.isascii() or not selected.isdigit() or not 0 < int(selected) <= 2**63 - 1:
                        raise HTTPException(400, '请选择保存笔记的空间')
                    team_id = int(selected)
                    member = db.execute('SELECT role FROM team_members WHERE team_id=? AND user_id=?', (team_id, user['id'])).fetchone()
                    if member is None:
                        raise HTTPException(404, '空间不存在或你已不在该空间')
                    if member['role'] not in WRITERS:
                        raise HTTPException(403, '当前空间角色不能创建笔记，请选择自己的空间')
                identity = create_document_record(db, team_id, user, title, body)
            except HTTPException as exc:
                # In particular, a failed first-note insert must not leave an empty space behind.
                db.rollback()
                return render(request, db, user, csrf, form, exc.detail, exc.status_code)
        return RedirectResponse(f'/teams?space={team_id}&document={identity}', 303)
