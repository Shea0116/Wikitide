"""Account-scoped API for the native WikiTide clients.

Uses revocable, expiring sessions, never the privileged file-sync token.
Cookies are deliberately ignored on these routes: writes require a bearer token.
"""
from __future__ import annotations

import math
import time

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from features import category_filter, personal_notes, search_notes, note_extras
from navigation_order import sort_note_rows
from storage import (SESSION_LIFETIME, can_edit, can_view, create_session, digest,
                     password_matches, save_note, visible_where)


def user_info(user):
    return {key: user[key] for key in ('id', 'username', 'role')} if user else None


def note_info(note):
    keys = ('id', 'title', 'summary', 'category', 'visibility', 'updated_at')
    item = {key: note[key] for key in keys if key in note.keys()}
    if 'position' in note.keys():
        item['position'] = note['position']
    return item


def register_client_api(app, db_path, g):
    database, current_user = g['database'], g['current_user']
    prefix = '/api/client/v1'

    async def payload(request):
        raw = await request.body()
        if len(raw) > g['MAX_BODY']:
            raise HTTPException(413, '内容过大')
        try:
            data = await request.json()
        except ValueError as exc:
            raise HTTPException(400, '请求格式无效') from exc
        if not isinstance(data, dict):
            raise HTTPException(400, '请求格式无效')
        return data

    def logged_in(db, request):
        user, _ = current_user(db, request)
        if not user:
            raise HTTPException(401, '请先登录')
        return user

    def authorized(db, request, note_id, edit=False):
        user, _ = current_user(db, request)
        note = db.execute('SELECT * FROM notes WHERE id=?', (note_id,)).fetchone()
        if note is None or not (can_edit(db, note, user) if edit else can_view(db, note, user)):
            raise HTTPException(404, '笔记不存在或无权访问')
        return note, user

    def detail(db, note, user):
        extras = note_extras(db, user, note, g['MARKDOWN'])
        item = note_info(note)
        item.update(html=str(g['rendered_markdown'](db, note, user)),
                    body_sha=note['body_sha'], editable=can_edit(db, note, user),
                    related=[note_info(row) for row in extras['related']],
                    backlinks=[note_info(row) for row in extras['backlinks']],
                    reading=dict(extras['reading']) if extras['reading'] else None)
        # Source is only needed by editors; readers receive sanitized HTML.
        if item['editable']:
            item['body'] = note['body']
        return item

    @app.get(prefix + '/session')
    def session(request: Request):
        with database(db_path) as db:
            user, _ = current_user(db, request)
            return {'api_version': 1, 'user': user_info(user)}

    @app.post(prefix + '/login')
    async def login(request: Request):
        data = await payload(request)
        username, password = data.get('username'), data.get('password')
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 64 or len(password) > 1024:
            raise HTTPException(400, '用户名或密码格式无效')
        username = username.strip()
        with database(db_path) as db:
            key = digest((request.client.host if request.client else '') + ':' + username)
            now = int(time.time())
            db.execute('DELETE FROM login_attempts WHERE happened_at<?', (now - 900,))
            count = db.execute('SELECT COUNT(*) FROM login_attempts WHERE key=?', (key,)).fetchone()[0]
            if count >= 10:
                return JSONResponse({'detail': '尝试次数过多，请稍后再试'}, 429)
            user = db.execute('SELECT * FROM users WHERE username=? AND active=1', (username,)).fetchone()
            if not user or not password_matches(password, user['password_hash']):
                db.execute('INSERT INTO login_attempts(key,happened_at) VALUES(?,?)', (key, now))
                # Return inside the transaction so failed attempts are committed.
                return JSONResponse({'detail': '用户名或密码错误'}, 401)
            db.execute('DELETE FROM login_attempts WHERE key=?', (key,))
            token, _ = create_session(db, user['id'])
            return {'token': token, 'expires_in': SESSION_LIFETIME, 'user': user_info(user)}

    @app.post(prefix + '/logout')
    def logout(request: Request):
        with database(db_path) as db:
            user = logged_in(db, request)
            db.execute('DELETE FROM sessions WHERE token_hash=? AND user_id=?',
                       (user['token_hash'], user['id']))
        return {'ok': True}

    @app.get(prefix + '/home')
    def home(request: Request):
        with database(db_path) as db:
            user, _ = current_user(db, request)
            where, params = visible_where(user)
            recent = db.execute(f"SELECT * FROM notes WHERE {where} AND category!='知识导览' ORDER BY updated_at DESC,id DESC LIMIT 12", params).fetchall()
            guides = db.execute(f"SELECT * FROM notes WHERE {where} AND category='知识导览' ORDER BY title LIMIT 20", params).fetchall()
            return {'categories': g['categories'](db, user),
                    'recent': [note_info(row) for row in recent],
                    'guides': [note_info(row) for row in guides],
                    'continue_reading': [note_info(row) for row in personal_notes(db, user)[:4]]}

    @app.get(prefix + '/notes')
    def browse(request: Request, category: str = '', page: int = 1):
        if not 1 <= page <= 10000 or len(category) > 240:
            raise HTTPException(400, '分类或页码无效')
        with database(db_path) as db:
            user, _ = current_user(db, request)
            where, params = category_filter(*visible_where(user), category)
            rows = db.execute(f'SELECT id,title,summary,category,visibility,updated_at FROM notes WHERE {where} ORDER BY category,title,id', params).fetchall()
            rows = sort_note_rows(db, user, rows)
            start = (page - 1) * 50
            return {'notes': [note_info(row) for row in rows[start:start + 50]],
                    'total': len(rows), 'has_more': len(rows) > start + 50}

    @app.get(prefix + '/search')
    def search(request: Request, q: str = '', category: str = '', page: int = 1):
        if not 1 <= page <= 10000 or len(q) > 100 or len(category) > 240:
            raise HTTPException(400, '搜索条件无效')
        with database(db_path) as db:
            user, _ = current_user(db, request)
            rows, total = search_notes(db, user, q.strip(), category, page)
            # Do not accidentally serialize SELECT * bodies into search results.
            items = [dict(note_info(row), snippet_html=str(row['snippet'])) for row in rows]
            return {'notes': items, 'total': total, 'has_more': total > page * 20}

    @app.get(prefix + '/library')
    def library(request: Request):
        with database(db_path) as db:
            user = logged_in(db, request)
            return {'bookmarks': [note_info(row) for row in personal_notes(db, user, True)],
                    'recent': [note_info(row) for row in personal_notes(db, user)]}

    @app.get(prefix + '/notes/{note_id}')
    def note(request: Request, note_id: int):
        with database(db_path) as db:
            note, user = authorized(db, request, note_id)
            return detail(db, note, user)

    @app.put(prefix + '/notes/{note_id}/bookmark')
    async def bookmark(request: Request, note_id: int):
        data = await payload(request)
        value = data.get('bookmarked')
        if not isinstance(value, bool):
            raise HTTPException(400, '收藏状态无效')
        with database(db_path) as db:
            user = logged_in(db, request)
            authorized(db, request, note_id)
            db.execute('INSERT INTO reading(user_id,note_id,bookmarked) VALUES(?,?,?) ON CONFLICT(user_id,note_id) DO UPDATE SET bookmarked=excluded.bookmarked',
                       (user['id'], note_id, int(value)))
        return {'bookmarked': value}

    @app.put(prefix + '/notes/{note_id}/reading')
    async def reading(request: Request, note_id: int):
        data = await payload(request)
        value = data.get('position')
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise HTTPException(400, '阅读位置无效')
        with database(db_path) as db:
            user = logged_in(db, request)
            authorized(db, request, note_id)
            db.execute('INSERT INTO reading(user_id,note_id,position,read_at) VALUES(?,?,?,?) ON CONFLICT(user_id,note_id) DO UPDATE SET position=excluded.position,read_at=excluded.read_at',
                       (user['id'], note_id, value, int(time.time())))
        return {'ok': True}

    @app.put(prefix + '/notes/{note_id}')
    async def edit(request: Request, note_id: int):
        data = await payload(request)
        body = data.get('body')
        if not isinstance(body, str) or not body.strip():
            raise HTTPException(400, '笔记内容不能为空')
        with database(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            note, user = authorized(db, request, note_id, edit=True)
            if data.get('base_sha') != note['body_sha']:
                return JSONResponse({'detail': '正文已更新，请对照最新正文合并后保存',
                                     'latest': {'body': note['body'], 'body_sha': note['body_sha']}}, 409)
            save_note(db, note, body, 'web', user['username'])
            latest = db.execute('SELECT * FROM notes WHERE id=?', (note_id,)).fetchone()
            return detail(db, latest, user)

    @app.post(prefix + '/notes/{note_id}/preview')
    async def preview(request: Request, note_id: int):
        data = await payload(request)
        if not isinstance(data.get('body'), str):
            raise HTTPException(400, '正文格式无效')
        with database(db_path) as db:
            note, user = authorized(db, request, note_id, edit=True)
            copy = dict(note, body=data['body'])
            return {'html': str(g['rendered_markdown'](db, copy, user))}

    @app.get(prefix + '/notes/{note_id}/history')
    def history(request: Request, note_id: int):
        with database(db_path) as db:
            authorized(db, request, note_id, edit=True)
            rows = db.execute('SELECT id,body_sha,actor,origin,created_at FROM revisions WHERE note_id=? ORDER BY id DESC LIMIT 100', (note_id,)).fetchall()
            return {'revisions': [dict(row) for row in rows]}

    @app.get(prefix + '/notes/{note_id}/history/{revision_id}')
    def revision(request: Request, note_id: int, revision_id: int):
        with database(db_path) as db:
            authorized(db, request, note_id, edit=True)
            row = db.execute('SELECT id,body,body_sha,actor,created_at FROM revisions WHERE note_id=? AND id=?', (note_id, revision_id)).fetchone()
            if not row:
                raise HTTPException(404, '版本不存在')
            return dict(row)

    @app.post(prefix + '/notes/{note_id}/restore')
    async def restore(request: Request, note_id: int):
        data = await payload(request)
        with database(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            note, user = authorized(db, request, note_id, edit=True)
            if data.get('base_sha') != note['body_sha']:
                return JSONResponse({'detail': '正文已更新，请重新载入后恢复'}, 409)
            revision_id = data.get('revision_id')
            if not isinstance(revision_id, int) or isinstance(revision_id, bool):
                raise HTTPException(400, '版本无效')
            row = db.execute('SELECT body FROM revisions WHERE note_id=? AND id=?', (note_id, revision_id)).fetchone()
            if not row:
                raise HTTPException(404, '版本不存在')
            save_note(db, note, row['body'], 'web', user['username'])
            return {'ok': True}
