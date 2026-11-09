"""Sharing boundaries, dynamic category access, revocation and account permissions."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
from app import create_app
from features import add_note
from storage import connect, create_user, create_session, can_edit, can_view, visible_where


class SharingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.content = self.root / 'content'
        self.content.mkdir()
        self.db = self.root / 'site.db'
        self.app = create_app(self.db, self.content)
        self.anon = TestClient(self.app)
        self.clients, self.csrf, self.ids = {}, {}, {}
        with connect(self.db) as db:
            for name, role in [('owner', 'admin'), ('reader', 'reader'), ('editor', 'editor')]:
                create_user(db, name, 'sharing-test-password', role)
                uid = db.execute('SELECT id FROM users WHERE username=?', (name,)).fetchone()[0]
                token, csrf = create_session(db, uid)
                client = TestClient(self.app)
                client.cookies.set('kb_session', token)
                self.clients[name], self.csrf[name], self.ids[name] = client, csrf, uid
            self.note = add_note(db, 'Developer/secret.md', '# Shared title\n\n![image](static/a.png)\n\n[child](child.md)\n\n[[child]]', category='私密 / 100%_项目', visibility='private')
            self.child = add_note(db, 'Developer/child.md', '# Child body', category='私密 / 100%_项目 / 子目录', visibility='private')
            self.other = add_note(db, 'Developer/other.md', 'HIDDEN BODY', category='私密 / 100%_项目备份', visibility='private')
            self.public = add_note(db, 'Frontend/public.md', 'Public body', category='Frontend', visibility='public')
        asset = self.content / 'Developer/static'
        asset.mkdir(parents=True)
        (asset / 'a.png').write_bytes(b'shared-image')
        (asset / 'unlinked.png').write_bytes(b'hidden-image')
        self.owner = self.clients['owner']

    def tearDown(self):
        self.anon.close()
        for client in self.clients.values():
            client.close()
        self.temp.cleanup()

    def state(self, **target):
        response = self.owner.get('/api/sharing', params=target)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def save(self, target=None, audience='link', members=None, version=None):
        target = target or {'note_id': self.note['id']}
        if version is None:
            version = self.state(**target)['version']
        return self.owner.post('/api/sharing', json={**target, 'audience': audience, 'members': members or [], 'version': version},
                               headers={'X-CSRF-Token': self.csrf['owner']})

    def member(self, name, permission='view'):
        return {'user_id': self.ids[name], 'permission': permission}

    def test_note_link_is_read_only_and_not_publicly_discoverable(self):
        response = self.save()
        self.assertEqual(response.status_code, 200, response.text)
        url = response.json()['url']
        shared = self.anon.get(url)
        self.assertIn('Shared title', shared.text)
        self.assertNotIn('Child body', shared.text)
        self.assertNotIn('HIDDEN BODY', shared.text)
        self.assertNotIn('链接分享</button>', shared.text)
        self.assertNotIn('data-editor', shared.text)
        self.assertEqual(shared.headers['cache-control'], 'no-store')
        self.assertIn('noindex', shared.headers['x-robots-tag'])
        for route in ['/', '/browse', '/search?q=Shared', '/api/client/v1/notes']:
            self.assertNotIn('Shared title', self.anon.get(route).text)
        self.assertEqual(self.anon.get(f"/n/{self.note['id']}").status_code, 404)
        self.assertEqual(self.anon.get(f"{url}/n/{self.child['id']}").status_code, 404)
        self.assertEqual(self.anon.post(f"{url}/n/{self.note['id']}/edit", data={'body': 'changed'}).status_code, 404)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT visibility FROM notes WHERE id=?', (self.note['id'],)).fetchone()[0], 'private')

    def test_share_assets_require_token_scope_and_body_reference(self):
        url = self.save().json()['url']
        response = self.anon.get(f"{url}/asset/{self.note['id']}?path=static/a.png")
        self.assertEqual(response.content, b'shared-image')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertIn(f'{url}/asset/{self.note["id"]}', self.anon.get(url).text)
        for path in ['static/unlinked.png', '../Frontend/public.png', 'https://example.com/a.png', '%2e%2e/Frontend/a.png']:
            self.assertEqual(self.anon.get(f"{url}/asset/{self.note['id']}", params={'path': path}).status_code, 404)
        self.assertEqual(self.anon.get(f"{url}/asset/{self.child['id']}?path=static/a.png").status_code, 404)
        token = url.split('/')[-1]
        self.assertEqual(self.anon.get(f"/asset/{self.note['id']}?path=static/a.png&token={token}").status_code, 404)

    def test_directory_contains_descendants_and_future_notes_without_prefix_collisions(self):
        target = {'category': self.note['category']}
        saved = self.save(target).json()
        self.assertEqual(saved['count'], 2)
        url = saved['url']
        listing = self.anon.get(url)
        self.assertIn(self.note['title'], listing.text)
        self.assertIn(self.child['title'], listing.text)
        self.assertNotIn(self.other['title'], listing.text)
        self.assertNotIn('Public body', listing.text)
        shared = self.anon.get(f"{url}/n/{self.note['id']}")
        self.assertEqual(shared.text.count(f'href="{url}/n/{self.child["id"]}"'), 2)
        self.assertEqual(self.anon.get(f"{url}/n/{self.other['id']}").status_code, 404)
        with connect(self.db) as db:
            future = add_note(db, 'Developer/future.md', 'Future body', category=target['category'] + ' / 新目录', visibility='private')
        self.assertIn('Future body', self.anon.get(f"{url}/n/{future['id']}").text)
        with connect(self.db) as db:
            db.execute('UPDATE notes SET category=? WHERE id=?', ('私密 / 别的目录', future['id']))
        self.assertEqual(self.anon.get(f"{url}/n/{future['id']}").status_code, 404)

    def test_directory_membership_applies_to_read_edit_search_and_client_api(self):
        target = {'category': self.note['category']}
        response = self.save(target, 'restricted', [self.member('reader'), self.member('editor', 'edit')])
        self.assertEqual(response.status_code, 200, response.text)
        restricted_url = response.json()['url']
        login = self.anon.get(restricted_url, follow_redirects=False)
        self.assertEqual(login.status_code, 303)
        self.assertTrue(login.headers['location'].startswith('/login?next='))
        self.assertEqual(self.clients['reader'].get(restricted_url).status_code, 200)
        for name in ['reader', 'editor']:
            client = self.clients[name]
            for note in [self.note, self.child]:
                self.assertEqual(client.get(f"/n/{note['id']}").status_code, 200)
            self.assertEqual(client.get(f"/n/{self.other['id']}").status_code, 404)
            self.assertIn(f'href="/n/{self.note["id"]}#shared-title"', client.get('/search?q=Shared').text)
            with connect(self.db) as db:
                user = db.execute('SELECT * FROM users WHERE id=?', (self.ids[name],)).fetchone()
                where, params = visible_where(user)
                visible = [r[0] for r in db.execute(f'SELECT id FROM notes WHERE {where}', params)]
                self.assertIn(self.note['id'], visible)
                self.assertNotIn(self.other['id'], visible)
                self.assertTrue(can_view(db, self.note, user))
                self.assertEqual(can_edit(db, self.note, user), name == 'editor')
                bearer, _ = create_session(db, user['id'])
            api = self.anon.get(f"/api/client/v1/notes/{self.note['id']}", headers={'Authorization': 'Bearer ' + bearer})
            self.assertEqual(api.status_code, 200, api.text)
        self.assertEqual(self.clients['reader'].get(f"/n/{self.note['id']}/edit").status_code, 403)
        editor = self.clients['editor']
        edited = editor.post(f"/n/{self.child['id']}/edit", data={'csrf': self.csrf['editor'], 'base_sha': self.child['body_sha'], 'body': 'Edited child'}, follow_redirects=False)
        self.assertEqual(edited.status_code, 303)
        self.assertEqual(self.anon.get(f"/n/{self.note['id']}").status_code, 404)

    def test_revoke_link_assets_and_members_then_reenable_uses_new_token(self):
        target = {'category': self.note['category']}
        saved = self.save(target, members=[self.member('reader')]).json()
        old = saved['url']
        self.assertEqual(self.anon.get(old).status_code, 200)
        self.assertEqual(self.clients['reader'].get(f"/n/{self.note['id']}").status_code, 200)
        response = self.save(target, 'restricted', version=saved['version'])
        self.assertEqual(response.status_code, 200)
        for route in [old, f"{old}/n/{self.note['id']}", f"{old}/asset/{self.note['id']}?path=static/a.png"]:
            self.assertEqual(self.anon.get(route).status_code, 404)
        self.assertEqual(self.clients['reader'].get(f"/n/{self.note['id']}").status_code, 404)
        new = self.save(target).json()['url']
        self.assertNotEqual(old, new)
        self.assertEqual(self.anon.get(old).status_code, 404)

    def test_direct_and_overlapping_grants_survive_removal_of_one_share(self):
        self.save({'category': '私密'}, 'restricted', [self.member('reader')])
        self.save(audience='restricted', members=[self.member('editor', 'edit')])
        self.save({'category': self.note['category']}, 'restricted', [self.member('reader')])
        self.save({'category': self.note['category']}, 'restricted')
        self.assertEqual(self.clients['reader'].get(f"/n/{self.note['id']}").status_code, 200)
        self.assertEqual(self.clients['editor'].get(f"/n/{self.note['id']}/edit").status_code, 200)
        with connect(self.db) as db:
            db.execute('INSERT INTO grants VALUES(?,?,1,0)', (self.ids['reader'], self.other['id']))
        self.save({'category': '私密'}, 'restricted')
        self.assertEqual(self.clients['reader'].get(f"/n/{self.other['id']}").status_code, 200)
        self.assertEqual(self.clients['reader'].get(f"/n/{self.note['id']}").status_code, 404)

    def test_management_requires_admin_csrf_and_valid_editor_and_version(self):
        target = {'note_id': self.note['id']}
        for client in [self.anon, self.clients['reader'], self.clients['editor']]:
            self.assertEqual(client.get('/api/sharing', params=target).status_code, 403)
            self.assertEqual(client.post('/api/sharing', json={**target, 'version': 0}).status_code, 403)
        self.assertEqual(self.owner.post('/api/sharing', json={**target, 'version': 0}).status_code, 403)
        self.assertEqual(self.save(members=[self.member('reader', 'edit')]).status_code, 400)
        saved = self.save().json()
        self.assertEqual(self.save(audience='restricted', version=0).status_code, 409)
        self.assertEqual(self.state(**target)['version'], saved['version'])
        with connect(self.db) as db:
            db.execute('UPDATE users SET active=0 WHERE id=?', (self.ids['reader'],))
        self.assertEqual(self.save(members=[self.member('reader')]).status_code, 400)
        self.assertEqual(self.owner.get('/api/sharing?category=').status_code, 400)
        self.assertEqual(self.owner.get('/api/sharing?category=missing').status_code, 404)

    def test_restricted_link_denies_other_accounts_and_stays_revoked_after_reenable(self):
        saved = self.save(audience='restricted', members=[self.member('reader')]).json()
        url = saved['url']
        self.assertEqual(self.clients['editor'].get(url).status_code, 403)
        self.assertEqual(self.clients['reader'].get(url).status_code, 200)
        self.assertEqual(self.save(audience='restricted').status_code, 200)
        self.assertEqual(self.clients['reader'].get(url).status_code, 404)
        renewed = self.save(audience='restricted', members=[self.member('reader')]).json()
        self.assertNotEqual(renewed['url'], url)
        self.assertEqual(self.clients['reader'].get(url).status_code, 404)
        self.assertEqual(self.clients['reader'].get(renewed['url']).status_code, 200)

    def test_malformed_settings_are_rejected_without_changing_access(self):
        defaults = {'note_id': self.note['id'], 'version': 0, 'audience': 'link', 'members': []}
        for invalid in [{'audience': []}, {'category': []}, {'note_id': True}, {'version': False},
                        {'members': [{'user_id': self.ids['reader'], 'permission': []}]}, {'members': 'reader'}]:
            response = self.owner.post('/api/sharing', json={**defaults, **invalid},
                                       headers={'X-CSRF-Token': self.csrf['owner']})
            self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.state(note_id=self.note['id'])['version'], 0)

    def test_migration_and_category_pagination(self):
        url = self.save({'category': self.note['category']}).json()['url']
        create_app(self.db, self.content)
        self.assertEqual(self.anon.get(url).status_code, 200)
        with connect(self.db) as db:
            for index in range(51):
                add_note(db, f'Developer/page{index:02}.md', 'Page body', category=self.note['category'], visibility='private')
        first = self.anon.get(url)
        second = self.anon.get(url + '?page_number=2')
        self.assertEqual(first.text.count('class="note-row"'), 50)
        self.assertEqual(second.text.count('class="note-row"'), 3)
        self.assertEqual(self.anon.get(url + '?page_number=0').status_code, 400)


if __name__ == '__main__':
    unittest.main()
