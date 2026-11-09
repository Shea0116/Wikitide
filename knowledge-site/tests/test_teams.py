"""Team boundaries and real multi-account workflows against an isolated SQLite database."""
import concurrent.futures
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
from app import create_app
from features import add_note
from storage import connect, create_session, create_user, digest
from teams import initialize_teams


class TeamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.content = self.root / 'content'
        self.content.mkdir()
        self.db = self.root / 'site.db'
        self.app = create_app(self.db, self.content, sync_token_hash=digest('test-sync'))
        self.clients, self.csrf, self.ids = {}, {}, {}
        with connect(self.db) as db:
            for name, role in [('alice', 'reader'), ('bob', 'reader'), ('carol', 'editor'), ('dana', 'reader'), ('root', 'admin')]:
                create_user(db, name, 'teams-test-password', role)
                self.ids[name] = db.execute('SELECT id FROM users WHERE username=?', (name,)).fetchone()[0]
                token, self.csrf[name] = create_session(db, self.ids[name])
                client = TestClient(self.app)
                client.cookies.set('kb_session', token)
                self.clients[name] = client
            self.personal = add_note(db, 'Developer/personal.md', 'PRIVATE ORIGINAL BODY', visibility='private')
        self.anon = TestClient(self.app)
        self.team = self.post('alice', '/api/teams', {'name': '研发手记', 'description': '一起记录'}).json()['id']
        self.other = self.post('carol', '/api/teams', {'name': '另一团队'}).json()['id']

    def tearDown(self):
        for client in self.clients.values():
            client.close()
        self.anon.close()
        self.temp.cleanup()

    def post(self, who, route, body):
        return self.clients[who].post(route, json=body, headers={'X-CSRF-Token': self.csrf[who]})

    def detail(self, who='alice', team=None):
        response = self.clients[who].get(f'/api/teams/{team or self.team}')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def change(self, who, suffix, body, team=None):
        tid = team or self.team
        body = {'version': self.detail(who, tid)['team']['version'], **body}
        return self.post(who, f'/api/teams/{tid}/' + suffix, body)

    def invite(self, username, role='editor', who='alice', team=None):
        response = self.change(who, 'invitations', {'username': username, 'role': role}, team)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    def join(self, username, role='editor', who='alice', team=None):
        invitation = self.invite(username, role, who, team)
        response = self.post(username, f'/api/teams/invitations/{invitation}/respond', {'action': 'accept'})
        self.assertEqual(response.status_code, 200, response.text)
        return invitation

    def doc(self, who='alice', team=None, body='TEAM ONLY NEEDLE'):
        response = self.post(who, f'/api/teams/{team or self.team}/documents', {'title': '团队约定', 'body': body})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def update(self, who, doc, **values):
        return self.clients[who].put(f'/api/teams/{doc["team_id"]}/documents/{doc["id"]}',
                                    json={'title': doc['title'], 'body': doc['body'], 'version': doc['version'], **values},
                                    headers={'X-CSRF-Token': self.csrf[who]})

    def test_login_csrf_and_same_origin_bundle(self):
        self.assertEqual(self.anon.get('/teams', follow_redirects=False).headers['location'], '/login?next=/teams')
        self.assertEqual(self.anon.get('/api/teams').status_code, 401)
        self.assertEqual(self.anon.post('/api/teams', json={'name': 'x'}).status_code, 401)
        self.assertEqual(self.clients['alice'].post('/api/teams', json={'name': 'x'}).status_code, 403)
        page = self.clients['alice'].get('/teams')
        self.assertEqual(page.status_code, 200)
        self.assertIn('/static/teams/app.js', page.text)
        self.assertIn('data-csrf=', page.text)
        self.assertEqual(page.headers['Cache-Control'], 'no-store')
        self.assertIn("script-src 'self'", page.headers['content-security-policy'])

    def test_team_listing_is_scoped_and_global_admin_has_no_bypass(self):
        listed = self.clients['alice'].get('/api/teams').json()['teams']
        self.assertEqual([row['id'] for row in listed], [self.team])
        self.assertEqual(listed[0]['role'], 'owner')
        self.assertEqual(self.clients['root'].get('/api/teams').json()['teams'], [])
        for who in ['bob', 'root']:
            for suffix in ['', '/documents', '/events']:
                self.assertEqual(self.clients[who].get(f'/api/teams/{self.team}' + suffix).status_code, 404)

    def test_targeted_invitation_accept_once_and_inbox_isolation(self):
        inv = self.invite('bob')
        self.assertEqual(self.clients['bob'].get(f'/api/teams/{self.team}').status_code, 404)
        self.assertEqual(len(self.clients['bob'].get('/api/teams/invitations').json()['invitations']), 1)
        self.assertEqual(self.clients['carol'].get('/api/teams/invitations').json()['invitations'], [])
        self.assertEqual(self.post('carol', f'/api/teams/invitations/{inv}/respond', {'action': 'accept'}).status_code, 404)
        route = f'/api/teams/invitations/{inv}/respond'
        self.assertEqual(self.post('bob', route, {'action': 'accept'}).status_code, 200)
        self.assertEqual(self.post('bob', route, {'action': 'accept'}).status_code, 409)
        self.assertEqual(self.detail('bob')['team']['role'], 'editor')
        self.doc('bob')  # Team editor works even if their site-wide role is reader.

    def test_invitation_expiry_revocation_and_decline(self):
        invitation = self.invite('bob')
        self.assertEqual(self.change('alice', f'invitations/{invitation}/revoke', {}).status_code, 200)
        self.assertEqual(self.post('bob', f'/api/teams/invitations/{invitation}/respond', {'action': 'accept'}).status_code, 409)
        invitation = self.invite('bob')
        with connect(self.db) as db:
            db.execute('UPDATE team_invitations SET expires_at=? WHERE id=?', (int(time.time()) - 1, invitation))
        self.assertEqual(self.post('bob', f'/api/teams/invitations/{invitation}/respond', {'action': 'accept'}).status_code, 409)
        invitation = self.invite('bob')
        self.assertEqual(self.post('bob', f'/api/teams/invitations/{invitation}/respond', {'action': 'decline'}).status_code, 200)
        self.assertEqual(self.clients['bob'].get(f'/api/teams/{self.team}').status_code, 404)

    def test_duplicate_invitations_and_existing_member_are_rejected(self):
        self.invite('bob')
        self.assertEqual(self.change('alice', 'invitations', {'username': 'bob', 'role': 'reader'}).status_code, 409)
        self.assertEqual(self.change('alice', 'invitations', {'username': 'alice', 'role': 'reader'}).status_code, 409)
        self.assertEqual(self.change('alice', 'invitations', {'username': 'unknown', 'role': 'reader'}).status_code, 400)

    def test_reader_cannot_write_preview_or_manage_members(self):
        self.join('bob', 'reader')
        doc = self.doc()
        self.assertEqual(self.clients['bob'].get(f'/api/teams/{self.team}/documents/{doc["id"]}').status_code, 200)
        self.assertEqual(self.post('bob', f'/api/teams/{self.team}/documents', {'title': 'x', 'body': 'x'}).status_code, 403)
        self.assertEqual(self.update('bob', doc, body='changed').status_code, 403)
        self.assertEqual(self.post('bob', f'/api/teams/{self.team}/preview', {'body': 'x'}).status_code, 403)
        self.assertEqual(self.change('bob', 'invitations', {'username': 'carol', 'role': 'editor'}).status_code, 403)

    def test_space_roles_do_not_grant_site_wide_note_access(self):
        self.join('bob', 'admin')
        self.assertEqual(self.clients['bob'].get(f'/n/{self.personal["id"]}').status_code, 404)
        self.assertEqual(self.clients['bob'].get('/admin').status_code, 403)
        self.assertEqual(self.clients['bob'].get(f'/api/teams/{self.other}').status_code, 404)

    def test_admin_cannot_promote_or_remove_another_admin_or_owner(self):
        self.join('bob', 'admin')
        self.join('dana', 'admin')
        for target in ['alice', 'dana']:
            self.assertEqual(self.change('bob', f'members/{self.ids[target]}', {'action': 'remove'}).status_code, 403)
        self.assertEqual(self.change('bob', 'invitations', {'username': 'carol', 'role': 'admin'}).status_code, 403)
        self.join('carol', 'reader', who='bob')
        self.assertEqual(self.change('bob', f'members/{self.ids["carol"]}', {'action': 'role', 'role': 'admin'}).status_code, 403)
        self.assertEqual(self.change('bob', f'members/{self.ids["carol"]}', {'action': 'role', 'role': 'editor'}).status_code, 200)

    def test_revocation_blocks_all_team_endpoints_immediately(self):
        self.join('bob')
        doc = self.doc()
        self.assertEqual(self.change('alice', f'members/{self.ids["bob"]}', {'action': 'remove'}).status_code, 200)
        for suffix in ['', '/documents', '/events', f'/documents/{doc["id"]}', f'/documents/{doc["id"]}/history']:
            self.assertEqual(self.clients['bob'].get(f'/api/teams/{self.team}' + suffix).status_code, 404)
        self.assertEqual(self.update('bob', doc, body='stolen').status_code, 404)

    def test_admin_demotion_revokes_pending_invitations(self):
        self.join('bob', 'admin')
        invitation = self.invite('carol', who='bob')
        self.assertEqual(self.change('alice', f'members/{self.ids["bob"]}', {'action': 'role', 'role': 'reader'}).status_code, 200)
        self.assertEqual(self.post('carol', f'/api/teams/invitations/{invitation}/respond', {'action': 'accept'}).status_code, 409)

    def test_owner_transfer_leave_and_single_owner_invariant(self):
        self.join('bob', 'reader')
        self.assertEqual(self.change('alice', 'leave', {}).status_code, 409)
        self.assertEqual(self.change('alice', 'transfer', {'user_id': self.ids['dana']}).status_code, 400)
        self.assertEqual(self.change('alice', 'transfer', {'user_id': self.ids['bob']}).status_code, 200)
        self.assertEqual(self.detail('bob')['team']['role'], 'owner')
        self.assertEqual(self.detail('alice')['team']['role'], 'admin')
        self.assertEqual(self.change('alice', 'leave', {}).status_code, 200)
        self.assertEqual(self.clients['alice'].get(f'/api/teams/{self.team}').status_code, 404)
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM team_members WHERE team_id=? AND role='owner'", (self.team,)).fetchone()[0], 1)

    def test_stale_membership_version_does_not_overwrite(self):
        self.join('bob')
        old = self.detail()['team']['version']
        self.doc()
        response = self.post('alice', f'/api/teams/{self.team}/members/{self.ids["bob"]}', {'action': 'remove', 'version': old})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.detail('bob')['team']['role'], 'editor')

    def test_foreign_document_ids_cannot_cross_team_boundary(self):
        foreign = self.doc('carol', self.other)
        self.assertEqual(self.clients['alice'].get(f'/api/teams/{self.team}/documents/{foreign["id"]}').status_code, 404)
        self.assertEqual(self.clients['alice'].get(f'/api/teams/{self.team}/documents/{foreign["id"]}/history').status_code, 404)
        foreign['team_id'] = self.team
        self.assertEqual(self.update('alice', foreign, body='stolen').status_code, 404)

    def test_concurrent_document_saves_have_one_winner_and_preserve_history(self):
        self.join('bob')
        doc = self.doc()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.update, name, doc, body=body) for name, body in [('alice', 'first'), ('bob', 'second')]]
            self.assertEqual(sorted(f.result().status_code for f in futures), [200, 409])
        history = self.clients['alice'].get(f'/api/teams/{self.team}/documents/{doc["id"]}/history').json()['revisions']
        self.assertEqual([r['version'] for r in history], [2, 1])
        current = self.clients['alice'].get(f'/api/teams/{self.team}/documents/{doc["id"]}').json()
        self.assertIn(current['body'], ['first', 'second'])

    def test_markdown_is_sanitized_and_never_uses_personal_assets(self):
        body = '    indented code\n\n<script>alert(1)</script>\n\n<img src="/asset/1?path=secret.png" onerror="alert(1)">\n\n[x](javascript:alert(1))\n\n[ok](https://example.com)'
        doc = self.doc(body=body)
        self.assertEqual(doc['body'], body)
        self.assertIn('<code>indented code', doc['html'])
        self.assertNotIn('<script', doc['html'])
        self.assertNotIn('onerror=', doc['html'])
        self.assertNotIn('/asset/', doc['html'])
        self.assertNotIn('href="javascript:', doc['html'])
        self.assertIn('https://example.com', doc['html'])
        preview = self.post('alice', f'/api/teams/{self.team}/preview', {'body': body}).json()
        self.assertEqual(preview['html'], doc['html'])

    def test_team_content_does_not_enter_personal_search_sync_or_public_graph(self):
        self.doc(body='UNIQUE-TEAM-SECRET')
        for route in ['/', '/browse', '/search?q=UNIQUE-TEAM-SECRET', '/api/client/v1/notes', '/explore']:
            response = self.clients['root'].get(route)
            self.assertNotIn('团队约定', response.text)
            if route.startswith('/search'):
                self.assertIn('共 0 篇', response.text)
        sync = self.anon.get('/api/sync/v2/state', headers={'Authorization': 'Bearer test-sync'}).json()
        self.assertEqual(len(sync['notes']), 1)
        self.assertEqual(sync['notes'][0]['path'], 'Developer/personal.md')
        self.assertNotIn('UNIQUE-TEAM-SECRET', str(sync))

    def test_search_pagination_counts_only_current_team_and_escapes_wildcards(self):
        self.doc('carol', self.other, body='alpha')
        for i in range(21):
            response = self.post('alice', f'/api/teams/{self.team}/documents', {'title': f'笔记 {i}', 'body': 'alpha %_ literal'})
            self.assertEqual(response.status_code, 201)
        page = self.clients['alice'].get(f'/api/teams/{self.team}/documents?q=alpha&page=2').json()
        self.assertEqual(page['total'], 21)
        self.assertEqual(len(page['documents']), 1)
        self.assertNotIn('body', page['documents'][0])
        self.assertEqual(self.clients['alice'].get(f'/api/teams/{self.team}/documents?q=absent%25').json()['total'], 0)
        self.assertEqual(self.clients['alice'].get(f'/api/teams/{self.team}/documents?page=0').status_code, 400)

    def test_input_limits_and_invalid_structures_return_errors(self):
        self.assertEqual(self.post('alice', '/api/teams', {'name': ' '}).status_code, 400)
        self.assertEqual(self.post('alice', '/api/teams', {'name': 'x' * 81}).status_code, 400)
        self.assertEqual(self.post('alice', '/api/teams', []).status_code, 400)
        self.assertEqual(self.change('alice', 'invitations', {'username': 'bob', 'role': []}).status_code, 400)
        self.assertEqual(self.post('alice', f'/api/teams/{self.team}/documents', {'title': 'x', 'body': 'x' * 100001}).status_code, 400)
        large = self.clients['alice'].post('/api/teams', content=b'x' * (256 * 1024 + 1), headers={'Content-Type': 'application/json', 'X-CSRF-Token': self.csrf['alice']})
        self.assertEqual(large.status_code, 413)
        self.assertEqual(self.clients['alice'].post('/api/teams', content='x').status_code, 415)
        self.assertEqual(self.post('alice', '/api/teams/invitations/1/respond', {'action': []}).status_code, 400)

    def test_initialization_is_idempotent_and_preserves_personal_data(self):
        doc = self.doc()
        initialize_teams(self.db)
        initialize_teams(self.db)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT body FROM notes WHERE id=?', (self.personal['id'],)).fetchone()[0], 'PRIVATE ORIGINAL BODY')
            self.assertEqual(db.execute('SELECT body FROM team_documents WHERE id=?', (doc['id'],)).fetchone()[0], doc['body'])
            events = db.execute('SELECT detail FROM team_events WHERE team_id=?', (self.team,)).fetchall()
            self.assertNotIn(doc['body'], str([tuple(e) for e in events]))


if __name__ == '__main__':
    unittest.main()
