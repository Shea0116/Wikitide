"""Research provenance, draft transfer and reviewed note commits on synthetic data."""
import json
import unittest

from features import add_note
from storage import connect, save_note
import knowledge_artifacts
import test_knowledge_jobs as jobs_fixture
from test_explore_write import Writer


class ArtifactTests(unittest.TestCase):
    setUp_jobs = jobs_fixture.KnowledgeJobTests.setUp
    tearDown = jobs_fixture.KnowledgeJobTests.tearDown
    create = jobs_fixture.KnowledgeJobTests.create
    detail = jobs_fixture.KnowledgeJobTests.detail

    def setUp(self):
        self.setUp_jobs()
        self.app.state.artifacts_enabled = True
        with connect(self.db) as db:
            db.execute("UPDATE users SET role='editor' WHERE id=?", (self.ids['alice'],))
            db.execute('UPDATE grants SET can_edit=1 WHERE user_id=?', (self.ids['alice'],))
            self.target = add_note(db, 'Developer/synthetic-target.md', '# 合成目标\n\n保留原来的案例。\n', category='私密', visibility='private')
            db.execute('INSERT INTO grants(user_id,note_id,can_view,can_edit) VALUES(?,?,1,1)', (self.ids['alice'], self.target['id']))

    def transfer(self, rid):
        response = self.client.post('/api/agent/v1/runs/' + rid + '/draft', headers=self.headers['alice'])
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['draft_id']

    def ready(self, **changes):
        job = self.create(**changes)
        self.service.run_once()
        self.assertEqual(self.detail(job['id'])['status'], 'succeeded')
        return self.transfer(job['id'])

    def url(self, did):
        return '/api/explore/drafts/' + did

    def draft(self, did):
        response = self.client.get(self.url(did))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def candidates(self, did):
        response = self.client.get(self.url(did) + '/targets')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['targets']

    def preview(self, did, **changes):
        target = next(item for item in self.candidates(did) if item['id'] == changes.get('note_id', self.target['id']))
        body = {'draft_version': self.draft(did)['version'], 'note_id': target['id'], 'target_version': target['version'], 'mode': 'append'}
        body.update(changes)
        response = self.client.post(self.url(did) + '/proposals', headers=self.headers['alice'], json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def apply(self, pid, **changes):
        return self.client.post('/api/explore/proposals/' + pid + '/apply', headers=self.headers['alice'], json={'confirmed': True, **changes})

    def edit(self, did, **changes):
        draft = self.draft(did)
        body = {key: draft[key] for key in ('title', 'body', 'outline', 'version')}
        body.update(changes)
        return self.client.put(self.url(did), headers=self.headers['alice'], json=body)

    def change_target(self):
        with connect(self.db) as db:
            note = db.execute('SELECT * FROM notes WHERE id=?', (self.target['id'],)).fetchone()
            save_note(db, note, note['body'] + '\n并发新增案例。\n', 'local')

    def test_transfer_is_durable_idempotent_and_copies_source_provenance(self):
        job = self.create(); self.service.run_once()
        calls = len(self.provider.calls)
        did = self.transfer(job['id'])
        self.assertEqual(self.transfer(job['id']), did)
        self.assertEqual(len(self.provider.calls), calls)
        draft = self.draft(did)
        self.assertTrue(draft['research_origin'])
        self.assertTrue(draft['private_only'])
        self.assertEqual(draft['memory_mode'], 'off')
        self.assertEqual(draft['sources'][0]['kind'], 'account_note')
        self.assertIn('[1]', draft['body'])
        self.assertIn('/n/', draft['html'])
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM explore_drafts').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT body FROM notes WHERE id=?', (self.target['id'],)).fetchone()[0], self.target['body'])
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            knowledge_artifacts.initialize_artifacts(db)
            knowledge_artifacts.initialize_artifacts(db)
            self.assertEqual(db.execute('SELECT count(*) FROM knowledge_draft_origins').fetchone()[0], 1)

    def test_auth_csrf_and_feature_gate_protect_artifact_routes(self):
        job = self.create()
        path = '/api/agent/v1/runs/' + job['id'] + '/draft'
        self.assertEqual(self.client.post(path).status_code, 403)
        self.assertEqual(self.clients['bob'].post(path, headers=self.headers['bob']).status_code, 404)
        self.assertEqual(self.client.post(path, headers=self.headers['alice']).status_code, 409)
        self.service.run_once(); did = self.transfer(job['id'])
        self.assertEqual(self.clients['bob'].get(self.url(did) + '/targets').status_code, 404)
        self.assertEqual(self.clients['bob'].get('/explore/drafts/' + did + '/review').status_code, 404)
        self.assertEqual(self.client.post(self.url(did) + '/proposals', json={'draft_version': 0, 'note_id': self.target['id'], 'target_version': 'a' * 64}).status_code, 403)
        proposal = self.preview(did)
        for suffix in ('', '/apply'):
            path = '/api/explore/proposals/' + proposal['id'] + suffix
            response = self.clients['bob'].post(path, headers=self.headers['bob'], json={'confirmed': True}) if suffix else self.clients['bob'].get(path)
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.post('/api/explore/proposals/' + proposal['id'] + '/apply', json={'confirmed': True}).status_code, 403)
        self.app.state.artifacts_enabled = False
        self.assertEqual(self.client.get(self.url(did) + '/targets').status_code, 404)

    def test_private_draft_read_edit_export_and_publication_restriction(self):
        did = self.ready()
        self.assertEqual(self.edit(did, body=self.draft(did)['body'] + '\n\n人工核对。').status_code, 200)
        exported = self.client.get(self.url(did) + '/export')
        self.assertEqual(exported.status_code, 200)
        self.assertIn('## 资料来源', exported.text)
        with connect(self.db) as db:
            db.execute("UPDATE users SET role='admin' WHERE id=?", (self.ids['alice'],))
        body = {'version': self.draft(did)['version'], 'path': 'Frontend/synthetic-export.md', 'visibility': 'public', 'confirmed': True}
        self.assertEqual(self.client.post(self.url(did) + '/publish', headers=self.headers['alice'], json=body).status_code, 403)
        body.update(path='Developer/synthetic-export.md', visibility='private')
        response = self.client.post(self.url(did) + '/publish', headers=self.headers['alice'], json=body)
        self.assertEqual(response.status_code, 200, response.text)

    def test_uncited_selected_source_revocation_invalidates_the_draft(self):
        with connect(self.db) as db:
            extra = add_note(db, 'Developer/uncited.md', '# 补充资料\n\n闭包和状态。', visibility='private')
            db.execute('INSERT INTO grants(user_id,note_id,can_view) VALUES(?,?,1)', (self.ids['alice'], extra['id']))
        did = self.ready(note_ids=[self.private['id'], extra['id']])
        cited = {source['note_id'] for source in self.draft(did)['sources']}
        uncited = next(nid for nid in (self.private['id'], extra['id']) if nid not in cited)
        with connect(self.db) as db:
            db.execute('DELETE FROM grants WHERE user_id=? AND note_id=?', (self.ids['alice'], uncited))
        self.assertEqual(self.client.get(self.url(did)).status_code, 409)
        listing = self.client.get('/api/explore/drafts').json()['drafts'][0]
        self.assertTrue(listing['stale'])
        self.assertEqual(listing['title'], '资料已失效的研究草稿')
        self.assertEqual(listing['topic'], '')

    def test_deleted_draft_is_not_silently_recreated(self):
        job = self.create(); self.service.run_once(); did = self.transfer(job['id'])
        self.assertEqual(self.client.delete(self.url(did), headers=self.headers['alice']).status_code, 200)
        self.assertEqual(self.client.post('/api/agent/v1/runs/' + job['id'] + '/draft', headers=self.headers['alice']).status_code, 409)

    def test_preview_and_confirm_append_only_once_with_history_and_sync(self):
        did = self.ready(); proposal = self.preview(did)
        self.assertIn('+', proposal['diff'])
        self.assertFalse(proposal['conflict'])
        self.assertEqual(self.preview(did)['id'], proposal['id'])
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT body FROM notes WHERE id=?', (self.target['id'],)).fetchone()[0], self.target['body'])
        self.assertEqual(self.apply(proposal['id'], confirmed=False).status_code, 400)
        response = self.apply(proposal['id'])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.apply(proposal['id']).json(), response.json())
        with connect(self.db) as db:
            note = db.execute('SELECT * FROM notes WHERE id=?', (self.target['id'],)).fetchone()
            self.assertIn(self.target['body'].strip(), note['body'])
            self.assertIn('## 资料来源', note['body'])
            self.assertEqual(note['revision'], self.target['revision'] + 1)
            self.assertEqual(db.execute("SELECT count(*) FROM edit_events WHERE note_id=? AND origin='web'", (note['id'],)).fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM revisions WHERE note_id=?', (note['id'],)).fetchone()[0], 2)
        self.assertEqual(self.draft(did)['saved_note_id'], self.target['id'])

    def test_replace_is_explicit_and_pending_sibling_cannot_apply_after_save(self):
        did = self.ready(); append = self.preview(did); replace = self.preview(did, mode='replace')
        self.assertNotEqual(append['id'], replace['id'])
        self.assertIn('-保留原来的案例。', replace['diff'])
        self.assertEqual(self.apply(replace['id']).status_code, 200)
        self.assertEqual(self.apply(append['id']).status_code, 409)
        with connect(self.db) as db:
            self.assertNotIn('保留原来的案例', db.execute('SELECT body FROM notes WHERE id=?', (self.target['id'],)).fetchone()[0])

    def test_concurrent_target_edit_requires_a_fresh_diff(self):
        did = self.ready(); proposal = self.preview(did)
        self.change_target()
        current = self.client.get('/api/explore/proposals/' + proposal['id']).json()
        self.assertTrue(current['conflict'])
        self.assertEqual(self.apply(proposal['id']).status_code, 409)
        fresh = self.preview(did)
        self.assertNotEqual(fresh['id'], proposal['id'])
        self.assertIn('并发新增案例', fresh['diff'])
        self.assertEqual(self.apply(fresh['id']).status_code, 200)

    def test_target_metadata_and_draft_versions_are_fenced(self):
        did = self.ready(); proposal = self.preview(did)
        with connect(self.db) as db:
            db.execute("UPDATE notes SET path='Developer/renamed-target.md' WHERE id=?", (self.target['id'],))
        self.assertEqual(self.apply(proposal['id']).status_code, 409)
        proposal = self.preview(did)
        self.assertEqual(self.edit(did, body=self.draft(did)['body'] + '\n\n更新草稿。').status_code, 200)
        self.assertEqual(self.apply(proposal['id']).status_code, 409)
        fresh = self.preview(did)
        self.assertIn('更新草稿', fresh['diff'])

    def test_stale_target_selection_is_rejected_before_preview(self):
        did = self.ready()
        target = next(item for item in self.candidates(did) if item['id'] == self.target['id'])
        self.change_target()
        body = {'draft_version': self.draft(did)['version'], 'note_id': target['id'], 'target_version': target['version']}
        response = self.client.post(self.url(did) + '/proposals', headers=self.headers['alice'], json=body)
        self.assertEqual(response.status_code, 409)

    def test_source_update_and_edit_permission_revocation_stop_commit(self):
        did = self.ready(); proposal = self.preview(did)
        with connect(self.db) as db:
            db.execute('UPDATE grants SET can_edit=0 WHERE user_id=? AND note_id=?', (self.ids['alice'], self.target['id']))
        self.assertEqual(self.apply(proposal['id']).status_code, 404)
        with connect(self.db) as db:
            db.execute('UPDATE grants SET can_edit=1 WHERE user_id=? AND note_id=?', (self.ids['alice'], self.target['id']))
            note = db.execute('SELECT * FROM notes WHERE id=?', (self.private['id'],)).fetchone()
            save_note(db, note, note['body'] + '\n来源更新。', 'local')
        self.assertEqual(self.apply(proposal['id']).status_code, 409)
        self.assertEqual(self.client.get(self.url(did) + '/export').status_code, 409)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT body FROM notes WHERE id=?', (self.target['id'],)).fetchone()[0], self.target['body'])

    def test_private_sources_cannot_be_written_to_public_targets(self):
        did = self.ready()
        with connect(self.db) as db:
            db.execute("UPDATE notes SET visibility='public' WHERE id=?", (self.target['id'],))
            note = db.execute('SELECT * FROM notes WHERE id=?', (self.target['id'],)).fetchone()
        self.assertNotIn(self.target['id'], {item['id'] for item in self.candidates(did)})
        body = {'draft_version': 0, 'note_id': note['id'], 'target_version': knowledge_artifacts.note_version(note)}
        self.assertEqual(self.client.post(self.url(did) + '/proposals', headers=self.headers['alice'], json=body).status_code, 403)

    def test_visibility_change_after_preview_blocks_commit(self):
        did = self.ready(); proposal = self.preview(did)
        with connect(self.db) as db:
            db.execute("UPDATE notes SET visibility='public' WHERE id=?", (self.target['id'],))
        self.assertEqual(self.apply(proposal['id']).status_code, 403)

    def test_updating_a_cited_note_has_an_idempotent_receipt(self):
        did = self.ready(); proposal = self.preview(did, note_id=self.private['id'])
        response = self.apply(proposal['id'])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.apply(proposal['id']).json(), response.json())
        self.assertEqual(self.client.get('/api/explore/proposals/' + proposal['id']).json(), response.json())
        self.assertEqual(self.client.get(self.url(did)).status_code, 409)  # old evidence version no longer current

    def test_revision_uses_fixed_sources_and_cannot_restart_public_research(self):
        did = self.ready(); before = self.draft(did)
        writer = Writer(); self.app.state.explore_provider = writer
        response = self.client.post(self.url(did) + '/generate', headers=self.headers['alice'], json={'version': before['version'], 'action': 'outline'})
        self.assertIn('研究草稿已固定资料', response.text)
        self.assertEqual(self.draft(did)['body'], before['body'])
        response = self.client.post(self.url(did) + '/generate', headers=self.headers['alice'], json={'version': before['version'], 'action': 'revise', 'instruction': '补充例子', 'memory_mode': 'off'})
        self.assertIn('event: done', response.text)
        data = json.loads(writer.calls[-1][0][-1]['content'])
        self.assertEqual(data['sources'][0]['kind'], 'account_note')
        self.assertEqual(data['scope'], '研究任务固定的资料')

    def test_public_research_can_update_an_explicitly_reviewed_public_target(self):
        did = self.ready(scope='public', note_id=self.note['id'], note_ids=[])
        self.assertFalse(self.draft(did)['private_only'])
        with connect(self.db) as db:
            db.execute('INSERT INTO grants(user_id,note_id,can_view,can_edit) VALUES(?,?,1,1)', (self.ids['alice'], self.note['id']))
        proposal = self.preview(did, note_id=self.note['id'])
        self.assertEqual(proposal['target']['visibility'], 'public')
        self.assertEqual(self.apply(proposal['id']).status_code, 200)

    def test_no_change_preview_does_not_create_a_version_or_proposal(self):
        response = self.client.post('/api/explore/drafts', headers=self.headers['alice'], json={'topic': '合成手写草稿', 'memory_mode': 'off'})
        self.assertEqual(response.status_code, 200, response.text)
        did = response.json()['id']
        self.assertEqual(self.edit(did, body=self.target['body']).status_code, 200)
        target = next(item for item in self.candidates(did) if item['id'] == self.target['id'])
        response = self.client.post(self.url(did) + '/proposals', headers=self.headers['alice'], json={
            'draft_version': self.draft(did)['version'], 'note_id': target['id'], 'target_version': target['version'], 'mode': 'replace'})
        self.assertEqual(response.status_code, 400)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM knowledge_note_proposals').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
