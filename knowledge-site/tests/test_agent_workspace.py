"""Synthetic end-to-end privacy, ownership and concurrent-forgetting acceptance."""
import json
import os
import time
import unittest
from unittest.mock import patch

import test_explore as existing
from storage import connect


class WorkspaceTests(unittest.TestCase):
    tearDown = existing.ExploreTests.tearDown
    ask = existing.ExploreTests.ask

    def setUp(self):
        with patch.dict(os.environ, {'KB_AGENT_CONTEXT_ENABLED':'true','KB_AGENT_MEMORY_ENABLED':'true',
                                     'KB_AGENT_TRANSIENT_ENABLED':'true','KB_AGENT_RUNTIME_ENABLED':'true'}):
            existing.ExploreTests.setUp(self)
        self.client = self.clients['alice']
        self.h = self.headers['alice']
        self.native = {'Authorization':'Bearer '+self.client.cookies.get('kb_session')}

    def post(self, route, data, *, who='alice'):
        return self.clients[who].post('/api/agent/v1/'+route, headers=self.headers[who], json=data)

    def project(self, title='闭包项目', **extra):
        response=self.post('projects',{'title':title, 'goal':'用代码解释闭包', **extra})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def memory(self, content='MEMORY_KEEP_CODE', **extra):
        response=self.post('memories',{'content':content, **extra})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def forget(self, memory):
        return self.client.request('DELETE','/api/agent/v1/memories/'+memory['id'],headers=self.h,
                                   json={'expected_revision':memory['revision']})

    def test_project_resource_ownership_continuation_and_container_deletion(self):
        p=self.project();other=self.project('其他项目');sid=self.ask(project_id=p['id']).json()['session_id']
        self.assertEqual(self.client.get('/api/explore/sessions/'+sid).json()['project_id'],p['id'])
        linked=self.post('projects/'+other['id']+'/resources',{'resource_kind':'session','resource_id':sid})
        self.assertEqual(linked.status_code,409)
        draft=self.client.post('/api/explore/drafts',headers=self.h,json={'topic':'闭包','project_id':p['id']}).json()
        run=self.post('runs',{'kind':'writing','topic':'闭包','project_id':other['id'],'draft_id':draft['id'],'idempotency_key':'cross-project-test'})
        self.assertEqual(run.status_code,409)
        self.assertEqual(self.clients['bob'].get('/api/agent/v1/projects/'+p['id']).status_code,404)
        self.assertEqual(self.post('projects/'+p['id']+'/resources',{'resource_kind':'note','resource_id':str(self.secret['id'])}).status_code,404)
        continued=self.post('projects/'+p['id']+'/continue',{}).json()
        response=self.ask(session_id=continued['session_id'],project_id=p['id'])
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('用代码解释闭包',json.dumps(self.provider.calls[-1],ensure_ascii=False))
        self.assertEqual(self.client.delete('/api/agent/v1/projects/'+p['id'],headers=self.h).status_code,200)
        self.assertEqual(self.client.get('/api/explore/sessions/'+sid).status_code,200)
        with connect(self.db) as db:self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_memory_cas_expiry_candidates_and_scope(self):
        m=self.memory();route='/api/agent/v1/memories/'+m['id']
        self.assertEqual(self.client.patch(route,headers=self.h,json={'expected_revision':1,'content':'最新要求'}).status_code,200)
        self.assertEqual(self.client.patch(route,headers=self.h,json={'expected_revision':1,'content':'旧窗口'}).status_code,409)
        self.assertEqual(self.clients['bob'].request('DELETE',route,headers=self.headers['bob'],json={'expected_revision':2}).status_code,404)
        p=self.project();self.memory('UNRELATED_PROJECT_ONLY',scope='project',project_id=p['id'])
        self.memory('EXPIRED_NEVER_SEND',expires_at=int(time.time())-1)
        result=self.ask().json()
        self.assertNotIn('UNRELATED_PROJECT_ONLY',json.dumps(self.provider.calls[-1]))
        self.assertNotIn('EXPIRED_NEVER_SEND',json.dumps(self.provider.calls[-1]))
        candidate=self.post('memories',{'content':'候选说明','source':{'kind':'message','id':str(result['message_id'])}})
        # Answers that already incorporate memory may not recursively become evidence.
        self.assertEqual(candidate.status_code,409)

    def test_candidate_confirmation_and_source_session_deletion(self):
        first=self.ask().json()
        m=self.memory('候选事实',source={'kind':'message','id':str(first['message_id'])})
        self.assertEqual(m['status'],'candidate')
        activated=self.post('memory-candidates/'+m['id']+'/activate',{'expected_revision':1})
        self.assertEqual(activated.status_code,200,activated.text)
        self.assertEqual(activated.json()['revision'],2)
        self.client.delete('/api/explore/sessions/'+first['session_id'],headers=self.h)
        self.ask()
        self.assertNotIn('候选事实',json.dumps(self.provider.calls[-1],ensure_ascii=False))

    def test_off_excludes_memory_derived_history_and_forget_invalidates_descendants(self):
        m=self.memory();first=self.ask().json();sid=first['session_id']
        self.provider.content='DERIVED_MEMORY_ANSWER。[1]'
        self.ask(session_id=sid)
        self.provider.content='OFF_INDEPENDENT_ANSWER。[1]'
        self.ask(session_id=sid,memory_mode='off')
        last=json.dumps(self.provider.calls[-1])
        self.assertNotIn('MEMORY_KEEP_CODE',last)
        self.assertNotIn('DERIVED_MEMORY_ANSWER',last)
        self.assertEqual(self.forget(m).status_code,200)
        history=self.client.get('/api/explore/sessions/'+sid).json()['messages']
        self.assertTrue(all('DERIVED_MEMORY_ANSWER' not in message['content'] for message in history))
        self.ask(session_id=sid)
        self.assertNotIn('MEMORY_KEEP_CODE',json.dumps(self.provider.calls[-1]))

    def test_forget_source_message_suppresses_reextraction_and_history(self):
        first=self.ask('React 闭包 SOURCE_FORGET_ME').json()
        m=self.memory('用户要求',source={'kind':'message','id':str(first['user_message_id'])})
        self.assertEqual(self.forget(m).status_code,200)
        self.assertEqual(self.post('memories',{'content':'重新提取','source':{'kind':'message','id':str(first['user_message_id'])}}).status_code,409)
        self.ask(session_id=first['session_id'])
        self.assertNotIn('SOURCE_FORGET_ME',json.dumps(self.provider.calls[-1]))

    def test_memory_change_during_stream_stops_delta_and_commit(self):
        m=self.memory();parent=self
        class Stream(existing.Provider):
            def stream_answer(self,messages,thinking=False):
                yield '已显示片段'
                parent.forget(m)
                yield 'PRIVATE_AFTER_FORGET。[1]'
        self.app.state.explore_provider=Stream()
        response=self.client.post('/api/explore/ask/stream',headers=self.h,json={'question':'React 闭包'})
        self.assertIn('event: error',response.text)
        self.assertNotIn('PRIVATE_AFTER_FORGET',response.text)
        with connect(self.db) as db:self.assertEqual(db.execute('SELECT count(*) FROM explore_messages').fetchone()[0],0)

    def test_conflicts_block_ambiguous_memory_and_project_overrides_account(self):
        p=self.project();a=self.memory('ACCOUNT_STYLE',conflict_key='style')
        self.memory('PROJECT_STYLE',scope='project',project_id=p['id'],conflict_key='style')
        self.ask(project_id=p['id']);sent=json.dumps(self.provider.calls[-1])
        self.assertIn('PROJECT_STYLE',sent);self.assertNotIn('ACCOUNT_STYLE',sent)
        self.memory('CONFLICT_STYLE',conflict_key='style')
        result=self.ask().json()
        self.assertTrue(result['context']['memory_conflicts'])
        self.assertNotIn('ACCOUNT_STYLE',json.dumps(self.provider.calls[-1]))

    def test_native_cookie_rejection_transient_nonpersistence_and_summary(self):
        route='/api/client/v1/agent/transient/ask/stream'
        payload={'question':'React 闭包','history':[{'role':'user','content':'LOCAL_HISTORY_MARKER'}],
                 'memory_context':[{'local_id':'local-only','revision':1,'content':'LOCAL_MEMORY_MARKER'}]}
        self.assertEqual(self.client.post(route,json=payload).status_code,401)
        self.provider.content='LOCAL_ANSWER_MARKER。[1]'
        response=self.client.post(route,headers=self.native,json=payload)
        self.assertIn('event: done',response.text);self.assertIn('LOCAL_ANSWER_MARKER',response.text)
        self.assertIn('LOCAL_MEMORY_MARKER',json.dumps(self.provider.calls[-1]))
        self.provider.summarize_local=lambda messages,usage=None:json.dumps({'schema_version':1,'goal':'LOCAL_SUMMARY_MARKER'})
        summary=self.client.post('/api/client/v1/agent/transient/summarize',headers=self.native,json=payload)
        self.assertEqual(summary.status_code,200,summary.text)
        with connect(self.db) as db:
            dump='\n'.join(db.iterdump())
            self.assertEqual(db.execute('SELECT count(*) FROM explore_requests').fetchone()[0],2)
        for marker in ('LOCAL_MEMORY_MARKER','LOCAL_HISTORY_MARKER','LOCAL_ANSWER_MARKER','LOCAL_SUMMARY_MARKER'):
            self.assertNotIn(marker,dump)

    def test_transient_rejects_forged_context_and_oversize_and_stops_revocation(self):
        route='/api/client/v1/agent/transient/ask/stream'
        for extra in ({'owner_id':2},{'history':[{'role':'system','content':'bad'}]},
                      {'summary':{'goal':'x','sources':['private']}},{'memory_context':[{'content':'x','owner':2}]}):
            self.assertEqual(self.client.post(route,headers=self.native,json={'question':'React 闭包',**extra}).status_code,400)
        response=self.client.post(route,headers=self.native,json={'question':'React 闭包','memory_context':[{'content':'中'*4000}]*5})
        self.assertIn('"status": 422',response.text)
        parent=self
        class Stream(existing.Provider):
            def stream_answer(self,messages,thinking=False):
                yield '可以发送'
                with connect(parent.db) as db:db.execute("UPDATE users SET active=0 WHERE username='alice'")
                yield 'REVOKED_TOKEN_MARKER。[1]'
        self.app.state.explore_provider=Stream()
        result=self.client.post(route,headers=self.native,json={'question':'React 闭包'})
        self.assertIn('event: error',result.text);self.assertNotIn('REVOKED_TOKEN_MARKER',result.text)

    def test_writing_context_budget_forget_and_off_generation(self):
        m=self.memory();p=self.project()
        response=self.client.post('/api/explore/drafts',headers=self.h,json={'topic':'React 闭包','project_id':p['id']})
        self.assertEqual(response.status_code,200,response.text)
        draft=response.json();self.assertEqual(draft['project_id'],p['id'])
        class Stream(existing.Provider):
            def stream_answer(inner,messages,thinking=False,**kw):
                inner.calls.append(messages);yield '## 提纲\n\n解释闭包 [1]'
        provider=Stream();self.app.state.explore_provider=provider
        route='/api/explore/drafts/'+draft['id']+'/generate'
        generated=self.client.post(route,headers=self.h,json={'action':'outline','version':0})
        self.assertIn('event: done',generated.text,generated.text)
        self.assertIn('MEMORY_KEEP_CODE',json.dumps(provider.calls))
        off=self.client.post(route,headers=self.h,json={'action':'write','version':1,'memory_mode':'off'})
        self.assertIn('"status": 409',off.text)
        self.assertEqual(self.forget(m).status_code,200)
        off=self.client.post(route,headers=self.h,json={'action':'outline','version':1,'memory_mode':'off'})
        self.assertIn('event: done',off.text,off.text)
        self.assertNotIn('MEMORY_KEEP_CODE',json.dumps(provider.calls[-1]))

    def test_provider_daily_budget_covers_transient_and_durable_work(self):
        with patch.dict(os.environ,{'KB_AGENT_DAILY_TOKENS':'1000'}):
            self.assertEqual(self.ask().status_code,429)
            response=self.client.post('/api/client/v1/agent/transient/ask/stream',headers=self.native,json={'question':'React 闭包'})
            self.assertIn('"status": 429',response.text)
            self.assertEqual(self.post('runs',{'kind':'writing','topic':'React 闭包','idempotency_key':'unique-test-key','max_tokens':2000}).status_code,429)
        self.assertFalse(self.provider.calls)

    def test_project_search_and_latest_constraints_handoff(self):
        p=self.project('搜索项目');self.project('无关项目')
        result=self.client.get('/api/agent/v1/projects',params={'q':'搜索','status':'active'}).json()
        self.assertEqual([row['id'] for row in result['projects']],[p['id']])
        first=self.ask(project_id=p['id']).json()
        response=self.client.patch('/api/agent/v1/sessions/'+first['session_id']+'/constraints',headers=self.h,
            json={'expected_version':1,'constraints':['最新要求：保留代码']})
        self.assertEqual(response.status_code,200,response.text)
        continued=self.post('projects/'+p['id']+'/continue',{}).json()
        self.assertEqual(self.ask(session_id=continued['session_id']).status_code,200)
        self.assertIn('最新要求：保留代码',json.dumps(self.provider.calls[-1],ensure_ascii=False))


if __name__=='__main__':unittest.main()
