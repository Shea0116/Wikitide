"""Thin cookie/CSRF and native-bearer adapters for durable account tasks."""
import asyncio
import json
import os
import time

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from agent_models import RunInput
from agent_runtime import RuntimeService


class ResumeInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    retry_uncertain: bool = False


def register_runtime(app, path, g):
    default = getattr(getattr(app.state, 'agent_memory', None), 'enabled', False)
    enabled = os.getenv('KB_AGENT_RUNTIME_ENABLED', str(default)).lower() in {'1', 'true', 'yes'}
    app.state.agent_runtime_enabled = enabled
    app.state.agent_runtime = RuntimeService(app, g['database'], path, enabled=enabled)
    from knowledge_jobs import initialize_knowledge_jobs
    with g['database'](path) as db:
        initialize_knowledge_jobs(db)

    def actor(request, mutation=False):
        if not app.state.agent_runtime_enabled: raise HTTPException(404, '后台任务尚未启用。')
        with g['database'](path) as db:
            user, csrf = g['current_user'](db, request)
            if not user: raise HTTPException(401, '请先登录。')
            if mutation and not request.url.path.startswith('/api/client/'):
                g['require_csrf']({'csrf': request.headers.get('X-CSRF-Token', '')}, csrf)
            return user['id']

    async def payload(request, schema):
        raw = await request.body()
        if len(raw) > 32768: raise HTTPException(413, '请求内容过长。')
        try: return schema.model_validate_json(raw)
        except ValidationError as exc: raise HTTPException(400, '任务参数格式无效。') from exc

    @app.get('/agent/tasks')
    def task_list(request: Request, cursor: int = 0):
        uid = actor(request)
        if cursor < 0:
            raise HTTPException(400, '分页参数无效。')
        with g['database'](path) as db:
            user, csrf = g['current_user'](db, request)
            rows = db.execute('''SELECT rowid AS cursor,id FROM agent_runs WHERE user_id=?
                AND kind IN ('knowledge','assessment') AND (?=0 OR rowid<?) ORDER BY rowid DESC LIMIT 21''', (uid, cursor, cursor)).fetchall()
            tasks = [app.state.agent_runtime.detail(db, uid, row['id']) for row in rows[:20]]
            return g['page'](request, 'knowledge_tasks.html', user=user, csrf=csrf, tasks=tasks,
                             next_cursor=rows[19]['cursor'] if len(rows) > 20 else None,
                             categories=g['categories'](db, user))

    @app.get('/agent/tasks/{rid}')
    def task_page(request: Request, rid: str):
        uid = actor(request)
        with g['database'](path) as db:
            user, csrf = g['current_user'](db, request)
            detail = app.state.agent_runtime.detail(db, uid, rid)
            return g['page'](request, 'knowledge_task.html', user=user, csrf=csrf, task=detail,
                             categories=g['categories'](db, user))

    def mount(prefix):
        @app.get(prefix + '/runs')
        def runs(request: Request, cursor: int = 0, limit: int = 20, project_id: str | None = None):
            uid = actor(request)
            if cursor < 0 or not 1 <= limit <= 100: raise HTTPException(400, '分页参数无效。')
            with g['database'](path) as db:
                if project_id:
                    from agent_projects import own_project
                    own_project(db, uid, project_id)
                rows = db.execute('''SELECT rowid AS cursor,id FROM agent_runs WHERE user_id=?
                    AND (?=0 OR rowid<?) AND (? IS NULL OR project_id=?) ORDER BY rowid DESC LIMIT ?''', (uid, cursor, cursor, project_id, project_id, limit + 1)).fetchall()
                return {'runs': [app.state.agent_runtime.detail(db, uid, row['id']) for row in rows[:limit]],
                        'next_cursor': rows[limit-1]['cursor'] if len(rows) > limit else None}

        @app.post(prefix + '/runs')
        async def create(request: Request):
            uid, data = actor(request, True), await payload(request, RunInput)
            with g['database'](path) as db:
                db.execute('BEGIN IMMEDIATE')
                return app.state.agent_runtime.create(db, uid, data)

        @app.get(prefix + '/runs/{rid}')
        def detail(request: Request, rid: str):
            uid = actor(request)
            with g['database'](path) as db: return app.state.agent_runtime.detail(db, uid, rid)

        @app.get(prefix + '/runs/{rid}/knowledge')
        def knowledge_result(request: Request, rid: str):
            uid = actor(request)
            from knowledge_jobs import result
            with g['database'](path) as db:
                return result(app.state.agent_runtime, db, uid, rid)

        @app.post(prefix + '/runs/{rid}/cancel')
        def cancel(request: Request, rid: str):
            uid = actor(request, True)
            with g['database'](path) as db:
                db.execute('BEGIN IMMEDIATE')
                return app.state.agent_runtime.cancel(db, uid, rid)

        @app.post(prefix + '/runs/{rid}/resume')
        async def resume(request: Request, rid: str):
            uid, data = actor(request, True), await payload(request, ResumeInput)
            with g['database'](path) as db:
                db.execute('BEGIN IMMEDIATE')
                return app.state.agent_runtime.resume(db, uid, rid, data.retry_uncertain)

        @app.get(prefix + '/runs/{rid}/events')
        def events(request: Request, rid: str, after_seq: int = 0, limit: int = 100):
            uid = actor(request)
            if after_seq < 0 or not 1 <= limit <= 200: raise HTTPException(400, '事件分页参数无效。')
            with g['database'](path) as db:
                initial = app.state.agent_runtime.events(db, uid, rid, after_seq, limit)
            if 'text/event-stream' not in request.headers.get('accept', ''): return initial
            async def stream():
                cursor, deadline = after_seq, time.monotonic() + 50
                yield ': connected\n\n'
                while time.monotonic() < deadline:
                    # Do not keep a stream alive after account/session revocation.
                    try:
                        if actor(request) != uid: break
                        with g['database'](path) as db:
                            page = app.state.agent_runtime.events(db, uid, rid, cursor, limit)
                            run = app.state.agent_runtime.owned(db, uid, rid)
                    except HTTPException: break
                    for event in page['events']:
                        cursor = event['seq']
                        yield 'id: ' + str(cursor) + '\nevent: ' + event['type'] + '\ndata: ' + json.dumps(event, ensure_ascii=False) + '\n\n'
                    if run['status'] in {'succeeded', 'failed', 'cancelled', 'waiting_user'} and not page['has_more']: break
                    if await request.is_disconnected(): break
                    if not page['has_more']:
                        yield ': keepalive\n\n'
                        await asyncio.sleep(1)
            return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-store, no-transform', 'X-Accel-Buffering': 'no'})

    mount('/api/agent/v1'); mount('/api/client/v1/agent')
