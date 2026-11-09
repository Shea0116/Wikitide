"""Writing workflow, ownership, source integrity and explicit note creation."""
import json
import unittest

from fastapi import HTTPException

import test_explore as explore_fixture
from explore_write import format_generated, validate_document
from storage import connect


class Writer:
    ready = True
    def __init__(self):
        self.calls, self.fail, self.content, self.callback = [], False, None, None
    def stream_answer(self, messages, thinking=False, *, max_tokens=None):
        self.calls.append((messages, max_tokens))
        data = json.loads(messages[-1]['content'])
        if self.fail: raise HTTPException(504, '模拟生成失败')
        if '只输出写作提纲' in messages[0]['content']:
            text = '# React 闭包\n\n## 为什么会拿到旧值？\n\n讲解词法作用域与状态更新。[1]\n\n## 实践\n\n提供函数式更新示例。[1]'
        else:
            text = '# React 闭包\n\n<span style="color:#1E90FF;font-weight:700;">闭包</span>保留词法作用域。[1]\n\n## 为什么会拿到旧值？\n\n状态更新与闭包捕获的值需要区分。[1]\n\n**注意：**<span style="color:#DC143C;">示例未运行。</span>\n\n```js\n// 字面量 [1] 应保留\nsetCount(previous => previous + 1);\n```'
            if data['current_document']: text += '\n\n## 修改补充\n\n已补充一个例子，仍需结合实际环境验证。[1]'
        text = self.content if self.content is not None else text
        yield text[:60]
        if self.callback: self.callback()
        yield text[60:]


class WritingTests(unittest.TestCase):
    setUp = explore_fixture.ExploreTests.setUp
    tearDown = explore_fixture.ExploreTests.tearDown

    def setup_writer(self):
        self.writer = Writer(); self.app.state.explore_provider = self.writer

    def create(self, **changes):
        self.setup_writer()
        response = self.clients['alice'].post('/api/explore/drafts', headers=self.headers['alice'],
            json={'topic':'React 闭包', **changes})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def generate(self, draft, action='outline', **changes):
        response = self.clients['alice'].post(f"/api/explore/drafts/{draft['id']}/generate", headers=self.headers['alice'],
            json={'version':draft['version'],'action':action,**changes})
        events = []
        for frame in response.text.split('\n\n'):
            lines = frame.splitlines()
            kind = next((line[7:] for line in lines if line.startswith('event: ')), '')
            raw = next((line[6:] for line in lines if line.startswith('data: ')), '')
            if kind and raw: events.append((kind,json.loads(raw)))
        self.assertTrue(events,response.text)
        return events[-1]

    def ready_draft(self):
        draft = self.create()
        kind,draft = self.generate(draft); self.assertEqual(kind,'done',draft)
        kind,draft = self.generate(draft,'write'); self.assertEqual(kind,'done',draft)
        return draft

    def edit(self, draft, **changes):
        return self.clients['alice'].put(f"/api/explore/drafts/{draft['id']}",headers=self.headers['alice'],
            json={'version':draft['version'],'title':draft['title'],'outline':draft['outline'],'body':draft['body'],**changes})

    def admin(self):
        with connect(self.db) as db: db.execute("UPDATE users SET role='admin' WHERE username='alice'")

    def publish(self,draft,**changes):
        return self.clients['alice'].post(f"/api/explore/drafts/{draft['id']}/publish",headers=self.headers['alice'],
            json={'version':draft['version'],'path':'Frontend/知识求索/新文档.md','confirmed':True,**changes})

    def test_outline_write_revision_and_export_keep_sources_and_color_format(self):
        draft = self.ready_draft()
        self.assertEqual(draft['stage'],'draft')
        self.assertIn('color:#1e90ff',draft['html'])
        self.assertIn('<span style="color:#DC143C;font-weight:700;">为什么会拿到旧值？</span>',draft['body'])
        self.assertIn('**注意：** <span',draft['body'])
        kind,revised = self.generate(draft,'revise',instruction='补充一个例子')
        self.assertEqual(kind,'done',revised)
        self.assertIn('修改补充',revised['body'])
        self.assertEqual(json.loads(self.writer.calls[-1][0][-1]['content'])['current_document'],draft['body'])
        response = self.clients['alice'].get(f"/api/explore/drafts/{draft['id']}/export")
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('filename*=UTF-8',response.headers['content-disposition'])
        self.assertIn('[1](<http://testserver/n/',response.text)
        self.assertIn('// 字面量 [1] 应保留',response.text)
        self.assertIn('## 资料来源',response.text)
        with connect(self.db) as db:
            self.assertIsNone(db.execute("SELECT id FROM notes WHERE path='Frontend/知识求索/新文档.md'").fetchone())

    def test_draft_ownership_and_csrf_protect_every_mutation(self):
        self.assertEqual(self.anon.post('/api/explore/drafts',json={'topic':'React'}).status_code,401)
        self.assertEqual(self.clients['alice'].post('/api/explore/drafts',json={'topic':'React'}).status_code,403)
        draft = self.create(); path='/api/explore/drafts/'+draft['id']
        self.assertEqual(self.clients['bob'].get(path).status_code,404)
        self.assertEqual(self.clients['bob'].get(path+'/export').status_code,404)
        self.assertEqual(self.clients['bob'].delete(path,headers=self.headers['bob']).status_code,404)
        self.assertEqual(self.clients['alice'].delete(path).status_code,403)
        self.assertEqual(self.clients['alice'].post(path+'/generate',json={'version':0,'action':'outline'}).status_code,403)
        self.assertEqual(self.clients['bob'].get('/api/explore/drafts').json()['drafts'],[])

    def test_manual_edits_version_conflicts_and_generation_failure_keep_previous_body(self):
        draft = self.ready_draft()
        edited = self.edit(draft,body=draft['body']+'\n\n人工补充。').json()
        self.assertIn('人工补充',edited['body'])
        self.assertEqual(self.edit(draft,body='不应覆盖').status_code,409)
        self.writer.fail = True
        kind,error = self.generate(edited,'revise',instruction='修改')
        self.assertEqual((kind,error['status']),('error',504))
        current = self.clients['alice'].get('/api/explore/drafts/'+draft['id']).json()
        self.assertEqual(current['body'],edited['body'])
        self.assertFalse(current['busy'])

    def test_revoked_sources_block_read_export_and_publish_and_allow_fresh_research(self):
        draft = self.ready_draft(); self.admin()
        with connect(self.db) as db: db.execute("UPDATE notes SET visibility='private' WHERE id=?",(self.react['id'],))
        path='/api/explore/drafts/'+draft['id']
        self.assertEqual(self.clients['alice'].get(path).status_code,409)
        self.assertEqual(self.clients['alice'].get(path+'/export').status_code,409)
        self.assertEqual(self.publish(draft).status_code,409)
        self.assertTrue(self.clients['alice'].get('/api/explore/drafts').json()['drafts'][0]['stale'])
        kind,error = self.generate(draft,'outline')
        self.assertEqual((kind,error['status']),('error',422))

    def test_mid_generation_source_change_and_unsafe_model_output_are_not_committed(self):
        draft = self.create()
        kind,draft = self.generate(draft); self.assertEqual(kind,'done',draft)
        def revoke():
            with connect(self.db) as db: db.execute("UPDATE notes SET visibility='private' WHERE id=?",(self.react['id'],))
        self.writer.callback = revoke
        kind,error = self.generate(draft,'write')
        self.assertEqual((kind,error['status']),('error',409))
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT body FROM explore_drafts WHERE id=?',(draft['id'],)).fetchone()[0],'')

    def test_invalid_citations_and_html_do_not_replace_saved_document(self):
        draft = self.ready_draft()
        for output in ['# 错误\n\n编造 [99]','<script>alert(1)</script>\n\n结论 [1]','# 无引用\n\n没有依据']:
            self.writer.content = output
            kind,error = self.generate(draft,'revise',instruction='修改')
            self.assertEqual((kind,error['status']),('error',422))
            current=self.clients['alice'].get('/api/explore/drafts/'+draft['id']).json()
            self.assertEqual(current['body'],draft['body'])

    def test_explicit_admin_publish_creates_private_note_and_sync_event_only_once(self):
        draft = self.ready_draft()
        self.assertEqual(self.publish(draft).status_code,403)
        self.admin()
        self.assertEqual(self.publish(draft,confirmed=False).status_code,400)
        response = self.publish(draft)
        self.assertEqual(response.status_code,200,response.text)
        saved = response.json()
        with connect(self.db) as db:
            note = db.execute('SELECT * FROM notes WHERE id=?',(saved['note_id'],)).fetchone()
            self.assertEqual(note['visibility'],'private')
            self.assertIn('## 资料来源',note['body'])
            self.assertEqual(db.execute('SELECT count(*) FROM edit_events WHERE note_id=? AND status=\'pending\'',(note['id'],)).fetchone()[0],1)
        self.assertEqual(self.publish(draft).json()['note_id'],saved['note_id'])
        self.assertEqual(self.edit(saved['draft']).status_code,409)
        path='/api/explore/drafts/'+draft['id']
        self.assertEqual(self.clients['alice'].delete(path,headers=self.headers['alice']).status_code,200)
        self.assertEqual(self.clients['alice'].get('/n/'+str(saved['note_id'])).status_code,200)

    def test_existing_note_is_never_overwritten_and_private_paths_cannot_be_public(self):
        draft=self.ready_draft(); self.admin()
        self.assertEqual(self.publish(draft,path='Frontend/React闭包.md').status_code,409)
        for path in ['../secret.md','/tmp/doc.md','Frontend/static/doc.md','AGENTS.md']:
            self.assertEqual(self.publish(draft,path=path).status_code,400,path)
        self.assertEqual(self.publish(draft,path='Developer/文档.md',visibility='public').status_code,400)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT body FROM notes WHERE id=?',(self.react['id'],)).fetchone()[0],self.react['body'])

    def test_online_research_uses_topic_only_and_exports_web_urls(self):
        class Search:
            ready=True
            def search(self,query):
                self.query=query
                return [dict(kind='web',title='官方资料',url='https://react.dev/reference/react',section='react.dev',excerpt='公开的资料摘要',retrieved_at='2026-10-19T00:00:00+00:00')]
        search=Search(); self.app.state.explore_web=search
        draft=self.create(topic='全新主题xyz',requirements='私人写作偏好，不发送给搜索',web_search=True)
        kind,draft=self.generate(draft); self.assertEqual(kind,'done',draft)
        self.assertEqual(search.query,'全新主题xyz')
        kind,draft=self.generate(draft,'write'); self.assertEqual(kind,'done',draft)
        self.assertEqual(draft['sources'][0]['kind'],'web')
        self.assertIn('https://react.dev/reference/react',self.clients['alice'].get('/api/explore/drafts/'+draft['id']+'/export').text)

    def test_limits_busy_sessions_and_invalid_payloads(self):
        draft=self.create()
        with connect(self.db) as db: db.execute('UPDATE explore_drafts SET busy_until=9999999999 WHERE id=?',(draft['id'],))
        kind,error=self.generate(draft)
        self.assertEqual((kind,error['status']),('error',409))
        for payload in [{'topic':' '},{'topic':'x','web_search':'true'},{'topic':'x','note_id':True},{'topic':'x','history':[]}]:
            self.assertEqual(self.clients['alice'].post('/api/explore/drafts',headers=self.headers['alice'],json=payload).status_code,400)
        self.assertEqual(self.anon.get('/explore/write').status_code,200)
        self.assertIn('知识文档写作',self.anon.get('/explore').text)


class FormatTests(unittest.TestCase):
    def test_generated_curly_style_quotes_normalize_but_keep_code_and_validation(self):
        text = '<span style=“color:#1E90FF;”>概念</span>\n\n<mark style=‘background-color:#FFFF99;color:#1F2937;’>定义</mark>\n\n`<span style=“color:red;”>`\n\n```html\n<span style=“color:red;”>\n```'
        result = format_generated(text)
        self.assertIn('<span style="color:#1E90FF;">概念</span>', result)
        self.assertIn('<mark style="background-color:#FFFF99;color:#1F2937;">', result)
        self.assertIn('`<span style=“color:red;”>`', result)
        self.assertIn('```html\n<span style=“color:red;”>\n```', result)
        self.assertIn('style=', validate_document(result, [])[0])
        for unsafe in ['<span style=“color:expression(alert(1));”>x</span>', '<span style=“color:red;” onclick="alert(1)">x</span>']:
            with self.assertRaises(HTTPException):
                validate_document(format_generated(unsafe), [])

    def test_question_heading_color_and_bold_spacing_preserve_code(self):
        text='# 总标题\n\n## 为什么？\n\n**注意：**<span style="color:#DC143C;">限制</span>\n\n```md\n## 代码里的问题？\n**注意：**literal\n```\n'
        result=format_generated(text)
        self.assertIn('## <span style="color:#DC143C;font-weight:700;">为什么？</span>',result)
        self.assertIn('**注意：** <span',result)
        self.assertIn('## 代码里的问题？\n**注意：**literal',result)
        self.assertIn('color:#DC143C',format_generated('## 为什么 `useEffect` 会执行？'))


if __name__ == '__main__': unittest.main()
