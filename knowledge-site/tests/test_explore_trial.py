"""Guest quotas cover both generation paths without granting account access."""
import json
import re
import unittest
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException
from fastapi.testclient import TestClient

import test_explore as fixtures
from explore_trial import COOKIE, finish_trial, reserve_trial
from storage import connect


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


class TrialTests(unittest.TestCase):
    setUp = fixtures.ExploreTests.setUp
    tearDown = fixtures.ExploreTests.tearDown
    ask = fixtures.ExploreTests.ask

    def start(self, client=None):
        client = client or self.anon
        response = client.get('/explore')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertIn('免费体验剩余 3 / 3 次', response.text)
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        return {'X-CSRF-Token': re.search(r'data-csrf="([^"]+)"', response.text)[1]}

    def remaining(self):
        return self.anon.get('/api/explore/trial').json()['trial']['remaining']

    def test_three_successes_shared_across_sync_stream_followups_and_refresh(self):
        headers = self.start()
        result = self.anon.post('/api/explore/ask', headers=headers, json={'question': 'React 闭包'}).json()
        self.assertEqual(result['trial']['remaining'], 2)
        response = self.anon.post('/api/explore/ask/stream', headers=headers,
                                  json={'question': '举个例子', 'session_id': result['session_id']})
        self.assertEqual(events(response)[-1]['trial']['remaining'], 1)
        self.assertIn('免费体验剩余 1 / 3 次', self.anon.get('/explore').text)
        self.assertIn('免费体验剩余 1 / 3 次', self.anon.get('/explore/write').text)
        response = self.anon.post('/api/explore/ask', headers=headers, json={'question': 'React 闭包'})
        self.assertEqual(response.json()['trial']['remaining'], 0)
        for route in ('/api/explore/ask', '/api/explore/ask/stream'):
            rejected = self.anon.post(route, headers=headers, json={'question': 'React 闭包'})
            self.assertEqual(rejected.status_code, 429, rejected.text)
            self.assertIn('登录', rejected.json()['detail'])
        page = self.anon.get('/explore').text
        self.assertIn('三次免费体验已用完', page)
        self.assertRegex(page, r'<textarea id="explore-question"[^>]*disabled')
        self.assertEqual(self.anon.get('/api/explore/search?q=闭包').status_code, 200)
        self.assertEqual(self.ask().status_code, 200)

    def test_csrf_invalid_payload_and_upstream_failures_do_not_spend_trials(self):
        headers = self.start()
        self.assertEqual(self.anon.post('/api/explore/ask', json={'question': 'React 闭包'}).status_code, 403)
        self.assertEqual(self.anon.post('/api/explore/ask', headers=headers, json={'question': ''}).status_code, 400)
        self.provider.ready = False
        self.assertEqual(self.anon.post('/api/explore/ask', headers=headers, json={'question': 'React 闭包'}).status_code, 503)
        self.provider.ready = True
        def fail(): raise HTTPException(504, '测试超时')
        self.provider.callback = fail
        for route in ('/api/explore/ask', '/api/explore/ask/stream'):
            response = self.anon.post(route, headers=headers, json={'question': 'React 闭包'})
            self.assertTrue(response.status_code == 504 or events(response)[-1].get('status') == 504)
            self.assertEqual(self.remaining(), 3)
        self.provider.callback = None
        self.assertEqual(self.anon.post('/api/explore/ask', headers=headers, json={'question': 'React 闭包'}).status_code, 200)

    def test_guest_data_isolated_from_other_visitors_and_real_accounts(self):
        headers = self.start()
        result = self.anon.post('/api/explore/ask', headers=headers, json={'question': 'React 闭包'}).json()
        route = '/api/explore/sessions/' + result['session_id']
        with TestClient(self.app) as visitor:
            other_headers = self.start(visitor)
            self.assertEqual(visitor.get(route).status_code, 404)
            self.assertEqual(visitor.delete(route, headers=other_headers).status_code, 404)
            self.assertEqual(visitor.post('/api/explore/ask', headers=other_headers,
                              json={'question': 'React 闭包', 'session_id': result['session_id']}).status_code, 404)
        self.assertEqual(self.clients['alice'].get(route).status_code, 404)
        account_result = self.ask().json()
        self.assertEqual(self.anon.get('/api/explore/sessions/' + account_result['session_id']).status_code, 404)
        self.assertEqual(len(self.anon.get(route).json()['messages']), 2)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0], 2)

    def test_writing_and_qa_share_quota_but_edit_export_stay_available(self):
        headers = self.start()
        self.anon.post('/api/explore/ask', headers=headers, json={'question': 'React 闭包'})
        draft = self.anon.post('/api/explore/drafts', headers=headers, json={'topic': 'React 闭包'}).json()
        route = '/api/explore/drafts/' + draft['id']
        for action, remaining in (('outline', 1), ('write', 0)):
            response = self.anon.post(route + '/generate', headers=headers, json={'version': draft['version'], 'action': action})
            draft = events(response)[-1]
            self.assertEqual(draft['trial']['remaining'], remaining)
        self.assertEqual(self.anon.post(route + '/generate', headers=headers,
                         json={'version': draft['version'], 'action': 'revise', 'instruction': '详细说明'}).status_code, 429)
        self.assertEqual(self.anon.post('/api/explore/drafts', headers=headers, json={'topic': '新文稿'}).status_code, 429)
        self.assertEqual(self.anon.get(route + '/export').status_code, 200)
        response = self.anon.put(route, headers=headers, json={'version': draft['version'], 'title': '我的文稿',
                                                             'outline': draft['outline'], 'body': draft['body']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.clients['alice'].get(route).status_code, 404)
        self.assertEqual(self.anon.post(route + '/publish', headers=headers,
                         json={'version': response.json()['version'], 'path': 'Frontend/guest.md', 'confirmed': True}).status_code, 403)

    def test_failed_writing_refunds_reservation(self):
        headers = self.start()
        draft = self.anon.post('/api/explore/drafts', headers=headers, json={'topic': 'xyz-no-matching-note'}).json()
        response = self.anon.post('/api/explore/drafts/' + draft['id'] + '/generate', headers=headers,
                                  json={'version': 0, 'action': 'outline'})
        self.assertEqual(events(response)[-1]['status'], 422)
        self.assertEqual(self.remaining(), 3)

    def test_concurrent_reservations_cannot_exceed_three(self):
        self.start()
        import hashlib
        user = {'id': hashlib.sha256(self.anon.cookies.get(COOKIE).encode()).hexdigest(), 'guest': True}
        def reserve(_):
            try:
                with connect(self.db) as db:
                    db.execute('BEGIN IMMEDIATE')
                    return reserve_trial(db, user)
            except HTTPException as exc:
                return exc.status_code
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(reserve, range(4)))
        self.assertEqual(results.count(429), 1)
        self.assertEqual(self.remaining(), 0)
        with connect(self.db) as db:
            for lease in results:
                if lease != 429: finish_trial(db, lease)
        self.assertEqual(self.remaining(), 3)

    def test_restart_preserves_quota_and_guest_storage(self):
        headers = self.start()
        self.anon.post('/api/explore/ask', headers=headers, json={'question': 'React 闭包'})
        from explore_trial import initialize_trials
        with connect(self.db) as db: initialize_trials(db)
        self.assertEqual(self.remaining(), 2)

    def test_expired_reservations_cannot_commit_late_results(self):
        self.start()
        import hashlib
        user = {'id': hashlib.sha256(self.anon.cookies.get(COOKIE).encode()).hexdigest(), 'guest': True}
        with connect(self.db) as db:
            db.execute('BEGIN IMMEDIATE')
            expired = reserve_trial(db, user)
            db.execute('UPDATE explore_trial_leases SET expires_at=0 WHERE id=?', (expired,))
            active = [reserve_trial(db, user) for _ in range(3)]
        with self.assertRaises(HTTPException):
            with connect(self.db) as db: finish_trial(db, expired, success=True)
        with connect(self.db) as db:
            for lease in active: finish_trial(db, lease, success=True)
        self.assertEqual(self.remaining(), 0)
