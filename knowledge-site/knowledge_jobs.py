"""Lease-fenced replay of bounded retrieval and account-owned research results."""
import hashlib
import json
import time

from fastapi import HTTPException

from agent_context import compact_json, legacy_messages
from agent_runtime import RunStopped
from knowledge_retrieval import PROMPT_VERSION, TOOL_VERSION, RetrievalRun
from knowledge_scope import read_source_chunk, selected_notes, selection_ids, selection_version

ENGINE_VERSION = 'knowledge-worker-v1'


def initialize_knowledge_jobs(db):
    db.execute('''CREATE TABLE IF NOT EXISTS knowledge_job_results (
        run_id TEXT PRIMARY KEY REFERENCES agent_runs(id) ON DELETE CASCADE,
        body TEXT NOT NULL, sources_json TEXT NOT NULL, retrieval_json TEXT NOT NULL,
        created_at INTEGER NOT NULL)''')


def prepare_knowledge(runtime, db, uid, data):
    from explore import eligible_note
    app = runtime.app
    if not app.state.knowledge_retrieval_enabled or not callable(getattr(app.state.explore_provider, 'plan_retrieval', None)):
        raise HTTPException(403, '资料补查尚未启用。')
    if not app.state.explore_provider.ready:
        raise HTTPException(503, '问答服务尚未配置。')
    if data['scope'] == 'selected':
        if not app.state.explore_selected_enabled:
            raise HTTPException(403, '所选笔记问答尚未启用。')
        ids = selection_ids(data['note_id'], data['note_ids'])
        notes = selected_notes(db, uid, ids)
        data['note_ids'] = ids
        data['source_refs'] = [{'kind': 'account_note', 'note_id': note['id'], 'body_sha': note['body_sha'], 'user_id': uid} for note in notes]
    else:
        if data['note_ids']:
            raise HTTPException(400, '请选择所选资料模式后再指定多篇笔记。')
        note = eligible_note(db, data['note_id']) if data['note_id'] else None
        if data['note_id'] and note is None:
            raise HTTPException(404, '文章不在公开资料范围。')
        data['source_refs'] = [{'kind': 'note', 'note_id': note['id'], 'body_sha': note['body_sha']}] if note else []
    data.update(engine_version=ENGINE_VERSION, prompt_version=PROMPT_VERSION, tool_version=TOOL_VERSION,
                model=str(getattr(app.state.explore_provider, 'model', 'test-provider')), memory_mode='off')


def step(runtime, run, key, inputs, operation=None, *, model_messages=None, output_limit=400,
         structured=None, search=False):
    fingerprint = hashlib.sha256(compact_json(inputs).encode()).hexdigest()
    with runtime.database(runtime.path) as db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT 1 FROM agent_run_steps WHERE run_id=? AND step_key=?', (run['id'], key)).fetchone()
        cached = runtime.start_step(db, run, key, fingerprint)
        if cached is not None:
            return cached
        if search and not previous:
            budget = db.execute('SELECT search_calls,max_search_calls FROM agent_run_budget WHERE run_id=?', (run['id'],)).fetchone()
            if budget['search_calls'] >= budget['max_search_calls']:
                raise RunStopped('search_limit')
            db.execute('UPDATE agent_run_budget SET search_calls=search_calls+1 WHERE run_id=?', (run['id'],))
    if model_messages is not None:
        return runtime.call_model(run, key, model_messages, output_limit, structured=structured)
    output = operation()
    with runtime.database(runtime.path) as db:
        db.execute('BEGIN IMMEDIATE')
        runtime.check(db, run, refs=output.get('refs', []))
        runtime.finish_step(db, run, key, output)
    return output


def execute_knowledge(runtime, run, data):
    from explore import SYSTEM, source_snapshot, render_answer
    app = runtime.app
    if (not app.state.knowledge_retrieval_enabled or not app.state.explore_provider.ready
        or (data['scope'] == 'selected' and not app.state.explore_selected_enabled)):
        raise RunStopped('provider_unavailable')
    if (data.get('engine_version') != ENGINE_VERSION or data.get('prompt_version') != PROMPT_VERSION
        or data.get('tool_version') != TOOL_VERSION or data.get('model') != str(getattr(app.state.explore_provider, 'model', 'test-provider'))):
        raise RunStopped('runner_version_changed')
    uid, ids = run['user_id'], data['note_ids']

    def guard(refs):
        with runtime.database(runtime.path) as db:
            runtime.check(db, run, refs=refs)

    def make_source(row, number):
        return source_snapshot(row, number, user_id=uid if data['scope'] == 'selected' else None)

    def search(query, limit, per_note_limit=None):
        with runtime.database(runtime.path) as db:
            runtime.check(db, run)
            db.commit()  # epoch() may write; do not hold that lock through index refresh.
            if data['scope'] == 'selected':
                rows, _ = app.state.explore_selected_index.search(db, uid, query, note_ids=ids, limit=limit, per_note_limit=per_note_limit)
            else:
                rows, _ = app.state.explore_index.search(db, query, note_id=data['note_id'], limit=limit, per_note_limit=per_note_limit)
        return {'rows': rows, 'refs': [make_source(row, index + 1) for index, row in enumerate(rows)]}

    initial = step(runtime, run, 'retrieve', {'topic': data['topic'], 'refs': data['source_refs']},
                   lambda: search(data['topic'], 4), search=True)
    decisions, lookups = 0, 0

    def decide(messages, *, usage):
        nonlocal decisions
        decisions += 1
        output = step(runtime, run, f'decision_{decisions}', messages, model_messages=messages, structured='plan_retrieval')
        usage.update(output['usage'])
        return output['text']

    def lookup(query):
        nonlocal lookups
        lookups += 1
        return step(runtime, run, f'lookup_{lookups}', {'query': query}, lambda: search(query, 6, 6), search=True)['rows']

    def read(row, direction):
        nonlocal lookups
        lookups += 1
        def operation():
            with runtime.database(runtime.path) as db:
                runtime.check(db, run)
                rows = read_source_chunk(db, row, direction, user_id=uid,
                    note_ids=ids if data['scope'] == 'selected' else None, public_note_id=data['note_id'])
            return {'rows': rows, 'refs': [make_source(item, index + 1) for index, item in enumerate(rows)]}
        return step(runtime, run, f'lookup_{lookups}', {'source': make_source(row, 1), 'direction': direction}, operation)['rows']

    runner = RetrievalRun(runtime.database, runtime.path, uid, None,
        selection_version(ids if data['scope'] == 'selected' else [data['note_id']] if data['note_id'] else []),
        app.state.explore_provider, initial['rows'], make_source, guard, lookup, read,
        decision_call=decide, trace_id='job_' + run['id'], check_write=lambda db: runtime.check(db, run),
        max_search_calls=data['max_search_calls'])
    rows = runner.execute(data['topic'])
    runner.finish('succeeded')
    sources, remaining = [], 7000
    for row in rows:
        source = make_source(row, len(sources) + 1)
        if len(source['excerpt']) <= remaining:
            sources.append(source)
            remaining -= len(source['excerpt'])
    if not sources:
        raise RunStopped('insufficient_evidence')
    system = SYSTEM + '\n依据资料综合回答，明确分歧和未覆盖部分，控制在约 800 字。'
    messages = legacy_messages(system, [], data['topic'] + '\n' + data['requirements'], '本次任务固定的笔记资料范围', sources)
    output = step(runtime, run, 'knowledge_answer', messages, model_messages=messages, output_limit=2500)
    _, used = render_answer(output['text'], sources)
    if not used:
        raise RunStopped('missing_citations')
    sources = [source for source in sources if source['number'] in used]
    with runtime.database(runtime.path) as db:
        db.execute('BEGIN IMMEDIATE')
        runtime.check(db, run)
        db.execute('INSERT OR IGNORE INTO knowledge_job_results VALUES(?,?,?,?,?)',
                   (run['id'], output['text'], compact_json(sources), compact_json(runner.info()), int(time.time())))
        db.execute("UPDATE agent_runs SET status='succeeded',result_json=?,lease_token='',lease_until=0,updated_at=? WHERE id=?",
                   (compact_json({'knowledge_id': run['id'], 'text': output['text'], 'retrieval': runner.info()}), int(time.time()), run['id']))
        runtime.event(db, run['id'], 'status', status='succeeded')


def result(runtime, db, uid, rid):
    from explore import render_answer
    run = runtime.owned(db, uid, rid)
    try:
        runtime.check(db, run, lease=False)
    except RunStopped as exc:
        raise HTTPException(409, '资料或访问权限已变化，请重新创建任务。') from exc
    row = db.execute('SELECT * FROM knowledge_job_results WHERE run_id=?', (rid,)).fetchone()
    if run['status'] != 'succeeded' or row is None:
        raise HTTPException(409, '任务尚未生成结果。')
    sources = json.loads(row['sources_json'])
    return {'body': row['body'], 'html': render_answer(row['body'], sources)[0], 'sources': sources,
            'retrieval': json.loads(row['retrieval_json'])}
