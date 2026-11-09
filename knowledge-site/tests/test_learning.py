"""Account-bound practice checkpoints and durable, revisable reference feedback."""
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from features import add_note
from storage import connect, save_note
import test_agent_runtime
from test_knowledge_jobs import ProcessDeath


class Assessor(test_agent_runtime.Provider):
    model = 'synthetic-assessor'

    def assess_learning(self, messages, *, usage):
        self.calls.append(messages)
        if self.callback:
            self.callback()
        usage.update(prompt_tokens=30, completion_tokens=20)
        return json.dumps({'verdict': 'partial', 'answer_quote': json.loads(messages[-1]['content'])['answer'][:120], 'reference_status': 'usable', 'feedback': '可补充外层函数返回后的访问。',
                           'matched_points': ['词法作用域'], 'missing_points': ['外层函数返回后的访问']})


class LearningTests(unittest.TestCase):
    tearDown = test_agent_runtime.RuntimeTests.tearDown

    def setUp(self):
        test_agent_runtime.RuntimeTests.setUp(self)
        self.app.state.learning_enabled = True
        self.provider = Assessor()
        self.app.state.explore_provider = self.provider
        with connect(self.db) as db:
            self.private = add_note(db, 'Developer/practice-private.md', '# 闭包\n\n闭包保留词法作用域。', category='私密', visibility='private')
            db.execute('INSERT INTO grants(user_id,note_id,can_view) VALUES(?,?,1)', (self.ids['alice'], self.private['id']))
            self.card = db.execute('INSERT INTO review_cards(user_id,note_id,question,answer,anchor,body_sha,due_at) VALUES(?,?,?,?,?,?,0)',
                                   (self.ids['alice'], self.private['id'], '闭包如何访问变量？', 'SECRET-REFERENCE 词法作用域与外层变量。', '', self.private['body_sha'])).lastrowid

    def start(self, key='practice-test-key'):
        response = self.client.post(f'/learn/cards/{self.card}/practice', data={'csrf': self.headers['alice']['X-CSRF-Token'], 'practice_key': key}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        return response.headers['location'].rsplit('/', 1)[1]

    def url(self, sid):
        return '/api/learn/sessions/' + sid

    def get(self, sid):
        response = self.client.get(self.url(sid))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def submit(self, sid, **changes):
        body = {'expected_version': self.get(sid)['version'], 'answer': '闭包保留词法作用域。', 'hint_used': False}
        body.update(changes)
        response = self.client.post(self.url(sid) + '/attempts', headers=self.headers['alice'], json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def mutate_card(self):
        with connect(self.db) as db:
            db.execute("UPDATE review_cards SET answer='新的参考答案',version=version+1 WHERE id=?", (self.card,))

    def test_entry_checkpoint_alias_and_no_reference_leak(self):
        self.assertIn('独立作答 / 继续练习', self.client.get('/learn').text)
        sid = self.start()
        self.assertEqual(self.start(), sid)
        self.assertEqual(self.start('other-device-key'), sid)
        self.assertNotIn('SECRET-REFERENCE', json.dumps(self.get(sid)))
        self.assertNotIn('SECRET-REFERENCE', self.client.get('/learn/practice/' + sid).text)
        self.app.state.learning_enabled = False
        self.assertEqual(self.client.get(self.url(sid)).status_code, 404)
        self.assertNotIn('独立作答 / 继续练习', self.client.get('/learn').text)

    def test_owner_and_csrf_on_all_mutations(self):
        sid = self.start()
        self.assertEqual(self.clients['bob'].get(self.url(sid)).status_code, 404)
        with TestClient(self.app) as anon:
            self.assertEqual(anon.get(self.url(sid)).status_code, 401)
        body = {'expected_version': 1, 'answer': 'draft', 'hint_used': False}
        for suffix, method, payload in [('', 'patch', body), ('/attempts', 'post', body), ('/corrections', 'post', {'expected_version': 1, 'verdict': 'correct'})]:
            self.assertEqual(getattr(self.client, method)(self.url(sid) + suffix, json=payload).status_code, 403)
            self.assertEqual(getattr(self.clients['bob'], method)(self.url(sid) + suffix, headers=self.headers['bob'], json=payload).status_code, 404)
        self.assertEqual(self.clients['bob'].post(f'/learn/cards/{self.card}/practice', data={'csrf': self.headers['bob']['X-CSRF-Token'], 'practice_key': 'other-owner-key'}).status_code, 404)

    def test_optimistic_draft_conflict_and_cross_device_resume(self):
        sid = self.start()
        body = {'expected_version': 1, 'answer': '未完成的解释', 'hint_used': True}
        saved = self.client.patch(self.url(sid), headers=self.headers['alice'], json=body)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()['version'], 2)
        body['answer'] = '过期设备覆盖'
        self.assertEqual(self.client.patch(self.url(sid), headers=self.headers['alice'], json=body).status_code, 409)
        self.assertEqual(self.get(self.start('another-device-key'))['answer_draft'], '未完成的解释')
        self.assertTrue(self.get(sid)['hint_used'])

    def test_submit_idempotency_and_immutable_answer(self):
        sid = self.start()
        first = self.submit(sid)
        second = self.submit(sid, expected_version=1)
        self.assertEqual(first['attempt'], second['attempt'])
        self.assertEqual(first['job'], second['job'])
        self.assertIn('SECRET-REFERENCE', first['reference_answer'])
        body = {'expected_version': first['version'], 'answer': 'changed', 'hint_used': False}
        self.assertEqual(self.client.post(self.url(sid) + '/attempts', headers=self.headers['alice'], json=body).status_code, 409)
        self.assertEqual(self.client.patch(self.url(sid), headers=self.headers['alice'], json=body).status_code, 409)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM learning_attempts').fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM agent_runs WHERE kind='assessment'").fetchone()[0], 1)

    def test_worker_feedback_and_user_correction_preserve_originals(self):
        sid = self.start()
        original = self.submit(sid)
        self.service.run_once()
        reviewed = self.get(sid)
        self.assertEqual(reviewed['status'], 'reviewed', reviewed)
        self.assertEqual(reviewed['job']['status'], 'succeeded')
        self.assertEqual(reviewed['attempt']['answer'], original['attempt']['answer'])
        feedback = reviewed['attempt']['feedback']
        self.assertEqual(feedback['verdict'], 'partial')
        correction = {'expected_version': reviewed['version'], 'verdict': 'correct', 'comment': '已核对原文。'}
        response = self.client.post(self.url(sid) + '/corrections', headers=self.headers['alice'], json=correction)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['attempt']['feedback'], feedback)
        self.assertEqual(response.json()['correction']['verdict'], 'correct')
        self.assertEqual(self.client.post(self.url(sid) + '/corrections', headers=self.headers['alice'], json=correction).status_code, 409)
        next_sid = self.start('next-' + sid)
        self.assertNotEqual(sid, next_sid)
        self.assertEqual(self.start('next-' + sid), next_sid)
        with connect(self.db) as db:
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_scheduling_changes_resume_but_card_edits_invalidate(self):
        sid = self.start()
        with connect(self.db) as db:
            db.execute('UPDATE review_cards SET reviews=reviews+1,version=version+1 WHERE id=?', (self.card,))
        self.assertEqual(self.start('new-schedule-key'), sid)
        self.assertEqual(self.get(sid)['question'], '闭包如何访问变量？')
        self.mutate_card()
        self.assertEqual(self.client.get(self.url(sid)).status_code, 409)
        self.assertNotEqual(self.start('edited-card-key'), sid)

    def test_source_update_blocks_draft_and_queued_feedback(self):
        sid = self.start()
        submitted = self.submit(sid)
        with connect(self.db) as db:
            note = db.execute('SELECT * FROM notes WHERE id=?', (self.private['id'],)).fetchone()
            save_note(db, note, '# 原文已更新\n\n内容更新。', 'local')
        self.service.run_once()
        self.assertEqual(self.client.get(self.url(sid)).status_code, 409)
        self.assertEqual(self.client.patch(self.url(sid), headers=self.headers['alice'], json={'expected_version': 2, 'answer': 'x'}).status_code, 409)
        job = self.client.get('/api/agent/v1/runs/' + submitted['job']['id']).json()
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['result'], {})
        self.assertEqual(self.provider.calls, [])

    def test_revocation_during_model_call_blocks_feedback_commit(self):
        sid = self.start()
        self.submit(sid)
        def revoke():
            with connect(self.db) as db:
                db.execute('DELETE FROM grants WHERE user_id=?', (self.ids['alice'],))
        self.provider.callback = revoke
        self.service.run_once()
        self.assertEqual(self.client.get(self.url(sid)).status_code, 404)
        with connect(self.db) as db:
            self.assertIsNone(db.execute('SELECT feedback_json FROM learning_attempts').fetchone()[0])
            self.assertEqual(db.execute('SELECT status FROM agent_runs').fetchone()[0], 'failed')

    def test_card_edit_during_model_call_blocks_feedback_commit(self):
        sid = self.start()
        self.submit(sid)
        self.provider.callback = self.mutate_card
        self.service.run_once()
        with connect(self.db) as db:
            self.assertIsNone(db.execute('SELECT feedback_json FROM learning_attempts').fetchone()[0])
            self.assertEqual(db.execute('SELECT status FROM agent_runs').fetchone()[0], 'failed')

    def test_cancelled_feedback_preserves_attempt_and_allows_new_practice(self):
        sid = self.start()
        job = self.submit(sid)['job']
        def cancel():
            self.client.post('/api/agent/v1/runs/' + job['id'] + '/cancel', headers=self.headers['alice'])
        self.provider.callback = cancel
        self.service.run_once()
        saved = self.get(sid)
        self.assertEqual(saved['job']['status'], 'cancelled')
        self.assertTrue(saved['attempt']['answer'])
        self.assertIsNone(saved['attempt']['feedback'])
        self.assertNotEqual(self.start('retry-cancelled-key'), sid)

    def test_unknown_provider_requires_confirmation_then_resumes(self):
        sid = self.start()
        job = self.submit(sid)['job']
        def crash():
            raise ProcessDeath()
        self.provider.callback = crash
        with self.assertRaises(ProcessDeath):
            self.service.execute(self.service.claim())
        with connect(self.db) as db:
            db.execute('UPDATE agent_runs SET lease_until=0 WHERE id=?', (job['id'],))
        self.assertIsNone(self.service.claim())
        self.assertEqual(self.get(sid)['job']['status'], 'waiting_user')
        self.provider.callback = None
        url = '/api/agent/v1/runs/' + job['id'] + '/resume'
        self.assertEqual(self.client.post(url, headers=self.headers['alice'], json={}).status_code, 409)
        self.assertEqual(self.client.post(url, headers=self.headers['alice'], json={'retry_uncertain': True}).status_code, 200)
        self.service.run_once()
        self.assertEqual(self.get(sid)['status'], 'reviewed')

    def test_cached_assessment_survives_restart_without_second_model_call(self):
        sid = self.start()
        job = self.submit(sid)['job']
        original = self.service.call_model
        def crash_after_save(*args, **kwargs):
            original(*args, **kwargs)
            raise ProcessDeath()
        with patch.object(self.service, 'call_model', side_effect=crash_after_save), self.assertRaises(ProcessDeath):
            self.service.execute(self.service.claim())
        with connect(self.db) as db:
            db.execute('UPDATE agent_runs SET lease_until=0 WHERE id=?', (job['id'],))
        self.service.run_once()
        self.assertEqual(self.get(sid)['status'], 'reviewed')
        self.assertEqual(len(self.provider.calls), 1)

    def test_invalid_feedback_is_rejected_and_attempt_kept(self):
        sid = self.start()
        self.submit(sid)
        self.provider.assess_learning = lambda *args, **kwargs: '{"verdict":"perfect"}'
        self.service.run_once()
        saved = self.get(sid)
        self.assertEqual(saved['job']['status'], 'failed')
        self.assertIsNone(saved['attempt']['feedback'])

    def test_fabricated_quote_cannot_be_committed_as_feedback(self):
        sid = self.start()
        original = self.submit(sid)
        self.provider.assess_learning = lambda *args, **kwargs: json.dumps({
            'verdict': 'correct', 'answer_quote': '学生没有写过的正确答案', 'reference_status': 'usable',
            'feedback': '你已经正确回答。', 'matched_points': [], 'missing_points': []})
        self.service.run_once()
        saved = self.get(sid)
        self.assertEqual(saved['job']['status'], 'failed')
        self.assertEqual(saved['job']['error_code'], 'validation_failed')
        self.assertEqual(saved['attempt']['answer'], original['attempt']['answer'])
        self.assertIsNone(saved['attempt']['feedback'])


if __name__ == '__main__':
    unittest.main()
