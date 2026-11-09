import concurrent.futures
import json
import html
from pathlib import Path
import sys
import tempfile
import re
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import create_app, TEMPLATES
from i18n import CATALOG, LOCALE, LocalizedEnvironment, ui
from storage import connect, create_user, create_session, digest


class InterfaceLanguages(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root/'site.db'
        self.app = create_app(self.db, self.root/'content')
        self.client = TestClient(self.app)
        self.body = '# 登录\n\n搜索笔记\n\n```js\nconst title = "我的阅读";\n```'
        with connect(self.db) as db:
            db.execute('INSERT INTO notes(path,title,summary,category,visibility,body,body_sha,updated_at) VALUES(?,?,?,?,?,?,?,0)',
                       ('Frontend/source.md','登录','搜索笔记','Frontend','public',self.body,digest(self.body)))
            self.note = db.execute('SELECT id FROM notes').fetchone()[0]
            create_user(db, 'reader', 'language-test-password', 'reader')
            uid = db.execute('SELECT id FROM users').fetchone()[0]
            self.token, _ = create_session(db, uid)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_english_pages_and_source_content_are_independent(self):
        response = self.client.get(f'/n/{self.note}', headers={'Accept-Language':'en-US,en;q=0.9'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('<html lang="en">', response.text)
        self.assertIn('Search notes', response.text)
        self.assertIn('All Notes', response.text)
        self.assertIn('<h1>登录</h1>', response.text)
        self.assertIn('<p class="article-summary">搜索笔记</p>', response.text)
        code = re.search(r'<pre><code[^>]*>(.*?)</code></pre>', response.text, re.S).group(1)
        self.assertIn('const title = "我的阅读";', html.unescape(re.sub(r'<[^>]+>', '', code)))
        self.assertIn('我的阅读', response.text)
        self.assertEqual(response.headers['Content-Language'], 'en')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertIn('Accept-Language', response.headers['Vary'])
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT body FROM notes WHERE id=?',(self.note,)).fetchone()[0], self.body)

    def test_language_cookie_survives_navigation_and_switches_back(self):
        target = '/search?q=React&scope=public&page_number=2#results'
        response = self.client.get('/language/en', params={'next':target}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['location'], target)
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.assertIn('SameSite=lax', response.headers['set-cookie'])
        self.assertIn('<html lang="en">', self.client.get('/').text)
        self.client.get('/language/zh-CN', follow_redirects=False)
        self.assertIn('<html lang="zh-CN">', self.client.get('/',headers={'Accept-Language':'en'}).text)

    def test_query_override_updates_saved_preference_and_switch_removes_it(self):
        self.assertIn('<html lang="en">', self.client.get('/?lang=en').text)
        response = self.client.get('/language/zh-CN',params={'next':'/search?lang=en&q=React'},follow_redirects=False)
        self.assertEqual(response.headers['location'], '/search?q=React')
        self.assertIn('<html lang="zh-CN">', self.client.get('/').text)

    def test_switch_cannot_redirect_to_another_host(self):
        for target in ['https://evil.example','//evil.example','/\\evil.example','/language/en','https://[','/\nexample']:
            with self.subTest(target=target):
                response = self.client.get('/language/en', params={'next':target}, follow_redirects=False)
                self.assertEqual(response.headers['location'], '/')
        self.assertEqual(self.client.get('/language/fr').status_code,404)

    def test_browser_preferences_honor_quality_and_exclusions(self):
        for header, expected in [('en;q=0,zh;q=0.7','zh-CN'),('zh;q=0.4,en-GB;q=0.9','en'),
                                 ('fr,en;q=0.7','en'),('fr','zh-CN'),('en;q=bogus,zh','zh-CN')]:
            with self.subTest(header=header):
                response = self.client.get('/',headers={'Accept-Language':header})
                self.assertEqual(response.headers['Content-Language'],expected)

    def test_locales_are_isolated_between_concurrent_requests(self):
        def request(locale):
            with TestClient(self.app) as client:
                return locale,client.get('/login',headers={'Accept-Language':locale}).text
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            for locale, markup in executor.map(request,['en','zh-CN']*4):
                self.assertIn(f'<html lang="{locale}">',markup)
                self.assertIn('Log in' if locale=='en' else '登录知识库',markup)
        self.assertEqual(LOCALE.get(),'zh-CN')

    def test_validation_errors_are_localized_but_access_is_unchanged(self):
        response = self.client.get('/n/999999',headers={'Accept-Language':'en'})
        self.assertEqual(response.status_code,404)
        self.assertEqual(response.json()['detail'], CATALOG['笔记不存在'])
        self.assertEqual(self.client.get('/admin',headers={'Accept-Language':'en'}).status_code,403)

    def test_account_pages_and_release_notice_use_selected_language(self):
        self.client.cookies.set('kb_session',self.token)
        for route in ['/account','/library','/learn','/teams','/imports','/team-inbox']:
            response = self.client.get(route,headers={'Accept-Language':'en'})
            self.assertEqual(response.status_code,200,route)
            self.assertIn('<html lang="en">',response.text)
        response=self.client.get('/api/updates',headers={'Accept-Language':'en'})
        from updates import load_releases
        latest = load_releases()[0]
        self.assertEqual(response.json()['latest']['title'],CATALOG[latest['title']])
        self.assertEqual(response.json()['latest']['id'],latest['id'])

    def test_literal_localization_never_changes_logic_keys_or_user_values(self):
        env=LocalizedEnvironment(autoescape=True)
        env.globals['ui']=ui
        template=env.from_string('''<a href="/n/{{ id }}" title="搜索笔记">登录</a>{{ value }}{{ '私人' if category=='私人' else '公开' }}{{ '知识导览'|urlencode }}''')
        token=LOCALE.set('en')
        try:
            result=template.render(id=4,value='<script>登录</script>',category='私人')
            self.assertIn('href="/n/4"',result)
            self.assertIn('title="Search notes"',result)
            self.assertIn('&lt;script&gt;登录&lt;/script&gt;',result)
            self.assertIn(CATALOG['私人'],result)
            self.assertIn('%E7%9F%A5',result)
        finally:
            LOCALE.reset(token)

    def test_all_templates_compile_in_both_languages(self):
        for name in TEMPLATES.env.list_templates():
            TEMPLATES.get_template(name)

    def test_dynamic_ui_messages_preserve_embedded_names_and_dictionary_keys(self):
        token=LOCALE.set('en')
        try:
            self.assertEqual(ui('创建文档「登录」'),'Created document “登录”')
            self.assertEqual(ui('title 不能为空'),'title cannot be empty')
            env=LocalizedEnvironment(autoescape=True);env.globals['ui']=ui
            template=env.from_string("{{ lookup['登录'] }}{{ {'登录':'公开'}['登录'] }}")
            self.assertEqual(template.render(lookup={'登录':'原始值'}),'原始值'+CATALOG['公开'])
        finally:
            LOCALE.reset(token)


if __name__=='__main__':
    unittest.main()
