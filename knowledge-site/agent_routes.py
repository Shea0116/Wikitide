"""Cookie-authenticated account context endpoints. Guest memory stays disabled."""
from fastapi import HTTPException, Request
from pydantic import ValidationError

from agent_models import ConstraintInput


def register_context_routes(app, db_path, g):
    def account(request, mutation=False):
        if not app.state.agent_context.policy.enabled:
            raise HTTPException(404, '会话上下文功能尚未启用。')
        with g['database'](db_path) as db:
            user, csrf = g['current_user'](db, request)
            if not user:
                raise HTTPException(401, '请登录后管理会话要求。')
            if mutation:
                g['require_csrf']({'csrf': request.headers.get('X-CSRF-Token', '')}, csrf)
            return user['id']

    @app.get('/api/agent/v1/capabilities')
    def capabilities(request: Request):
        with g['database'](db_path) as db:
            user, _ = g['current_user'](db, request)
        enabled = app.state.agent_context.policy.enabled and user is not None
        workspace=app.state.agent_memory.enabled and user is not None
        return {'api_version': 1, 'context_enabled': enabled, 'memory_enabled': workspace,
                'projects_enabled': workspace, 'local_memory_enabled': bool(getattr(app.state,'agent_transient_enabled',False) and user),
                'runtime_enabled':bool(getattr(app.state,'agent_runtime_enabled',False) and user)}

    @app.get('/api/agent/v1/sessions/{session_id}/context')
    def context(request: Request, session_id: str):
        return app.state.agent_context.context_detail(account(request), session_id)

    @app.patch('/api/agent/v1/sessions/{session_id}/constraints')
    async def constraints(request: Request, session_id: str):
        user_id = account(request, True)
        raw = await request.body()
        if len(raw) > 8192:
            raise HTTPException(413, '固定要求过长。')
        try:
            data = ConstraintInput.model_validate_json(raw)
        except ValidationError as exc:
            raise HTTPException(400, '固定要求格式无效：最多 8 条，每条 250 字，总计 1000 字。') from exc
        return app.state.agent_context.update_constraints(user_id, session_id, data)
