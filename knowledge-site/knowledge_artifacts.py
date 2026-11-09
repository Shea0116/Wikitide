"""Research-to-draft provenance and explicitly reviewed, version-fenced note updates."""
import difflib
import json
import os
import secrets
import time
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_context import compact_json
from storage import can_edit, can_view, digest, save_note, visible_where


def initialize_artifacts(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS knowledge_draft_origins (
            run_id TEXT PRIMARY KEY REFERENCES agent_runs(id) ON DELETE CASCADE,
            draft_id TEXT UNIQUE REFERENCES explore_drafts(id) ON DELETE SET NULL,
            refs_json TEXT NOT NULL,private_only INTEGER NOT NULL,created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS knowledge_note_proposals (
            id TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            request_sha TEXT NOT NULL,draft_id TEXT REFERENCES explore_drafts(id) ON DELETE SET NULL,
            draft_version INTEGER NOT NULL,note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
            target_version TEXT NOT NULL,base_body TEXT NOT NULL,proposed_body TEXT NOT NULL,
            mode TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'review',
            result_sha TEXT NOT NULL DEFAULT '',created_at INTEGER NOT NULL,applied_at INTEGER,
            UNIQUE(user_id,request_sha)
        );
        CREATE TABLE IF NOT EXISTS discussion_draft_origins (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            session_id TEXT NOT NULL,message_id INTEGER NOT NULL,
            draft_id TEXT UNIQUE REFERENCES explore_drafts(id) ON DELETE SET NULL,
            snapshot_sha TEXT NOT NULL,refs_json TEXT NOT NULL,
            private_only INTEGER NOT NULL DEFAULT 1,created_at INTEGER NOT NULL,
            PRIMARY KEY(user_id,session_id,message_id)
        );
    ''')


def origin(db, draft, *, verify=True):
    from explore import sources_valid
    row = db.execute('SELECT * FROM knowledge_draft_origins WHERE draft_id=?', (draft['id'],)).fetchone()
    if row is None:
        row = db.execute('SELECT * FROM discussion_draft_origins WHERE draft_id=? AND user_id=?', (draft['id'], draft['user_id'])).fetchone()
        if row and verify:
            snapshot = discussion_snapshot(db, draft['user_id'], row['message_id'], session_id=row['session_id'])
            if snapshot['sha'] != row['snapshot_sha']:
                raise HTTPException(409, '源讨论已变化或不再可用，请重新选择回答。')
    if row and verify and not sources_valid(db, json.loads(row['refs_json']), user_id=draft['user_id']):
        raise HTTPException(409, '研究草稿依赖的资料或权限已变化，请重新研究。')
    return row


def discussion_snapshot(db, uid, mid, *, session_id=None):
    """Conservatively retain all dependencies in the prefix leading to an answer."""
    from explore import sources_valid
    from knowledge_scope import session_note_ids
    message = db.execute('''SELECT m.* FROM explore_messages m JOIN explore_sessions s ON s.id=m.session_id
        WHERE m.id=? AND s.user_id=?''', (mid, uid)).fetchone()
    if message is None or message['role'] != 'assistant' or (session_id and message['session_id'] != session_id):
        raise HTTPException(404, '源回答不存在。')
    session = db.execute('SELECT * FROM explore_sessions WHERE id=?', (message['session_id'],)).fetchone()
    rows = [dict(row) for row in db.execute('''SELECT m.id,m.role,m.content,m.sources,
        coalesce(a.sources_json,'[]') AS context_sources,coalesce(a.invalidated,0) AS invalidated
        FROM explore_messages m LEFT JOIN agent_answer_context a ON a.message_id=m.id
        WHERE m.session_id=? AND m.id<=? ORDER BY m.id''', (session['id'], mid))]
    if len(rows) < 2 or len(rows) % 2 or any(row['role'] != ('user' if index % 2 == 0 else 'assistant') for index, row in enumerate(rows)):
        raise HTTPException(409, '讨论记录不完整，请重新提问。')
    suppressed = {row[0] for row in db.execute('SELECT source_message_id FROM agent_memory_suppressions WHERE user_id=?', (uid,))}
    refs = []
    for row in rows:
        if row['invalidated'] or row['id'] in suppressed:
            raise HTTPException(409, '源讨论已变化或不再可用，请重新选择回答。')
        refs.extend(json.loads(row['sources']) + json.loads(row['context_sources']))
    if not sources_valid(db, refs, user_id=uid):
        raise HTTPException(409, '讨论依赖的资料或记忆已失效，请重新提问。')
    if session['scope'] == 'selected':
        # Older turns may lack complete selection provenance; never invent old versions.
        covered = {ref['note_id'] for ref in refs if ref.get('kind') == 'account_note' and ref.get('user_id') == uid}
        if not set(session_note_ids(db, session)).issubset(covered):
            raise HTTPException(409, '旧回答缺少完整资料版本，请重新提问后再保存。')
    keys = ('kind', 'note_id', 'body_sha', 'url', 'id', 'user_id', 'revision')
    refs = list({compact_json({key: ref[key] for key in keys if key in ref}):
                 {key: ref[key] for key in keys if key in ref} for ref in refs}.values())
    return {'sha': digest(compact_json(rows)), 'session_id': session['id'], 'refs': refs,
            'answer': message['content'], 'sources': json.loads(message['sources']), 'question': rows[-2]['content']}


def transfer_discussion(app, db, user, mid):
    from explore_write import format_generated, validate_document
    from agent_dependencies import record_dependencies
    snapshot = discussion_snapshot(db, user['id'], mid)
    previous = db.execute('SELECT * FROM discussion_draft_origins WHERE user_id=? AND session_id=? AND message_id=?',
                          (user['id'], snapshot['session_id'], mid)).fetchone()
    if previous:
        if not previous['draft_id']:
            raise HTTPException(409, '这条回答对应的草稿已删除，请重新提问后再保存。')
        draft = owned_draft(app, db, user, previous['draft_id'])
        return {'draft_id': draft['id'], 'url': '/explore/write?draft_id=' + draft['id']}
    if db.execute('SELECT count(*) FROM explore_drafts WHERE user_id=?', (user['id'],)).fetchone()[0] >= 200:
        raise HTTPException(429, '文稿已达到 200 份，请先导出并清理旧草稿。')
    body = format_generated(snapshot['answer'])
    if len(body) > 60000:
        raise HTTPException(413, '回答过长，请分段整理。')
    _, used = validate_document(body, snapshot['sources'])
    if not used:
        raise HTTPException(422, '这条回答没有有效来源引用，请重新提问后再整理。')
    did, now = secrets.token_urlsafe(24), int(time.time())
    title = snapshot['question'][:200]
    db.execute('''INSERT INTO explore_drafts(id,user_id,topic,title,body,sources,stage,created_at,updated_at)
        VALUES(?,?,?,?,?,?,'draft',?,?)''', (did, user['id'], title, title, body, compact_json(snapshot['sources']), now, now))
    link = db.execute('SELECT project_id FROM agent_session_links WHERE session_id=?', (snapshot['session_id'],)).fetchone()
    project_id = link['project_id'] if link else None
    db.execute("INSERT INTO agent_draft_context(draft_id,project_id,memory_mode) VALUES(?,?,'off')", (did, project_id))
    if project_id:
        db.execute('INSERT OR IGNORE INTO agent_project_links(project_id,resource_kind,resource_id,created_at) VALUES(?,?,?,?)', (project_id, 'draft', did, now))
    db.execute('INSERT INTO discussion_draft_origins VALUES(?,?,?,?,?,?,1,?)',
               (user['id'], snapshot['session_id'], mid, did, snapshot['sha'], compact_json(snapshot['refs']), now))
    record_dependencies(db, user['id'], snapshot['refs'], 'draft', did)
    return {'draft_id': did, 'url': '/explore/write?draft_id=' + did}


def private_only(db, draft):
    row = origin(db, draft)
    return bool((row and row['private_only']) or any(ref.get('kind') == 'account_note' for ref in json.loads(draft['sources'])))


def note_version(note):
    # Include path and visibility as well as text: the reviewed destination is fixed.
    return digest(compact_json([note[key] for key in ('body_sha', 'path', 'title', 'visibility', 'summary', 'category')]))


def owned_draft(app, db, user, did, *, editable=False):
    row = db.execute('SELECT * FROM explore_drafts WHERE id=? AND user_id=?', (did, user['id'])).fetchone()
    if row is None:
        raise HTTPException(404, '文稿不存在')
    app.state.writing_detail(db, row)  # Shared citation, memory and origin validation.
    if editable and (row['saved_note_id'] or row['busy_until'] > int(time.time())):
        raise HTTPException(409, '文稿已保存或仍在生成，请重新打开。')
    return row


def transfer(app, db, user, rid):
    from knowledge_jobs import result
    from explore_write import format_generated, validate_document
    from agent_dependencies import record_dependencies
    runtime = app.state.agent_runtime
    report = result(runtime, db, user['id'], rid)
    previous = db.execute('SELECT draft_id FROM knowledge_draft_origins WHERE run_id=?', (rid,)).fetchone()
    if previous:
        if not previous['draft_id']:
            raise HTTPException(409, '这次研究对应的草稿已删除，请新建研究任务。')
        draft = owned_draft(app, db, user, previous['draft_id'])
        return {'draft_id': draft['id'], 'url': '/explore/write?draft_id=' + draft['id']}
    if db.execute('SELECT count(*) FROM explore_drafts WHERE user_id=?', (user['id'],)).fetchone()[0] >= 200:
        raise HTTPException(429, '文稿已达到 200 份，请先导出并清理旧草稿。')
    run = runtime.owned(db, user['id'], rid)
    data = json.loads(run['input_json'])
    body = format_generated(report['body'])
    _, used = validate_document(body, report['sources'])
    if not used:
        raise HTTPException(422, '正文没有有效来源引用，本次结果未保存，请重试。')
    refs = list({compact_json({key: ref[key] for key in ('kind', 'note_id', 'body_sha', 'user_id') if key in ref}):
                 {key: ref[key] for key in ('kind', 'note_id', 'body_sha', 'user_id') if key in ref}
                 for ref in runtime.references(db, rid)}.values())
    did, now = secrets.token_urlsafe(24), int(time.time())
    title = data['topic'][:200]
    db.execute('''INSERT INTO explore_drafts(id,user_id,topic,requirements,title,body,sources,stage,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,'draft',?,?)''', (did, user['id'], title, data['requirements'], title, body, compact_json(report['sources']), now, now))
    project_id = data.get('project_id')
    db.execute("INSERT INTO agent_draft_context(draft_id,project_id,memory_mode) VALUES(?,?,'off')", (did, project_id))
    if project_id:
        db.execute('INSERT OR IGNORE INTO agent_project_links(project_id,resource_kind,resource_id,created_at) VALUES(?,?,?,?)', (project_id, 'draft', did, now))
    db.execute('INSERT INTO knowledge_draft_origins VALUES(?,?,?,?,?)', (rid, did, compact_json(refs), int(any(ref['kind'] == 'account_note' for ref in refs)), now))
    record_dependencies(db, user['id'], refs, 'draft', did)
    return {'draft_id': did, 'url': '/explore/write?draft_id=' + did}


def target_for(db, user, nid, draft):
    note = db.execute('SELECT * FROM notes WHERE id=?', (nid,)).fetchone()
    if note is None or not can_edit(db, note, user):
        raise HTTPException(404, '目标笔记不可编辑。')
    if private_only(db, draft) and note['visibility'] != 'private':
        raise HTTPException(403, '所选资料生成的草稿只能存入私密笔记。')
    if len(note['body']) > 200000:
        raise HTTPException(422, '目标笔记过长，请使用原笔记编辑器分段整理。')
    return note


def targets(app, db, user, did, query):
    draft = owned_draft(app, db, user, did, editable=True)
    access, params = visible_where(user)
    refs = json.loads(draft['sources'])
    ids = {ref.get('note_id') for ref in refs if ref.get('note_id')}
    rows = db.execute(f'''SELECT * FROM notes WHERE {access}
        AND (instr(lower(title),lower(?))>0 OR instr(lower(path),lower(?))>0)
        ORDER BY title,id''', (*params, query, query))
    items = []
    restricted = private_only(db, draft)
    for note in rows:
        if not can_edit(db, note, user) or (restricted and note['visibility'] != 'private'):
            continue
        items.append({'id': note['id'], 'title': note['title'], 'path': note['path'], 'visibility': note['visibility'],
                      'version': note_version(note), 'cited': note['id'] in ids})
    items.sort(key=lambda item: not item['cited'])
    return {'targets': items[:30], 'private_only': restricted}


class ProposalInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    draft_version: int = Field(ge=0)
    note_id: int = Field(gt=0)
    target_version: str = Field(min_length=64, max_length=64)
    mode: Literal['append', 'replace'] = 'append'


class ApplyInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    confirmed: bool = False


def propose(app, db, user, did, data, base_url):
    from explore_write import export_document
    draft = owned_draft(app, db, user, did, editable=True)
    if draft['version'] != data.draft_version:
        raise HTTPException(409, '草稿已变化，请重新打开并审阅。')
    note = target_for(db, user, data.note_id, draft)
    if note_version(note) != data.target_version:
        raise HTTPException(409, '目标笔记已变化，请刷新目标并重新审阅差异。')
    if not draft['body'].strip():
        raise HTTPException(400, '文稿正文不能为空。')
    text = export_document(draft['body'], json.loads(draft['sources']), base_url)
    body = note['body'].rstrip() + '\n\n' + text if data.mode == 'append' else text
    if body == note['body']:
        raise HTTPException(400, '草稿与目标内容一致，没有需要保存的修改。')
    request_sha = digest(compact_json([did, draft['version'], note['id'], data.target_version, data.mode, body]))
    existing = db.execute('SELECT id FROM knowledge_note_proposals WHERE user_id=? AND request_sha=?', (user['id'], request_sha)).fetchone()
    pid = existing['id'] if existing else secrets.token_urlsafe(24)
    if not existing:
        db.execute('''INSERT INTO knowledge_note_proposals(id,user_id,request_sha,draft_id,draft_version,note_id,
            target_version,base_body,proposed_body,mode,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
            (pid, user['id'], request_sha, did, draft['version'], note['id'], data.target_version, note['body'], body, data.mode, int(time.time())))
    return proposal_detail(app, db, user, pid)


def own_proposal(db, user, pid):
    row = db.execute('SELECT * FROM knowledge_note_proposals WHERE id=? AND user_id=?', (pid, user['id'])).fetchone()
    if row is None:
        raise HTTPException(404, '修改建议不存在。')
    return row


def receipt(db, user, proposal):
    note = db.execute('SELECT * FROM notes WHERE id=?', (proposal['note_id'],)).fetchone()
    if note is None or not can_view(db, note, user):
        raise HTTPException(404, '目标笔记不可访问。')
    return {'id': proposal['id'], 'status': 'applied', 'note_id': note['id'], 'url': f"/n/{note['id']}", 'body_sha': proposal['result_sha']}


def proposal_detail(app, db, user, pid):
    proposal = own_proposal(db, user, pid)
    if proposal['status'] == 'applied':
        return receipt(db, user, proposal)
    draft = owned_draft(app, db, user, proposal['draft_id'], editable=True)
    note = target_for(db, user, proposal['note_id'], draft)
    if draft['version'] != proposal['draft_version']:
        raise HTTPException(409, '草稿已变化，请重新打开并审阅。')
    diff = difflib.unified_diff(proposal['base_body'].splitlines(keepends=True), proposal['proposed_body'].splitlines(keepends=True), fromfile='before', tofile='after')
    # Make a missing final newline visible instead of joining deleted/added lines.
    diff_text = ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n' for line in diff)
    return {'id': pid, 'status': 'review', 'draft_id': draft['id'], 'mode': proposal['mode'],
            'target': {'id': note['id'], 'title': note['title'], 'path': note['path'], 'visibility': note['visibility']},
            'conflict': note_version(note) != proposal['target_version'],
            'diff': diff_text}


def apply_proposal(app, db, user, pid, confirmed):
    proposal = own_proposal(db, user, pid)
    if not confirmed:
        raise HTTPException(400, '请先核对完整差异并确认保存。')
    if proposal['status'] == 'applied':
        return receipt(db, user, proposal)
    state = proposal_detail(app, db, user, pid)
    if state['conflict']:
        raise HTTPException(409, '目标笔记已变化，请刷新目标并重新审阅差异。')
    note = db.execute('SELECT * FROM notes WHERE id=?', (proposal['note_id'],)).fetchone()
    sha = save_note(db, note, proposal['proposed_body'], 'web', user['username'])
    now = int(time.time())
    db.execute("UPDATE knowledge_note_proposals SET status='applied',result_sha=?,applied_at=? WHERE id=?", (sha, now, pid))
    db.execute('UPDATE explore_drafts SET saved_note_id=?,version=version+1,updated_at=? WHERE id=?', (note['id'], now, proposal['draft_id']))
    return receipt(db, user, own_proposal(db, user, pid))


def register_artifacts(app, path, api):
    app.state.artifacts_enabled = os.getenv('KB_AGENT_ARTIFACTS_ENABLED', 'false').lower() in {'true', '1'}
    with api['database'](path) as db:
        initialize_artifacts(db)

    def actor(db, request, mutation=False):
        if not app.state.artifacts_enabled:
            raise HTTPException(404, '研究成果整理尚未启用。')
        user, csrf = api['current_user'](db, request)
        if user is None:
            raise HTTPException(401, '请先登录。')
        if mutation:
            api['require_csrf']({'csrf': request.headers.get('X-CSRF-Token', '')}, csrf)
        return user, csrf

    async def payload(request, schema):
        raw = await request.body()
        if len(raw) > 4096:
            raise HTTPException(413, '请求内容过长。')
        try:
            return schema.model_validate_json(raw)
        except ValidationError as exc:
            raise HTTPException(400, '修改建议参数无效。') from exc

    @app.post('/api/agent/v1/runs/{rid}/draft')
    def create_draft(request: Request, rid: str):
        with api['database'](path) as db:
            db.execute('BEGIN IMMEDIATE')
            user, _ = actor(db, request, True)
            return transfer(app, db, user, rid)

    @app.post('/api/explore/messages/{mid}/draft')
    def save_discussion(request: Request, mid: int):
        with api['database'](path) as db:
            db.execute('BEGIN IMMEDIATE')
            user, _ = actor(db, request, True)
            return transfer_discussion(app, db, user, mid)

    @app.get('/explore/drafts/{did}/review')
    def review_page(request: Request, did: str):
        with api['database'](path) as db:
            user, csrf = actor(db, request)
            draft = owned_draft(app, db, user, did)
            return api['page'](request, 'knowledge_proposal.html', user=user, csrf=csrf,
                               draft=app.state.writing_detail(db, draft), categories=api['categories'](db, user))

    @app.get('/api/explore/drafts/{did}/targets')
    def list_targets(request: Request, did: str, q: str = ''):
        if len(q) > 100:
            raise HTTPException(400, '搜索条件过长。')
        with api['database'](path) as db:
            user, _ = actor(db, request)
            return targets(app, db, user, did, q.strip())

    @app.post('/api/explore/drafts/{did}/proposals')
    async def create_proposal(request: Request, did: str):
        data = await payload(request, ProposalInput)
        with api['database'](path) as db:
            db.execute('BEGIN IMMEDIATE')
            user, _ = actor(db, request, True)
            return propose(app, db, user, did, data, str(request.base_url))

    @app.get('/api/explore/proposals/{pid}')
    def get_proposal(request: Request, pid: str):
        with api['database'](path) as db:
            user, _ = actor(db, request)
            return proposal_detail(app, db, user, pid)

    @app.post('/api/explore/proposals/{pid}/apply')
    async def commit_proposal(request: Request, pid: str):
        data = await payload(request, ApplyInput)
        with api['database'](path) as db:
            db.execute('BEGIN IMMEDIATE')
            user, _ = actor(db, request, True)
            return apply_proposal(app, db, user, pid, data.confirmed)
