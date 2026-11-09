import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import create_app
from storage import connect, create_user, create_session


class NoteCreationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / 'content').mkdir()
        self.db = root / 'site.db'
        self.app = create_app(self.db, root / 'content')
        self.clients, self.csrf, self.ids = {}, {}, {}
        with connect(self.db) as db:
            for name, role in [('admin', 'admin'), ('reader', 'reader'), ('other', 'reader')]:
                create_user(db, name, 'creation-tests-password', role)
                identity = db.execute('SELECT id FROM users WHERE username=?', (name,)).fetchone()[0]
                token, nonce = create_session(db, identity)
                client = TestClient(self.app)
                client.cookies.set('kb_session', token)
                self.clients[name], self.csrf[name], self.ids[name] = client, nonce, identity
        self.anon = TestClient(self.app)

    def tearDown(self):
        self.app.state.import_executor.shutdown(wait=True, cancel_futures=True)
        for client in [*self.clients.values(), self.anon]:
            client.close()
        self.temp.cleanup()

    def create(self, who='reader', **values):
        data = {'csrf': self.csrf[who], 'title': '第一篇笔记', 'body': '# Hello\n\nMy first note', 'team_id': 'new', 'space_name': '我的笔记', **values}
        return self.clients[who].post('/create', data=data, follow_redirects=False)

    def team(self, who='reader'):
        response = self.clients[who].post('/api/teams', json={'name': who + ' space'}, headers={'X-CSRF-Token': self.csrf[who]})
        self.assertEqual(response.status_code, 201)
        return response.json()['id']

    def test_global_actions_are_available_to_logged_in_roles(self):
        self.assertNotIn('class="note-entry-actions"', self.anon.get('/').text)
        for who in ['reader', 'admin']:
            for route in ['/', '/browse', '/imports', '/teams']:
                body = self.clients[who].get(route).text
                self.assertIn('class="note-entry-actions"', body)
                self.assertIn('记录与导入', body)
                target = '/new' if who == 'admin' else '/create'
                self.assertIn(f'href="{target}"', body)
                self.assertIn('资料导入', body)
        redirect = self.anon.get('/create', follow_redirects=False)
        self.assertEqual(redirect.status_code, 303)
        self.assertEqual(redirect.headers['location'], '/login?next=/create')
        self.assertEqual(self.clients['admin'].get('/create', follow_redirects=False).headers['location'], '/new')

    def test_new_reader_can_create_first_note_without_global_permissions(self):
        page = self.clients['reader'].get('/create')
        self.assertEqual(page.status_code, 200)
        self.assertIn('新空间开始时只有你', page.text)
        created = self.create()
        self.assertEqual(created.status_code, 303, created.text)
        with connect(self.db) as db:
            doc = db.execute('SELECT * FROM team_documents').fetchone()
            membership = db.execute('SELECT * FROM team_members').fetchall()
            self.assertEqual(len(membership), 1)
            self.assertEqual((membership[0]['user_id'], membership[0]['role']), (self.ids['reader'], 'owner'))
            self.assertEqual(db.execute('SELECT role FROM users WHERE id=?', (self.ids['reader'],)).fetchone()[0], 'reader')
            self.assertEqual(db.execute('SELECT count(*) FROM notes').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM team_document_revisions').fetchone()[0], 1)
            endpoint = f'/api/teams/{doc["team_id"]}/documents/{doc["id"]}'
        self.assertEqual(self.clients['reader'].get(endpoint).status_code, 200)
        self.assertEqual(self.clients['other'].get(endpoint).status_code, 404)
        self.assertEqual(self.clients['admin'].get(endpoint).status_code, 404)
        self.assertEqual(self.clients['reader'].post('/new', data={'csrf': self.csrf['reader'], 'path': 'Frontend/unauthorized.md', 'body': 'no'}).status_code, 403)

    def test_scope_is_explicit_and_membership_is_rechecked(self):
        own = self.team()
        foreign = self.team('other')
        listing = self.clients['reader'].get('/create').text
        self.assertIn('reader space', listing)
        self.assertNotIn('other space', listing)
        self.assertEqual(self.create(team_id=str(foreign)).status_code, 404)
        with connect(self.db) as db:
            db.execute("UPDATE team_members SET role='reader' WHERE team_id=? AND user_id=?", (own, self.ids['reader']))
        denied = self.create(team_id=str(own), body='Retain this unsaved input')
        self.assertEqual(denied.status_code, 403)
        self.assertIn('Retain this unsaved input', denied.text)
        with connect(self.db) as db:
            db.execute("UPDATE team_members SET role='editor' WHERE team_id=? AND user_id=?", (own, self.ids['reader']))
        self.assertEqual(self.create(team_id=str(own)).status_code, 303)

    def test_csrf_validation_and_first_note_failure_do_not_leave_empty_spaces(self):
        self.assertEqual(self.create(csrf='wrong').status_code, 403)
        self.assertEqual(self.create(body='').status_code, 400)
        self.assertEqual(self.create(team_id='9' * 100).status_code, 400)
        with patch('note_creation.create_document_record', side_effect=HTTPException(409, '版本冲突')):
            failed = self.create(body='Keep this correction')
        self.assertEqual(failed.status_code, 409)
        self.assertIn('Keep this correction', failed.text)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM teams').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM team_documents').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM team_events').fetchone()[0], 0)
