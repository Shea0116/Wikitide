"""Account-selected retrieval through the normal search, ask and history routes."""
import json
import unittest
from dataclasses import replace

from storage import connect, save_note
from explore import sources_valid
from agent_context import ContextManager
from agent_memory import valid_memory_source
from knowledge_scope import initialize_selected
import test_explore


class SelectedKnowledgeTests(unittest.TestCase):
    ask = test_explore.ExploreTests.ask
    tearDown = test_explore.ExploreTests.tearDown

    def setUp(self):
        test_explore.ExploreTests.setUp(self)
        self.app.state.explore_selected_enabled = True
        with connect(self.db) as db:
            self.alice_id = db.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
            self.bob_id = db.execute("SELECT id FROM users WHERE username='bob'").fetchone()[0]
            db.execute('INSERT INTO grants(user_id,note_id,can_view) VALUES(?,?,1)', (self.alice_id, self.secret['id']))

    def selected_ask(self, **kwargs):
        return self.ask(note_id=self.secret['id'], scope='selected', **kwargs)

    def revoke(self):
        with connect(self.db) as db:
            db.execute('DELETE FROM grants WHERE user_id=? AND note_id=?', (self.alice_id, self.secret['id']))

    def test_explicit_selection_has_a_working_page_reader_search_and_cited_answer(self):
        client = self.clients['alice']
        note_id = self.secret['id']
        page = client.get('/explore', params={'note_id': note_id, 'scope': 'selected'})
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn('data-scope="selected"', page.text)
        self.assertIn('所选笔记的相关片段', page.text)
        self.assertIn('scope=selected', client.get(f'/n/{note_id}').text)
        reader = client.get(f'/api/explore/articles/{note_id}', params={'scope': 'selected'})
        self.assertEqual(reader.status_code, 200)
        self.assertIn('PRIVATE-CONTENT', reader.json()['html'])
        results = client.get('/api/explore/search', params={'q': '闭包', 'scope': 'selected', 'note_id': note_id}).json()
        self.assertEqual({row['note_id'] for row in results['results']}, {note_id})
        result = self.selected_ask()
        self.assertEqual(result.status_code, 200, result.text)
        source = result.json()['sources'][0]
        self.assertEqual(source['kind'], 'account_note')
        self.assertEqual(source['schema_version'], 2)
        self.assertEqual(source['user_id'], self.alice_id)
        self.assertIn('PRIVATE-CONTENT', json.dumps(self.provider.calls))
        with connect(self.db) as db:
            self.assertIsNone(db.execute('SELECT 1 FROM explore_chunks WHERE note_id=?', (note_id,)).fetchone())
            self.assertIsNone(db.execute('SELECT 1 FROM explore_selected_documents WHERE user_id=?', (self.bob_id,)).fetchone())
            self.assertFalse(sources_valid(db, [source]))
            self.assertFalse(sources_valid(db, [source], user_id=self.bob_id))
            self.assertTrue(sources_valid(db, [source], user_id=self.alice_id))
            user_message = db.execute('SELECT content FROM explore_messages WHERE id=?', (result.json()['user_message_id'],)).fetchone()[0]
            from agent_memory import text_hash
            self.assertFalse(valid_memory_source(db, {'user_id': self.alice_id, 'source_json': json.dumps({
                'kind': 'message', 'id': result.json()['user_message_id'], 'sha': text_hash(user_message)})}))
        history = client.get('/api/explore/sessions/' + result.json()['session_id']).json()
        self.assertEqual(history['scope'], 'selected')
        self.assertEqual(len(history['messages']), 2)
        feedback = client.post(f"/api/explore/messages/{result.json()['message_id']}/feedback",
                               headers=self.headers['alice'], json={'rating': 'helpful'})
        self.assertEqual(feedback.status_code, 200, feedback.text)

    def test_selection_cannot_expand_the_public_corpus_or_another_accounts_access(self):
        note_id = self.secret['id']
        self.assertEqual(self.ask(note_id=note_id).status_code, 404)
        self.assertEqual(self.clients['bob'].post('/api/explore/ask', headers=self.headers['bob'],
            json={'question': '闭包', 'note_id': note_id, 'scope': 'selected'}).status_code, 404)
        for client in (self.clients['bob'], self.anon):
            for route in ('/explore', '/api/explore/search', f'/api/explore/articles/{note_id}'):
                response = client.get(route, params={'q': '闭包', 'note_id': note_id, 'scope': 'selected'})
                self.assertIn(response.status_code, (401, 404))
                self.assertNotIn('PRIVATE-CONTENT', response.text)
        public = self.clients['alice'].get('/api/explore/search', params={'q': '闭包'})
        self.assertNotIn('PRIVATE-CONTENT', public.text)
        self.assertEqual(self.clients['alice'].get('/api/explore/search', params={'q': '闭包', 'scope': 'selected'}).status_code, 400)
        self.assertEqual(len(self.provider.calls), 0)

    def test_scope_is_bound_to_the_session_and_owner(self):
        result = self.selected_ask().json()
        sid = result['session_id']
        self.assertEqual(self.ask(session_id=sid, note_id=self.secret['id']).status_code, 404)
        self.assertEqual(self.ask(session_id=sid, note_id=self.react['id']).status_code, 409)
        self.assertEqual(self.clients['bob'].get('/api/explore/sessions/' + sid).status_code, 404)
        followup = self.selected_ask(question='举个例子', session_id=sid)
        self.assertEqual(followup.status_code, 200, followup.text)
        self.assertTrue(any(message['role'] == 'assistant' for message in self.provider.calls[-1][0]))

    def test_revocation_hides_history_and_blocks_reads_without_model_calls(self):
        sid = self.selected_ask().json()['session_id']
        calls = len(self.provider.calls)
        self.revoke()
        self.assertEqual(self.clients['alice'].get('/api/explore/sessions/' + sid).status_code, 404)
        self.assertNotIn(sid, self.clients['alice'].get('/api/explore/sessions').text)
        self.assertEqual(self.selected_ask(session_id=sid).status_code, 404)
        self.assertEqual(len(self.provider.calls), calls)

    def test_edit_invalidates_old_answers_and_incrementally_rebuilds_only_selected_index(self):
        result = self.selected_ask().json()
        sid = result['session_id']
        with connect(self.db) as db:
            note = db.execute('SELECT * FROM notes WHERE id=?', (self.secret['id'],)).fetchone()
            save_note(db, note, '# 更新\n\n新的词法作用域资料 FRESH-SELECTED-CONTENT', 'web')
        history = self.clients['alice'].get('/api/explore/sessions/' + sid).json()
        self.assertEqual(history['messages'][1]['sources'], [])
        self.assertNotIn('函数式更新', history['messages'][1]['content'])
        response = self.selected_ask(question='新的词法作用域资料', session_id=sid)
        self.assertEqual(response.status_code, 200, response.text)
        sent = json.dumps(self.provider.calls[-1][0])
        self.assertIn('FRESH-SELECTED-CONTENT', sent)
        self.assertNotIn('PRIVATE-CONTENT', sent)
        with connect(self.db) as db:
            chunks = db.execute('SELECT text FROM explore_selected_chunks WHERE user_id=?', (self.alice_id,)).fetchall()
            self.assertNotIn('PRIVATE-CONTENT', str([tuple(row) for row in chunks]))

    def test_revocation_during_generation_never_commits_an_answer(self):
        self.provider.callback = self.revoke
        response = self.selected_ask()
        self.assertEqual(response.status_code, 404, response.text)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM explore_messages').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM explore_sessions').fetchone()[0], 0)

    def test_streaming_checks_access_before_every_delta(self):
        def stream(messages, thinking=False):
            yield '有效的第一段 [1]'
            self.revoke()
            yield 'REVOKED-DELTA-MUST-NOT-LEAK'
        self.provider.stream_answer = stream
        response = self.clients['alice'].post('/api/explore/ask/stream', headers=self.headers['alice'],
            json={'question': '闭包', 'note_id': self.secret['id'], 'scope': 'selected'})
        self.assertIn('有效的第一段', response.text)
        self.assertIn('event: error', response.text)
        self.assertNotIn('REVOKED-DELTA', response.text)
        self.assertNotIn('event: done', response.text)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM explore_messages').fetchone()[0], 0)

    def test_feature_gate_network_scope_and_csrf_are_enforced(self):
        self.assertEqual(self.selected_ask(web_search=True).status_code, 400)
        self.assertEqual(self.clients['alice'].post('/api/explore/ask',
            json={'question': '闭包', 'note_id': self.secret['id'], 'scope': 'selected'}).status_code, 403)
        self.app.state.explore_selected_enabled = False
        self.assertEqual(self.selected_ask().status_code, 404)
        self.assertNotIn('scope=selected', self.clients['alice'].get(f"/n/{self.secret['id']}").text)
        self.assertEqual(len(self.provider.calls), 0)

    def test_account_context_preserves_selected_sources_and_invalidates_on_change(self):
        context_service = self.app.state.agent_context
        context_service.policy = replace(context_service.policy, enabled=True)
        context_service.manager = ContextManager(context_service.policy)
        result = self.selected_ask().json()
        self.assertTrue(result['context']['enabled'])
        followup = self.selected_ask(question='再解释一下', session_id=result['session_id'])
        self.assertEqual(followup.status_code, 200, followup.text)
        self.assertTrue(any(message['role'] == 'assistant' for message in self.provider.calls[-1][0]))
        with connect(self.db) as db:
            initialize_selected(db)
            self.assertEqual(db.execute('SELECT scope FROM explore_sessions').fetchone()[0], 'selected')
            context = db.execute('SELECT sources_json FROM agent_answer_context ORDER BY message_id DESC').fetchone()[0]
            self.assertIn('account_note', context)
        self.revoke()
        with connect(self.db) as db:
            refs = json.loads(context)
            self.assertFalse(sources_valid(db, refs, user_id=self.alice_id))

    def test_shared_note_indexes_and_sources_stay_bound_to_each_account(self):
        first = self.selected_ask().json()
        with connect(self.db) as db:
            db.execute('INSERT INTO grants(user_id,note_id,can_view) VALUES(?,?,1)', (self.bob_id, self.secret['id']))
        second = self.clients['bob'].post('/api/explore/ask', headers=self.headers['bob'],
            json={'question': '闭包', 'note_id': self.secret['id'], 'scope': 'selected'}).json()
        self.assertEqual(second['sources'][0]['user_id'], self.bob_id)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM explore_selected_documents WHERE note_id=?', (self.secret['id'],)).fetchone()[0], 2)
            self.assertFalse(sources_valid(db, first['sources'], user_id=self.bob_id))
        self.revoke()
        self.assertEqual(self.clients['bob'].get('/api/explore/sessions/' + second['session_id']).status_code, 200)


if __name__ == '__main__':
    unittest.main()
