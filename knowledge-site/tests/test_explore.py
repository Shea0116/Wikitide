"""End-to-end exploration behavior using disposable notes and no external API calls."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from app import create_app
from explore import DeepSeek, render_answer
from explore_search import ExploreIndex, split_article
from explore_web import WebSearch, public_web_url
from features import add_note
from storage import connect, create_session, create_user, save_note


class Provider:
    ready = True
    def __init__(self):
        self.calls = []
        self.content = "闭包保留词法作用域，可以使用函数式更新处理旧值。[1]"
        self.callback = None

    def answer(self, messages, thinking=False):
        self.calls.append((messages, thinking))
        if self.callback:
            self.callback()
        return self.content


class ExploreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / 'site.db'
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': '', 'KB_EXPLORE_EMBEDDING_MODEL': ''}):
            self.app = create_app(self.db, self.root / 'content')
        self.provider = Provider()
        self.app.state.explore_provider = self.provider
        self.anon = TestClient(self.app)
        self.clients, self.headers = {}, {}
        with connect(self.db) as db:
            for name in ('alice', 'bob'):
                create_user(db, name, 'explore-test-password', 'reader')
                user_id = db.execute('SELECT id FROM users WHERE username=?', (name,)).fetchone()[0]
                token, csrf = create_session(db, user_id)
                client = TestClient(self.app); client.cookies.set('kb_session', token)
                self.clients[name] = client
                self.headers[name] = {'X-CSRF-Token': csrf}
            self.react = add_note(db, 'Frontend/React闭包.md',
                '# React 闭包\n\n## 为什么拿到旧值？\n\n闭包保留词法作用域。函数式更新可以读取最新状态。\n\n'
                '```js\nsetCount(previous => previous + 1);\n```\n', category='Frontend / React', visibility='public')
            self.promise = add_note(db, 'Frontend/Promise.md',
                '# Promise\n\n## 异步处理\n\nPromise 和 async await 用于处理异步结果。', category='Frontend', visibility='public')
            self.secret = add_note(db, 'Developer/secret.md', 'PRIVATE-CONTENT-NEVER-SEND 闭包', category='私密', visibility='private')
            self.outside = add_note(db, 'Developer/public.md', 'OUTSIDE-CONTENT-NEVER-SEND 闭包', category='其他', visibility='public')
            self.hidden = add_note(db, 'Frontend/hidden.md', 'HIDDEN-CONTENT-NEVER-SEND 闭包', category='Frontend', visibility='private')
            self.asset = dict(add_note(db, 'Frontend/fake.md', 'STATIC-NEVER-INDEX 闭包', category='Frontend', visibility='public'))
            db.execute("UPDATE notes SET path='Frontend/static/fake.md' WHERE id=?", (self.asset['id'],))

    def tearDown(self):
        self.anon.close()
        for client in self.clients.values(): client.close()
        self.temp.cleanup()

    def ask(self, question='React 闭包', **kwargs):
        return self.clients['alice'].post('/api/explore/ask', headers=self.headers['alice'],
                                          json={'question': question, **kwargs})

    def test_search_and_article_reader_exclude_private_and_outside_scope(self):
        response = self.anon.get('/api/explore/search', params={'q': '闭包'})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual({r['note_id'] for r in payload['results']}, {self.react['id']})
        for excluded in (self.secret, self.outside, self.hidden, self.asset):
            self.assertEqual(self.anon.get(f"/api/explore/articles/{excluded['id']}").status_code, 404)
            self.assertNotIn(excluded['body'], response.text)
        article = self.anon.get(f"/api/explore/articles/{self.react['id']}").json()
        self.assertIn('setCount', article['html'])
        self.assertIn('知识求索', self.anon.get('/explore').text)
        self.assertIn('/explore?note_id=', self.anon.get(f"/n/{self.react['id']}").text)

    def test_cited_answers_followups_and_history_are_server_owned(self):
        first = self.ask(thinking=True)
        self.assertEqual(first.status_code, 200, first.text)
        result = first.json()
        self.assertIn('/n/', result['html'])
        self.assertTrue(result['sources'])
        self.assertTrue(self.provider.calls[0][1])
        serialized = json.dumps(self.provider.calls, ensure_ascii=False)
        for marker in ('PRIVATE-CONTENT', 'HIDDEN-CONTENT', 'OUTSIDE-CONTENT', 'STATIC-NEVER'):
            self.assertNotIn(marker, serialized)
        followup = self.ask('举个例子', session_id=result['session_id'])
        self.assertEqual(followup.status_code, 200, followup.text)
        self.assertTrue(any(m['role'] == 'assistant' for m in self.provider.calls[-1][0]))
        history = self.clients['alice'].get('/api/explore/sessions/' + result['session_id']).json()
        self.assertEqual(len(history['messages']), 4)
        self.assertEqual(history['messages'][0]['content'], 'React 闭包')

    def test_single_article_summary_and_scope_switch(self):
        result = self.ask('总结一下这篇文章', note_id=self.react['id'])
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(all(s['note_id'] == self.react['id'] for s in result.json()['sources']))
        wrong = self.ask('继续', session_id=result.json()['session_id'], note_id=self.promise['id'])
        self.assertEqual(wrong.status_code, 409)
        for excluded in (self.secret, self.hidden, self.outside):
            self.assertEqual(self.ask(note_id=excluded['id']).status_code, 404)

    def test_login_csrf_and_account_isolation(self):
        self.assertEqual(self.anon.post('/api/explore/ask', json={'question': '闭包'}).status_code, 401)
        self.assertEqual(self.clients['alice'].post('/api/explore/ask', json={'question': '闭包'}).status_code, 403)
        result = self.ask().json(); route = '/api/explore/sessions/' + result['session_id']
        self.assertEqual(self.clients['bob'].get(route).status_code, 404)
        self.assertEqual(self.clients['bob'].delete(route, headers=self.headers['bob']).status_code, 404)
        self.assertEqual(self.clients['alice'].delete(route).status_code, 403)
        self.assertEqual(self.clients['alice'].delete(route, headers=self.headers['alice']).status_code, 200)
        self.assertEqual(self.clients['alice'].get(route).status_code, 404)

    def test_incremental_edit_delete_and_visibility_changes(self):
        self.anon.get('/api/explore/search', params={'q': '闭包'})
        with connect(self.db) as db:
            current = db.execute('SELECT * FROM notes WHERE id=?', (self.react['id'],)).fetchone()
            save_note(db, current, '# 改写\n\n全新的唯一标记 new-content-marker', 'web')
            db.execute('UPDATE notes SET title=? WHERE id=?', ('改写', self.react['id']))
        self.assertFalse(self.anon.get('/api/explore/search', params={'q': '闭包'}).json()['results'])
        self.assertTrue(self.anon.get('/api/explore/search', params={'q': 'new-content-marker'}).json()['results'])
        with connect(self.db) as db:
            db.execute("UPDATE notes SET visibility='private' WHERE id=?", (self.react['id'],))
        self.assertFalse(self.anon.get('/api/explore/search', params={'q': 'new-content-marker'}).json()['results'])
        with connect(self.db) as db:
            self.assertFalse(db.execute('SELECT 1 FROM explore_chunks WHERE note_id=?', (self.react['id'],)).fetchone())
            db.execute('DELETE FROM notes WHERE id=?', (self.promise['id'],))
        self.assertFalse(self.anon.get('/api/explore/search', params={'q': 'Promise'}).json()['results'])

    def test_old_answers_hidden_after_source_becomes_private(self):
        result = self.ask().json()
        with connect(self.db) as db:
            db.execute("UPDATE notes SET visibility='private' WHERE id=?", (self.react['id'],))
        history = self.clients['alice'].get('/api/explore/sessions/' + result['session_id']).json()
        answer = history['messages'][1]
        self.assertEqual(answer['sources'], [])
        self.assertNotIn('函数式更新', answer['content'])
        self.assertIn('重新提问', answer['content'])

    def test_source_change_during_generation_discards_answer(self):
        def revoke():
            with connect(self.db) as db:
                db.execute("UPDATE notes SET visibility='private' WHERE id=?", (self.react['id'],))
        self.provider.callback = revoke
        result = self.ask()
        self.assertEqual(result.status_code, 409, result.text)
        self.assertNotIn('函数式更新', result.text)
        self.assertEqual(self.clients['alice'].get('/api/explore/sessions').json()['sessions'], [])

    def test_unavailable_api_keeps_search_and_failed_followup_history(self):
        first = self.ask().json()
        def fail(): raise HTTPException(504, '回答等待超时')
        self.provider.callback = fail
        failed = self.ask('再解释一下', session_id=first['session_id'])
        self.assertEqual(failed.status_code, 504)
        self.provider.callback = None
        retry = self.ask('再解释一下', session_id=first['session_id'])
        self.assertEqual(retry.status_code, 200, retry.text)
        self.provider.ready = False
        self.assertEqual(self.ask().status_code, 503)
        self.assertEqual(self.anon.get('/api/explore/search', params={'q': '闭包'}).status_code, 200)

    def test_insufficient_evidence_and_invalid_citations(self):
        result = self.ask('xyznotinanydocument')
        self.assertEqual(result.status_code, 200)
        self.assertFalse(self.provider.calls)
        self.assertIn('资料不足', result.json()['answer'])
        self.provider.content = '凭空编造的结论。[99]'
        result = self.ask().json()
        self.assertEqual(result['sources'], [])
        self.assertNotIn('凭空编造', result['answer'])

    def test_busy_sessions_rate_limits_and_payload_validation(self):
        first = self.ask().json()
        with connect(self.db) as db:
            db.execute('UPDATE explore_sessions SET busy_until=9999999999 WHERE id=?', (first['session_id'],))
        self.assertEqual(self.ask(session_id=first['session_id']).status_code, 409)
        for payload in ({'question': ''}, {'question': ' '}, {'question': 'x' * 1001}, {'question': '闭包', 'history': []}, {'question': '闭包', 'note_id': True}, []):
            response = self.clients['alice'].post('/api/explore/ask', json=payload, headers=self.headers['alice'])
            self.assertEqual(response.status_code, 400, response.text)
        import time
        with connect(self.db) as db:
            user_id = db.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
            db.executemany('INSERT INTO explore_requests VALUES(?,?)', [(user_id, int(time.time()))] * 12)
        self.assertEqual(self.ask().status_code, 429)

    def test_hybrid_semantic_retrieval_without_keyword_overlap(self):
        class Embeddings:
            available = False  # no background work, deterministically injected vectors
            key = 'test-model-v1'
            def encode(self, texts, query=False):
                return [[1., 0.] if ('闭包' in t or '陈旧状态' in t) else [0., 1.] for t in texts]
        index = ExploreIndex(Embeddings())
        with connect(self.db) as db: index.build_vectors(db)
        self.app.state.explore_index = index
        result = self.anon.get('/api/explore/search', params={'q': '陈旧状态'}).json()
        self.assertEqual(result['mode'], 'hybrid')
        self.assertEqual(result['results'][0]['note_id'], self.react['id'])


class StreamAndWebTests(unittest.TestCase):
    setUp = ExploreTests.setUp
    tearDown = ExploreTests.tearDown
    ask = ExploreTests.ask

    def stream(self, question='React 闭包', **kwargs):
        response = self.clients['alice'].post('/api/explore/ask/stream', headers=self.headers['alice'],
                                               json={'question': question, **kwargs})
        events = []
        for frame in response.text.split('\n\n'):
            lines = frame.splitlines()
            kind = next((line[7:] for line in lines if line.startswith('event: ')), '')
            data = next((line[6:] for line in lines if line.startswith('data: ')), '')
            if kind and data: events.append((kind, json.loads(data)))
        return response, events

    def test_stream_deltas_finalize_citations_and_save_followup(self):
        class Streaming(Provider):
            def stream_answer(self, messages, thinking=False):
                self.calls.append((messages, thinking))
                yield '闭包保留'
                yield '词法作用域。[1]'
        self.app.state.explore_provider = Streaming()
        response, events = self.stream()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['x-accel-buffering'], 'no')
        self.assertEqual([data['text'] for kind, data in events if kind == 'delta'], ['闭包保留', '词法作用域。[1]'])
        self.assertEqual(events[-1][0], 'done')
        result = events[-1][1]
        self.assertIn('/n/', result['html'])
        _, followup = self.stream('举个例子', session_id=result['session_id'])
        self.assertEqual(followup[-1][0], 'done')
        history = self.clients['alice'].get('/api/explore/sessions/' + result['session_id']).json()
        self.assertEqual(len(history['messages']), 4)

    def test_stream_rejects_revoked_sources_and_does_not_save_partial_answer(self):
        def revoke():
            with connect(self.db) as db: db.execute("UPDATE notes SET visibility='private' WHERE id=?", (self.react['id'],))
        class Streaming(Provider):
            def stream_answer(self, messages, thinking=False):
                yield '开始回答'
                revoke()
                yield '禁止继续发送的内容。[1]'
        self.app.state.explore_provider = Streaming()
        response, events = self.stream()
        self.assertNotIn('禁止继续发送', response.text)
        self.assertEqual(events[-1][0], 'error')
        self.assertEqual(events[-1][1]['status'], 409)
        self.assertEqual(self.clients['alice'].get('/api/explore/sessions').json()['sessions'], [])

    def test_stream_validates_login_csrf_and_question_before_opening_stream(self):
        self.assertEqual(self.anon.post('/api/explore/ask/stream', json={'question': '闭包'}).status_code, 401)
        self.assertEqual(self.clients['alice'].post('/api/explore/ask/stream', json={'question': '闭包'}).status_code, 403)
        response = self.clients['alice'].post('/api/explore/ask/stream', json={'question': '闭包', 'web_search': 'yes'}, headers=self.headers['alice'])
        self.assertEqual(response.status_code, 400)

    def test_online_only_answer_keeps_web_citations_in_history(self):
        class Search:
            ready = True
            def search(self, query):
                self.query = query
                return [dict(kind='web', title='官方文档', url='https://react.dev/reference/react',
                             section='react.dev', excerpt='测试资料摘要', retrieved_at='2026-10-18T00:00:00+00:00')]
        search = Search(); self.app.state.explore_web = search
        self.provider.content = '依据联网摘要回答。[1]'
        response = self.ask('独有联网问题xyz', web_search=True)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(search.query, '独有联网问题xyz')
        self.assertEqual(result['sources'][0]['kind'], 'web')
        self.assertIn('https://react.dev/reference/react', result['html'])
        self.assertIn('target="_blank"', result['html'])
        history = self.clients['alice'].get('/api/explore/sessions/' + result['session_id']).json()
        self.assertIn('依据联网', history['messages'][1]['content'])
        self.assertEqual(history['messages'][1]['sources'][0]['kind'], 'web')

    def test_web_failure_is_explicit_and_offline_questions_do_not_use_search(self):
        class Search:
            ready = True
            def search(self, query): raise HTTPException(503, '联网搜索失败')
        self.app.state.explore_web = Search()
        self.assertEqual(self.ask(web_search=True).status_code, 503)
        self.assertFalse(self.provider.calls)
        self.assertEqual(self.ask(web_search=False).status_code, 200)


class WebProviderTests(unittest.TestCase):
    def test_search_filters_unsafe_urls_deduplicates_and_caches_without_sending_notes(self):
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={'results': [
                {'title': '文档', 'url': 'https://react.dev/reference', 'content': '摘要'},
                {'title': '重复', 'url': 'https://react.dev/reference', 'content': '重复'},
                {'title': '本机', 'url': 'http://127.0.0.1/private', 'content': '不允许'},
                {'title': '脚本', 'url': 'javascript:alert(1)', 'content': '不允许'},
            ]})
        with patch.dict(os.environ, {'TAVILY_API_KEY': 'synthetic-tavily-key'}): search = WebSearch()
        client = httpx.Client(transport=httpx.MockTransport(handler))
        with patch('explore_web.httpx.Client', return_value=client):
            first = search.search('React 文档')
            second = search.search('React 文档')
        self.assertEqual(len(first), 1)
        self.assertEqual(second, first)
        self.assertEqual(len(seen), 1)
        payload = json.loads(seen[0].content)
        self.assertEqual(payload['query'], 'React 文档')
        self.assertNotIn('synthetic-tavily-key', seen[0].content.decode())
        self.assertFalse(payload['include_raw_content'])
        for url in ['http://localhost/', 'http://169.254.169.254/', 'http://[::1]/', 'https://example.com:8080/']:
            self.assertFalse(public_web_url(url), url)

    def test_provider_stream_never_exposes_reasoning_and_rejects_truncation(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'synthetic-key'}): provider = DeepSeek()
        for finish in ['stop', 'length', None]:
            chunks = [
                {'choices': [{'delta': {'reasoning_content': 'PRIVATE-REASONING'}}]},
                {'choices': [{'delta': {'content': '正文 [1]'}, 'finish_reason': finish}]},
            ]
            body = ''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n'
            client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)))
            with patch('explore.httpx.Client', return_value=client):
                if finish == 'stop':
                    self.assertEqual(''.join(provider.stream_answer([])), '正文 [1]')
                else:
                    with self.assertRaises(HTTPException): list(provider.stream_answer([]))


class RenderingAndProviderTests(unittest.TestCase):
    def test_web_configuration_preserves_deepseek_and_private_permissions(self):
        import contextlib
        import importlib.util
        import io
        import stat
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root/'explore-config.json'
            config.write_text(json.dumps({'api_key': 'synthetic-deepseek', 'model': 'custom-model'}))
            spec = importlib.util.spec_from_file_location('configure_web_fixture', BASE/'scripts/configure_explore.py')
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            output = io.StringIO()
            with patch.dict(os.environ, {'KB_EXPLORE_CONFIG': str(config)}), \
                    patch.object(module.getpass, 'getpass', return_value='synthetic-search-key'), contextlib.redirect_stdout(output):
                module.main(['--web-search'])
            saved = json.loads(config.read_text())
            self.assertEqual(saved['api_key'], 'synthetic-deepseek')
            self.assertEqual(saved['model'], 'custom-model')
            self.assertEqual(saved['tavily_api_key'], 'synthetic-search-key')
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
            self.assertNotIn('synthetic-search-key', output.getvalue())

    def test_standalone_setup_outside_documents_saves_private_config(self):
        import contextlib
        import importlib.util
        import io
        import stat
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'setup.py'
            script.write_text((BASE / 'scripts/configure_explore.py').read_text())
            spec = importlib.util.spec_from_file_location('explore_setup_fixture', script)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            output = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), patch.object(Path, 'home', return_value=root), \
                    patch.object(module.getpass, 'getpass', return_value='synthetic-test-key'), contextlib.redirect_stdout(output):
                module.main()
                config = root / '.config/wikitide/explore-config.json'
                self.assertTrue(config.is_file())
                self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
                provider = DeepSeek()
                self.assertTrue(provider.ready)
                self.assertEqual(provider.key, 'synthetic-test-key')
            self.assertNotIn('synthetic-test-key', output.getvalue())

    def test_environment_config_overrides_user_config_and_legacy_remains_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / 'site/work/explore-config.json'
            legacy.parent.mkdir(parents=True)
            legacy.write_text(json.dumps({'api_key': 'legacy-test-key'}))
            with patch.dict(os.environ, {}, clear=True), patch.object(Path, 'home', return_value=root), \
                    patch('explore.__file__', str(root / 'site/explore.py')):
                self.assertEqual(DeepSeek().key, 'legacy-test-key')
                user_config = root / '.config/wikitide/explore-config.json'
                user_config.parent.mkdir(parents=True)
                user_config.write_text(json.dumps({'api_key': 'user-test-key'}))
                self.assertEqual(DeepSeek().key, 'user-test-key')
                with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'environment-test-key'}):
                    self.assertEqual(DeepSeek().key, 'environment-test-key')
                with patch.dict(os.environ, {'KB_EXPLORE_CONFIG': str(root / 'missing.json')}):
                    self.assertFalse(DeepSeek().ready)

    def test_code_fences_and_colored_duplicate_headings_remain_intact(self):
        body = '## <span style="color:red">问题？</span>\n\n' + ('说明内容。\n\n' * 160) + '```js\n' + ('const x = 1;\n' * 160) + '```\n\n## 问题？\n\n最后一段。'
        chunks = split_article(body)
        code = [c for c in chunks if 'const x' in c['text']]
        self.assertEqual(len(code), 1)
        self.assertIn('```js', code[0]['text']); self.assertTrue(code[0]['text'].endswith('```'))
        self.assertEqual(chunks[-1]['anchor'], '问题-2')
        self.assertTrue(all(c['start_line'] <= c['end_line'] for c in chunks))

    def test_rendering_blocks_xss_external_links_and_fake_citations(self):
        source = dict(number=1, url='/n/7#section', title='Article', section='Section')
        rendered, used = render_answer('<script>alert(1)</script>\n\n结论 [1] [99]\n\n[恶意](javascript:alert(1)) ![追踪](https://evil.test/x)\n\n`code [1]`', [source])
        self.assertNotIn('<script>', rendered)
        self.assertNotIn('src="https://evil', rendered)
        self.assertNotIn('href="javascript:', rendered)
        self.assertIn('href="/n/7#section"', rendered)
        self.assertIn('[来源不可用]', rendered)
        self.assertEqual(used, {1})
        self.assertEqual(render_answer('```js\n// [1]\n```\n\n`[1]`', [source])[1], set())

    def test_deepseek_request_and_errors_do_not_disclose_upstream_details(self):
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '回答 [1]', 'reasoning_content': 'not returned'}}]})
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key', 'DEEPSEEK_MODEL': 'configured-model'}):
            provider = DeepSeek()
        client = httpx.Client(transport=httpx.MockTransport(handler))
        with patch('explore.httpx.Client', return_value=client):
            self.assertEqual(provider.answer([{'role': 'user', 'content': 'question'}], True), '回答 [1]')
        self.assertEqual(str(seen[0].url), 'https://api.deepseek.com/chat/completions')
        payload = json.loads(seen[0].content)
        self.assertEqual(payload['model'], 'configured-model')
        self.assertEqual(payload['thinking'], {'type': 'enabled'})
        for status in (401, 402, 429, 500):
            client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status, text='SECRET-UPSTREAM-DETAIL')))
            with patch('explore.httpx.Client', return_value=client), self.assertRaises(HTTPException) as caught:
                provider.answer([])
            self.assertNotIn('SECRET-UPSTREAM', caught.exception.detail)


if __name__ == '__main__':
    unittest.main()
