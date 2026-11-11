"""Durable, fenced, bounded account tasks. No credentials or reasoning in events."""
from contextlib import contextmanager
import difflib
import hashlib
import inspect
import json
import os
from pathlib import Path
import secrets
import threading
import time

from fastapi import HTTPException

from agent_context import compact_json, estimate_tokens
from agent_dependencies import analyze_change, record_dependencies
from agent_memory import epoch, identifier, public_note
from agent_projects import own_project

STEPS = ('retrieve', 'outline', 'write', 'validate', 'save_draft')
TERMINAL = {'succeeded', 'failed', 'cancelled'}


class RunStopped(Exception):
    def __init__(self, code): self.code = code


class LeaseLost(RunStopped):
    def __init__(self): super().__init__('lease_lost')


class RuntimeService:
    def __init__(self, app, database, path, *, enabled=False, lease_seconds=60):
        self.app, self.database, self.path = app, database, Path(path)
        self.enabled, self.lease_seconds = enabled, max(5, lease_seconds)

    @staticmethod
    def owned(db, uid, rid):
        row = db.execute('SELECT * FROM agent_runs WHERE id=? AND user_id=?', (rid, uid)).fetchone()
        if not row: raise HTTPException(404, '任务不存在。')
        return dict(row)

    @staticmethod
    def event(db, rid, kind, **payload):
        # Caller-supplied model text can never enter the replay stream.
        allowed = {'status', 'step', 'draft_id', 'error_code', 'attempt'}
        payload = {key: value for key, value in payload.items() if key in allowed}
        seq = db.execute('SELECT coalesce(max(seq),0)+1 FROM agent_run_events WHERE run_id=?', (rid,)).fetchone()[0]
        db.execute('INSERT INTO agent_run_events VALUES(?,?,?,?,?)', (rid, seq, kind, compact_json(payload), int(time.time())))

    def create(self, db, uid, data):
        external = data.model_dump(); key = external.pop('idempotency_key')
        if external['kind'] in {'writing', 'research', 'impact'}:
            for field in ('scope', 'note_ids', 'attempt_id'):
                external.pop(field)
        request_hash = hashlib.sha256(compact_json(external).encode()).hexdigest()
        existing = db.execute('SELECT * FROM agent_runs WHERE user_id=? AND idempotency_key=?', (uid, key)).fetchone()
        if existing:
            if existing['request_hash'] != request_hash: raise HTTPException(409, '相同幂等键已用于不同任务。')
            return self.detail(db, uid, existing['id'])
        user = db.execute('SELECT active FROM users WHERE id=?', (uid,)).fetchone()
        if not user or not user['active']: raise HTTPException(401, '账户不可用。')
        if not external['topic'].strip(): raise HTTPException(400, '主题不能为空。')
        if external['project_id']:
            project = own_project(db, uid, external['project_id'])
            if project['status'] != 'active': raise HTTPException(409, '请先恢复项目。')
            if external['kind'] == 'knowledge':
                self.app.state.agent_projects.bind_knowledge_request(db, uid, external)
        if external['kind'] == 'knowledge':
            from knowledge_jobs import prepare_knowledge
            prepare_knowledge(self, db, uid, external)
        elif external['kind'] == 'assessment':
            from learning_service import prepare_assessment
            prepare_assessment(self, db, uid, external)
        elif external['note_id'] and not public_note(db, external['note_id']): raise HTTPException(404, '文章不在公开资料范围。')
        if external['kind'] == 'impact' and not external['note_id']: raise HTTPException(400, '影响分析需要指定文章。')
        if external['draft_id']:
            draft = db.execute('SELECT * FROM explore_drafts WHERE id=? AND user_id=?', (external['draft_id'], uid)).fetchone()
            if not draft: raise HTTPException(404, '文稿不存在。')
            if draft['saved_note_id'] or draft['busy_until'] > int(time.time()): raise HTTPException(409, '文稿暂时不能修改。')
            context=db.execute('SELECT project_id FROM agent_draft_context WHERE draft_id=?',(external['draft_id'],)).fetchone()
            if context and context['project_id'] and external['project_id']!=context['project_id']:
                raise HTTPException(409,'文稿已属于其他项目，请先在工作台调整关联。')
            external['expected_draft_version'] = draft['version']
        if db.execute("SELECT count(*) FROM agent_runs WHERE user_id=? AND status NOT IN ('succeeded','failed','cancelled')", (uid,)).fetchone()[0] >= 10:
            raise HTTPException(429, '待处理任务已达上限。')
        now = int(time.time())
        from agent_budget import daily_used,daily_limit
        if daily_used(db,uid,now) + external['max_tokens'] > daily_limit():
            raise HTTPException(429, '账户每日任务 token 预留额度已用完。')
        rid = identifier()
        db.execute('''INSERT INTO agent_runs(id,user_id,project_id,kind,status,idempotency_key,request_hash,input_json,context_epoch,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (rid, uid, external['project_id'], external['kind'], 'queued', key, request_hash, compact_json(external), epoch(db, uid), now, now))
        db.execute('INSERT INTO agent_run_budget(run_id,max_tokens,max_search_calls) VALUES(?,?,?)', (rid, external['max_tokens'], external['max_search_calls']))
        self.event(db, rid, 'status', status='queued')
        return self.detail(db, uid, rid)

    def references(self, db, rid):
        row = db.execute('SELECT kind,input_json FROM agent_runs WHERE id=?', (rid,)).fetchone()
        if row and row['kind'] in {'knowledge', 'assessment'}:
            refs = list(json.loads(row['input_json']).get('source_refs', []))
            for step in db.execute("SELECT output_json FROM agent_run_steps WHERE run_id=? AND status='succeeded'", (rid,)):
                refs.extend(json.loads(step[0]).get('refs', []))
            return refs
        step = db.execute("SELECT output_json FROM agent_run_steps WHERE run_id=? AND step_key='retrieve' AND status='succeeded'", (rid,)).fetchone()
        return json.loads(step[0]).get('refs', []) if step else []

    def check(self, db, run, *, lease=True, refs=None, allow_cancel=False):
        current = db.execute('SELECT * FROM agent_runs WHERE id=?', (run['id'],)).fetchone()
        if not current: raise RunStopped('run_missing')
        if lease and (current['lease_token'] != run['lease_token'] or current['lease_generation'] != run['lease_generation']
                      or current['lease_until'] <= int(time.time()) or current['status'] != 'running'):
            raise LeaseLost()
        if current['cancel_requested'] and not allow_cancel: raise RunStopped('cancelled')
        user = db.execute('SELECT active FROM users WHERE id=?', (current['user_id'],)).fetchone()
        if not user or not user[0]: raise RunStopped('account_inactive')
        if epoch(db, current['user_id']) != current['context_epoch']: raise RunStopped('context_changed')
        data = json.loads(current['input_json'])
        if data['project_id']:
            try: project = own_project(db, current['user_id'], data['project_id'])
            except HTTPException: raise RunStopped('project_unavailable')
            if project['status'] != 'active': raise RunStopped('project_unavailable')
        modern = current['kind'] in {'knowledge', 'assessment'}
        if not modern and data['note_id'] and not public_note(db, data['note_id']): raise RunStopped('source_unavailable')
        from explore import sources_valid
        evidence = [*self.references(db, current['id']), *(refs or [])] if modern else refs if refs is not None else self.references(db, current['id'])
        if not sources_valid(db, evidence, user_id=current['user_id'] if modern else None): raise RunStopped('source_changed')
        return dict(current)

    def detail(self, db, uid, rid):
        row = self.owned(db, uid, rid)
        result = {k: row[k] for k in ('id', 'project_id', 'kind', 'status', 'created_at', 'updated_at', 'error_code')}
        result['topic']=json.loads(row['input_json'])['topic']
        result['steps'] = [{k: r[k] for k in ('step_key', 'status', 'attempt', 'error_code')} for r in db.execute('SELECT * FROM agent_run_steps WHERE run_id=? ORDER BY rowid', (rid,))]
        result['budget'] = dict(db.execute('SELECT reserved_tokens,used_tokens,search_calls,max_tokens,max_search_calls FROM agent_run_budget WHERE run_id=?', (rid,)).fetchone())
        result['result'] = {}
        if row['kind'] in {'knowledge', 'assessment'}:
            try:
                self.check(db, row, lease=False, allow_cancel=True)
            except RunStopped:
                result['topic'] = '资料任务'
                result['result_available'] = False
                result['error_code'] = 'source_or_context_changed'
                return result
        if row['status'] == 'succeeded':
            try:
                self.check(db, row, lease=False)
                result['result'] = json.loads(row['result_json'])
            except RunStopped:
                result['result_available'] = False
                result['error_code'] = 'source_or_context_changed'
        return result

    def events(self, db, uid, rid, after=0, limit=100):
        self.owned(db, uid, rid)
        rows = db.execute('SELECT * FROM agent_run_events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?', (rid, after, limit + 1)).fetchall()
        return {'events': [{'seq': r['seq'], 'type': r['type'], 'payload': json.loads(r['payload_json']), 'created_at': r['created_at']} for r in rows[:limit]],
                'next_seq': rows[min(limit, len(rows))-1]['seq'] if rows else after, 'has_more': len(rows) > limit}

    def cancel(self, db, uid, rid):
        run = self.owned(db, uid, rid)
        if run['status'] in TERMINAL: return self.detail(db, uid, rid)
        status = 'cancelled' if run['status'] != 'running' else 'running'
        db.execute('UPDATE agent_runs SET cancel_requested=1,status=?,updated_at=? WHERE id=?', (status, int(time.time()), rid))
        self.event(db, rid, 'status', status='cancelling' if status == 'running' else status)
        return self.detail(db, uid, rid)

    def resume(self, db, uid, rid, retry_uncertain=False):
        run = self.owned(db, uid, rid)
        if run['status'] != 'waiting_user': raise HTTPException(409, '任务不处于等待确认状态。')
        if run['error_code'] == 'provider_uncertain' and not retry_uncertain:
            raise HTTPException(409, '上次调用可能已计费；请明确确认重试。')
        try: self.check(db, run, lease=False)
        except RunStopped as exc: raise HTTPException(409, '任务上下文或来源已变化，请重新创建任务。') from exc
        db.execute("UPDATE agent_run_steps SET status='pending',provider_started_at=NULL,provider_completed_at=NULL,error_code='' WHERE run_id=? AND status='running'", (rid,))
        db.execute("UPDATE agent_runs SET status='queued',error_code='',lease_token='',lease_until=0,updated_at=? WHERE id=?", (int(time.time()), rid))
        self.event(db, rid, 'status', status='queued')
        return self.detail(db, uid, rid)

    def claim(self):
        if not self.enabled: return None
        now = int(time.time())
        with self.database(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            for expired in db.execute("SELECT * FROM agent_runs WHERE status='running' AND lease_until<=?", (now,)).fetchall():
                uncertain = db.execute('''SELECT 1 FROM agent_run_steps WHERE run_id=? AND status='running'
                    AND provider_started_at IS NOT NULL AND provider_completed_at IS NULL''', (expired['id'],)).fetchone()
                status = 'cancelled' if expired['cancel_requested'] else ('waiting_user' if uncertain else 'queued')
                code = 'provider_uncertain' if uncertain and status != 'cancelled' else ''
                db.execute("UPDATE agent_runs SET status=?,error_code=?,lease_token='',lease_until=0,updated_at=? WHERE id=?", (status, code, now, expired['id']))
                if uncertain:
                    db.execute('UPDATE agent_run_budget SET used_tokens=used_tokens+reserved_tokens,reserved_tokens=0 WHERE run_id=?', (expired['id'],))
                self.event(db, expired['id'], 'status', status=status, error_code=code)
            # A single shared database worker lease is the initial concurrency policy.
            if db.execute("SELECT 1 FROM agent_runs WHERE status='running' AND lease_until>?", (now,)).fetchone(): return None
            queued = db.execute("SELECT * FROM agent_runs WHERE status='queued' AND cancel_requested=0 ORDER BY created_at,rowid LIMIT 1").fetchone()
            if not queued: return None
            token = secrets.token_urlsafe(24)
            db.execute("UPDATE agent_runs SET status='running',lease_token=?,lease_generation=lease_generation+1,lease_until=?,heartbeat_at=?,updated_at=? WHERE id=? AND status='queued'", (token, now + self.lease_seconds, now, now, queued['id']))
            run = dict(db.execute('SELECT * FROM agent_runs WHERE id=?', (queued['id'],)).fetchone())
            self.event(db, run['id'], 'status', status='running')
            return run

    def heartbeat(self, run):
        now = int(time.time())
        with self.database(self.path) as db:
            changed = db.execute("UPDATE agent_runs SET lease_until=?,heartbeat_at=? WHERE id=? AND status='running' AND lease_token=? AND lease_generation=? AND lease_until>?", (now + self.lease_seconds, now, run['id'], run['lease_token'], run['lease_generation'], now)).rowcount
            return changed == 1

    @contextmanager
    def heartbeats(self, run):
        stop = threading.Event()
        def beat():
            while not stop.wait(max(1, self.lease_seconds // 3)):
                try:
                    if not self.heartbeat(run): break
                except Exception: break
        thread = threading.Thread(target=beat, daemon=True); thread.start()
        try: yield
        finally: stop.set(); thread.join(timeout=2)

    def start_step(self, db, run, key, fingerprint):
        self.check(db, run)
        row = db.execute('SELECT * FROM agent_run_steps WHERE run_id=? AND step_key=?', (run['id'], key)).fetchone()
        if row and row['status'] == 'succeeded':
            if row['input_fingerprint'] != fingerprint: raise RunStopped('step_input_changed')
            return json.loads(row['output_json'])
        if row:
            db.execute("UPDATE agent_run_steps SET status='running',attempt=attempt+1,input_fingerprint=?,error_code='',updated_at=? WHERE run_id=? AND step_key=?", (fingerprint, int(time.time()), run['id'], key))
        else:
            db.execute("INSERT INTO agent_run_steps(run_id,step_key,status,attempt,input_fingerprint,updated_at) VALUES(?,?,'running',1,?,?)", (run['id'], key, fingerprint, int(time.time())))
        self.event(db, run['id'], 'step', step=key, status='running')
        return None

    def finish_step(self, db, run, key, output):
        self.check(db, run, refs=output.get('refs') if key == 'retrieve' else None)
        db.execute("UPDATE agent_run_steps SET status='succeeded',output_json=?,updated_at=? WHERE run_id=? AND step_key=?", (compact_json(output), int(time.time()), run['id'], key))
        self.event(db, run['id'], 'step', step=key, status='succeeded')

    def retrieve(self, run, data):
        from explore import source_snapshot
        sources, seen, no_new = [], set(), 0
        rounds = data['max_search_calls'] if data['kind'] == 'research' else 1
        queries = [data['topic'], data['topic'] + ' 原理 限制', data['topic'] + ' 示例 版本 差异']
        for attempt in range(rounds):
            with self.database(self.path) as db:
                db.execute('BEGIN IMMEDIATE'); self.check(db, run)
                budget = db.execute('SELECT * FROM agent_run_budget WHERE run_id=?', (run['id'],)).fetchone()
                if budget['search_calls'] >= budget['max_search_calls']: break
                db.execute('UPDATE agent_run_budget SET search_calls=search_calls+1 WHERE run_id=?', (run['id'],))
            # Index.search commits its own incremental index changes; never call it
            # inside an open task state transaction.
            with self.database(self.path) as db:
                self.check(db, run)
                rows, _ = self.app.state.explore_index.search(db, queries[attempt], note_id=data['note_id'], limit=4)
                candidates = [source_snapshot(row, 1) for row in rows]
            if data['web_search']:
                if not getattr(self.app.state.explore_web, 'ready', False): raise RunStopped('web_unavailable')
                candidates.extend(self.app.state.explore_web.search(queries[attempt]))
            added = 0
            for source in candidates:
                marker = (source.get('kind'), source.get('note_id'), source.get('section'), source.get('url'))
                if marker in seen: continue
                seen.add(marker); added += 1
                sources.append({**source, 'number': len(sources) + 1})
                if len(sources) >= 8: break
            no_new = no_new + 1 if not added else 0
            if no_new >= 2 or len(sources) >= 8: break
        if not sources: raise RunStopped('insufficient_evidence')
        with self.database(self.path) as db:
            self.check(db, run, refs=sources)
            memory, refs, conflicts = (self.app.state.agent_memory.select(db, run['user_id'], data['project_id'], data['topic']) if data['memory_mode'] == 'account' and self.app.state.agent_memory.enabled else ([], [], []))
            project = own_project(db, run['user_id'], data['project_id']) if data['project_id'] else None
            output = {'sources': sources, 'memory': memory, 'refs': [*sources, *refs], 'conflicts': conflicts,
                      'project': {key: project[key] for key in ('title', 'goal', 'progress', 'next_action')} if project else None}
            if data['kind'] == 'impact': output['impact'] = analyze_change(db, run['user_id'], data['note_id'])
            return output

    def call_model(self, run, key, messages, output_limit, *, structured=None):
        input_cost = estimate_tokens(messages)
        with self.database(self.path) as db:
            db.execute('BEGIN IMMEDIATE'); self.check(db, run)
            budget = db.execute('SELECT * FROM agent_run_budget WHERE run_id=?', (run['id'],)).fetchone()
            remaining = budget['max_tokens'] - budget['used_tokens'] - budget['reserved_tokens'] - input_cost
            if structured and remaining < output_limit: raise RunStopped('budget_exceeded')
            output_limit = min(output_limit, remaining)
            if output_limit < 128: raise RunStopped('budget_exceeded')
            reserved = input_cost + output_limit
            db.execute('UPDATE agent_run_budget SET reserved_tokens=reserved_tokens+? WHERE run_id=?', (reserved, run['id']))
            db.execute('UPDATE agent_run_steps SET provider_started_at=?,provider_completed_at=NULL WHERE run_id=? AND step_key=?', (int(time.time()), run['id'], key))
        provider, usage = self.app.state.explore_provider, {}
        method = getattr(provider, structured) if structured else provider.stream_answer if callable(getattr(provider, 'stream_answer', None)) else provider.answer
        parameters = inspect.signature(method).parameters
        options = {}
        if 'max_tokens' in parameters: options['max_tokens'] = output_limit
        if 'usage' in parameters: options['usage'] = usage
        try:
            chunks = method(messages, **options) if structured else method(messages, False, **options)
            if isinstance(chunks, str): chunks = [chunks]
            text = ''
            for delta in chunks:
                with self.database(self.path) as db: self.check(db, run)
                if not isinstance(delta, str): raise ValueError('Invalid provider output')
                text += delta
                if len(text) > (12000 if structured else 60000) or (not structured and 'max_tokens' not in parameters and estimate_tokens(text) > output_limit): raise RunStopped('provider_output_limit')
            if not structured:
                from explore_write import format_generated
                text = format_generated(text)
            if not text: raise ValueError('Empty provider output')
        except RunStopped: raise
        except Exception as exc: raise RunStopped('provider_uncertain') from exc
        reported = usage.get('total_tokens')
        if type(reported) is not int:
            a, b = usage.get('prompt_tokens'), usage.get('completion_tokens')
            reported = a + b if type(a) is int and type(b) is int else reserved
        charged = max(0, reported)
        with self.database(self.path) as db:
            db.execute('BEGIN IMMEDIATE'); self.check(db, run)
            db.execute('UPDATE agent_run_budget SET reserved_tokens=reserved_tokens-?,used_tokens=used_tokens+? WHERE run_id=?', (reserved, charged, run['id']))
            db.execute('UPDATE agent_run_steps SET provider_completed_at=? WHERE run_id=? AND step_key=?', (int(time.time()), run['id'], key))
            # Completion and output are atomic: a crash after this point reuses it.
            output = {'text': text, 'usage': usage} if structured else {'text': text}
            self.finish_step(db, run, key, output)
            if charged > reserved: raise RunStopped('budget_exceeded')
        return output

    def generate(self, run, data, key, retrieved, outline):
        from explore_write import WRITER_SYSTEM
        task = '只生成简短 Markdown 提纲，并标明可用来源。' if key == 'outline' else '按提纲生成完整文稿，以单个 # 总标题开始，事实结论标注来源编号。'
        if data['kind'] == 'impact': task += ' 生成知识更新的影响说明与修订建议；保留不确定性，不声称已应用修改。'
        messages = [{'role': 'system', 'content': WRITER_SYSTEM + '\n任务：' + task},
                    {'role': 'user', 'content': compact_json({'topic': data['topic'], 'requirements': data['requirements'],
                     'sources': retrieved['sources'], 'preferences_unverified': retrieved['memory'], 'project': retrieved['project'],
                     'outline': outline, 'change_analysis': retrieved.get('impact')})}]
        return self.call_model(run, key, messages, 900 if key == 'outline' else 2500)

    def save(self, db, run, data, retrieved, outline, validated):
        self.check(db, run)
        now = int(time.time())
        draft_id = data['draft_id'] if data['draft_id'] and data['kind'] != 'impact' else 'run_' + run['id']
        sources = compact_json(retrieved['sources'])
        if data['draft_id'] and data['kind'] != 'impact':
            draft = db.execute('SELECT * FROM explore_drafts WHERE id=? AND user_id=?', (draft_id, run['user_id'])).fetchone()
            if not draft or draft['version'] != data['expected_draft_version'] or draft['saved_note_id'] or draft['busy_until'] > now: raise RunStopped('draft_conflict')
            db.execute("UPDATE explore_drafts SET outline=?,body=?,sources=?,stage='draft',version=version+1,updated_at=? WHERE id=?", (outline['text'], validated['text'], sources, now, draft_id))
        else:
            if db.execute('SELECT count(*) FROM explore_drafts WHERE user_id=?', (run['user_id'],)).fetchone()[0] >= 200: raise RunStopped('draft_limit')
            db.execute('''INSERT INTO explore_drafts(id,user_id,topic,requirements,title,note_id,web_search,outline,body,sources,stage,version,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,'draft',1,?,?)''', (draft_id, run['user_id'], data['topic'], data['requirements'], data['topic'][:200], data['note_id'], int(data['web_search']), outline['text'], validated['text'], sources, now, now))
        db.execute('INSERT OR REPLACE INTO agent_draft_context(draft_id,project_id,sources_json,memory_mode) VALUES(?,?,?,?)', (draft_id, data['project_id'], compact_json(retrieved['refs']), data['memory_mode']))
        record_dependencies(db, run['user_id'], retrieved['refs'], 'draft', draft_id)
        record_dependencies(db, run['user_id'], retrieved['refs'], 'run', run['id'])
        if data['project_id']:
            db.execute("INSERT OR IGNORE INTO agent_project_links VALUES(?,'draft',?,?)", (data['project_id'], draft_id, now))
            db.execute('UPDATE agent_projects SET progress=?,next_action=?,version=version+1,updated_at=? WHERE id=?', ('已完成任务文稿', '审阅文稿与引用', now, data['project_id']))
        result = {'draft_id': draft_id}
        if retrieved.get('impact'):
            analysis = dict(retrieved['impact'])
            # Source diff is bounded and only available while the source is public.
            result['impact'] = analysis
            base = ''
            if data['draft_id']:
                row = db.execute('SELECT body,sources FROM explore_drafts WHERE id=? AND user_id=?', (data['draft_id'], run['user_id'])).fetchone()
                from explore import sources_valid
                other_refs = [r for r in json.loads(row['sources']) if r.get('note_id') != data['note_id']] if row else []
                if row and sources_valid(db, other_refs): base = row['body']
            result['proposal_diff'] = ''.join(difflib.unified_diff(base.splitlines(keepends=True), validated['text'].splitlines(keepends=True), fromfile='current-owned-draft', tofile='proposed-review-draft'))[:40000]
        self.finish_step(db, run, 'save_draft', result)
        db.execute("UPDATE agent_runs SET status='succeeded',result_json=?,lease_token='',lease_until=0,updated_at=? WHERE id=?", (compact_json(result), now, run['id']))
        self.event(db, run['id'], 'status', status='succeeded', draft_id=draft_id)
        return result

    def stop(self, run, code):
        with self.database(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM agent_runs WHERE id=?', (run['id'],)).fetchone()
            if not current or current['lease_token'] != run['lease_token'] or current['lease_generation'] != run['lease_generation']: return
            if code == 'lease_lost': return
            status = 'cancelled' if current['cancel_requested'] or code == 'cancelled' else ('waiting_user' if code == 'provider_uncertain' else 'failed')
            db.execute("UPDATE agent_runs SET status=?,error_code=?,lease_token='',lease_until=0,updated_at=? WHERE id=?", (status, code, int(time.time()), run['id']))
            # Unknown calls are conservatively charged their reservation.
            db.execute('UPDATE agent_run_budget SET used_tokens=used_tokens+reserved_tokens,reserved_tokens=0 WHERE run_id=?', (run['id'],))
            if status != 'waiting_user': db.execute("UPDATE agent_run_steps SET status='failed',error_code=? WHERE run_id=? AND status='running'", (code, run['id']))
            if current['kind'] == 'knowledge':
                db.execute("UPDATE knowledge_retrieval_runs SET status=?,stop_reason=? WHERE id=? AND status='running'", (status, code, 'job_' + run['id']))
            self.event(db, run['id'], 'status', status=status, error_code=code)

    def execute(self, run):
        data, completed, started = json.loads(run['input_json']), {}, time.monotonic()
        try:
            with self.heartbeats(run):
                if data['kind'] == 'knowledge':
                    from knowledge_jobs import execute_knowledge
                    execute_knowledge(self, run, data)
                    return
                if data['kind'] == 'assessment':
                    from learning_service import execute_assessment
                    execute_assessment(self, run, data)
                    return
                for key in STEPS:
                    if time.monotonic() - started > int(os.getenv('KB_AGENT_RUN_SECONDS', '300')): raise RunStopped('time_budget_exceeded')
                    fingerprint = hashlib.sha256(compact_json({'input': data, 'previous': completed}).encode()).hexdigest()
                    with self.database(self.path) as db:
                        db.execute('BEGIN IMMEDIATE'); cached = self.start_step(db, run, key, fingerprint)
                    if cached is not None: completed[key] = cached; continue
                    if key == 'retrieve': output = self.retrieve(run, data)
                    elif key in {'outline', 'write'}:
                        output = self.generate(run, data, key, completed['retrieve'], completed.get('outline', {}).get('text', ''))
                        completed[key] = output; continue  # call_model atomically saved its result
                    elif key == 'validate':
                        from explore_write import validate_document
                        _, used = validate_document(completed['write']['text'], completed['retrieve']['sources'])
                        if not used: raise RunStopped('missing_citations')
                        output = completed['write']
                    else:
                        with self.database(self.path) as db:
                            db.execute('BEGIN IMMEDIATE')
                            self.save(db, run, data, completed['retrieve'], completed['outline'], completed['validate'])
                        return
                    with self.database(self.path) as db:
                        db.execute('BEGIN IMMEDIATE'); self.finish_step(db, run, key, output)
                    completed[key] = output
        except RunStopped as exc: self.stop(run, exc.code)
        except HTTPException as exc: self.stop(run, 'validation_failed' if exc.status_code == 422 else 'task_failed')
        except Exception: self.stop(run, 'task_failed')

    def run_once(self):
        run = self.claim()
        if not run: return False
        self.execute(run)
        return True
