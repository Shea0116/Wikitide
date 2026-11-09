"""Durable retrieval replay, uncertain calls and private result fences."""
import json
import unittest
from unittest.mock import patch

from agent_runtime import LeaseLost
from features import add_note
from storage import connect, save_note
import knowledge_jobs
import test_agent_runtime


class ProcessDeath(BaseException):
    pass


class Planner(test_agent_runtime.Provider):
    model = 'synthetic-worker'

    def __init__(self):
        super().__init__()
        self.plans = []
        self.on_plan = None

    def plan_retrieval(self, messages, *, usage):
        self.plans.append(messages)
        if self.on_plan:
            self.on_plan()
        usage.update(prompt_tokens=10, completion_tokens=5)
        payload = json.loads(messages[-1]['content'])
        return json.dumps({'action': 'search_knowledge', 'query': '闭包 词法作用域'} if len(payload['actions']) == 1 else {'action': 'finish'})


class KnowledgeJobTests(unittest.TestCase):
    tearDown = test_agent_runtime.RuntimeTests.tearDown
    detail = test_agent_runtime.RuntimeTests.detail

    def setUp(self):
        test_agent_runtime.RuntimeTests.setUp(self)
        self.app.state.explore_selected_enabled = self.app.state.knowledge_retrieval_enabled = True
        self.provider = Planner()
        self.app.state.explore_provider = self.provider
        with connect(self.db) as db:
            self.private = add_note(db, 'Developer/worker-private.md', '# 合成私密资料\n\n闭包保留词法作用域。PRIVATE-WORKER-SOURCE', category='私密', visibility='private')
            db.execute('INSERT INTO grants(user_id,note_id,can_view) VALUES(?,?,1)', (self.ids['alice'], self.private['id']))

    def create(self, **changes):
        body = dict(kind='knowledge', topic='闭包', scope='selected', note_ids=[self.private['id']],
                    max_tokens=48000, idempotency_key='knowledge-worker-key')
        body.update(changes)
        response = self.client.post('/api/agent/v1/runs', headers=self.headers['alice'], json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def expire(self, rid):
        with connect(self.db) as db:
            db.execute('UPDATE agent_runs SET lease_until=0 WHERE id=?', (rid,))

    def test_worker_saves_private_research_once_and_uses_only_run_budget(self):
        job = self.create()
        self.assertEqual(self.create()['id'], job['id'])
        self.assertTrue(self.service.run_once())
        result = self.detail(job['id'])
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(result['budget']['used_tokens'], 160)
        self.assertEqual(result['budget']['search_calls'], 2)
        self.assertIn('PRIVATE-WORKER-SOURCE', json.dumps(self.provider.calls))
        report = self.client.get('/api/agent/v1/runs/' + job['id'] + '/knowledge')
        self.assertEqual(report.status_code, 200, report.text)
        self.assertIn('/n/', report.json()['html'])
        self.assertEqual(self.clients['bob'].get('/api/agent/v1/runs/' + job['id'] + '/knowledge').status_code, 404)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM agent_usage_reservations').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM knowledge_job_results').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        events = self.client.get('/api/agent/v1/runs/' + job['id'] + '/events').text
        self.assertNotIn('PRIVATE-WORKER-SOURCE', events)

    def test_restart_replays_saved_decision_without_another_model_call(self):
        job = self.create()
        claimed = self.service.claim()
        original = self.service.call_model
        def die_after_saved(run, key, *args, **kwargs):
            output = original(run, key, *args, **kwargs)
            if key == 'decision_1':
                raise ProcessDeath()
            return output
        with patch.object(self.service, 'call_model', side_effect=die_after_saved), self.assertRaises(ProcessDeath):
            self.service.execute(claimed)
        self.assertEqual(len(self.provider.plans), 1)
        self.expire(job['id'])
        self.service.run_once()
        self.assertEqual(self.detail(job['id'])['status'], 'succeeded')
        self.assertEqual(len(self.provider.plans), 2)
        self.assertEqual(self.detail(job['id'])['budget']['used_tokens'], 160)

    def test_restart_reuses_saved_tool_output_and_search_slot(self):
        job = self.create()
        claimed = self.service.claim()
        original = knowledge_jobs.step
        def die_after_lookup(runtime, run, key, *args, **kwargs):
            output = original(runtime, run, key, *args, **kwargs)
            if key == 'lookup_1':
                raise ProcessDeath()
            return output
        with patch('knowledge_jobs.step', side_effect=die_after_lookup), self.assertRaises(ProcessDeath):
            self.service.execute(claimed)
        self.expire(job['id'])
        self.service.run_once()
        result = self.detail(job['id'])
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(result['budget']['search_calls'], 2)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM knowledge_retrieval_runs').fetchone()[0], 1)

    def test_unknown_model_outcome_waits_for_explicit_retry(self):
        job = self.create()
        def crash():
            raise ProcessDeath()
        self.provider.on_plan = crash
        with self.assertRaises(ProcessDeath):
            self.service.execute(self.service.claim())
        self.expire(job['id'])
        self.assertIsNone(self.service.claim())
        self.assertEqual(self.detail(job['id'])['status'], 'waiting_user')
        route = '/api/agent/v1/runs/' + job['id'] + '/resume'
        self.assertEqual(self.client.post(route, headers=self.headers['alice'], json={}).status_code, 409)
        self.assertEqual(self.client.post(route, headers=self.headers['alice'], json={'retry_uncertain': True}).status_code, 200)
        self.provider.on_plan = None
        self.service.run_once()
        self.assertEqual(self.detail(job['id'])['status'], 'succeeded')

    def test_revoked_source_blocks_queued_and_completed_results(self):
        job = self.create()
        self.service.run_once()
        with connect(self.db) as db:
            db.execute('DELETE FROM grants WHERE user_id=?', (self.ids['alice'],))
        detail = self.detail(job['id'])
        self.assertEqual(detail['result'], {})
        self.assertEqual(detail['topic'], '资料任务')
        self.assertEqual(self.client.get('/api/agent/v1/runs/' + job['id'] + '/knowledge').status_code, 409)

    def test_cancel_during_planning_fences_output_and_trace(self):
        job = self.create()
        def cancel():
            with connect(self.db) as db:
                self.service.cancel(db, self.ids['alice'], job['id'])
        self.provider.on_plan = cancel
        self.service.run_once()
        self.assertEqual(self.detail(job['id'])['status'], 'cancelled')
        self.assertEqual(len(self.provider.calls), 0)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM knowledge_job_results').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT status FROM knowledge_retrieval_runs').fetchone()[0], 'cancelled')

    def test_expired_worker_cannot_write_trace_after_new_claim(self):
        job = self.create()
        old = self.service.claim()
        self.expire(job['id'])
        fresh = self.service.claim()
        self.assertGreater(fresh['lease_generation'], old['lease_generation'])
        with self.assertRaises(LeaseLost):
            knowledge_jobs.execute_knowledge(self.service, old, json.loads(old['input_json']))
        self.service.execute(fresh)
        self.assertEqual(self.detail(job['id'])['status'], 'succeeded')

    def test_task_list_survives_without_memory_workspace_and_hides_changed_sources(self):
        job = self.create()
        self.app.state.agent_memory.enabled = False
        listing = self.client.get('/agent/tasks')
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertIn('/agent/tasks/' + job['id'], listing.text)
        self.assertNotIn(job['id'], self.clients['bob'].get('/agent/tasks').text)
        with connect(self.db) as db:
            db.execute('DELETE FROM grants WHERE user_id=?', (self.ids['alice'],))
        listing = self.client.get('/agent/tasks')
        self.assertIn('资料或访问权限已变化', listing.text)
        self.assertNotIn('闭包', listing.text)
        self.assertEqual(self.client.get('/agent/tasks?cursor=-1').status_code, 400)


if __name__ == '__main__':
    unittest.main()
