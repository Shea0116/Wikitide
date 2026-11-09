"""Native API permission, session, attachment and concurrent-edit regression tests."""
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from fastapi.testclient import TestClient
from app import create_app
from features import add_note
from storage import connect, create_user, digest, save_note

PREFIX = '/api/client/v1'


class ClientApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db, self.content = root / 'site.db', root / 'content'
        self.content.mkdir()
        self.client = TestClient(create_app(self.db, self.content, sync_token_hash=digest('sync-secret')))
        with connect(self.db) as db:
            for username, role in [('owner', 'admin'), ('reader', 'reader'), ('editor', 'editor')]:
                create_user(db, username, 'test-password-123', role)
            self.public = add_note(db, 'Frontend/hello.md', '# Hello\n\n<span style="color:#DC143C">彩色</span>\n\n![image](static/image.png)', category='Frontend', visibility='public')
            self.private = add_note(db, 'Developer/secret.md', 'private-marker', category='私密类别', visibility='private')
            self.reader_id = db.execute("SELECT id FROM users WHERE username='reader'").fetchone()[0]
            self.editor_id = db.execute("SELECT id FROM users WHERE username='editor'").fetchone()[0]
        folder = self.content / 'Frontend/static'
        folder.mkdir(parents=True)
        (folder / 'image.png').write_bytes(b'example-image')
        (folder / 'unlinked.png').write_bytes(b'not-linked')

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def login(self, name='owner'):
        response = self.client.post(PREFIX + '/login', json={'username': name, 'password': 'test-password-123'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('password_hash', response.text)
        return {'Authorization': 'Bearer ' + response.json()['token']}

    def test_anonymous_scope_and_cookie_or_sync_token_cannot_authorize_client(self):
        for route in ['/home', '/notes', '/search?q=private-marker']:
            response = self.client.get(PREFIX + route)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('private-marker', response.text)
            self.assertNotIn('私密类别', response.text)
        self.assertEqual(self.client.get(PREFIX + f"/notes/{self.private['id']}").status_code, 404)
        self.assertEqual(self.client.get(PREFIX + '/session', headers={'Authorization': 'Bearer sync-secret'}).status_code, 401)
        headers = self.login()
        token = headers['Authorization'].split()[1]
        self.client.cookies.set('kb_session', token)
        self.assertIsNone(self.client.get(PREFIX + '/session').json()['user'])
        self.assertEqual(self.client.get(PREFIX + '/library').status_code, 401)

    def test_login_expiry_logout_and_failed_attempts_are_persistent(self):
        headers = self.login('reader')
        self.assertEqual(self.client.get(PREFIX + '/session', headers=headers).json()['user']['username'], 'reader')
        self.assertEqual(self.client.post(PREFIX + '/logout', headers=headers).status_code, 200)
        self.assertEqual(self.client.get(PREFIX + '/session', headers=headers).status_code, 401)
        headers = self.login('reader')
        with connect(self.db) as db:
            db.execute('UPDATE sessions SET expires_at=0')
        self.assertEqual(self.client.get(PREFIX + '/notes', headers=headers).status_code, 401)
        for _ in range(10):
            self.assertEqual(self.client.post(PREFIX + '/login', json={'username': 'reader', 'password': 'wrong'}).status_code, 401)
        self.assertEqual(self.client.post(PREFIX + '/login', json={'username': 'reader', 'password': 'wrong'}).status_code, 429)

    def test_revoked_permissions_hide_library_and_block_assets_and_writes(self):
        with connect(self.db) as db:
            db.execute('INSERT INTO grants VALUES(?,?,1,0)', (self.reader_id, self.private['id']))
        headers = self.login('reader')
        note_path = PREFIX + f"/notes/{self.private['id']}"
        self.assertEqual(self.client.put(note_path + '/bookmark', headers=headers, json={'bookmarked': True}).status_code, 200)
        self.assertEqual(self.client.put(note_path + '/reading', headers=headers, json={'position': .5}).status_code, 200)
        self.assertIn('secret', self.client.get(PREFIX + '/library', headers=headers).text)
        self.assertNotIn('body', self.client.get(note_path, headers=headers).json())
        with connect(self.db) as db:
            db.execute('DELETE FROM grants WHERE user_id=?', (self.reader_id,))
        self.assertNotIn('secret', self.client.get(PREFIX + '/library', headers=headers).text)
        self.assertEqual(self.client.get(note_path, headers=headers).status_code, 404)
        self.assertEqual(self.client.put(note_path + '/bookmark', headers=headers, json={'bookmarked': False}).status_code, 404)
        asset = f"/api/client/notes/{self.private['id']}/asset?path=x.png"
        self.assertEqual(self.client.get(asset, headers=headers).status_code, 404)

    def test_search_pagination_never_serializes_bodies(self):
        with connect(self.db) as db:
            for i in range(25):
                add_note(db, f'Frontend/hello-{i}.md', 'search-marker', category='Frontend', visibility='public')
        response = self.client.get(PREFIX + '/search?q=search-marker')
        data = response.json()
        self.assertEqual(data['total'], 25)
        self.assertEqual(len(data['notes']), 20)
        self.assertTrue(data['has_more'])
        self.assertNotIn('body', data['notes'][0])
        self.assertNotIn('body_sha', data['notes'][0])
        self.assertEqual(len(self.client.get(PREFIX + '/search?q=search-marker&page=2').json()['notes']), 5)
        self.assertEqual(self.client.get(PREFIX + '/notes?page=0').status_code, 400)

    def test_asset_scope_rendering_and_oversized_or_invalid_requests(self):
        url = f"/api/client/notes/{self.public['id']}/asset"
        self.assertEqual(self.client.get(url, params={'path': 'static/image.png'}).content, b'example-image')
        for path in ['static/unlinked.png', '../Developer/secret.png', '/etc/passwd']:
            self.assertEqual(self.client.get(url, params={'path': path}).status_code, 404)
        detail = self.client.get(PREFIX + f"/notes/{self.public['id']}").json()
        self.assertIn('#dc143c', detail['html'])
        self.assertNotIn('body', detail)
        headers = self.login()
        self.assertEqual(self.client.put(PREFIX + f"/notes/{self.public['id']}/reading", headers=headers, json={'position': True}).status_code, 400)
        self.assertEqual(self.client.post(PREFIX + '/login', json=[]).status_code, 400)
        self.assertEqual(self.client.post(PREFIX + '/login', content=b'x' * (8 * 1024 * 1024 + 1)).status_code, 413)

    def test_editor_preview_concurrent_save_history_restore(self):
        with connect(self.db) as db:
            db.execute('INSERT INTO grants VALUES(?,?,1,1)', (self.editor_id, self.public['id']))
        headers = self.login('editor')
        path = PREFIX + f"/notes/{self.public['id']}"
        preview = self.client.post(path + '/preview', headers=headers, json={'body': '<script>bad()</script>\n\n**Good**'})
        self.assertNotIn('<script>', preview.json()['html'])
        self.assertIn('<strong>Good</strong>', preview.json()['html'])
        self.assertEqual(self.client.put(path, headers=headers, json={'body': 'edit', 'base_sha': self.public['body_sha']}).status_code, 200)
        stale = self.client.put(path, headers=headers, json={'body': 'do not overwrite', 'base_sha': self.public['body_sha']})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()['latest']['body'], 'edit')
        latest = self.client.get(path, headers=headers).json()
        revisions = self.client.get(path + '/history', headers=headers).json()['revisions']
        old_id = revisions[-1]['id']
        self.assertEqual(self.client.get(path + f'/history/{old_id}', headers=headers).status_code, 200)
        self.assertEqual(self.client.get(path + '/history').status_code, 404)
        self.assertEqual(self.client.post(path + '/restore', headers=headers, json={'revision_id': old_id, 'base_sha': self.public['body_sha']}).status_code, 409)
        self.assertEqual(self.client.post(path + '/restore', headers=headers, json={'revision_id': old_id, 'base_sha': latest['body_sha']}).status_code, 200)
        with connect(self.db) as db:
            note = db.execute('SELECT * FROM notes WHERE id=?', (self.public['id'],)).fetchone()
            self.assertEqual(note['body'], self.public['body'])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM edit_events WHERE origin='web' AND status='pending'").fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
