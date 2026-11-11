"""Cookie/CSRF routes for account-owned independent practice checkpoints."""
import os
import time

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

import learning_service as learning


class DraftInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_version: int = Field(ge=1)
    answer: str = Field(max_length=5000)
    hint_used: bool = False


class CorrectionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_version: int = Field(ge=1)
    verdict: learning.VERDICTS
    comment: str = Field(default='', max_length=1000)


def register_learning(app, path, api):
    app.state.learning_enabled = os.getenv('KB_AGENT_LEARNING_ENABLED', 'false').lower() in {'true', '1'}
    with api['database'](path) as db:
        learning.initialize_learning(db)

    def actor(db, request, mutation=False):
        if not app.state.learning_enabled:
            raise HTTPException(404, '独立作答尚未启用。')
        user, csrf = api['current_user'](db, request)
        if user is None:
            raise HTTPException(401, '请先登录。')
        if mutation:
            api['require_csrf']({'csrf': request.headers.get('X-CSRF-Token', '')}, csrf)
        return user, csrf

    async def payload(request, schema):
        raw = await request.body()
        if len(raw) > 32768:
            raise HTTPException(413, '作答内容过长。')
        try:
            return schema.model_validate_json(raw)
        except ValidationError as exc:
            raise HTTPException(400, '作答参数无效。') from exc

    @app.post('/learn/cards/{card_id}/practice')
    async def start(request: Request, card_id: int):
        raw = await request.body()
        if len(raw) > 4096:
            raise HTTPException(413, '请求内容过长。')
        form = api['form_values'](raw)
        with api['database'](path) as db:
            db.execute('BEGIN IMMEDIATE')
            user, csrf = actor(db, request)
            api['require_csrf'](form, csrf)
            sid = learning.start(db, user['id'], card_id, form.get('practice_key', ''), form.get('project_id') or None)
        return RedirectResponse('/learn/practice/' + sid, 303)

    @app.get('/learn/practice/{sid}')
    def practice(request: Request, sid: str):
        with api['database'](path) as db:
            user, csrf = actor(db, request)
            session = learning.present(db, user['id'], sid)
            return api['page'](request, 'practice.html', user=user, csrf=csrf, session=session,
                               categories=api['categories'](db, user))

    @app.get('/api/learn/sessions/{sid}')
    def detail(request: Request, sid: str):
        with api['database'](path) as db:
            user, _ = actor(db, request)
            return learning.present(db, user['id'], sid)

    @app.patch('/api/learn/sessions/{sid}')
    async def save(request: Request, sid: str):
        data = await payload(request, DraftInput)
        with api['database'](path) as db:
            db.execute('BEGIN IMMEDIATE')
            user, _ = actor(db, request, True)
            return learning.save_draft(db, user['id'], sid, data.expected_version, data.answer, data.hint_used)

    @app.post('/api/learn/sessions/{sid}/attempts')
    async def submit(request: Request, sid: str):
        data = await payload(request, DraftInput)
        with api['database'](path) as db:
            db.execute('BEGIN IMMEDIATE')
            user, _ = actor(db, request, True)
            return learning.submit(app, db, user['id'], sid, data.expected_version, data.answer, data.hint_used)

    @app.post('/api/learn/sessions/{sid}/corrections')
    async def correct(request: Request, sid: str):
        data = await payload(request, CorrectionInput)
        with api['database'](path) as db:
            db.execute('BEGIN IMMEDIATE')
            user, _ = actor(db, request, True)
            session = learning.session_for(db, user['id'], sid)
            if session['status'] != 'reviewed' or session['version'] != data.expected_version:
                raise HTTPException(409, '学习记录已变化，当前输入请先保留并重新读取。')
            db.execute('INSERT INTO learning_feedback_corrections VALUES(?,?,?,?,?)',
                       (sid, session['version'] + 1, data.verdict, data.comment, int(time.time())))
            db.execute('UPDATE learning_sessions SET version=version+1,updated_at=? WHERE id=?', (int(time.time()), sid))
            return learning.present(db, user['id'], sid)
