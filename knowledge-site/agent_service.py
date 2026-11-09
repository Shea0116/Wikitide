"""Account-only context preparation, provenance checks and optimistic commits."""
import hashlib
import json
import secrets
import time

from fastapi import HTTPException

from agent_context import (ContextManager, PROMPT_VERSION, PreparedContext, compact_json,
                           estimate_tokens, history_message, select_recent)
from agent_models import ContextSummary
from agent_provider import SUMMARY_SYSTEM, summary_schema


def history_digest(rows):
    return hashlib.sha256(compact_json(rows).encode('utf-8')).hexdigest()


def unique_sources(pairs):
    result, seen = [], set()
    for pair in pairs:
        for message in pair:
            for source in [*message['sources'], *message.get('context_sources', [])]:
                source = {key: source[key] for key in ('kind', 'note_id', 'body_sha', 'url','id','user_id','revision') if key in source}
                key = compact_json(source)
                if key not in seen:
                    seen.add(key)
                    result.append(source)
    return result


class ContextService:
    def __init__(self, policy, database, db_path, sources_valid):
        self.policy = policy
        self.database = database
        self.db_path = db_path
        self.sources_valid = sources_valid
        self.manager = ContextManager(policy)

    def state(self, db, user_id, session_id):
        session = db.execute('''SELECT s.* FROM explore_sessions s JOIN users u ON u.id=s.user_id
            WHERE s.id=? AND s.user_id=? AND u.active=1''', (session_id, user_id)).fetchone()
        if session is None:
            raise HTTPException(404, '会话不存在。')
        db.execute('INSERT OR IGNORE INTO agent_user_state(user_id) VALUES(?)', (user_id,))
        db.execute('INSERT OR IGNORE INTO agent_session_links(session_id) VALUES(?)', (session_id,))
        link = dict(db.execute('SELECT * FROM agent_session_links WHERE session_id=?', (session_id,)).fetchone())
        epoch = db.execute('SELECT context_epoch FROM agent_user_state WHERE user_id=?', (user_id,)).fetchone()[0]
        rows = [dict(row) for row in db.execute('''SELECT m.id,m.role,m.content,m.sources,
            coalesce(a.sources_json,'[]') AS context_sources,coalesce(a.invalidated,0) AS invalidated FROM explore_messages m
            LEFT JOIN agent_answer_context a ON a.message_id=m.id
            WHERE m.session_id=? ORDER BY m.id''', (session_id,))]
        for row in rows:
            row['sources'] = json.loads(row['sources'])
            row['context_sources'] = json.loads(row['context_sources'])
        return dict(session), link, epoch, rows

    def usable_pairs(self, db, rows, memory_mode='account', user_id=None):
        result = []
        suppressed={r[0] for r in db.execute('SELECT source_message_id FROM agent_memory_suppressions WHERE user_id=?',(user_id,))}
        for index in range(0, len(rows) - 1, 2):
            pair = rows[index:index + 2]
            if [m['role'] for m in pair] == ['user', 'assistant'] and all(
                    not m['invalidated'] and m['id'] not in suppressed
                    and not(memory_mode=='off' and any(ref.get('kind')=='memory' for ref in m['context_sources']))
                    and self.sources_valid(db, [*m['sources'], *m['context_sources']], user_id=user_id) for m in pair):
                result.append(pair)
        return result

    def snapshot(self, db, user_id, session_id, link, epoch, rows, memory_mode=None):
        if not link['current_snapshot_id']:
            return None
        row = db.execute('''SELECT * FROM agent_context_snapshots
            WHERE id=? AND user_id=? AND session_id=?''',
                         (link['current_snapshot_id'], user_id, session_id)).fetchone()
        if row is None:
            return None
        if memory_mode is not None and row['memory_mode']!=memory_mode:
            return None
        valid = False
        try:
            prefix = [message for message in rows if message['id'] <= row['covered_message_id']]
            summary = ContextSummary.model_validate_json(row['summary_json'])
            summary.check_sources({m['id'] for m in prefix})
            valid = (row['status'] == 'valid' and row['prompt_version'] == PROMPT_VERSION
                     and row['context_epoch'] == epoch and row['constraints_version'] == link['version']
                     and row['history_digest'] == history_digest(prefix)
                     and self.sources_valid(db, json.loads(row['sources_json']), user_id=user_id))
        except (ValueError, KeyError, TypeError):
            valid = False
        if valid:
            return dict(row)
        # Stale private excerpts must not survive in the visible summary or future prompts.
        db.execute("UPDATE agent_context_snapshots SET status='invalid',summary_json='',sources_json='[]' WHERE id=?", (row['id'],))
        db.execute('UPDATE agent_session_links SET current_snapshot_id=NULL WHERE session_id=?', (session_id,))
        return None

    def assert_current(self, db, guard, *, check_history=True):
        current = db.execute('''SELECT s.version,s.busy_token,l.version AS constraints_version,
            a.context_epoch FROM explore_sessions s JOIN users u ON u.id=s.user_id
            JOIN agent_session_links l ON l.session_id=s.id JOIN agent_user_state a ON a.user_id=u.id
            WHERE s.id=? AND s.user_id=? AND u.active=1''', (guard['session_id'], guard['user_id'])).fetchone()
        if current is None:
            raise HTTPException(404, '会话不存在。')
        if (current['version'] != guard['session_version'] or current['busy_token'] != guard['busy_token']
                or current['context_epoch'] != guard['epoch']
                or current['constraints_version'] != guard['constraints_version']
                or not self.sources_valid(db, guard['sources'], user_id=guard['user_id'])):
            raise HTTPException(409, '会话要求或历史资料已变化，请重新提问。')
        if check_history:
            _, _, _, rows = self.state(db, guard['user_id'], guard['session_id'])
            if history_digest(rows) != guard['history_digest']:
                raise HTTPException(409, '会话历史已变化，请重新提问。')

    def prepare(self, user_id, session_id, session_version, busy_token,
                provider, system, question, scope, sources, cancelled=None, prepared=None, memory_mode='account'):
        prepared = prepared or PreparedContext([], sources)
        with self.database(self.db_path) as db:
            session, link, epoch, rows = self.state(db, user_id, session_id)
            if session['version'] != session_version or session['busy_token'] != busy_token:
                raise HTTPException(409, '会话已变化，请重新提问。')
            memory=getattr(self,'memory',None)
            memory_mode=memory_mode if memory and memory.enabled else 'off'
            pairs = self.usable_pairs(db, rows, memory_mode, user_id)
            snapshot = self.snapshot(db, user_id, session_id, link, epoch, rows, memory_mode)
            selected,memory_refs,conflicts=(memory.select(db,user_id,link['project_id'],question) if memory_mode=='account' else ([],[],[]))
            projects=getattr(self,'projects',None)
            project,handoff_refs=projects.context(db,user_id,link['project_id'],session_id,memory_mode) if projects else (None,[])
            guard = dict(user_id=user_id, session_id=session_id, session_version=session_version,
                         busy_token=busy_token, epoch=epoch, constraints_version=link['version'],
                         history_digest=history_digest(rows), sources=[*sources, *unique_sources(pairs),*memory_refs,*handoff_refs])
            constraints = json.loads(link['pinned_json'])

        summary = json.loads(snapshot['summary_json']) if snapshot else None
        covered = snapshot['covered_message_id'] if snapshot else 0
        pending = [pair for pair in pairs if pair[-1]['id'] > covered]
        recent = select_recent(pending, self.policy.recent_budget)
        older = pending[:len(pending) - len(recent)]
        prepared.guard = guard
        degraded = len(pairs) * 2 != len(rows)
        if older and self.policy.enabled:
            # Leave headroom: compact a batch, instead of paying for a summary every turn.
            recent = select_recent(pending, max(1, self.policy.recent_budget // 2))
            older = pending[:len(pending) - len(recent)]
            rebuild = bool(snapshot and snapshot['compression_depth'] >= 3)
            summary_base = None if rebuild else summary
            if rebuild:
                recent = select_recent(pairs, max(1, self.policy.recent_budget // 2))
                older = pairs[:len(pairs) - len(recent)]
            if callable(getattr(provider, 'summarize_context', None)):
                candidate_pairs = []
                payload = None
                for pair in older:
                    candidate = self.summary_payload(summary_base, [*candidate_pairs, pair], self.policy.summary_budget)
                    if estimate_tokens(candidate) + estimate_tokens(SUMMARY_SYSTEM) > self.policy.summary_input_budget:
                        break
                    candidate_pairs.append(pair)
                    payload = candidate
                if payload is not None:
                    if cancelled and cancelled.is_set():
                        raise HTTPException(499, '请求已取消。')
                    prepared.summary_requested = True
                    started = time.perf_counter()
                    reservation = None
                    try:
                        from agent_budget import reserve,settle
                        reservation=reserve(self.database,self.db_path,user_id,[SUMMARY_SYSTEM,payload],2000,'context_summary')
                        raw = provider.summarize_context(payload, usage=prepared.summary_usage)
                        new_covered = candidate_pairs[-1][-1]['id']
                        allowed_ids = {m['id'] for pair in candidate_pairs for m in pair}
                        if summary_base:
                            for group in ('goal', 'constraints', 'decisions', 'completed', 'open_questions', 'next_actions'):
                                facts = [summary_base[group]] if group == 'goal' else summary_base[group]
                                for fact in facts:
                                    allowed_ids.update(fact['source_message_ids'])
                        validated = ContextSummary.model_validate_json(raw).check_sources(allowed_ids)
                        serialized = compact_json(validated.model_dump())
                        if estimate_tokens(serialized) > self.policy.summary_budget:
                            raise ValueError('Summary exceeds budget')
                    except (HTTPException, ValueError, TypeError):
                        degraded = True
                    else:
                        if cancelled and cancelled.is_set():
                            raise HTTPException(499, '请求已取消。')
                        with self.database(self.db_path) as db:
                            db.execute('BEGIN IMMEDIATE')
                            self.assert_current(db, guard)
                            snapshot_id = secrets.token_urlsafe(24)
                            prefix = [m for m in rows if m['id'] <= new_covered]
                            refs = unique_sources([pair for pair in pairs if pair[-1]['id'] <= new_covered])
                            db.execute('''INSERT INTO agent_context_snapshots
                                (id,user_id,session_id,base_session_version,covered_message_id,
                                 context_epoch,constraints_version,history_digest,summary_json,
                                 sources_json,estimated_tokens,prompt_version,model_id,created_at,compression_depth,memory_mode)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                                (snapshot_id, user_id, session_id, session_version, new_covered,
                                 epoch, link['version'], history_digest(prefix), serialized,
                                 compact_json(refs), estimate_tokens(serialized), PROMPT_VERSION,
                                 str(getattr(provider, 'model', 'custom-provider'))[:100], int(time.time()),
                                 1 if rebuild or not snapshot else snapshot['compression_depth'] + 1,memory_mode))
                            db.execute('UPDATE agent_session_links SET current_snapshot_id=? WHERE session_id=?', (snapshot_id, session_id))
                            from agent_dependencies import record_dependencies
                            record_dependencies(db,user_id,refs,'snapshot',snapshot_id)
                            db.execute('''UPDATE agent_context_snapshots SET status='stale',summary_json='',sources_json='[]'
                                WHERE session_id=? AND id NOT IN (SELECT id FROM agent_context_snapshots
                                  WHERE session_id=? ORDER BY rowid DESC LIMIT 5)''', (session_id, session_id))
                        summary = validated.model_dump()
                        covered = new_covered
                        snapshot = {'id': snapshot_id}
                    finally:
                        settle(self.database,self.db_path,reservation,prepared.summary_usage)
                        prepared.summary_ms = round((time.perf_counter() - started) * 1000)
                else:
                    degraded = True
            else:
                degraded = True
        pending = [pair for pair in pairs if pair[-1]['id'] > covered]
        recent = select_recent(pending, self.policy.recent_budget)
        degraded = degraded or len(recent) != len(pending)
        prepared.messages, prepared.sources, estimated, dropped = self.manager.compose(
            system, question, scope, sources, recent, summary, constraints, selected, project)
        prepared.info = dict(enabled=True, snapshot_id=snapshot['id'] if snapshot else None,
                             compacted=summary is not None, degraded=degraded or dropped,
                             constraints_version=link['version'], estimated_input_tokens=estimated,
                             estimator='utf8_bytes_conservative_v1')
        prepared.info.update(project_id=link['project_id'],memory_context=[{k:item[k] for k in ('id','revision')} for item in selected],memory_conflicts=conflicts,memory_mode=memory_mode)
        with self.database(self.db_path) as db:
            self.assert_current(db, guard)
        return prepared

    @staticmethod
    def summary_payload(previous, pairs, output_budget=1500):
        return compact_json({'schema': summary_schema(), 'previous_summary': previous,
                             'output_byte_budget': max(0, output_budget - 16),
                             'messages': [dict(id=m['id'], **history_message(m)) for pair in pairs for m in pair]})

    def context_detail(self, user_id, session_id):
        with self.database(self.db_path) as db:
            _, link, epoch, rows = self.state(db, user_id, session_id)
            snapshot = self.snapshot(db, user_id, session_id, link, epoch, rows)
            return dict(enabled=True, version=link['version'], constraints=json.loads(link['pinned_json']),
                        snapshot_id=snapshot['id'] if snapshot else None,
                        summary=json.loads(snapshot['summary_json']) if snapshot else None)

    def update_constraints(self, user_id, session_id, data):
        with self.database(self.db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            _, link, _, _ = self.state(db, user_id, session_id)
            if link['version'] != data.expected_version:
                raise HTTPException(409, '固定要求已在其他窗口修改，请重新读取后再保存。')
            db.execute('''UPDATE agent_session_links SET pinned_json=?,version=version+1,current_snapshot_id=NULL
                WHERE session_id=?''', (compact_json(data.constraints), session_id))
            db.execute('UPDATE agent_user_state SET context_epoch=context_epoch+1 WHERE user_id=?', (user_id,))
            db.execute("UPDATE agent_context_snapshots SET status='invalid',summary_json='',sources_json='[]' WHERE session_id=?", (session_id,))
        return self.context_detail(user_id, session_id)

    def record_metric(self, prepared, status):
        usage = prepared.summary_usage
        def count(name):
            value = usage.get(name)
            return value if type(value) is int and value >= 0 else None
        with self.database(self.db_path) as db:
            db.execute('''INSERT INTO agent_context_metrics
              (created_at,status,compacted,degraded,summary_requested,summary_ms,estimated_input_tokens,
               summary_prompt_tokens,summary_completion_tokens) VALUES(?,?,?,?,?,?,?,?,?)''',
              (int(time.time()), status, bool(prepared.info.get('compacted')), bool(prepared.info.get('degraded')),
               prepared.summary_requested, prepared.summary_ms, prepared.info.get('estimated_input_tokens', 0),
               count('prompt_tokens'), count('completion_tokens')))
            db.execute('DELETE FROM agent_context_metrics WHERE created_at<?', (int(time.time()) - 90 * 86400,))
