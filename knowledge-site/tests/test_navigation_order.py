"""Ordering persists per account, respects access, and precedes pagination."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from fastapi.testclient import TestClient
from app import create_app, categories
from storage import connect, create_session, digest
from navigation_order import sort_article_tree


class NavigationOrderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.content = self.root / 'content'
        self.content.mkdir()
        self.db_path = self.root / 'site.db'
        self.app = create_app(self.db_path, self.content)
        self.clients = {}
        self.csrf = {}
        self.users = {}
        with connect(self.db_path) as db:
            for user_id, name, role in [(1, 'owner', 'admin'), (2, 'reader', 'reader')]:
                db.execute('INSERT INTO users(id,username,password_hash,role,created_at) VALUES(?,?,?,?,0)',
                           (user_id, name, 'unused-test-hash', role))
                token, csrf = create_session(db, user_id)
                client = TestClient(self.app)
                client.cookies.set('kb_session', token)
                self.clients[name] = client
                self.csrf[name] = csrf
                self.users[name] = user_id
            self.notes = []
            for index in range(55):
                self.notes.append(self.add_note(db, f'Article {index:02d}', 'Frontend'))
            self.nested = self.add_note(db, 'Nested', 'Frontend / React')
            self.other = self.add_note(db, 'Other', 'Backend')
            self.hidden = self.add_note(db, 'Hidden', '私人笔记 / Secret', 'private')
        self.owner = self.clients['owner']
        self.reader = self.clients['reader']
        self.anon = TestClient(self.app)

    def tearDown(self):
        for client in [self.anon, *self.clients.values()]:
            client.close()
        self.temp.cleanup()

    def add_note(self, db, title, category, visibility='public'):
        body = '# Test body'
        cursor = db.execute('''INSERT INTO notes(path,title,category,visibility,body,body_sha,updated_at)
                               VALUES(?,?,?,?,?,?,0)''',
                            (f'{category}/{title}.md', title, category, visibility, body, digest(body)))
        return str(cursor.lastrowid)

    def snapshot(self, client=None):
        return (client or self.owner).get('/api/navigation-order').json()['groups']

    def group(self, kind, parent, client=None):
        return next(group for group in self.snapshot(client) if group['kind'] == kind and group['parent'] == parent)

    def save(self, group, client=None, csrf=None):
        return (client or self.owner).put('/api/navigation-order', json=group,
                                         headers={'X-CSRF-Token': csrf or self.csrf['owner']})

    def test_persists_per_account_and_sorts_before_pagination(self):
        group = self.group('note', 'Frontend')
        group['keys'].reverse()
        self.assertEqual(self.save(group).status_code, 200)
        self.assertEqual(self.group('note', 'Frontend')['keys'], group['keys'])
        self.assertEqual(self.group('note', 'Frontend', self.reader)['keys'], self.notes)
        page = self.owner.get('/browse?category=Frontend').text
        # Article 54 used to be on page two; saved order places it first.
        self.assertLess(page.index('Article 54'), page.index('Article 53'))
        self.assertNotIn('Article 00', page)
        another_app = create_app(self.db_path, self.content)
        another_device = TestClient(another_app)
        another_device.cookies.update(self.owner.cookies)
        try:
            self.assertEqual(self.group('note', 'Frontend', another_device)['keys'], group['keys'])
        finally:
            another_device.close()

    def test_category_order_shared_by_tree_and_note_listing(self):
        group = self.group('category', '')
        group['keys'] = ['Frontend', 'Backend', '私人笔记']
        self.assertEqual(self.save(group).status_code, 200)
        with connect(self.db_path) as db:
            user = db.execute('SELECT * FROM users WHERE id=1').fetchone()
            self.assertEqual([item['path'] for item in categories(db, user)], group['keys'])
        page = self.owner.get('/browse').text
        self.assertIn('data-order-root', page)
        self.assertIn('data-order-kind="category"', page)
        self.assertIn('data-order-csrf=', page)
        self.assertIn('Article 00', page)
        self.assertNotIn('<strong>Other</strong>', page)

    def test_anonymous_csrf_and_private_access(self):
        self.assertEqual(self.anon.get('/api/navigation-order').status_code, 401)
        group = self.group('note', 'Frontend')
        self.assertEqual(self.anon.put('/api/navigation-order', json=group).status_code, 401)
        self.assertEqual(self.owner.put('/api/navigation-order', json=group).status_code, 403)
        self.assertNotIn('Secret', json.dumps(self.snapshot(self.reader)))
        self.assertEqual(self.save({'kind': 'note', 'parent': '私人笔记 / Secret', 'keys': [self.hidden], 'version': 0},
                                   self.reader, self.csrf['reader']).status_code, 400)

    def test_duplicate_partial_and_cross_parent_orders_rejected(self):
        group = self.group('note', 'Frontend')
        for keys in [group['keys'][:-1], [self.other] + group['keys'][1:], [group['keys'][0]] * len(group['keys'])]:
            self.assertEqual(self.save({**group, 'keys': keys}).status_code, 400)
        self.assertEqual(self.group('note', 'Frontend')['version'], 0)

    def test_stale_device_returns_latest_without_overwriting(self):
        group = self.group('note', 'Frontend')
        changed = {**group, 'keys': list(reversed(group['keys']))}
        first = self.save(changed)
        self.assertEqual(first.status_code, 200)
        stale = self.save(group)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()['keys'], changed['keys'])
        self.assertEqual(stale.json()['version'], 1)
        unrelated = self.group('category', '')
        self.assertEqual(self.save(unrelated).status_code, 200)

    def test_new_and_revoked_items_are_revalidated(self):
        group = self.group('note', 'Frontend', self.reader)
        group['keys'].reverse()
        self.assertEqual(self.save(group, self.reader, self.csrf['reader']).status_code, 200)
        with connect(self.db_path) as db:
            db.execute('UPDATE notes SET visibility=? WHERE id=?', ('private', self.notes[-1]))
            added = self.add_note(db, 'New note', 'Frontend')
        latest = self.group('note', 'Frontend', self.reader)
        self.assertNotIn(self.notes[-1], latest['keys'])
        self.assertEqual(latest['keys'][-1], added)
        self.assertEqual(self.save({**group, 'version': 1}, self.reader, self.csrf['reader']).status_code, 400)

    def test_article_tree_uses_same_note_order(self):
        group = self.group('note', 'Frontend')
        group['keys'].reverse()
        self.save(group)
        with connect(self.db_path) as db:
            user = db.execute('SELECT * FROM users WHERE id=1').fetchone()
            rows = db.execute("SELECT id,title,category FROM notes WHERE category='Frontend' ORDER BY title").fetchall()
            tree = {'path': 'Frontend', 'notes': list(rows), 'children': []}
            sort_article_tree(db, user, tree)
            self.assertEqual(str(tree['notes'][0]['id']), self.notes[-1])
        # The live reading template exposes the same group keys to drag controls.
        response = self.owner.get(f'/n/{self.notes[0]}')
        self.assertEqual(response.status_code, 200)
        if (BASE / 'templates/article_navigation.html').exists():
            self.assertIn('data-order-kind="note"', response.text)


if __name__ == '__main__':
    unittest.main()
