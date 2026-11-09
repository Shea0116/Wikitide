"""Signup, site account lifecycle, concurrency and admin isolation with disposable data."""
import concurrent.futures
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
from app import create_app
from accounts import initialize_accounts
from features import add_note
from storage import connect, create_session, create_user, password_matches, session_user


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.content = self.root / 'content'
        self.content.mkdir()
        self.db = self.root / 'site.db'
        self.app = create_app(self.db, self.content, cookie_secure=True)
        self.admin = TestClient(self.app, base_url='https://testserver')
        self.reader = TestClient(self.app, base_url='https://testserver')
        self.anon = TestClient(self.app, base_url='https://testserver')
        self.ids = {}
        self.tokens = {}
        self.csrf = {}
        with connect(self.db) as db:
            for name, role, client in [('shea', 'admin', self.admin), ('reader', 'reader', self.reader)]:
                create_user(db, name, 'account-test-password', role)
                uid = db.execute('SELECT id FROM users WHERE username=?', (name,)).fetchone()[0]
                self.ids[name] = uid
                token, self.csrf[name] = create_session(db, uid)
                self.tokens[name] = token
                client.cookies.set('kb_session', token)
            self.private = add_note(db, 'Developer/private.md', 'PRIVATE SECRET', visibility='private')

    def tearDown(self):
        self.admin.close(); self.reader.close(); self.anon.close()
        self.temp.cleanup()

    def signup(self, name='new_member', client=None, **extra):
        client = client or self.anon
        response = client.get('/register')
        csrf = re.search(r'name="csrf" value="([^"]+)"', response.text).group(1)
        data = {'csrf': csrf, 'username': name, 'password': 'signup-test-password', 'password_confirm': 'signup-test-password', **extra}
        return client.post('/register', data=data, follow_redirects=False)

    def update(self, uid=None, **data):
        return self.admin.patch('/api/admin/users/' + str(uid or self.ids['reader']),
            json={'role': 'reader', 'active': True, 'version': 1, **data}, headers={'X-CSRF-Token': self.csrf['shea']})

    def test_signup_normalizes_name_ignores_privilege_fields_and_sets_secure_session(self):
        response = self.signup(' New_Member ', role='admin', active='0', next='/teams')
        self.assertEqual(response.status_code, 303, response.text)
        self.assertEqual(response.headers['location'], '/teams')
        self.assertIn('HttpOnly', response.headers.get('set-cookie'))
        self.assertIn('Secure', response.headers.get('set-cookie'))
        with connect(self.db) as db:
            user = db.execute("SELECT * FROM users WHERE username='new_member'").fetchone()
            self.assertEqual((user['role'], user['active']), ('reader', 1))
            self.assertTrue(password_matches('signup-test-password', user['password_hash']))
            self.assertNotIn('signup-test-password', user['password_hash'])
        self.assertEqual(self.anon.get('/api/teams').status_code, 200)
        self.assertEqual(self.anon.get('/admin').status_code, 403)
        self.assertEqual(self.anon.get(f'/n/{self.private["id"]}').status_code, 404)
        self.assertEqual(self.anon.post('/api/teams', json={'name': 'My Team'}, headers={'X-CSRF-Token': self.session_csrf(self.anon)}).status_code, 201)

    def session_csrf(self, client):
        response = client.get('/teams')
        return re.search(r'data-csrf="([^"]+)"', response.text).group(1)

    def test_registration_csrf_and_password_validation_do_not_echo_password(self):
        self.assertEqual(self.anon.post('/register', data={'username': 'x'}).status_code, 403)
        for values in [{'password': 'short'}, {'password_confirm': 'not-the-same'}, {'password': 'x' * 129}]:
            response = self.signup(**values)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn('value="signup-test-password"', response.text)
            self.assertIn('name="username" value="new_member"', response.text)
        self.assertEqual(self.anon.get('/register').headers['Cache-Control'], 'no-store')

    def test_reserved_names_and_invalid_names_are_rejected(self):
        for name in ['shea', 'SHEA', 'admin', 'root', 'ab', 'abc@site', '张三', 'a' * 33]:
            self.assertEqual(self.signup(name).status_code, 400, name)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0], 2)

    def test_duplicate_names_casefold_and_concurrent_signup(self):
        clients = [TestClient(self.app, base_url='https://testserver') for _ in range(2)]
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda c: self.signup('Same_Name', c), clients))
            self.assertEqual(sorted(r.status_code for r in results), [303, 400])
            with connect(self.db) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM users WHERE lower(username)='same_name'").fetchone()[0], 1)
        finally:
            for client in clients: client.close()

    def test_signup_rate_limit_persists_failed_attempts_ignores_fake_forwarded_header(self):
        self.anon.headers['X-Forwarded-For'] = '1.2.3.4'
        for _ in range(10):
            # Invalid names consume attempts without expensive password hashing.
            self.assertEqual(self.signup('!').status_code, 400)
        self.anon.headers['X-Forwarded-For'] = '9.9.9.9'
        self.assertEqual(self.signup('!').status_code, 429)

    def test_signup_safe_redirect_and_bounded_body(self):
        for index, destination in enumerate(['//evil.test', '/\\evil.test', 'https://evil.test', '/\n/evil.test', '/admin', '/login']):
            client = TestClient(self.app, base_url='https://testserver')
            try:
                response = self.signup(f'user_{index}', client, next=destination)
                self.assertEqual(response.headers['location'], '/teams')
            finally: client.close()
        response = self.anon.post('/register', content=b'x' * 16385, headers={'Content-Type': 'application/x-www-form-urlencoded'})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.anon.post('/register', json={}).status_code, 415)

    def test_admin_surfaces_and_apis_reject_non_admin(self):
        for client in [self.anon, self.reader]:
            for route in ['/admin', '/admin/permissions', '/api/admin/overview', '/api/admin/users', '/api/admin/audit']:
                self.assertEqual(client.get(route).status_code, 403, route)
            self.assertEqual(client.patch('/api/admin/users/1', json={'role': 'admin'}).status_code, 403)
        self.assertIn('/static/admin/app.js', self.admin.get('/admin').text)
        self.assertEqual(self.admin.get('/admin?category=Frontend').status_code, 200)

    def test_overview_aggregates_without_team_bodies_or_credentials(self):
        with connect(self.db) as db:
            db.execute("INSERT INTO teams(name,created_at) VALUES('SECRET TEAM',1)")
        response = self.admin.get('/api/admin/overview')
        self.assertEqual(response.json()['counts']['teams'], 1)
        self.assertNotIn('SECRET TEAM', response.text)
        self.assertNotIn('PRIVATE SECRET', response.text)
        for response in [response, self.admin.get('/api/admin/users'), self.admin.get('/api/admin/audit')]:
            self.assertNotIn('password_hash', response.text)
            self.assertNotIn('token_hash', response.text)
            self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_account_update_requires_csrf_valid_data_and_version(self):
        url = '/api/admin/users/' + str(self.ids['reader'])
        self.assertEqual(self.admin.patch(url, json={}).status_code, 403)
        for data in [{'active': 0}, {'version': True}, {'role': []}, {'role': 'owner'}]:
            self.assertEqual(self.update(**data).status_code, 400)
        self.assertEqual(self.update(role='editor').status_code, 200)
        self.assertEqual(self.update(role='reader').status_code, 409)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT role FROM users WHERE id=?', (self.ids['reader'],)).fetchone()[0], 'editor')

    def test_disable_revokes_web_native_sessions_and_pending_invites(self):
        with connect(self.db) as db:
            token, _ = create_session(db, self.ids['reader'])
            team = db.execute("INSERT INTO teams(name,created_at) VALUES('team',1)").lastrowid
            db.execute("INSERT INTO team_invitations(team_id,user_id,invited_by,role,created_at,expires_at) VALUES(?,?,?,'reader',1,9999999999)", (team, self.ids['reader'], self.ids['shea']))
        self.assertEqual(self.update(active=False).status_code, 200)
        self.assertEqual(self.reader.get('/api/teams').status_code, 401)
        with connect(self.db) as db:
            self.assertIsNone(session_user(db, token)[0])
            self.assertEqual(db.execute('SELECT count(*) FROM sessions WHERE user_id=?', (self.ids['reader'],)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT status FROM team_invitations').fetchone()[0], 'revoked')

    def test_concurrent_admin_update_only_one_wins_and_records_actor(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda role: self.update(role=role), ['editor', 'admin']))
        self.assertEqual(sorted(r.status_code for r in responses), [200, 409])
        event = self.admin.get('/api/admin/audit').json()['events'][0]
        self.assertEqual(event['actor'], 'shea')
        self.assertEqual(event['target'], 'reader')

    def test_admin_cannot_disable_or_demote_self_in_both_interfaces(self):
        self.assertEqual(self.update(self.ids['shea'], role='reader').status_code, 400)
        self.assertEqual(self.update(self.ids['shea'], role='admin', active=False).status_code, 400)
        response = self.admin.post('/admin/users', data={'csrf': self.csrf['shea'], 'action': 'update', 'user_id': self.ids['shea'], 'version': 1, 'role': 'reader', 'active': 'on'})
        self.assertEqual(response.status_code, 400)

    def test_pause_registration_blocks_posts_and_stale_settings(self):
        data = {'registration_open': False, 'version': 1}
        headers = {'X-CSRF-Token': self.csrf['shea']}
        self.assertEqual(self.admin.post('/api/admin/settings', json=data).status_code, 403)
        self.assertEqual(self.admin.post('/api/admin/settings', json=data, headers=headers).status_code, 200)
        self.assertEqual(self.admin.post('/api/admin/settings', json=data, headers=headers).status_code, 409)
        page = self.anon.get('/register')
        self.assertIn('目前暂停开放注册', page.text)
        token = self.anon.cookies.get('kb_signup_csrf')
        self.assertEqual(self.anon.post('/register', data={'csrf': token, 'username': 'visitor'}).status_code, 403)
        self.assertEqual(self.reader.get('/teams').status_code, 200)

    def test_admin_filter_pagination_bounds_and_literal_search(self):
        with connect(self.db) as db:
            for i in range(21): create_user(db, f'member_{i}', 'account-test-password', 'reader')
        self.assertEqual(len(self.admin.get('/api/admin/users?page=2').json()['users']), 3)
        self.assertEqual(self.admin.get('/api/admin/users?q=%25').json()['total'], 0)
        self.assertEqual(self.admin.get('/api/admin/users?role=admin').json()['total'], 1)
        self.assertEqual(self.admin.get('/api/admin/users?page=0').status_code, 400)
        self.assertEqual(self.admin.get('/api/admin/audit?page=0').status_code, 400)

    def test_role_only_bootstrap_preserves_credentials_and_is_not_public(self):
        with connect(self.db) as db:
            before = db.execute('SELECT password_hash FROM users WHERE id=?', (self.ids['reader'],)).fetchone()[0]
        command = [sys.executable, str(Path(__file__).resolve().parent.parent / 'scripts/admin_user.py'), '--db', str(self.db), '--content', str(self.content), '--username', 'reader', '--promote-only']
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with connect(self.db) as db:
            account = db.execute('SELECT * FROM users WHERE id=?', (self.ids['reader'],)).fetchone()
            self.assertEqual(account['password_hash'], before)
            self.assertEqual(account['role'], 'admin')
        initialize_accounts(self.db)
        self.assertEqual(self.admin.get('/api/admin/users').json()['total'], 2)

    def test_additive_migration_is_safe_for_concurrent_startup(self):
        with connect(self.db) as db:
            db.execute('ALTER TABLE users DROP COLUMN version')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: initialize_accounts(self.db), range(2)))
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT username,version FROM users WHERE id=?', (self.ids['shea'],)).fetchone()['version'], 1)
            self.assertEqual(db.execute('SELECT body FROM notes WHERE id=?', (self.private['id'],)).fetchone()[0], 'PRIVATE SECRET')


if __name__ == '__main__': unittest.main()
