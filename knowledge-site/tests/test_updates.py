import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import create_app
from storage import connect, create_user, create_session
from updates import load_releases, validate_upgrade


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name); self.path = self.root/'site.db'
        self.manifest = self.root/'release-notes.json'
        self.release = {'id':'20261023.1','sequence':1,'date':'2026-10-23','title':'测试更新','summary':'简短摘要','highlights':['标题折叠','<script>plain text</script>']}
        self.manifest.write_text(json.dumps({'releases':[self.release]}))
        self.patcher = patch('updates.RELEASES_FILE', self.manifest); self.patcher.start()
        self.app = create_app(self.path, self.root/'content')
        self.clients = {}; self.csrf = {}
        with connect(self.path) as db:
            for name in ['one','two']:
                create_user(db,name,'test-updates-123','reader')
                uid = db.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()[0]
                token, csrf = create_session(db,uid)
                client = TestClient(self.app); client.cookies.set('kb_session',token)
                self.clients[name] = client; self.csrf[name] = csrf
        self.anon = TestClient(self.app)

    def tearDown(self):
        for client in [self.anon,*self.clients.values()]: client.close()
        self.patcher.stop(); self.temp.cleanup()

    def seen(self, name, release_id='20261023.1'):
        return self.clients[name].post('/api/updates/seen',json={'release_id':release_id},headers={'X-CSRF-Token':self.csrf[name]})

    def test_public_notes_escape_markup_and_never_mark_read_on_get(self):
        page = self.anon.get('/updates')
        self.assertEqual(page.status_code,200)
        self.assertIn('&lt;script&gt;plain text',page.text)
        self.assertNotIn('<script>plain text',page.text)
        response = self.clients['one'].get('/api/updates')
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(set(response.headers['vary'].split(', ')),{'Cookie','Accept-Language'})
        self.assertTrue(response.json()['unread'])
        with connect(self.path) as db: self.assertEqual(db.execute('SELECT count(*) FROM release_reads').fetchone()[0],0)

    def test_account_isolation_and_read_receipt_follows_another_session(self):
        self.assertEqual(self.seen('one').status_code,200)
        self.assertFalse(self.clients['one'].get('/api/updates').json()['unread'])
        self.assertTrue(self.clients['two'].get('/api/updates').json()['unread'])
        with connect(self.path) as db:
            uid = db.execute("SELECT id FROM users WHERE username='one'").fetchone()[0]
            token,_ = create_session(db,uid)
        with TestClient(self.app) as other:
            other.cookies.set('kb_session',token)
            self.assertFalse(other.get('/api/updates').json()['unread'])

    def test_ack_requires_login_csrf_and_current_version(self):
        self.assertEqual(self.anon.post('/api/updates/seen',json={'release_id':'20261023.1'}).status_code,401)
        self.assertEqual(self.clients['one'].post('/api/updates/seen',json={'release_id':'20261023.1'}).status_code,403)
        self.assertEqual(self.seen('one','20990101.9').status_code,409)
        self.assertEqual(self.clients['one'].post('/api/updates/seen',content='x'*1025).status_code,413)
        self.assertEqual(self.clients['one'].post('/api/updates/seen',json=[]).status_code,400)
        self.assertTrue(self.clients['one'].get('/api/updates').json()['unread'])

    def test_new_release_is_unread_and_old_pages_cannot_ack_it(self):
        self.seen('one')
        newer = {**self.release,'id':'20261023.2','sequence':2,'title':'下一次更新'}
        self.manifest.write_text(json.dumps({'releases':[newer,self.release]}))
        app = create_app(self.path,self.root/'content')
        with TestClient(app) as client:
            client.cookies.update(self.clients['one'].cookies)
            self.assertTrue(client.get('/api/updates').json()['unread'])
            self.assertEqual(client.post('/api/updates/seen',json={'release_id':self.release['id']},headers={'X-CSRF-Token':self.csrf['one']}).status_code,409)

    def test_rollback_does_not_lower_existing_receipt(self):
        with connect(self.path) as db:
            uid = db.execute("SELECT id FROM users WHERE username='one'").fetchone()[0]
            db.execute('INSERT INTO release_reads VALUES(?,?,?,0)',(uid,5,'20261024.5'))
        self.assertEqual(self.seen('one').status_code,200)
        self.assertFalse(self.clients['one'].get('/api/updates').json()['unread'])
        with connect(self.path) as db: self.assertEqual(db.execute('SELECT sequence FROM release_reads').fetchone()[0],5)

    def test_manifest_and_upgrade_guard_reject_stale_or_rewritten_notes(self):
        current = load_releases(self.manifest)
        with self.assertRaises(ValueError): validate_upgrade(current,current)
        next_release = {**self.release,'id':'20261023.2','sequence':2}
        validate_upgrade([next_release,*current],current)
        changed = copy.deepcopy(current); changed[0]['title'] = 'rewritten'
        with self.assertRaises(ValueError): validate_upgrade([next_release,*changed],current)
        for value in [[],[self.release,self.release],[{**self.release,'sequence':True}],[{**self.release,'date':4}]]:
            self.manifest.write_text(json.dumps({'releases':value}))
            with self.assertRaises(ValueError): load_releases(self.manifest)
