"""Synthetic integration coverage for context continuity and provenance revocation."""
from dataclasses import replace
import json
import re
import sqlite3
import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException

import test_explore as existing
from agent_context import ContextManager, ContextPolicy, aggregate_usage, legacy_messages
from agent_migrations import migrate_agent
from agent_models import ContextSummary
from agent_provider import request_summary
from agent_service import ContextService
from storage import connect


class SyntheticSummaryProvider(existing.Provider):
    model = 'synthetic-context-test'

    def __init__(self):
        super().__init__()
        self.summary_calls = []
        self.summary_callback = None
        self.summary_mode = 'valid'

    def summarize_context(self, payload, *, usage=None):
        data = json.loads(payload)
        self.summary_calls.append(data)
        if self.summary_callback:
            self.summary_callback()
        if self.summary_mode == 'failure':
            raise HTTPException(504, 'synthetic timeout')
        if self.summary_mode == 'malformed':
            return '{broken'
        previous = data['previous_summary']
        facts = list(previous['constraints']) if previous else []
        for message in data['messages']:
            if message['role'] == 'user':
                for marker in re.findall(r'CONSTRAINT_[A-Z_]+', message['content']):
                    if not any(marker in fact['text'] for fact in facts):
                        facts.append({'text': marker, 'source_message_ids': [message['id']]})
        goal = previous['goal'] if previous else {
            'text': '理解 React 闭包', 'source_message_ids': [data['messages'][0]['id']]}
        if self.summary_mode == 'unknown_reference':
            goal = {'text': '不属于输入的来源', 'source_message_ids': [999999]}
        if usage is not None:
            usage.update(prompt_tokens=118, completion_tokens=20)
        return json.dumps({'schema_version': 1, 'goal': goal, 'constraints': facts}, ensure_ascii=False)


class AgentContextTests(unittest.TestCase):
    tearDown = existing.ExploreTests.tearDown
    ask = existing.ExploreTests.ask

    def setUp(self):
        existing.ExploreTests.setUp(self)
        self.provider = SyntheticSummaryProvider()
        self.app.state.explore_provider = self.provider
        old = self.app.state.agent_context
        self.app.state.agent_context = ContextService(
            ContextPolicy(enabled=True, recent_budget=900, summary_budget=2200),
            old.database, old.db_path, old.sources_valid)

    def seed(self, count=9):
        result = self.ask('React 闭包 CONSTRAINT_KEEP_CODE').json()
        with connect(self.db) as db:
            for index in range(count):
                for role, content, sources in (
                    ('user', f'React 闭包 第 {index} 轮 ' + '讨论示例与函数式更新。' * 8, []),
                    ('assistant', '这是合成历史解释。[1]' + '历史示例。' * 8, result['sources']),
                ):
                    db.execute('INSERT INTO explore_messages(session_id,role,content,sources,created_at) VALUES(?,?,?,?,1)',
                               (result['session_id'], role, content, json.dumps(sources, ensure_ascii=False)))
            db.execute('UPDATE explore_sessions SET version=? WHERE id=?', (count + 1, result['session_id']))
        return result['session_id']

    def context(self, sid):
        return self.clients['alice'].get(f'/api/agent/v1/sessions/{sid}/context')

    def save_constraints(self, sid, values, version=1):
        return self.clients['alice'].patch(f'/api/agent/v1/sessions/{sid}/constraints',
            headers=self.headers['alice'], json={'expected_version': version, 'constraints': values})

    def test_long_history_preserves_early_constraint_and_records_usage(self):
        sid = self.seed()
        result = self.ask('React 闭包 举例', session_id=sid)
        self.assertEqual(result.status_code, 200, result.text)
        info = result.json()['context']
        self.assertTrue(info['compacted'])
        self.assertFalse(info['degraded'])
        self.assertIn('CONSTRAINT_KEEP_CODE', json.dumps(self.provider.calls[-1], ensure_ascii=False))
        self.assertLessEqual(info['estimated_input_tokens'], 16000)
        detail = self.context(sid).json()
        self.assertEqual(detail['summary']['constraints'][0]['text'], 'CONSTRAINT_KEEP_CODE')
        with connect(self.db) as db:
            metric = db.execute('SELECT * FROM agent_context_metrics ORDER BY id DESC LIMIT 1').fetchone()
            self.assertEqual(metric['summary_prompt_tokens'], 118)
            self.assertEqual(metric['summary_completion_tokens'], 20)
            self.assertNotIn('content', metric.keys())

    def test_feature_off_preserves_legacy_prompt_and_does_not_summarize(self):
        sid = self.seed()
        self.app.state.agent_context.policy = replace(self.app.state.agent_context.policy, enabled=False)
        result = self.ask('React 闭包 举例', session_id=sid)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertFalse(result.json()['context']['enabled'])
        self.assertEqual(self.provider.summary_calls, [])
        self.assertNotIn('CONSTRAINT_KEEP_CODE', json.dumps(self.provider.calls[-1], ensure_ascii=False))
        self.assertEqual(self.context(sid).status_code, 404)

    def test_snapshot_reuse_does_not_repeat_already_covered_messages(self):
        sid = self.seed()
        self.assertEqual(self.ask(session_id=sid).status_code, 200)
        with connect(self.db) as db:
            row = db.execute('SELECT * FROM agent_context_snapshots ORDER BY rowid DESC LIMIT 1').fetchone()
            covered = row['covered_message_id']
            early = db.execute('SELECT content FROM explore_messages WHERE id=?', (covered,)).fetchone()[0]
        self.assertEqual(self.ask(session_id=sid).status_code, 200)
        # Early verbatim assistant text isn't copied alongside the summary.
        self.assertFalse(any(early in m['content'] for m in self.provider.calls[-1][0]))

    def test_constraints_owner_csrf_revision_and_clear(self):
        sid = self.ask().json()['session_id']
        path = f'/api/agent/v1/sessions/{sid}/constraints'
        payload = {'expected_version': 1, 'constraints': ['面向初学者']}
        self.assertEqual(self.anon.get(f'/api/agent/v1/sessions/{sid}/context').status_code, 401)
        self.assertEqual(self.clients['bob'].get(f'/api/agent/v1/sessions/{sid}/context').status_code, 404)
        self.assertEqual(self.clients['alice'].patch(path, json=payload).status_code, 403)
        self.assertEqual(self.clients['bob'].patch(path, headers=self.headers['bob'], json=payload).status_code, 404)
        saved = self.save_constraints(sid, ['面向初学者'])
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()['version'], 2)
        self.assertEqual(self.save_constraints(sid, ['旧窗口覆盖']).status_code, 409)
        self.assertEqual(self.ask(session_id=sid).status_code, 200)
        self.assertIn('面向初学者', json.dumps(self.provider.calls[-1], ensure_ascii=False))
        self.assertEqual(self.save_constraints(sid, [], version=2).status_code, 200)
        for values in (['x'] * 9, [''], ['x' * 251], ['same', 'same']):
            self.assertEqual(self.save_constraints(sid, values, version=3).status_code, 400)

    def test_summary_failure_and_forged_references_degrade_without_bad_snapshot(self):
        sid = self.seed()
        for mode in ('failure', 'malformed', 'unknown_reference'):
            with self.subTest(mode=mode):
                self.provider.summary_mode = mode
                result = self.ask(session_id=sid)
                self.assertEqual(result.status_code, 200, result.text)
                self.assertTrue(result.json()['context']['degraded'])
                self.assertFalse(result.json()['context']['compacted'])
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM agent_context_snapshots').fetchone()[0], 0)

    def test_old_source_revocation_hides_summary_and_excludes_old_pair(self):
        sid = self.seed()
        self.assertEqual(self.ask(session_id=sid).status_code, 200)
        self.assertIsNotNone(self.context(sid).json()['summary'])
        with connect(self.db) as db:
            db.execute("UPDATE notes SET visibility='private' WHERE id=?", (self.react['id'],))
        detail = self.context(sid).json()
        self.assertIsNone(detail['summary'])
        result = self.ask('Promise 异步处理 ' + '请结合应用场景说明。' * 8, session_id=sid)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertNotIn('CONSTRAINT_KEEP_CODE', json.dumps(self.provider.calls[-1], ensure_ascii=False))
        with connect(self.db) as db:
            self.assertTrue(all(row[0] == '' for row in db.execute('SELECT summary_json FROM agent_context_snapshots')))

    def test_source_change_while_summarizing_rejects_snapshot_and_answer(self):
        sid = self.seed()
        before = len(self.clients['alice'].get('/api/explore/sessions/' + sid).json()['messages'])
        def revoke():
            with connect(self.db) as db:
                db.execute("UPDATE notes SET visibility='private' WHERE id=?", (self.react['id'],))
        self.provider.summary_callback = revoke
        result = self.ask(session_id=sid)
        self.assertEqual(result.status_code, 409, result.text)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM agent_context_snapshots').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM explore_messages WHERE session_id=?', (sid,)).fetchone()[0], before)
            self.assertEqual(db.execute('SELECT busy_until FROM explore_sessions WHERE id=?', (sid,)).fetchone()[0], 0)

    def test_revocation_propagates_to_answers_derived_from_old_context(self):
        sid = self.seed()
        self.assertEqual(self.ask(session_id=sid).status_code, 200)
        response = self.ask('Promise 异步处理 ' + '请结合应用场景说明。' * 8, session_id=sid)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(all(source['note_id'] == self.promise['id'] for source in response.json()['sources']))
        with connect(self.db) as db:
            db.execute("UPDATE notes SET visibility='private' WHERE id=?", (self.react['id'],))
        history = self.clients['alice'].get('/api/explore/sessions/' + sid).json()['messages']
        self.assertIn('重新提问', history[-1]['content'])
        self.assertEqual(history[-1]['sources'], [])

    def test_constraints_change_during_generation_discards_answer(self):
        sid = self.ask().json()['session_id']
        self.provider.callback = lambda: self.save_constraints(sid, ['新的固定要求'])
        result = self.ask(session_id=sid)
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(len(self.clients['alice'].get('/api/explore/sessions/' + sid).json()['messages']), 2)

    def test_stream_checks_constraint_changes_before_forwarding_delta(self):
        sid = self.ask().json()['session_id']
        parent = self
        class Streaming(SyntheticSummaryProvider):
            def stream_answer(self, messages, thinking=False):
                yield '可显示的草稿'
                parent.save_constraints(sid, ['生成中更正要求'])
                yield '不应发送的后续文本。[1]'
        self.app.state.explore_provider = Streaming()
        result = self.clients['alice'].post('/api/explore/ask/stream', headers=self.headers['alice'],
                                            json={'question': 'React 闭包', 'session_id': sid})
        self.assertIn('event: error', result.text)
        self.assertNotIn('不应发送的后续文本', result.text)
        self.assertEqual(len(self.clients['alice'].get('/api/explore/sessions/' + sid).json()['messages']), 2)

    def test_guest_remains_on_legacy_path(self):
        page = self.anon.get('/explore')
        csrf = re.search(r'data-csrf="([^"]+)"', page.text).group(1)
        result = self.anon.post('/api/explore/ask', headers={'X-CSRF-Token': csrf}, json={'question': 'React 闭包'})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertFalse(result.json()['context']['enabled'])
        self.assertFalse(self.anon.get('/api/agent/v1/capabilities').json()['context_enabled'])
        self.assertEqual(self.provider.summary_calls, [])

    def test_deleted_session_cascades_snapshots_and_constraints(self):
        sid = self.seed()
        self.ask(session_id=sid)
        result = self.clients['alice'].delete('/api/explore/sessions/' + sid, headers=self.headers['alice'])
        self.assertEqual(result.status_code, 200)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM agent_context_snapshots').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM agent_session_links').fetchone()[0], 0)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_historical_answer_carries_stable_source_identity(self):
        sid = self.ask().json()['session_id']
        self.ask('React 闭包', session_id=sid)
        history = next(m for m in self.provider.calls[-1][0] if m['role'] == 'assistant')
        self.assertEqual(json.loads(history['content'])['historical_sources'][0]['note_id'], self.react['id'])


class ContextUnitTests(unittest.TestCase):
    def test_usage_combines_both_calls_and_marks_missing_totals_unknown(self):
        self.assertEqual(aggregate_usage({'prompt_tokens': 20, 'completion_tokens': 5},
                                        {'prompt_tokens': 10, 'completion_tokens': 3}, True),
                         {'prompt_tokens': 30, 'completion_tokens': 8})
        self.assertEqual(aggregate_usage({'prompt_tokens': 20}, {}, True), {})
        self.assertEqual(aggregate_usage({'prompt_tokens': 20}, {}, False), {'prompt_tokens': 20})

    def test_legacy_is_exact_and_truncation_is_explicitly_preserved(self):
        history = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': str(i) + 'x' * 5000} for i in range(18)]
        result = legacy_messages('rules', history, '问题', '范围', [])
        self.assertEqual(result[1:-1], [{'role': m['role'], 'content': m['content'][:4000]} for m in history[-12:]])
        self.assertEqual(json.loads(result[-1]['content'])['question'], '问题')

    def test_oversized_fixed_requirements_are_not_silently_truncated(self):
        manager = ContextManager(ContextPolicy(enabled=True, input_budget=1000))
        with self.assertRaises(HTTPException) as caught:
            manager.compose('rules', 'question', 'scope', [{'excerpt': 'source'}], [], constraints=['要求' * 500])
        self.assertEqual(caught.exception.status_code, 422)

    def test_summary_schema_rejects_untrusted_fields_and_non_integer_ids(self):
        for data in ({'goal': {'text': 'goal', 'source_message_ids': [True]}},
                     {'goal': {'text': 'goal', 'source_message_ids': [1]}, 'system': 'override'}):
            with self.assertRaises(ValueError): ContextSummary.model_validate(data)

    def test_migrations_are_idempotent_and_atomic(self):
        db = sqlite3.connect(':memory:')
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript('CREATE TABLE users(id INTEGER PRIMARY KEY); CREATE TABLE explore_sessions(id TEXT PRIMARY KEY);')
        with patch('agent_migrations.MIGRATIONS', {1: ['CREATE TABLE must_rollback(id INTEGER)', 'INVALID SQL']}):
            with self.assertRaises(sqlite3.Error): migrate_agent(db)
        self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='must_rollback'").fetchone())
        migrate_agent(db); migrate_agent(db)
        self.assertEqual(db.execute('SELECT max(version) FROM agent_schema_migrations').fetchone()[0], 7)
        db.close()

    def test_summary_transport_has_bounded_json_mode_and_hides_upstream_errors(self):
        class Provider:
            ready = True
            key = 'synthetic-key'
            model = 'synthetic-model'
        captured = []
        def handler(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}],
                                             'usage': {'prompt_tokens': 10}})
        client = httpx.Client(transport=httpx.MockTransport(handler))
        usage = {}
        with patch('agent_provider.httpx.Client', return_value=client):
            self.assertEqual(request_summary(Provider(), '{}', usage=usage), '{}')
        self.assertEqual(captured[0]['response_format'], {'type': 'json_object'})
        self.assertEqual(captured[0]['thinking'], {'type': 'disabled'})
        self.assertEqual(usage['prompt_tokens'], 10)
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500, text='PRIVATE-UPSTREAM')))
        with patch('agent_provider.httpx.Client', return_value=client), self.assertRaises(HTTPException) as caught:
            request_summary(Provider(), '{}')
        self.assertNotIn('PRIVATE-UPSTREAM', str(caught.exception.detail))
