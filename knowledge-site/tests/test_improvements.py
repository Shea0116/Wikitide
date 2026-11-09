"""Cross-device drafts, collaboration boundaries, expiry, offline and quality regression."""
import io
import json
import re
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
from app import create_app
from document_parser import extract
from imports import parse_job
from features import add_note
from storage import connect,create_user,create_session,digest,save_note,password_matches


class ImprovementsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.db=self.root/'site.db';self.content=self.root/'content'
        self.app=create_app(self.db,self.content);self.clients={};self.csrf={};self.ids={}
        with connect(self.db) as db:
            for name,role in [('owner','admin'),('writer','editor'),('outsider','admin')]:
                create_user(db,name,'improvement-tests-123',role);uid=db.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()[0]
                token,csrf=create_session(db,uid);client=TestClient(self.app);client.cookies.set('kb_session',token)
                self.clients[name]=client;self.csrf[name]=csrf;self.ids[name]=uid
            self.note=add_note(db,'Frontend/lesson.md','# Heading\n\n## Nested\n\nLearning material about closures and lexical scope.',category='Frontend',visibility='public')
            self.private=add_note(db,'Developer/private.md','private data',category='私人',visibility='private')
        self.team=self.clients['owner'].post('/api/teams',json={'name':'Review team'},headers=self.headers()).json()['id']
        with connect(self.db) as db: db.execute('INSERT INTO team_members VALUES(?,?,?,?)',(self.team,self.ids['writer'],'editor',int(time.time())))
        self.document=self.clients['owner'].post(f'/api/teams/{self.team}/documents',json={'title':'Working note','body':'version one'},headers=self.headers()).json()
        self.base=f"/teams/{self.team}/documents/{self.document['id']}"
        self.anon=TestClient(self.app)
    def tearDown(self):
        self.app.state.import_executor.shutdown(wait=True,cancel_futures=True)
        for client in [*self.clients.values(),self.anon]:client.close()
        self.temp.cleanup()
    def headers(self,who='owner'):return {'X-CSRF-Token':self.csrf[who]}
    def form(self,path,who='owner',**values):return self.clients[who].post(path,data={'csrf':self.csrf[who],**values},follow_redirects=False)
    def png(self):
        from PIL import Image
        raw=io.BytesIO();Image.new('RGB',(8,8),'navy').save(raw,format='PNG');return raw.getvalue()

    def test_drafts_are_account_scoped_and_versioned(self):
        url=f"/api/drafts/note/{self.note['id']}"
        data={'version':0,'base':self.note['body_sha'],'body':'my draft'}
        self.assertEqual(self.clients['owner'].put(url,json=data,headers=self.headers()).status_code,200)
        self.assertEqual(self.clients['owner'].put(url,json=data,headers=self.headers()).status_code,409)
        self.assertIsNone(self.clients['outsider'].get(url).json()['draft'])
        self.assertEqual(self.clients['writer'].get(url).status_code,404)
        with connect(self.db) as db:save_note(db,self.note,'new server version','web','owner')
        comparison=self.clients['owner'].post(url+'/compare',json={'base':self.note['body_sha'],'body':'my draft'},headers=self.headers()).json()
        self.assertEqual(comparison['base'],self.note['body']);self.assertEqual(comparison['server'],'new server version')
        self.assertEqual(comparison['local'],'my draft')
        deleted=self.clients['owner'].request('DELETE',url,json={'version':1},headers=self.headers())
        self.assertEqual(deleted.status_code,200)
        latest=self.clients['owner'].get(url).json();self.assertIsNone(latest['draft']);self.assertEqual(latest['version'],2)
        self.assertEqual(self.clients['owner'].put(url,json={**data,'version':1},headers=self.headers()).status_code,409)
        self.assertEqual(self.clients['owner'].put(url,json={**data,'version':2},headers=self.headers()).status_code,200)

    def test_team_draft_and_attachment_cannot_be_read_by_site_admin_outside_team(self):
        url=f"/api/drafts/team/{self.document['id']}?team_id={self.team}"
        self.assertEqual(self.clients['writer'].put(url,json={'version':0,'base':'1','body':'team draft'},headers=self.headers('writer')).status_code,200)
        self.assertEqual(self.clients['outsider'].get(url).status_code,404)
        upload=self.clients['owner'].post(self.base+'/attachments',data={'csrf':self.csrf['owner']},files={'file':('picture.png',self.png(),'image/png')},follow_redirects=False)
        self.assertEqual(upload.status_code,303,upload.text)
        endpoint=f'/api/teams/{self.team}/attachments/1'
        self.assertEqual(self.clients['writer'].get(endpoint).status_code,200)
        self.assertEqual(self.clients['outsider'].get(endpoint).status_code,404)
        self.assertEqual(self.anon.get(endpoint).status_code,401)
        preview=self.clients['writer'].post(f'/api/teams/{self.team}/preview',json={'body':f'![photo]({endpoint})','blocks':True},headers=self.headers('writer'))
        self.assertIn(endpoint,json.dumps(preview.json()))
        with connect(self.db) as db:db.execute('DELETE FROM team_members WHERE team_id=? AND user_id=?',(self.team,self.ids['writer']))
        self.assertEqual(self.clients['writer'].get(endpoint).status_code,404)
        self.assertEqual(self.clients['writer'].get(url).status_code,404)

    def test_comments_mentions_and_tasks_require_current_membership_and_versions(self):
        response=self.form(self.base+'/comments','writer',document_version='1',body='@owner please check',quote='version one',assignee=str(self.ids['owner']))
        self.assertEqual(response.status_code,303)
        self.assertIn('please check',self.clients['owner'].get('/team-inbox').text)
        self.assertNotIn('please check',self.clients['outsider'].get('/team-inbox').text)
        self.assertEqual(self.form(self.base+'/comments/1/toggle',version='1').status_code,303)
        self.assertEqual(self.form(self.base+'/comments/1/toggle',version='1').status_code,409)
        self.assertEqual(self.form(self.base+'/comments',document_version='0',body='stale').status_code,409)

    def test_reviews_pin_a_snapshot_and_do_not_publish_subsequent_edits(self):
        self.assertEqual(self.form(self.base+'/reviews','writer',version='1').status_code,303)
        self.assertEqual(self.form(self.base+'/reviews/1','writer',action='approved').status_code,403)
        self.assertEqual(self.form(self.base+'/reviews/1',action='approved').status_code,303)
        self.assertIn('version one',self.clients['writer'].get(self.base+'/published').text)
        updated=self.clients['writer'].put(f"/api/teams/{self.team}/documents/{self.document['id']}",json={'title':'Working note','body':'version two','version':1},headers=self.headers('writer'))
        self.assertEqual(updated.status_code,200)
        published=self.clients['writer'].get(self.base+'/published').text
        self.assertIn('version one',published);self.assertNotIn('version two',published)
        self.assertEqual(self.form(self.base+'/reviews',version='1').status_code,409)
        self.assertEqual(self.clients['outsider'].get(self.base+'/published').status_code,404)

    def test_expiring_share_revokes_anonymous_and_named_access(self):
        endpoint='/api/sharing';payload={'note_id':self.private['id'],'version':0,'audience':'link','members':[{'user_id':self.ids['writer'],'permission':'edit'}],'expires_at':int(time.time())+3600}
        response=self.clients['owner'].post(endpoint,json=payload,headers=self.headers());self.assertEqual(response.status_code,200,response.text)
        url=response.json()['url'];self.assertEqual(self.anon.get(url).status_code,200)
        self.assertEqual(self.clients['writer'].get(f"/n/{self.private['id']}").status_code,200)
        with connect(self.db) as db:db.execute('UPDATE shares SET expires_at=?',(int(time.time())-1,))
        self.assertEqual(self.anon.get(url).status_code,404)
        self.assertEqual(self.clients['writer'].get(f"/n/{self.private['id']}").status_code,404)
        self.assertEqual(self.clients['writer'].get(f"/n/{self.private['id']}/edit").status_code,403)
        self.assertIn('已到期',self.clients['owner'].get('/admin/shares').text)

    def test_offline_exports_only_public_and_embeds_referenced_images(self):
        image=self.content/'Frontend/static/image.png';image.parent.mkdir(parents=True);image.write_bytes(self.png())
        with connect(self.db) as db:save_note(db,self.note,'# Offline\n\n![photo](static/image.png)','web','owner')
        response=self.anon.get(f"/api/offline/{self.note['id']}");self.assertEqual(response.status_code,200,response.text)
        self.assertIn('data:image/png;base64,',response.json()['html'])
        self.assertEqual(self.clients['owner'].get(f"/api/offline/{self.private['id']}").status_code,404)
        with connect(self.db) as db:db.execute("UPDATE notes SET visibility='private' WHERE id=?",(self.note['id'],))
        self.assertEqual(self.anon.get(f"/api/offline/{self.note['id']}/status").status_code,404)

    def test_quality_feedback_cannot_target_other_accounts(self):
        with connect(self.db) as db:
            sid='synthetic-session-123456'
            db.execute('INSERT INTO explore_sessions(id,user_id,title,created_at,updated_at) VALUES(?,?,?,?,?)',(sid,self.ids['owner'],'synthetic',1,1))
            mid=db.execute("INSERT INTO explore_messages(session_id,role,content,sources,created_at) VALUES(?,'assistant','answer','[]',1)",(sid,)).lastrowid
        url=f'/api/explore/messages/{mid}/feedback'
        self.assertEqual(self.clients['owner'].post(url,json={'rating':'helpful'},headers=self.headers()).status_code,200)
        self.assertEqual(self.clients['outsider'].post(url,json={'rating':'incorrect'},headers=self.headers('outsider')).status_code,404)
        self.assertEqual(self.clients['owner'].post(url,json={'rating':'bad'},headers=self.headers()).status_code,400)
        self.assertEqual(self.clients['writer'].get('/admin/explore-quality').status_code,403)
        self.assertIn('有帮助',self.clients['owner'].get('/admin/explore-quality').text)

    def test_recovery_codes_rotate_and_revoke_all_sessions(self):
        response=self.form('/account/recovery-codes',current_password='improvement-tests-123')
        self.assertEqual(response.status_code,200,response.text)
        code=re.search(r'<pre>([^\n<]+)',response.text)[1]
        self.assertNotIn(code,self.clients['owner'].get('/account').text)
        self.anon.get('/recover');csrf=self.anon.cookies.get('kb_recovery_csrf')
        result=self.anon.post('/recover',data={'csrf':csrf,'username':'owner','code':code,'password':'new-password-12345','confirm':'new-password-12345'})
        self.assertEqual(result.status_code,200,result.text)
        with connect(self.db) as db:
            self.assertTrue(password_matches('new-password-12345',db.execute('SELECT password_hash FROM users WHERE id=?',(self.ids['owner'],)).fetchone()[0]))
            self.assertEqual(db.execute('SELECT count(*) FROM sessions WHERE user_id=?',(self.ids['owner'],)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM recovery_codes WHERE user_id=?',(self.ids['owner'],)).fetchone()[0],0)

    def test_session_revocation_only_affects_own_session(self):
        with connect(self.db) as db:other=db.execute('SELECT token_hash FROM sessions WHERE user_id=?',(self.ids['writer'],)).fetchone()[0]
        self.assertEqual(self.form('/account/sessions/revoke',session=other).status_code,303)
        self.assertEqual(self.clients['writer'].get('/account').status_code,200)

    def test_zip_and_docx_assets_are_bounded_and_code_is_unchanged(self):
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as archive:
            archive.writestr('notes/lesson.md','![photo](../images/a.png)\n\n```md\n![sample](../images/a.png)\n```')
            archive.writestr('images/a.png',self.png())
        result=extract(raw.getvalue(),'.zip');self.assertEqual(len(result['assets']),1)
        from import_assets import replace_links
        replaced=replace_links(result['body'],{'../images/a.png':'/api/teams/1/attachments/1'})
        self.assertIn('![photo](/api/teams/1/attachments/1)',replaced)
        self.assertIn('![sample](../images/a.png)',replaced)
        bad=io.BytesIO()
        with zipfile.ZipFile(bad,'w') as archive:archive.writestr('../outside.md','bad')
        with self.assertRaises(ValueError):extract(bad.getvalue(),'.zip')

    def test_imported_images_survive_draft_cleanup_and_keep_team_isolation(self):
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as archive:
            archive.writestr('note.md','# Imported image\n\n![photo](static/a.png)')
            archive.writestr('static/a.png',self.png())
        self.app.state.import_parse=lambda _:None
        def ready():
            response=self.clients['owner'].post('/api/imports?filename=package.zip',content=raw.getvalue(),headers=self.headers())
            self.assertEqual(response.status_code,202,response.text);jid=response.json()['id'];parse_job(self.db,jid)
            job=self.clients['owner'].get(f'/api/imports/{jid}').json();self.assertEqual(job['status'],'ready',job.get('error'))
            return job
        job=ready();url=f"/api/imports/{job['id']}"
        self.assertIn('/assets/0',job['html'])
        self.assertEqual(self.clients['outsider'].get(url+'/assets/0').status_code,404)
        personal=self.clients['owner'].post(url+'/commit',json={'version':job['version'],'team_id':0,'path':'Developer/Imported/new.md'},headers=self.headers())
        self.assertEqual(personal.status_code,200,personal.text)
        note_id=int(personal.json()['url'].split('/')[-1]);asset=f'/asset/{note_id}?path=static/a.png'
        self.assertEqual(self.clients['owner'].get(asset).content,self.png())
        self.assertEqual(self.anon.get(asset).status_code,404)
        self.clients['owner'].delete(url,headers=self.headers())
        self.assertEqual(self.clients['owner'].get(asset).status_code,200)
        job=ready();url=f"/api/imports/{job['id']}"
        team=self.clients['owner'].post(url+'/commit',json={'version':job['version'],'team_id':self.team},headers=self.headers())
        self.assertEqual(team.status_code,200,team.text)
        with connect(self.db) as db:
            item=db.execute('SELECT * FROM team_attachments WHERE team_id=?',(self.team,)).fetchone()
            doc=db.execute('SELECT body FROM team_documents WHERE id=?',(item['document_id'],)).fetchone()[0]
            endpoint=f"/api/teams/{self.team}/attachments/{item['id']}"
            self.assertIn(endpoint,doc)
        self.clients['owner'].delete(url,headers=self.headers())
        self.assertEqual(self.clients['writer'].get(endpoint).status_code,200)
        self.assertEqual(self.clients['outsider'].get(endpoint).status_code,404)

    def test_search_evaluation_reports_public_fixture_recall(self):
        from scripts.evaluate_search import evaluate
        from explore_search import ExploreIndex
        with connect(self.db) as db:
            result=evaluate(db,[{'question':'lexical scope','expected_paths':['Frontend/lesson.md']}],index=ExploreIndex())
        self.assertEqual(result['hit_rate'],1);self.assertEqual(result['mean_recall'],1)
        self.assertGreater(result['mrr'],0)

    def test_duplicate_merge_keeps_new_reading_learning_and_draft_data(self):
        from note_renames import merge_duplicate
        with connect(self.db) as db:
            duplicate=add_note(db,'Frontend/duplicate.md','duplicate content',category='Frontend',visibility='public')
            db.execute('INSERT INTO reading_items VALUES(?,?,?,?,?,?,?)',(self.ids['owner'],duplicate['id'],'学习路线',1,0,2,100))
            db.execute('INSERT INTO annotations(user_id,note_id,quote,comment,body_sha,created_at) VALUES(?,?,?,?,?,?)',(self.ids['owner'],duplicate['id'],'duplicate','annotation',duplicate['body_sha'],100))
            db.execute('INSERT INTO review_cards(user_id,note_id,question,answer,body_sha,due_at) VALUES(?,?,?,?,?,?)',(self.ids['owner'],duplicate['id'],'question','answer',duplicate['body_sha'],100))
            db.execute('INSERT INTO editor_drafts VALUES(?,?,?,?,?,?,?,?,?,0)',(self.ids['owner'],'note',duplicate['id'],0,'title','draft body',duplicate['body_sha'],1,100))
            merge_duplicate(db,self.note,duplicate)
            for table in ['reading_items','annotations','review_cards']:
                self.assertEqual(db.execute(f'SELECT note_id FROM {table}').fetchone()[0],self.note['id'])
            draft=db.execute('SELECT * FROM editor_drafts').fetchone()
            self.assertEqual(draft['resource_id'],self.note['id']);self.assertEqual(draft['body'],'draft body')


if __name__=='__main__':unittest.main()
