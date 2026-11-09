"""Learning projects restore scope, one Q&A thread, practice, drafts, and the last failure."""
import json
import unittest

from features import add_note
from storage import connect, save_note
import test_knowledge_jobs as jobs


class LearningProjectTests(unittest.TestCase):
    tearDown = jobs.KnowledgeJobTests.tearDown
    detail = jobs.KnowledgeJobTests.detail

    def setUp(self):
        jobs.KnowledgeJobTests.setUp(self)
        self.app.state.learning_enabled = True
        self.app.state.artifacts_enabled = True
        with connect(self.db) as db:
            db.execute("UPDATE users SET role='editor' WHERE id=?", (self.ids['alice'],))
            db.execute('UPDATE grants SET can_edit=1 WHERE user_id=?', (self.ids['alice'],))
            self.other = add_note(db, 'Developer/project-other.md', '# 其他\n\n另一篇闭包笔记。', category='私密', visibility='private')
            self.hidden = add_note(db, 'Developer/project-hidden.md', '# 隐藏\n\nHIDDEN-PROJECT-NOTE', category='私密', visibility='private')
            db.execute('INSERT INTO grants(user_id,note_id,can_view,can_edit) VALUES(?,?,1,1)', (self.ids['alice'], self.other['id']))
            self.card = db.execute('''INSERT INTO review_cards(user_id,note_id,question,answer,anchor,body_sha,due_at)
                VALUES(?,?,?,?,?,?,0)''', (self.ids['alice'], self.private['id'], '闭包如何访问变量？',
                                           'SECRET-REFERENCE 词法作用域与外层变量。', '', self.private['body_sha'])).lastrowid

    def post(self, route, body=None):
        return self.client.post('/api/agent/v1/' + route, headers=self.headers['alice'], json=body or {})

    def project(self, note_ids=None, **extra):
        body = {'title': '闭包学习', 'goal': '弄清词法作用域', 'kind': 'learning'}
        if note_ids is not None:
            body['note_ids'] = note_ids
        body.update(extra)
        response = self.post('projects', body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def opened(self, pid):
        response = self.post('projects/' + pid + '/open')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def ask(self, pid, session_id, note_ids):
        response = self.client.post('/api/explore/ask', headers=self.headers['alice'], json={
            'question': '闭包保留什么？', 'session_id': session_id, 'project_id': pid,
            'scope': 'selected', 'note_ids': note_ids, 'memory_mode': 'off'})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def research(self, pid, key='project-research-key', **extra):
        body = {'topic': '闭包的词法作用域', 'requirements': '保留原文依据', 'idempotency_key': key}
        body.update(extra)
        response = self.post('projects/' + pid + '/research', body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_create_reopen_scope_and_single_thread(self):
        self.assertEqual(self.post('projects', {'title': '普通项目', 'goal': '', 'note_ids': [self.private['id']]}).status_code, 400)
        self.assertEqual(self.post('projects', {'title': '过多', 'kind': 'learning', 'note_ids': list(range(1, 14))}).status_code, 400)
        self.assertEqual(self.post('projects', {'title': '不可见', 'kind': 'learning', 'note_ids': [self.hidden['id']]}).status_code, 404)
        created = self.project([])
        self.assertIsNone(created['learning']['thread'])
        self.assertEqual(created['learning']['scope'], [])
        bound = self.post('projects/' + created['id'] + '/scope', {'note_ids': [self.private['id'], self.other['id']]})
        self.assertEqual(bound.status_code, 200, bound.text)
        self.assertEqual([note['note_id'] for note in bound.json()['learning']['scope']], sorted([self.private['id'], self.other['id']]))
        self.assertEqual(self.clients['bob'].get('/api/agent/v1/projects/' + created['id']).status_code, 404)
        first = self.opened(created['id'])
        self.assertEqual(self.opened(created['id'])['session_id'], first['session_id'])
        self.assertEqual(self.post('projects/' + created['id'] + '/continue').json()['session_id'], first['session_id'])
        answer = self.ask(created['id'], first['session_id'], [self.private['id'], self.other['id']])
        self.assertTrue(answer['sources'])
        again = self.client.get('/api/agent/v1/projects/' + created['id']).json()
        self.assertEqual(again['learning']['thread']['session_id'], first['session_id'])
        self.assertEqual(again['learning']['thread']['message_count'], 2)
        self.assertTrue(again['learning']['thread']['citations'])
        self.assertNotIn('SECRET-REFERENCE', json.dumps(again, ensure_ascii=False))
        self.assertEqual(self.client.post('/api/explore/ask', headers=self.headers['alice'], json={
            'question': '再问一次', 'project_id': created['id'], 'scope': 'selected',
            'note_ids': [self.private['id'], self.other['id']], 'memory_mode': 'off'}).status_code, 409)
        self.assertEqual(self.post('projects/' + created['id'] + '/scope', {'note_ids': [self.private['id']]}).status_code, 409)
        page = self.client.get('/learn/projects/' + created['id'])
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn('闭包学习', page.text)
        self.assertIn('继续这条问答', page.text)
        self.assertIn(first['session_id'], page.text)
        english = self.client.get('/learn/projects', headers={'Accept-Language': 'en'})
        self.assertIn('Learning project', english.text)
        self.assertIn('Personal knowledge loop', english.text)
        moved = self.client.get('/agent/projects/' + created['id'], follow_redirects=False)
        self.assertEqual(moved.status_code, 303)
        self.assertEqual(moved.headers['location'], '/learn/projects/' + created['id'])

    def test_research_failure_stays_available_for_retry(self):
        created = self.project([self.private['id']])
        self.assertEqual(self.client.post('/api/agent/v1/runs', headers=self.headers['alice'], json={
            'kind': 'knowledge', 'topic': '闭包', 'scope': 'selected', 'note_ids': [self.other['id']],
            'project_id': created['id'], 'memory_mode': 'off', 'max_tokens': 48000,
            'idempotency_key': 'wrong-scope-key'}).status_code, 409)
        queued = self.research(created['id'])
        self.assertEqual(self.research(created['id'])['id'], queued['id'])
        waiting = self.client.get('/api/agent/v1/projects/' + created['id']).json()['learning']
        self.assertEqual(waiting['active_jobs'][0]['id'], queued['id'])
        self.assertEqual(waiting['active_jobs'][0]['status'], 'queued')
        original = type(self.provider).answer
        self.provider.answer = lambda *args, **kwargs: '没有引用'
        self.assertTrue(self.service.run_once())
        failed = self.client.get('/api/agent/v1/projects/' + created['id']).json()['learning']
        self.assertEqual(failed['last_failure']['id'], queued['id'])
        self.assertEqual(failed['last_failure']['status'], 'failed')
        self.assertEqual(failed['last_failure']['error_code'], 'missing_citations')
        self.assertIsNone(failed['latest_research'])
        page = self.client.get('/learn/projects/' + created['id'])
        self.assertIn('data-failure="missing_citations"', page.text)
        self.assertIn('用当前资料重试', page.text)
        retried = self.post('projects/' + created['id'] + '/runs/' + queued['id'] + '/retry', {'idempotency_key': 'project-retry-key'})
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(retried.json()['failed_run_id'], queued['id'])
        self.assertEqual(retried.json()['failed_error'], 'missing_citations')
        self.assertNotEqual(retried.json()['run']['id'], queued['id'])
        with connect(self.db) as db:
            kept = db.execute('SELECT status,error_code FROM agent_runs WHERE id=?', (queued['id'],)).fetchone()
            self.assertEqual(tuple(kept), ('failed', 'missing_citations'))
        self.provider.answer = lambda *args, **kwargs: original(self.provider, *args, **kwargs)
        self.assertTrue(self.service.run_once())
        restored = self.client.get('/api/agent/v1/projects/' + created['id']).json()['learning']
        self.assertEqual(restored['last_failure']['id'], queued['id'])
        self.assertEqual(restored['latest_research']['run_id'], retried.json()['run']['id'])
        self.assertTrue(restored['latest_research']['citations'])
        self.assertIn('/n/' + str(self.private['id']), json.dumps(restored['latest_research']))

    def test_source_change_hides_citations_and_keeps_the_run(self):
        created = self.project([self.private['id']])
        job = self.research(created['id'], key='project-source-key')
        self.assertTrue(self.service.run_once())
        self.assertEqual(self.detail(job['id'])['status'], 'succeeded')
        with connect(self.db) as db:
            note = db.execute('SELECT * FROM notes WHERE id=?', (self.private['id'],)).fetchone()
            save_note(db, note, '# 原文已更新\n\n不再是原来的闭包说明。', 'local')
        state = self.client.get('/api/agent/v1/projects/' + created['id']).json()['learning']
        self.assertTrue(state['latest_research']['source_changed'])
        self.assertEqual(state['latest_research']['citations'], [])
        self.assertNotIn('PRIVATE-WORKER-SOURCE', json.dumps(state, ensure_ascii=False))
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT status FROM agent_runs WHERE id=?', (job['id'],)).fetchone()[0], 'succeeded')

    def test_practice_draft_and_failed_feedback_reopen(self):
        created = self.project([self.private['id']])
        response = self.client.post(f'/learn/cards/{self.card}/practice', data={
            'csrf': self.headers['alice']['X-CSRF-Token'], 'practice_key': 'project-practice-key', 'project_id': created['id']}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        sid = response.headers['location'].rsplit('/', 1)[1]
        saved = self.client.patch('/api/learn/sessions/' + sid, headers=self.headers['alice'],
                                  json={'expected_version': 1, 'answer': '未完成的闭包解释', 'hint_used': False})
        self.assertEqual(saved.status_code, 200, saved.text)
        state = self.client.get('/api/agent/v1/projects/' + created['id']).json()['learning']
        self.assertEqual(state['practice'][0]['id'], sid)
        self.assertEqual(state['practice'][0]['answer_draft'], '未完成的闭包解释')
        self.assertEqual(state['practice'][0]['status'], 'answering')
        self.assertNotIn('SECRET-REFERENCE', json.dumps(state, ensure_ascii=False))
        self.provider.assess_learning = lambda *args, **kwargs: '{"verdict":"perfect"}'
        submitted = self.client.post('/api/learn/sessions/' + sid + '/attempts', headers=self.headers['alice'],
                                     json={'expected_version': saved.json()['version'], 'answer': '未完成的闭包解释', 'hint_used': False})
        self.assertEqual(submitted.status_code, 200, submitted.text)
        self.assertEqual(self.detail(submitted.json()['job']['id'])['project_id'], created['id'])
        self.assertTrue(self.service.run_once())
        failed = self.client.get('/api/agent/v1/projects/' + created['id']).json()['learning']
        practice = next(item for item in failed['practice'] if item['id'] == sid)
        self.assertEqual(practice['job']['status'], 'failed')
        self.assertEqual(practice['job']['error_code'], 'validation_failed')
        self.assertEqual(practice['attempt']['answer'], '未完成的闭包解释')
        self.assertIsNone(practice['attempt']['feedback'])
        page = self.client.get('/learn/practice/' + sid)
        self.assertIn('/learn/projects/' + created['id'], page.text)

    def test_research_draft_and_proposal_stay_on_the_project(self):
        created = self.project([self.private['id']])
        job = self.research(created['id'], key='project-draft-key')
        self.assertTrue(self.service.run_once())
        self.assertEqual(self.detail(job['id'])['status'], 'succeeded')
        transferred = self.client.post('/api/agent/v1/runs/' + job['id'] + '/draft', headers=self.headers['alice'])
        self.assertEqual(transferred.status_code, 200, transferred.text)
        did = transferred.json()['draft_id']
        targets = self.client.get('/api/explore/drafts/' + did + '/targets')
        self.assertEqual(targets.status_code, 200, targets.text)
        target = next(item for item in targets.json()['targets'] if item['id'] == self.private['id'])
        proposal = self.client.post('/api/explore/drafts/' + did + '/proposals', headers=self.headers['alice'], json={
            'draft_version': 0, 'note_id': self.private['id'], 'target_version': target['version'], 'mode': 'append'})
        self.assertEqual(proposal.status_code, 200, proposal.text)
        state = self.client.get('/api/agent/v1/projects/' + created['id']).json()['learning']
        self.assertEqual(state['drafts'][0]['id'], did)
        self.assertEqual(state['proposals'][0]['id'], proposal.json()['id'])
        self.assertEqual(state['proposals'][0]['status'], 'review')
        page = self.client.get('/learn/projects/' + created['id'])
        self.assertIn('/explore/write?draft_id=' + did, page.text)
        self.assertIn('打开修改建议', page.text)
        with connect(self.db) as db:
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])


if __name__ == '__main__':
    unittest.main()
