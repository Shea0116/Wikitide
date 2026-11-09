"""Real route coverage for multi-note scope and bounded model-chosen tools."""
import json
import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from explore import DeepSeek
from storage import connect, save_note
import test_explore


class Planner(test_explore.Provider):
    model = 'synthetic-planner'

    def __init__(self, actions):
        super().__init__()
        self.actions = iter(actions)
        self.plans = []
        self.on_plan = None

    def plan_retrieval(self, messages, *, usage):
        self.plans.append(messages)
        if self.on_plan:
            self.on_plan()
        usage.update(prompt_tokens=10, completion_tokens=5)
        return json.dumps(next(self.actions, {'action': 'finish'}))


class KnowledgeRetrievalTests(unittest.TestCase):
    ask = test_explore.ExploreTests.ask
    tearDown = test_explore.ExploreTests.tearDown

    def setUp(self):
        test_explore.ExploreTests.setUp(self)
        self.app.state.explore_selected_enabled = True
        self.app.state.knowledge_retrieval_enabled = True
        with connect(self.db) as db:
            self.uid = db.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
            db.execute('INSERT INTO grants(user_id,note_id,can_view) VALUES(?,?,1)', (self.uid, self.secret['id']))

    def plan(self, actions):
        self.planner = Planner(actions)
        self.app.state.explore_provider = self.planner
        return self.planner

    def multi(self, **kwargs):
        return self.ask(scope='selected', note_ids=[self.secret['id'], self.react['id']], **kwargs)

    def test_multi_note_selection_is_canonical_persisted_and_checked_on_every_read(self):
        response = self.multi()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['note_ids'], sorted([self.secret['id'], self.react['id']]))
        detail = self.clients['alice'].get('/api/explore/sessions/' + result['session_id']).json()
        self.assertEqual(detail['note_id'], None)
        self.assertEqual({note['id'] for note in detail['notes']}, set(result['note_ids']))
        self.assertEqual(detail['scope_version'], result['scope_version'])
        self.assertEqual(self.multi(session_id=result['session_id']).status_code, 200)
        self.assertEqual(self.ask(scope='selected', note_id=self.secret['id'], session_id=result['session_id']).status_code, 409)
        selected = self.clients['alice'].get('/api/explore/search', params=[('q', '闭包'), ('scope', 'selected'),
            ('note_ids', self.secret['id']), ('note_ids', self.react['id'])])
        self.assertEqual(selected.status_code, 200, selected.text)
        self.assertEqual({item['note_id'] for item in selected.json()['results']}, set(result['note_ids']))
        with connect(self.db) as db:
            db.execute('DELETE FROM grants WHERE user_id=?', (self.uid,))
        self.assertEqual(self.clients['alice'].get('/api/explore/sessions/' + result['session_id']).status_code, 404)
        self.assertNotIn(result['session_id'], self.clients['alice'].get('/api/explore/sessions').text)

    def test_picker_and_whole_selection_cannot_expose_ungranted_notes(self):
        alice = self.clients['alice'].get('/api/explore/notes').json()['notes']
        bob = self.clients['bob'].get('/api/explore/notes').json()['notes']
        self.assertIn(self.secret['id'], {note['id'] for note in alice})
        self.assertNotIn(self.secret['id'], {note['id'] for note in bob})
        self.assertEqual(self.anon.get('/api/explore/notes').status_code, 401)
        self.assertEqual(self.ask(scope='selected', note_ids=[self.secret['id'], self.hidden['id']]).status_code, 404)
        self.assertEqual(self.ask(note_ids=[self.react['id']]).status_code, 400)
        self.assertEqual(self.ask(scope='selected', note_ids=list(range(1, 14))).status_code, 400)
        self.assertEqual(self.ask(scope='selected', note_id=self.secret['id'], note_ids=[self.react['id']]).status_code, 400)
        self.assertEqual(len(self.provider.calls), 0)

    def test_search_actions_add_evidence_record_trace_and_charge_each_model_call(self):
        self.plan([{'action': 'search_knowledge', 'query': 'Promise 异步处理'}, {'action': 'finish'}])
        response = self.ask(question='闭包和异步结果', research=True)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        info = result['retrieval']
        self.assertEqual((info['model_calls'], info['search_calls'], info['stop_reason']), (2, 2, 'finished'))
        self.assertIn('Promise', json.dumps(self.planner.calls, ensure_ascii=False))
        trace = self.clients['alice'].get('/api/explore/retrieval/' + info['run_id'])
        self.assertEqual(trace.status_code, 200, trace.text)
        self.assertEqual(trace.json()['run']['status'], 'succeeded')
        self.assertEqual(trace.json()['run']['prompt_tokens'], 20)
        followup_payload = json.loads(self.planner.plans[1][1]['content'])
        self.assertEqual(followup_payload['actions'][1]['query'], 'Promise 异步处理')
        self.assertNotIn('query', trace.text)
        self.assertNotIn('PRIVATE-CONTENT', json.dumps(self.planner.plans))
        self.assertEqual(self.clients['bob'].get('/api/explore/retrieval/' + info['run_id']).status_code, 404)
        history = self.clients['alice'].get('/api/explore/sessions/' + result['session_id']).json()
        self.assertEqual(history['messages'][1]['retrieval']['run_id'], info['run_id'])
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM agent_usage_reservations WHERE kind='retrieval' AND charged_tokens=15").fetchone()[0], 2)

    def test_read_source_reads_an_adjacent_chunk_within_the_selected_scope(self):
        with connect(self.db) as db:
            current = db.execute('SELECT * FROM notes WHERE id=?', (self.secret['id'],)).fetchone()
            save_note(db, current, '# 资料\n\n## 检索目标\n\n检索目标特征词。\n\n## 后续章节\n\nADJACENT-EVIDENCE', 'web')
        self.plan([{'action': 'read_source', 'source_id': 's1', 'direction': 'next'}, {'action': 'finish'}])
        response = self.ask(question='检索目标特征词', scope='selected', note_id=self.secret['id'], research=True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['retrieval']['read_calls'], 1)
        self.assertIn('ADJACENT-EVIDENCE', json.dumps(self.planner.calls))

    def test_repeated_and_invalid_actions_stop_without_executing_arbitrary_arguments(self):
        self.plan([{'action': 'search_knowledge', 'query': 'Promise'}, {'action': 'search_knowledge', 'query': 'Promise'}])
        response = self.ask(research=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['retrieval']['stop_reason'], 'duplicate_action')
        self.assertEqual(response.json()['retrieval']['search_calls'], 2)
        self.plan([{'action': 'read_source', 'source_id': 's99', 'direction': 'next'}])
        response = self.multi(research=True)
        self.assertEqual(response.json()['retrieval']['stop_reason'], 'read_limit_or_scope')
        invalid = {'action': 'search_knowledge', 'query': '闭包', 'user_id': 2, 'note_ids': [self.hidden['id']]}
        self.plan([invalid, invalid])
        response = self.multi(research=True)
        self.assertEqual(response.json()['retrieval']['stop_reason'], 'invalid_action')
        self.assertEqual(response.json()['retrieval']['search_calls'], 1)

    def test_search_limit_and_schema_failure_preserve_truthful_partial_results(self):
        self.plan([{'action': 'search_knowledge', 'query': query} for query in ('Promise', '词法作用域', '不能再查')])
        response = self.ask(research=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['retrieval']['search_calls'], 3)
        self.assertEqual(response.json()['retrieval']['stop_reason'], 'search_limit')
        self.plan([{'action': 'execute_shell', 'command': 'invalid'}] * 2)
        self.assertEqual(self.ask(research=True).json()['retrieval']['stop_reason'], 'invalid_action')

    def test_one_schema_repair_is_bounded_and_does_not_execute_bad_arguments(self):
        planner = self.plan([{'action': 'search_knowledge', 'arguments': {'query': 'bad'}}, {'action': 'finish'}])
        response = self.ask(research=True)
        self.assertEqual(response.status_code, 200, response.text)
        info = response.json()['retrieval']
        self.assertEqual((info['model_calls'], info['search_calls'], info['stop_reason']), (2, 1, 'finished'))
        self.assertEqual(info['actions'][0]['status'], 'invalid_format')
        payload = json.loads(planner.plans[1][1]['content'])
        self.assertIn('extra_forbidden', payload['format_errors'])

    def test_finish_prioritizes_chosen_sources_and_preserves_other_found_evidence(self):
        self.plan([{'action': 'search_knowledge', 'query': 'Promise'}, {'action': 'finish', 'source_ids': ['s1']}])
        response = self.ask(research=True)
        self.assertEqual(response.status_code, 200, response.text)
        messages = json.dumps(self.planner.calls, ensure_ascii=False)
        self.assertIn('闭包', messages)
        self.assertIn('Promise', messages)

    def test_revoke_or_edit_during_planning_blocks_final_answer_and_trace(self):
        planner = self.plan([{'action': 'finish'}])
        def revoke():
            with connect(self.db) as db:
                db.execute('DELETE FROM grants WHERE user_id=?', (self.uid,))
        planner.on_plan = revoke
        response = self.multi(research=True)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(planner.calls), 0)
        with connect(self.db) as db:
            run = db.execute('SELECT * FROM knowledge_retrieval_runs').fetchone()
            self.assertEqual(run['status'], 'failed')
            self.assertEqual(db.execute('SELECT count(*) FROM explore_messages').fetchone()[0], 0)
            run_id = run['id']
        self.assertEqual(self.clients['alice'].get('/api/explore/retrieval/' + run_id).status_code, 409)

    def test_research_gate_and_uncertain_provider_usage_are_honest(self):
        planner = self.plan([])
        self.app.state.knowledge_retrieval_enabled = False
        self.assertEqual(self.ask(research=True).status_code, 403)
        self.app.state.knowledge_retrieval_enabled = True
        def unavailable(messages, *, usage):
            raise HTTPException(502, 'temporary')
        planner.plan_retrieval = unavailable
        response = self.ask(research=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['retrieval']['stop_reason'], 'provider_or_budget_error')
        self.assertEqual(response.json()['retrieval']['usage'], {})
        with connect(self.db) as db:
            reservation = db.execute("SELECT * FROM agent_usage_reservations WHERE kind='retrieval'").fetchone()
            self.assertIsNone(reservation['charged_tokens'])

    def test_real_provider_action_protocol_has_structured_output_and_sanitized_failures(self):
        seen = []
        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '{"action":"finish"}'}}],
                                            'usage': {'prompt_tokens': 2, 'completion_tokens': 3}})
        provider = DeepSeek()
        client = httpx.Client(transport=httpx.MockTransport(handler))
        usage = {}
        with patch('knowledge_retrieval.httpx.Client', return_value=client):
            self.assertEqual(provider.plan_retrieval([], usage=usage), '{"action":"finish"}')
        self.assertEqual(seen[0]['response_format'], {'type': 'json_object'})
        self.assertEqual(usage['completion_tokens'], 3)
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500, text='SECRET-UPSTREAM')))
        with patch('knowledge_retrieval.httpx.Client', return_value=client), self.assertRaises(HTTPException) as caught:
            provider.plan_retrieval([], usage={})
        self.assertNotIn('SECRET', caught.exception.detail)

    def test_real_provider_path_aggregates_planning_and_answer_usage_once(self):
        original_client = httpx.Client
        def handler(request):
            payload = json.loads(request.content)
            decision = 'response_format' in payload
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
                'content': '{"action":"finish"}' if decision else '回答 [1]'}}],
                'usage': {'prompt_tokens': 2 if decision else 10, 'completion_tokens': 3 if decision else 5}})
        provider = DeepSeek()
        provider.key = 'synthetic-test-key'
        self.app.state.explore_provider = provider
        with patch('explore.httpx.Client', side_effect=lambda *args, **kwargs: original_client(transport=httpx.MockTransport(handler))):
            response = self.ask(research=True, note_id=self.react['id'])
        self.assertEqual(response.status_code, 200, response.text)
        trace = self.clients['alice'].get('/api/explore/retrieval/' + response.json()['retrieval']['run_id']).json()
        self.assertEqual(trace['run']['scope_version'], response.json()['scope_version'])
        with connect(self.db) as db:
            metric = db.execute('SELECT prompt_tokens,completion_tokens FROM explore_metrics ORDER BY id DESC LIMIT 1').fetchone()
            self.assertEqual(tuple(metric), (12, 8))
            charges = [row[0] for row in db.execute('SELECT charged_tokens FROM agent_usage_reservations ORDER BY charged_tokens')]
            self.assertEqual(charges, [5, 15])

    def test_planning_usage_is_still_metered_when_no_answer_generation_is_needed(self):
        planner = self.plan([{'action': 'finish'}])
        response = self.ask(question='unfindable-marker-zzqq8877', research=True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['sources'], [])
        self.assertEqual(len(planner.calls), 0)
        with connect(self.db) as db:
            metric = db.execute('SELECT prompt_tokens,completion_tokens FROM explore_metrics ORDER BY id DESC LIMIT 1').fetchone()
            self.assertEqual(tuple(metric), (10, 5))


if __name__ == '__main__':
    unittest.main()
