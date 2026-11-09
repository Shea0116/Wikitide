"""Synthetic durable-worker tests, including actual process death and restart."""
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
# app has an eager module-level ASGI instance. Never import it against work/site.db.
_import_root = tempfile.TemporaryDirectory(prefix='agent-runtime-import-')
with patch.dict(os.environ, {'KB_DB_PATH': str(Path(_import_root.name) / 'import.db'),
                            'KB_CONTENT_ROOT': str(Path(_import_root.name) / 'content'),
                            'KB_EXPLORE_CONFIG': str(Path(_import_root.name) / 'absent-config.json'),
                            'DEEPSEEK_API_KEY': '', 'KB_EXPLORE_EMBEDDING_MODEL': ''}):
    from app import create_app, database, current_user, require_csrf
from fastapi.testclient import TestClient
from fastapi import HTTPException
from storage import connect, create_user, create_session, save_note
from features import add_note
from agent_dependencies import analyze_change, snapshot_note, source_changed
from agent_models import RunInput
from agent_runtime import LeaseLost
from agent_runtime_routes import register_runtime


class Provider:
    ready = True
    def __init__(self): self.calls = []; self.callback = None
    def answer(self, messages, thinking=False, *, max_tokens=None, usage=None):
        self.calls.append(messages)
        if self.callback: self.callback()
        if usage is not None: usage.update(prompt_tokens=100, completion_tokens=30)
        return '# React 闭包\n\n闭包保留词法作用域。[1]\n'


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name); self.db = self.root / 'site.db'
        self.config = {'KB_DB_PATH': str(self.db), 'KB_CONTENT_ROOT': str(self.root / 'content'),
                       'KB_EXPLORE_CONFIG': str(self.root / 'absent-config.json'), 'DEEPSEEK_API_KEY': '',
                       'KB_EXPLORE_EMBEDDING_MODEL': '', 'KB_AGENT_MEMORY_ENABLED': 'true', 'KB_AGENT_RUNTIME_ENABLED': 'true'}
        with patch.dict(os.environ, self.config): self.app = create_app(self.db, self.root / 'content')
        if not hasattr(self.app.state, 'agent_runtime'):
            register_runtime(self.app, self.db, {'database': database, 'current_user': current_user, 'require_csrf': require_csrf})
        self.app.state.agent_memory.enabled = True
        self.app.state.agent_runtime.enabled = self.app.state.agent_runtime_enabled = True
        self.service = self.app.state.agent_runtime
        self.provider = Provider(); self.app.state.explore_provider = self.provider
        self.clients, self.headers, self.tokens, self.ids = {}, {}, {}, {}
        with connect(self.db) as db:
            for name in ('alice', 'bob'):
                create_user(db, name, 'synthetic-runtime-password', 'reader')
                uid = db.execute('SELECT id FROM users WHERE username=?', (name,)).fetchone()[0]
                token, csrf = create_session(db, uid)
                self.clients[name] = TestClient(self.app); self.clients[name].cookies.set('kb_session', token)
                self.headers[name], self.tokens[name], self.ids[name] = {'X-CSRF-Token': csrf}, token, uid
            self.note = add_note(db, 'Frontend/Runtime合成.md', '# React 闭包\n\n闭包保留词法作用域。\n', category='Frontend', visibility='public')
        self.client = self.clients['alice']

    def tearDown(self):
        for client in self.clients.values(): client.close()
        self.temp.cleanup()

    def create(self, **changes):
        body = {'kind': 'writing', 'topic': 'React 闭包', 'note_id': self.note['id'], 'idempotency_key': 'runtime-synthetic-key'}
        body.update(changes)
        result = self.client.post('/api/agent/v1/runs', headers=self.headers['alice'], json=body)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def detail(self, rid): return self.client.get('/api/agent/v1/runs/' + rid).json()

    def test_success_idempotency_budget_and_event_privacy(self):
        run = self.create()
        self.assertTrue(self.service.run_once())
        detail = self.detail(run['id'])
        self.assertEqual(detail['status'], 'succeeded', detail)
        self.assertEqual(detail['budget']['used_tokens'], 260)
        self.assertEqual(detail['budget']['reserved_tokens'], 0)
        self.assertEqual(self.create()['id'], run['id'])
        self.assertFalse(self.service.run_once())
        self.assertEqual(len(self.provider.calls), 2)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM explore_drafts').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        events = self.client.get(f"/api/agent/v1/runs/{run['id']}/events").json()['events']
        self.assertTrue(events)
        self.assertTrue(all(set(e['payload']) <= {'status','step','draft_id','error_code','attempt'} for e in events))
        self.assertNotIn('词法', json.dumps(events))
        response = self.client.post('/api/agent/v1/runs', headers=self.headers['alice'], json={'kind':'writing','topic':'different','idempotency_key':'runtime-synthetic-key'})
        self.assertEqual(response.status_code, 409)

    def test_auth_owner_csrf_native_and_pagination(self):
        run = self.create(); path = '/api/agent/v1/runs/' + run['id']
        for suffix in ('', '/events'):
            self.assertEqual(self.clients['bob'].get(path + suffix).status_code, 404)
            with TestClient(self.app) as anon: self.assertEqual(anon.get(path + suffix).status_code, 401)
        self.assertEqual(self.client.post(path + '/cancel').status_code, 403)
        self.assertEqual(self.clients['bob'].post(path + '/cancel', headers=self.headers['bob']).status_code, 404)
        self.assertEqual(self.client.get('/api/client/v1/agent/runs/' + run['id']).status_code, 401)
        native = {'Authorization': 'Bearer ' + self.tokens['alice']}
        self.assertEqual(self.client.get('/api/client/v1/agent/runs/' + run['id'], headers=native).status_code, 200)
        self.assertEqual(self.client.get(path + '/events?limit=201').status_code, 400)
        self.assertEqual(self.client.get('/api/agent/v1/runs?limit=101').status_code, 400)

    def test_uncertain_call_requires_explicit_retry(self):
        self.provider.callback = lambda: (_ for _ in ()).throw(RuntimeError('PRIVATE-UPSTREAM'))
        run = self.create(max_tokens=64000)
        self.service.run_once()
        detail = self.detail(run['id']); self.assertEqual(detail['status'], 'waiting_user', detail)
        path = '/api/agent/v1/runs/' + run['id'] + '/resume'
        self.assertEqual(self.client.post(path, headers=self.headers['alice'], json={}).status_code, 409)
        self.assertNotIn('PRIVATE-UPSTREAM', json.dumps(detail))
        self.provider.callback = None
        self.assertEqual(self.client.post(path, headers=self.headers['alice'], json={'retry_uncertain': True}).status_code, 200)
        self.service.run_once(); self.assertEqual(self.detail(run['id'])['status'], 'succeeded')

    def test_cancel_competes_with_final_commit(self):
        run = self.create()
        def cancel(): self.client.post('/api/agent/v1/runs/' + run['id'] + '/cancel', headers=self.headers['alice'])
        self.provider.callback = cancel
        self.service.run_once()
        self.assertEqual(self.detail(run['id'])['status'], 'cancelled')
        with connect(self.db) as db: self.assertEqual(db.execute('SELECT count(*) FROM explore_drafts').fetchone()[0], 0)

    def test_revoked_source_stops_provider_output_and_hides_saved_result(self):
        run = self.create()
        def revoke():
            with connect(self.db) as db: db.execute("UPDATE notes SET visibility='private' WHERE id=?", (self.note['id'],))
        self.provider.callback = revoke
        self.service.run_once(); self.assertEqual(self.detail(run['id'])['status'], 'failed')
        with connect(self.db) as db: self.assertEqual(db.execute('SELECT count(*) FROM explore_drafts').fetchone()[0], 0)

    def test_budget_is_enforced_before_model_request(self):
        run = self.create(max_tokens=1000)
        self.service.run_once(); detail = self.detail(run['id'])
        self.assertEqual(detail['status'], 'failed', detail)
        self.assertEqual(detail['error_code'], 'budget_exceeded')
        self.assertEqual(self.provider.calls, [])

    def crash_process(self, mode):
        script = '''
import os, sys
sys.path.insert(0, sys.argv[1])
from app import app
from agent_runtime_routes import register_runtime
from app import database,current_user,require_csrf
if not hasattr(app.state,'agent_runtime'):
 register_runtime(app,os.environ['KB_DB_PATH'],{'database':database,'current_user':current_user,'require_csrf':require_csrf})
class Provider:
 ready=True
 def answer(self,messages,thinking=False,**kwargs):
  if sys.argv[2]=='uncertain': os._exit(23)
  return '# React 闭包\\n\\n闭包保留词法作用域。[1]'
app.state.explore_provider=Provider()
runtime=app.state.agent_runtime
original=runtime.start_step
def start(db,run,key,fingerprint):
 if sys.argv[2]=='safe' and key=='write': os._exit(24)
 return original(db,run,key,fingerprint)
runtime.start_step=start
runtime.run_once()
'''
        env = {**os.environ, **self.config}
        return subprocess.run([sys.executable, '-c', script, str(BASE), mode], env=env, capture_output=True, timeout=30)

    def test_real_worker_crash_after_completed_call_resumes_without_regeneration(self):
        run = self.create(max_tokens=64000)
        result = self.crash_process('safe'); self.assertEqual(result.returncode, 24, result.stderr.decode())
        with connect(self.db) as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM agent_run_steps WHERE run_id=? AND step_key='outline' AND status='succeeded'", (run['id'],)).fetchone())
            db.execute('UPDATE agent_runs SET lease_until=0 WHERE id=?', (run['id'],))
        self.service.run_once()
        self.assertEqual(self.detail(run['id'])['status'], 'succeeded')
        self.assertEqual(len(self.provider.calls), 1)

    def test_real_worker_crash_during_provider_waits_for_user(self):
        run = self.create(max_tokens=64000)
        result = self.crash_process('uncertain'); self.assertEqual(result.returncode, 23, result.stderr.decode())
        with connect(self.db) as db: db.execute('UPDATE agent_runs SET lease_until=0 WHERE id=?', (run['id'],))
        self.assertIsNone(self.service.claim())
        detail = self.detail(run['id']); self.assertEqual(detail['status'], 'waiting_user', detail)
        self.assertEqual(detail['error_code'], 'provider_uncertain')
        self.assertGreater(detail['budget']['used_tokens'], 0)

    def test_fencing_rejects_old_worker_and_account_deactivation(self):
        self.create(); old = self.service.claim()
        with connect(self.db) as db: db.execute('UPDATE agent_runs SET lease_until=0 WHERE id=?', (old['id'],))
        current = self.service.claim(); self.assertGreater(current['lease_generation'], old['lease_generation'])
        self.assertFalse(self.service.heartbeat(old))
        with connect(self.db) as db:
            with self.assertRaises(LeaseLost): self.service.check(db, old)
            db.execute('UPDATE users SET active=0 WHERE id=?', (self.ids['alice'],))
        self.service.execute(current)
        with connect(self.db) as db: self.assertEqual(db.execute('SELECT error_code FROM agent_runs WHERE id=?', (old['id'],)).fetchone()[0], 'account_inactive')

    def test_research_stops_after_two_rounds_without_new_evidence(self):
        run = self.create(kind='research', max_search_calls=3, max_tokens=64000)
        self.service.run_once(); detail = self.detail(run['id'])
        self.assertEqual(detail['status'], 'succeeded', detail)
        self.assertEqual(detail['budget']['search_calls'], 3)

    def test_impact_diff_is_proposal_and_public_scope_only(self):
        with connect(self.db) as db:
            snapshot_note(db, self.note['id'])
            original = dict(db.execute('SELECT * FROM notes WHERE id=?', (self.note['id'],)).fetchone())
            save_note(db, original, '# React 闭包\n\n闭包保留词法作用域，函数式更新读取最新状态。\n', 'local')
            latest_sha = db.execute('SELECT body_sha FROM notes WHERE id=?', (self.note['id'],)).fetchone()[0]
            analysis = analyze_change(db, self.ids['alice'], self.note['id'])
            self.assertEqual(analysis['classification'], 'review_required')
            self.assertIn('函数式更新', analysis['source_diff'])
        run = self.create(kind='impact', max_tokens=64000)
        self.service.run_once(); detail = self.detail(run['id'])
        self.assertEqual(detail['status'], 'succeeded', detail)
        self.assertIn('proposal_diff', detail['result'])
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT body_sha FROM notes WHERE id=?', (self.note['id'],)).fetchone()[0], latest_sha)
            db.execute("UPDATE notes SET visibility='private' WHERE id=?", (self.note['id'],))
            source_changed(db, self.note['id'])
            self.assertEqual(db.execute('SELECT count(*) FROM agent_note_versions WHERE note_id=?', (self.note['id'],)).fetchone()[0], 0)
            self.assertFalse(analyze_change(db, self.ids['alice'], self.note['id'])['available'])
        self.assertEqual(self.detail(run['id'])['result'], {})


if __name__ == '__main__': unittest.main()
