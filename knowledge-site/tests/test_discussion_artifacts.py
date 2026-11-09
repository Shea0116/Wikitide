"""Answer copies keep conversation, memory and selected-source provenance."""
import json
import unittest
from dataclasses import replace

from agent_context import ContextManager
from agent_models import MemoryInput
from features import add_note
from storage import connect, save_note
import test_selected_knowledge as selected_fixture


class DiscussionArtifactTests(unittest.TestCase):
    tearDown = selected_fixture.SelectedKnowledgeTests.tearDown
    selected_ask = selected_fixture.SelectedKnowledgeTests.selected_ask
    ask = selected_fixture.SelectedKnowledgeTests.ask
    revoke = selected_fixture.SelectedKnowledgeTests.revoke

    def setUp(self):
        selected_fixture.SelectedKnowledgeTests.setUp(self)
        self.app.state.artifacts_enabled = True
        self.client = self.clients['alice']

    def save(self, mid):
        response = self.client.post(f'/api/explore/messages/{mid}/draft', headers=self.headers['alice'])
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['draft_id']

    def ready(self):
        answer = self.selected_ask().json()
        return answer, self.save(answer['message_id'])

    def draft_url(self, did):
        return '/api/explore/drafts/' + did

    def test_answer_copy_is_idempotent_and_keeps_fixed_private_sources(self):
        answer, did = self.ready()
        calls = len(self.provider.calls)
        self.assertEqual(self.save(answer['message_id']), did)
        draft = self.client.get(self.draft_url(did)).json()
        self.assertTrue(draft['discussion_origin'])
        self.assertTrue(draft['private_only'])
        self.assertEqual(draft['memory_mode'], 'off')
        self.assertEqual(draft['sources'], answer['sources'])
        self.assertEqual(len(self.provider.calls), calls)
        self.assertEqual(self.client.get(self.draft_url(did) + '/export').status_code, 200)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM explore_drafts').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_owner_csrf_roles_and_feature_gate(self):
        answer = self.selected_ask().json()
        url = f"/api/explore/messages/{answer['message_id']}/draft"
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertEqual(self.clients['bob'].post(url, headers=self.headers['bob']).status_code, 404)
        self.assertEqual(self.client.post(f"/api/explore/messages/{answer['user_message_id']}/draft", headers=self.headers['alice']).status_code, 404)
        self.app.state.artifacts_enabled = False
        self.assertEqual(self.client.post(url, headers=self.headers['alice']).status_code, 404)

    def test_source_update_and_revocation_hide_copies_and_list_titles(self):
        answer, did = self.ready()
        self.revoke()
        self.assertEqual(self.client.get(self.draft_url(did)).status_code, 409)
        self.assertEqual(self.client.get(self.draft_url(did) + '/export').status_code, 409)
        self.assertEqual(self.client.post(f"/api/explore/messages/{answer['message_id']}/draft", headers=self.headers['alice']).status_code, 409)
        listing = self.client.get('/api/explore/drafts').json()['drafts'][0]
        self.assertTrue(listing['stale'])
        self.assertEqual(listing['title'], '来源已失效的讨论草稿')
        self.assertEqual(listing['topic'], '')

    def test_deleted_conversation_cannot_leave_a_readable_or_resubmittable_copy(self):
        answer, did = self.ready()
        response = self.client.delete('/api/explore/sessions/' + answer['session_id'], headers=self.headers['alice'])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(self.draft_url(did)).status_code, 404)
        self.assertEqual(self.client.get(self.draft_url(did) + '/export').status_code, 404)

    def test_later_turns_do_not_change_a_saved_answer_prefix(self):
        answer, did = self.ready()
        followup = self.selected_ask(session_id=answer['session_id'], question='举一个使用场景。')
        self.assertEqual(followup.status_code, 200, followup.text)
        self.assertEqual(self.client.get(self.draft_url(did)).status_code, 200)
        other = self.save(followup.json()['message_id'])
        self.assertNotEqual(other, did)

    def test_prefix_edits_or_explicit_forgetting_invalidate_the_copy(self):
        answer, did = self.ready()
        with connect(self.db) as db:
            db.execute("UPDATE explore_messages SET content='更改后的问题' WHERE id=?", (answer['user_message_id'],))
        self.assertEqual(self.client.get(self.draft_url(did)).status_code, 409)

    def test_deleted_draft_is_not_recreated(self):
        answer, did = self.ready()
        self.assertEqual(self.client.delete(self.draft_url(did), headers=self.headers['alice']).status_code, 200)
        response = self.client.post(f"/api/explore/messages/{answer['message_id']}/draft", headers=self.headers['alice'])
        self.assertEqual(response.status_code, 409)

    def test_no_citation_or_unsafe_answer_cannot_create_a_draft(self):
        answer = self.selected_ask().json()
        url = f"/api/explore/messages/{answer['message_id']}/draft"
        for text in ('没有引用', '<script>alert(1)</script>\n\n结论 [1]'):
            with connect(self.db) as db:
                db.execute('UPDATE explore_messages SET content=? WHERE id=?', (text, answer['message_id']))
            self.assertEqual(self.client.post(url, headers=self.headers['alice']).status_code, 422)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM explore_drafts').fetchone()[0], 0)

    def test_context_enabled_answers_keep_uncited_selected_provenance(self):
        context = self.app.state.agent_context
        context.policy = replace(context.policy, enabled=True)
        context.manager = ContextManager(context.policy)
        with connect(self.db) as db:
            extra = add_note(db, 'Developer/unrelated-selected.md', '# 额外资料\n\n独立的资料内容。', visibility='private')
            db.execute('INSERT INTO grants(user_id,note_id,can_view) VALUES(?,?,1)', (self.alice_id, extra['id']))
        response = self.ask(scope='selected', note_ids=[self.secret['id'], extra['id']])
        self.assertEqual(response.status_code, 200, response.text)
        answer = response.json(); did = self.save(answer['message_id'])
        with connect(self.db) as db:
            refs = json.loads(db.execute('SELECT sources_json FROM agent_answer_context WHERE message_id=?', (answer['message_id'],)).fetchone()[0])
            self.assertIn(extra['id'], {ref.get('note_id') for ref in refs})
            db.execute('DELETE FROM grants WHERE user_id=? AND note_id=?', (self.alice_id, extra['id']))
        self.assertEqual(self.client.get(self.draft_url(did)).status_code, 409)

    def test_incomplete_legacy_selected_provenance_is_not_backfilled_with_current_versions(self):
        answer = self.selected_ask().json()
        with connect(self.db) as db:
            extra = add_note(db, 'Developer/legacy-unrecorded.md', '# 旧资料\n\n未记录版本。', visibility='private')
            db.execute('INSERT INTO grants(user_id,note_id,can_view) VALUES(?,?,1)', (self.alice_id, extra['id']))
            db.execute('INSERT INTO explore_session_notes VALUES(?,?)', (answer['session_id'], extra['id']))
        self.assertEqual(self.client.post(f"/api/explore/messages/{answer['message_id']}/draft", headers=self.headers['alice']).status_code, 409)

    def test_memory_provenance_and_forgetting_reach_the_discussion_draft(self):
        self.app.state.agent_memory.enabled = True
        with connect(self.db) as db:
            memory = self.app.state.agent_memory.create(db, self.alice_id, MemoryInput(content='解释闭包时先给一个例子。'))
        answer = self.ask(memory_mode='account').json()
        did = self.save(answer['message_id'])
        self.assertTrue(self.client.get(self.draft_url(did)).json()['private_only'])
        with connect(self.db) as db:
            refs = json.loads(db.execute('SELECT refs_json FROM discussion_draft_origins WHERE draft_id=?', (did,)).fetchone()[0])
            self.assertTrue(any(ref.get('kind') == 'memory' and ref.get('id') == memory['id'] for ref in refs))
            self.app.state.agent_memory.mutate(db, self.alice_id, memory['id'], memory['revision'], delete=True)
        self.assertEqual(self.client.get(self.draft_url(did)).status_code, 409)

    def test_discussion_copy_uses_the_shared_review_and_save_flow(self):
        answer, did = self.ready()
        with connect(self.db) as db:
            db.execute("UPDATE users SET role='editor' WHERE id=?", (self.alice_id,))
            target = add_note(db, 'Developer/discussion-result.md', '# 合成整理\n\n原有说明。', visibility='private')
            db.execute('INSERT INTO grants(user_id,note_id,can_view,can_edit) VALUES(?,?,1,1)', (self.alice_id, target['id']))
        targets = self.client.get(self.draft_url(did) + '/targets').json()['targets']
        selected = next(item for item in targets if item['id'] == target['id'])
        proposal = self.client.post(self.draft_url(did) + '/proposals', headers=self.headers['alice'], json={
            'draft_version': 0, 'note_id': selected['id'], 'target_version': selected['version']})
        self.assertEqual(proposal.status_code, 200, proposal.text)
        response = self.client.post('/api/explore/proposals/' + proposal.json()['id'] + '/apply', headers=self.headers['alice'], json={'confirmed': True})
        self.assertEqual(response.status_code, 200, response.text)
        with connect(self.db) as db:
            body = db.execute('SELECT body FROM notes WHERE id=?', (target['id'],)).fetchone()[0]
            self.assertIn('原有说明', body)
            self.assertIn('## 资料来源', body)


if __name__ == '__main__':
    unittest.main()
