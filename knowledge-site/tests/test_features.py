"""Behavior and permission checks using disposable notes and a real HTTP client."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

BASE=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(BASE))
sys.path.insert(0,str(BASE/'scripts'))
from fastapi.testclient import TestClient
from app import create_app
from storage import connect, create_session, create_user, digest, save_note
from features import add_note, record_sha
from sync_local import sync_once
import sync_local


class ClientAPI:
    def __init__(self,client): self.client=client
    def call(self,route,payload=None):
        response=self.client.get(route,headers={'Authorization':'Bearer test-token'}) if payload is None else self.client.post(route,json=payload,headers={'Authorization':'Bearer test-token'})
        if response.status_code==409: return {'conflict':True}
        if response.status_code==404: return {'unsupported':True}
        response.raise_for_status()
        return response.json()


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.content=self.root/'content'; self.content.mkdir()
        self.db=self.root/'site.db'
        self.app=create_app(self.db,self.content,sync_token_hash=digest('test-token'))
        self.anon=TestClient(self.app)
        self.clients={}; self.csrf={}
        with connect(self.db) as db:
            for name,role in [('owner','admin'),('editor','editor'),('reader','reader')]:
                create_user(db,name,'test-password-123',role)
                user_id=db.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()[0]
                token,csrf=create_session(db,user_id)
                client=TestClient(self.app); client.cookies.set('kb_session',token)
                self.clients[name]=client; self.csrf[name]=csrf
            self.public=add_note(db,'Frontend/example.md','# Example\n\nFirst body.\n',category='Frontend',visibility='public')
            self.private=add_note(db,'Developer/secret.md','private-unique-needle',category='私人笔记',visibility='private')
        self.owner=self.clients['owner']; self.api=ClientAPI(self.anon)
        self.source=self.root/'source'; (self.source/'Frontend').mkdir(parents=True)
        self.file=self.source/'Frontend/example.md'; self.file.write_text(self.public['body'])
        self.config={'source_root':str(self.source),'state_path':str(self.root/'work/state.json'),
                     'conflict_root':str(self.root/'work/conflicts'),'base_url':'https://example.test','token':'test-token'}
        self.original_api=sync_local.SyncAPI; sync_local.SyncAPI=lambda *a,**kw:self.api

    def tearDown(self):
        sync_local.SyncAPI=self.original_api
        self.anon.close()
        for client in self.clients.values(): client.close()
        self.temp.cleanup()

    def current(self,note_id=None):
        with connect(self.db) as db: return db.execute('SELECT * FROM notes WHERE id=?',(note_id or self.public['id'],)).fetchone()

    def form(self,**values): return {'csrf':self.csrf['owner'],**values}

    def edit(self,body):
        current=self.current()
        return self.owner.post(f"/n/{current['id']}/edit",data=self.form(body=body,base_sha=current['body_sha']),follow_redirects=False)

    def test_stale_edit_keeps_submitted_body_and_latest_version(self):
        before=self.current(); self.edit('Other person saved')
        response=self.owner.post(f"/n/{before['id']}/edit",data=self.form(body='MY UNSAVED INPUT',base_sha=before['body_sha']))
        self.assertEqual(response.status_code,409)
        self.assertIn('MY UNSAVED INPUT</textarea>',response.text)
        self.assertIn('Other person saved',response.text)
        self.assertEqual(self.current()['body'],'Other person saved')

    def test_preview_is_sanitized_and_requires_edit_permission_and_csrf(self):
        path=f"/n/{self.public['id']}/preview"
        response=self.owner.post(path,data=self.form(body='<script>alert(1)</script>\n\n**safe**'))
        self.assertEqual(response.status_code,200); self.assertNotIn('<script>',response.text); self.assertIn('<strong>safe</strong>',response.text)
        self.assertEqual(self.anon.post(path,data={'body':'text'}).status_code,404)
        self.assertEqual(self.owner.post(path,data={'body':'text'}).status_code,403)

    def test_reading_and_preview_preserve_ordered_list_start_numbers(self):
        body = (
            '1. **typeof**\n\n- 类型说明\n\n'
            '2. **instanceof**\n\n- 实例说明\n\n'
            '3. **根据原型判断**\n'
        )
        with connect(self.db) as db:
            save_note(db, self.public, body, 'web', 'owner')
        path = f"/n/{self.public['id']}"
        reading = self.anon.get(path)
        preview = self.owner.post(f'{path}/preview', data=self.form(body=body))
        for response in (reading, preview):
            self.assertEqual(response.status_code, 200)
            self.assertIn('<ol start="2">\n<li><strong>instanceof</strong></li>', response.text)
            self.assertIn('<ol start="3">\n<li><strong>根据原型判断</strong></li>', response.text)
        self.assertEqual(self.current()['body'], body)

    def test_live_editor_blocks_keep_utf16_ranges_rendering_and_permissions(self):
        body = ('# 标题 🌊\n\n<span style="color:#DC143C;">**结论**</span>\n\n'
                '| 语言 | 目标 |\n| --- | --- |\n| Python | API |\n\n'
                '> 引用\n\n- [x] 完成\n- [ ] 待办\n\n'
                '```js\nconst text = "<script>";\n```\n\n'
                '<script>alert(1)</script>\n\n[原文](example.md)\n')
        path = f"/n/{self.public['id']}/preview"
        original = self.current()['body']
        response = self.owner.post(path, data=self.form(body=body, blocks='1'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        blocks = response.json()['blocks']
        source = body.encode('utf-16-le')
        slices = [source[item['from']*2:item['to']*2].decode('utf-16-le') for item in blocks]
        self.assertEqual(slices[0], '# 标题 🌊')
        self.assertEqual(slices[1], '<span style="color:#DC143C;">**结论**</span>')
        self.assertTrue(any(text.startswith('| 语言') and text.endswith('| Python | API |') for text in slices))
        html = ''.join(item['html'] for item in blocks)
        self.assertIn('<table>', html)
        self.assertIn('color:#dc143c', html)
        self.assertIn('<strong>结论</strong>', html)
        self.assertIn('task-checkbox', html)
        self.assertIn(f'href="/n/{self.public["id"]}"', html)
        self.assertNotIn('<script>', html)
        self.assertEqual(self.current()['body'], original)
        self.assertEqual(self.owner.post(path, data={'body': body, 'blocks': '1'}).status_code, 403)
        self.assertEqual(self.clients['reader'].post(path, data={'csrf': self.csrf['reader'], 'blocks': '1'}).status_code, 404)
        self.assertEqual(self.anon.post(path, data={'blocks': '1'}).status_code, 404)
        self.assertEqual(self.owner.post(path, data=self.form(body='', blocks='1')).json(), {'blocks': []})

    def test_editor_styles_have_a_fresh_nonce_without_allowing_inline_scripts(self):
        path = f"/n/{self.public['id']}/edit"
        first, second = self.owner.get(path), self.owner.get(path)
        import re
        nonce = re.search(r'data-style-nonce="([^"]+)"', first.text).group(1)
        self.assertIn(f"style-src 'self' 'nonce-{nonce}';", first.headers['Content-Security-Policy'])
        self.assertIn("script-src 'self';", first.headers['Content-Security-Policy'])
        self.assertNotIn(f'data-style-nonce="{nonce}"', second.text)
        self.assertNotIn("'nonce-", self.anon.get(f"/n/{self.public['id']}").headers['Content-Security-Policy'])

    def test_search_ranks_title_paginates_and_never_leaks_private(self):
        with connect(self.db) as db:
            for i in range(25): add_note(db,f'Frontend/title-{i}.md','needle body',category='Frontend',visibility='public')
            titled=add_note(db,'Backend/needle.md','no hit in body',category='Backend',visibility='public')
            add_note(db,'Backend/percent.md','100% literal',category='Backend',visibility='public')
        response=self.anon.get('/search?q=needle')
        self.assertEqual(response.status_code,200); self.assertIn('<mark>needle</mark>',response.text)
        self.assertLess(response.text.index(f'/n/{titled["id"]}'),response.text.index('/n/3'))
        self.assertIn('下一页',response.text)
        self.assertNotIn('private-unique-needle',response.text)
        self.assertNotIn('私人笔记',response.text)
        self.assertIn('title-',self.anon.get('/search?q=needle&page_number=2&category=Frontend').text)
        self.assertIn('共 1 篇',self.anon.get('/search?q=%25').text)

    def test_history_restore_tracks_actor_and_preserves_newer_edits(self):
        self.edit('changed')
        with connect(self.db) as db:
            rows=db.execute('SELECT * FROM revisions WHERE note_id=? ORDER BY id',(self.public['id'],)).fetchall()
        self.assertEqual(len(rows),2); self.assertEqual(rows[-1]['actor'],'owner')
        path=f"/n/{self.public['id']}/restore"
        stale=self.public['body_sha']
        self.assertEqual(self.owner.post(path,data=self.form(revision=rows[0]['id'],base_sha=stale)).status_code,409)
        response=self.owner.post(path,data=self.form(revision=rows[0]['id'],base_sha=self.current()['body_sha']),follow_redirects=False)
        self.assertEqual(response.status_code,303); self.assertEqual(self.current()['body'],self.public['body'])
        self.assertEqual(self.anon.get(f"/n/{self.public['id']}/history").status_code,404)
        self.assertEqual(self.owner.get(f"/n/{self.public['id']}/history?revision={rows[0]['id']}").status_code,200)

    def test_creation_rejects_tool_paths_and_preserves_private_scope(self):
        for path in ['../escape.md','Frontend/.secret.md','HowToLiveBetter/private.md','Frontend/AGENTS.md','Frontend//alias.md']:
            self.assertEqual(self.owner.post('/new',data=self.form(path=path,body='note')).status_code,400)
        response=self.owner.post('/new',data=self.form(path='Developer/new-private.md',body='new-private'),follow_redirects=False)
        self.assertEqual(response.status_code,303); self.assertEqual(self.anon.get(response.headers['location']).status_code,404)
        self.assertEqual(self.clients['editor'].post('/new',data={'csrf':self.csrf['editor'],'path':'Frontend/x.md','body':'x'}).status_code,403)

    def test_links_keep_fragments_and_code_and_avoid_ambiguous_stems(self):
        with connect(self.db) as db:
            a=add_note(db,'Backend/a/Same.md','## Section',category='Backend',visibility='public')
            add_note(db,'Backend/b/Same.md','other',category='Backend',visibility='public')
            body='[[Same]] [[Backend/a/Same#Section|target]] `[[Backend/a/Same]]`\n\n```\n[[Backend/a/Same]]\n```'
            save_note(db,self.public,body,'web','owner')
        response=self.anon.get(f"/n/{self.public['id']}")
        self.assertIn('[[Same]]',response.text)
        self.assertIn(f'href="/n/{a["id"]}#Section"',response.text)
        self.assertIn('<code>[[Backend/a/Same]]</code>',response.text)
        self.assertIn('引用本文的笔记',self.anon.get(f"/n/{a['id']}").text)

    def test_bookmark_reading_and_revoked_access(self):
        with connect(self.db) as db:
            reader=db.execute("SELECT id FROM users WHERE username='reader'").fetchone()[0]
            db.execute('INSERT INTO grants VALUES(?,?,1,0)',(reader,self.private['id']))
        client=self.clients['reader']; path=f"/n/{self.private['id']}"
        self.assertEqual(client.post(path+'/bookmark',data={'csrf':self.csrf['reader']},follow_redirects=False).status_code,303)
        self.assertEqual(client.post(path+'/reading',json={'position':0.5},headers={'X-CSRF-Token':self.csrf['reader']}).status_code,200)
        self.assertIn('secret',client.get('/library').text); self.assertIn('继续阅读',client.get('/').text)
        with connect(self.db) as db: db.execute('DELETE FROM grants WHERE user_id=?',(reader,))
        self.assertNotIn('secret',client.get('/library').text)
        self.assertEqual(client.post(path+'/reading',json={'position':0.5},headers={'X-CSRF-Token':self.csrf['reader']}).status_code,404)

    def test_bulk_grants_filter_scope_and_admin_pagination(self):
        with connect(self.db) as db:
            editor=db.execute("SELECT id FROM users WHERE username='editor'").fetchone()[0]
            for i in range(52): add_note(db,f'Frontend/n{i}.md','body',category='Frontend / child',visibility='private')
        response=self.owner.post('/admin/bulk',data=self.form(category='Frontend',user_id=editor,mode='edit'),follow_redirects=False)
        self.assertEqual(response.status_code,303)
        with connect(self.db) as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM grants WHERE user_id=?',(editor,)).fetchone()[0],53)
        self.assertIn('下一页',self.owner.get('/admin?category=Frontend').text)
        self.assertIn('n',self.owner.get('/admin?category=Frontend&page_number=2').text)

    def test_sync_body_creation_and_persistent_conflict_resolution(self):
        self.assertEqual(sync_once(self.config)['conflicts'],0)
        self.file.write_text('local one'); self.assertEqual(sync_once(self.config)['uploaded'],1)
        self.assertEqual(self.current()['body'],'local one')
        self.edit('web one'); self.assertEqual(sync_once(self.config)['downloaded'],1)
        self.file.write_text('local conflict'); self.edit('web conflict')
        self.assertEqual(sync_once(self.config)['conflicts'],1)
        self.assertEqual(sync_once(self.config)['conflicts'],1)
        self.assertEqual(self.file.read_text(),'local conflict')
        with connect(self.db) as db: conflict=db.execute('SELECT * FROM sync_conflicts').fetchone()
        response=self.owner.post(f"/admin/conflicts/{conflict['id']}",data=self.form(action='merge',body='merged',local_sha=conflict['local_sha'],server_sha=record_sha(self.current())),follow_redirects=False)
        self.assertEqual(response.status_code,303)
        self.assertEqual(sync_once(self.config)['downloaded'],1); self.assertEqual(self.file.read_text(),'merged')
        with connect(self.db) as db: self.assertEqual(db.execute('SELECT status FROM sync_conflicts').fetchone()[0],'applied')
        response=self.owner.post('/new',data=self.form(path='Frontend/from-web.md',body='website new'),follow_redirects=False)
        self.assertEqual(response.status_code,303); sync_once(self.config)
        self.assertEqual((self.source/'Frontend/from-web.md').read_text(),'website new')
        (self.source/'Frontend/from-local.md').write_text('local new'); sync_once(self.config)
        with connect(self.db) as db: self.assertEqual(db.execute("SELECT body FROM notes WHERE path='Frontend/from-local.md'").fetchone()[0],'local new')

    def test_resolution_reopens_if_local_file_changed_again(self):
        sync_once(self.config); self.file.write_text('local'); self.edit('web'); sync_once(self.config)
        with connect(self.db) as db: conflict=db.execute('SELECT * FROM sync_conflicts').fetchone()
        self.owner.post(f"/admin/conflicts/{conflict['id']}",data=self.form(action='server',local_sha=conflict['local_sha'],server_sha=record_sha(self.current())))
        self.file.write_text('local changed again'); sync_once(self.config)
        self.assertEqual(self.file.read_text(),'local changed again')
        with connect(self.db) as db:
            current=db.execute('SELECT * FROM sync_conflicts').fetchone()
            self.assertEqual(current['status'],'open'); self.assertEqual(current['local_body'],'local changed again')

    def test_sync_metadata_and_linked_assets_both_directions(self):
        self.file.write_text('# Example\n\n![image](static/picture.png)')
        with connect(self.db) as db: save_note(db,self.public,self.file.read_text(),'local')
        asset=self.source/'Frontend/static/picture.png'; asset.parent.mkdir(); asset.write_bytes(b'local-image')
        sync_once(self.config); self.assertEqual((self.content/'Frontend/static/picture.png').read_bytes(),b'local-image')
        asset.write_bytes(b'local-image-update'); sync_once(self.config)
        self.assertEqual((self.content/'Frontend/static/picture.png').read_bytes(),b'local-image-update')
        (self.content/'Frontend/static/picture.png').write_bytes(b'server-image-update'); sync_once(self.config)
        self.assertEqual(asset.read_bytes(),b'server-image-update')
        catalog={'name':'Frontend','path':'Frontend','documents':[{'path':'example.md','title':'example','summary':'new summary','size':1}]}
        (self.source/'categories.json').write_text(json.dumps(catalog)); sync_once(self.config)
        self.assertEqual(self.current()['summary'],'new summary')
        note=self.current(); self.owner.post(f"/n/{note['id']}/metadata",data=self.form(record_sha=record_sha(note),category='Frontend / new',summary='website summary'))
        sync_once(self.config); sync_once(self.config)
        self.assertEqual(self.current()['summary'],'website summary')

    def test_sync_endpoints_reject_unauthorized_and_unlinked_assets(self):
        self.assertEqual(self.anon.get('/api/sync/v2/state').status_code,401)
        self.assertEqual(self.anon.post('/api/sync/v2/rename',json={}).status_code,401)
        self.assertEqual(self.anon.get('/admin/sync/status').status_code,403)
        response=self.anon.post(f"/api/sync/v2/asset/{self.public['id']}",headers={'Authorization':'Bearer test-token'},json={'path':'Developer/private.png','base_sha':'','data':'eA=='})
        self.assertEqual(response.status_code,404)

    def test_public_catalog_updates_without_private_entries(self):
        (self.source/'SCHEMA').mkdir(); (self.source/'WIKI').mkdir(); (self.source/'Backend').mkdir()
        shutil.copy(BASE.parent/'SCHEMA/wiki.py',self.source/'SCHEMA/wiki.py')
        (self.source/'WIKI.md').write_text('')
        (self.source/'WIKI/补充资料.md').write_text('只收录 Backend 与 Frontend。')
        catalog={'name':'Private','slug':'private','path':'/Private','documents':[],
                 'children':[{'name':name,'slug':name.lower(),'path':name,'documents':[],'children':[]} for name in ('Backend','Frontend')],
                 'stats':{'total_documents':0,'total_size':0,'generated_at':'2026-10-01'}}
        (self.source/'categories.json').write_text(json.dumps(catalog))
        self.owner.post('/new',data=self.form(path='Developer/from-web-private.md',body='hidden-body'))
        sync_once(self.config)
        result=json.loads((self.source/'categories.json').read_text())
        self.assertEqual(result['stats']['total_documents'],1)
        self.assertNotIn('Developer',(self.source/'categories.json').read_text())
        self.assertNotIn('hidden-body',(self.source/'WIKI/分类目录.md').read_text())
        self.assertTrue((self.source/'Developer/from-web-private.md').is_file())
        note=self.current(); self.owner.post(f"/n/{note['id']}/metadata",data=self.form(record_sha=record_sha(note),category='Frontend / 新分类',summary='网站摘要'))
        sync_once(self.config)
        self.assertIn('网站摘要',(self.source/'categories.json').read_text())
        self.assertIn('新分类',(self.source/'WIKI/分类目录.md').read_text())

    def test_attachment_conflict_preserves_both_copies(self):
        self.file.write_text('![image](static/a.png)')
        with connect(self.db) as db: save_note(db,self.public,self.file.read_text(),'local')
        asset=self.source/'Frontend/static/a.png'; asset.parent.mkdir(); asset.write_bytes(b'base')
        sync_once(self.config)
        asset.write_bytes(b'local-new'); (self.content/'Frontend/static/a.png').write_bytes(b'server-new')
        self.assertEqual(sync_once(self.config)['conflicts'],1)
        self.assertEqual(asset.read_bytes(),b'local-new')
        self.assertEqual((self.content/'Frontend/static/a.png').read_bytes(),b'server-new')
        self.assertEqual((self.root/'work/conflicts/Frontend/static/a.png.server.png').read_bytes(),b'server-new')

    def prepare_catalog_workspace(self):
        (self.source/'SCHEMA').mkdir(); (self.source/'WIKI').mkdir(); (self.source/'Backend').mkdir()
        shutil.copy(BASE.parent/'SCHEMA/wiki.py',self.source/'SCHEMA/wiki.py')
        (self.source/'WIKI.md').write_text('')
        (self.source/'WIKI/补充资料.md').write_text('只收录 Backend 与 Frontend。')
        catalog={'name':'Private','slug':'private','path':'/Private','documents':[],
                 'children':[{'name':name,'slug':name.lower(),'path':name,'documents':[],'children':[]} for name in ('Backend','Frontend')],
                 'stats':{'total_documents':0,'total_size':0,'generated_at':'2026-10-01'}}
        (self.source/'categories.json').write_text(json.dumps(catalog))

    def test_renamed_catalog_entry_does_not_block_sync_or_publish_private_files(self):
        self.prepare_catalog_workspace(); sync_once(self.config)
        original=self.file.read_text(); self.file.rename(self.source/'Frontend/renamed.md')
        (self.source/'Developer').mkdir(); secret=self.source/'Developer/secret.md'; secret.write_text('PRIVATE BODY')
        (self.source/'Frontend/linked.md').symlink_to(secret)
        (self.source/'Frontend/static').mkdir(); (self.source/'Frontend/static/hidden.md').write_text('PRIVATE BODY')
        (self.source/'Frontend/.hidden').mkdir(); (self.source/'Frontend/.hidden/hidden.md').write_text('PRIVATE BODY')
        counts=sync_once(self.config)
        self.assertEqual(counts['uploaded'],1)
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT body FROM notes WHERE path='Frontend/renamed.md'").fetchone()[0],original)
            self.assertIsNone(db.execute("SELECT body FROM notes WHERE path='Frontend/example.md'").fetchone())
            self.assertEqual(db.execute("SELECT id FROM notes WHERE path='Frontend/renamed.md'").fetchone()[0],self.public['id'])
            paths=[row[0] for row in db.execute('SELECT path FROM notes')]
            self.assertNotIn('Frontend/linked.md',paths); self.assertNotIn('Frontend/static/hidden.md',paths)
            self.assertNotIn('Frontend/.hidden/hidden.md',paths)
            self.assertEqual(db.execute('SELECT status FROM sync_runs ORDER BY id DESC LIMIT 1').fetchone()[0],'success')
        catalog=(self.source/'categories.json').read_text(); wiki=(self.source/'WIKI/分类目录.md').read_text()
        self.assertIn('renamed.md',catalog); self.assertNotIn('example.md',catalog)
        self.assertNotIn('PRIVATE BODY',catalog+wiki); self.assertNotIn('Developer',catalog+wiki)
        self.assertIn('renamed.md',wiki); self.assertNotIn('example.md',wiki)
        backup=self.source/'knowledge-site/work/catalog-before-sync.json'
        self.assertIn('example.md',backup.read_text())

    def test_rename_with_body_edit_preserves_id_and_history(self):
        sync_once(self.config)
        renamed=self.source/'Frontend/renamed.md'
        self.file.rename(renamed); renamed.write_text('Edited after rename')
        counts=sync_once(self.config)
        self.assertEqual(counts['uploaded'],1); self.assertEqual(counts['conflicts'],0)
        self.assertEqual(self.current()['path'],'Frontend/renamed.md')
        self.assertEqual(self.current()['body'],'Edited after rename')
        self.assertEqual(sync_once(self.config)['uploaded'],0)
        with connect(self.db) as db:
            history=[row[0] for row in db.execute('SELECT body FROM revisions WHERE note_id=?',(self.public['id'],))]
            self.assertIn(self.public['body'],history); self.assertIn('Edited after rename',history)

    def test_rename_rejects_concurrent_website_change(self):
        sync_once(self.config)
        self.file.rename(self.source/'Frontend/renamed.md')
        self.edit('Concurrent website edit')
        counts=sync_once(self.config)
        self.assertEqual(counts['conflicts'],1)
        self.assertEqual(self.current()['body'],'Concurrent website edit')
        self.assertEqual(self.current()['path'],'Frontend/example.md')
        with connect(self.db) as db:
            self.assertIsNone(db.execute("SELECT id FROM notes WHERE path='Frontend/renamed.md'").fetchone())

    def test_rename_endpoint_checks_both_versions_and_preserves_nonempty_conflicts(self):
        headers={'Authorization':'Bearer test-token'}
        original=self.current()
        with connect(self.db) as db:
            target=add_note(db,'Frontend/renamed.md',original['body'],category='Frontend',visibility='public')
        payload=dict(from_path='Frontend/example.md',path='Frontend/renamed.md',body=target['body'],base_sha=record_sha(original),target_sha=record_sha(target))
        self.assertEqual(self.anon.post('/api/sync/v2/rename',headers=headers,json={**payload,'target_sha':'stale'}).status_code,409)
        self.assertEqual(self.anon.post('/api/sync/v2/rename',headers=headers,json={**payload,'base_sha':'stale'}).status_code,409)
        self.api.call('/api/sync/v2/conflict',dict(path=original['path'],local_body='unsynced local content',server_sha=record_sha(original)))
        self.assertEqual(self.anon.post('/api/sync/v2/rename',headers=headers,json=payload).status_code,409)
        self.assertEqual(self.current()['path'],'Frontend/example.md')
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT local_body FROM sync_conflicts WHERE note_id=?',(original['id'],)).fetchone()[0],'unsynced local content')

    def test_explicit_rename_consolidates_legacy_duplicates_and_preserves_records(self):
        sync_once(self.config)
        self.file.rename(self.source/'Frontend/renamed.md')
        (self.source/'Frontend/renamed.md').write_text('New title and edited body')
        original_call=self.api.call
        def legacy(route,payload=None):
            result=original_call(route,payload)
            if route=='/api/sync/v2/state': result.pop('capabilities',None)
            return result
        self.api.call=legacy; sync_once(self.config); self.api.call=original_call
        with connect(self.db) as db:
            duplicate=db.execute("SELECT * FROM notes WHERE path='Frontend/renamed.md'").fetchone()
            user=db.execute("SELECT id FROM users WHERE username='editor'").fetchone()[0]
            db.execute('INSERT INTO grants VALUES(?,?,1,1)',(user,duplicate['id']))
            db.execute('INSERT INTO reading VALUES(?,?,1,0.75,100)',(user,duplicate['id']))
            db.execute("INSERT INTO navigation_orders(user_id,kind,parent,keys_json) VALUES(?,'note','Frontend',?)",(user,json.dumps([str(duplicate['id']),str(self.public['id'])])))
        self.config['renames']={'Frontend/example.md':'Frontend/renamed.md'}
        counts=sync_once(self.config)
        self.assertEqual(counts['conflicts'],0)
        self.assertEqual(self.current()['path'],'Frontend/renamed.md')
        self.assertEqual(self.current()['body'],'New title and edited body')
        with connect(self.db) as db:
            self.assertIsNone(db.execute('SELECT id FROM notes WHERE id=?',(duplicate['id'],)).fetchone())
            self.assertEqual(db.execute('SELECT can_edit FROM grants WHERE user_id=? AND note_id=?',(user,self.public['id'])).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT position FROM reading WHERE user_id=? AND note_id=?',(user,self.public['id'])).fetchone()[0],0.75)
            self.assertEqual(json.loads(db.execute("SELECT keys_json FROM navigation_orders WHERE user_id=?",(user,)).fetchone()[0]),[str(self.public['id'])])
            self.assertEqual(db.execute('SELECT status FROM sync_conflicts WHERE note_id=?',(self.public['id'],)).fetchone()[0],'applied')
            self.assertGreater(add_note(db,'Frontend/new.md','new',category='Frontend',visibility='public')['id'],duplicate['id'])
        response=self.anon.get(f"/n/{duplicate['id']}",follow_redirects=False)
        self.assertEqual(response.status_code,308); self.assertEqual(response.headers['location'],f"/n/{self.public['id']}")
        self.assertEqual(sync_once(self.config)['conflicts'],0)

    def test_rename_recovers_lost_checkpoint_and_old_manifest_does_not_resurrect_note(self):
        sync_once(self.config)
        state_path=Path(self.config['state_path']).with_suffix('.v2.json')
        checkpoint=state_path.read_text()
        self.file.rename(self.source/'Frontend/renamed.md'); sync_once(self.config)
        state_path.write_text(checkpoint)
        self.assertEqual(sync_once(self.config)['conflicts'],0)
        self.assertIn('Frontend/renamed.md',json.loads(state_path.read_text())['notes'])
        (self.content/'Frontend').mkdir(exist_ok=True)
        (self.content/'Frontend/example.md').write_text(self.public['body'])
        (self.content/'manifest.json').write_text(json.dumps([dict(path='Frontend/example.md',title='example',summary='',category='Frontend',visibility='public')]))
        create_app(self.db,self.content,sync_token_hash=digest('test-token'))
        with connect(self.db) as db:
            self.assertIsNone(db.execute("SELECT id FROM notes WHERE path='Frontend/example.md'").fetchone())

    def private_source(self):
        file=self.source/self.private['path']; file.parent.mkdir(parents=True,exist_ok=True)
        file.write_text(self.private['body'])
        return file

    def test_private_move_and_edit_preserves_identity_access_and_public_catalog_isolation(self):
        self.prepare_catalog_workspace(); file=self.private_source(); sync_once(self.config)
        with connect(self.db) as db:
            editor=db.execute("SELECT id FROM users WHERE username='editor'").fetchone()[0]
            db.execute('INSERT INTO grants VALUES(?,?,1,1)',(editor,self.private['id']))
        saved=self.owner.post('/api/sharing',headers={'X-CSRF-Token':self.csrf['owner']},
                              json={'note_id':self.private['id'],'audience':'link','version':0,'members':[]}).json()
        renamed=self.source/'Developer/project/new-title.md'; renamed.parent.mkdir()
        file.rename(renamed); renamed.write_text('Edited private move body')
        counts=sync_once(self.config)
        self.assertEqual(counts['conflicts'],0); self.assertEqual(counts['uploaded'],1)
        note=self.current(self.private['id'])
        self.assertEqual(note['path'],'Developer/project/new-title.md')
        self.assertEqual(note['title'],'new-title'); self.assertEqual(note['visibility'],'private')
        self.assertEqual(note['category'],self.private['category'])
        self.assertIn('Edited private move body',self.anon.get(saved['url']).text)
        self.assertEqual(self.clients['editor'].get(f"/n/{note['id']}/edit").status_code,200)
        self.assertEqual(self.anon.get(f"/n/{note['id']}").status_code,404)
        for path in ['categories.json','WIKI/分类目录.md']:
            self.assertNotIn('Developer/',(self.source/path).read_text())
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM note_moves WHERE note_id=?',(note['id'],)).fetchone()[0],1)
        self.assertEqual(sync_once(self.config)['uploaded'],0)

    def test_private_duplicate_from_old_client_is_merged_and_keeps_target_metadata_and_share(self):
        file=self.private_source(); sync_once(self.config)
        renamed=self.source/'Developer/project/renamed.md'; renamed.parent.mkdir()
        file.rename(renamed); renamed.write_text('Edited renamed private note')
        with connect(self.db) as db:
            duplicate=add_note(db,'Developer/project/renamed.md',renamed.read_text(),category='私人笔记 / 已整理',summary='Updated summary',visibility='private')
        # An old client tracks the new server article separately and reports the missing original.
        original=self.api.call
        def legacy(route,payload=None):
            result=original(route,payload)
            if route=='/api/sync/v2/state': result.pop('capabilities',None)
            return result
        self.api.call=legacy; sync_once(self.config); self.api.call=original
        share=self.owner.post('/api/sharing',headers={'X-CSRF-Token':self.csrf['owner']},
                              json={'note_id':duplicate['id'],'version':0,'audience':'link','members':[]}).json()
        counts=sync_once(self.config)
        self.assertEqual(counts['conflicts'],0)
        note=self.current(self.private['id'])
        self.assertEqual(note['path'],'Developer/project/renamed.md')
        self.assertEqual(note['summary'],'Updated summary'); self.assertEqual(note['category'],'私人笔记 / 已整理')
        self.assertEqual(note['body'],renamed.read_text())
        self.assertIn('Edited renamed private note',self.anon.get(share['url']).text)
        with connect(self.db) as db:
            self.assertIsNone(db.execute('SELECT id FROM notes WHERE id=?',(duplicate['id'],)).fetchone())
            self.assertEqual(db.execute('SELECT status FROM sync_conflicts WHERE note_id=?',(note['id'],)).fetchone()[0],'applied')
            self.assertEqual(db.execute('SELECT note_id FROM note_redirects WHERE old_id=?',(duplicate['id'],)).fetchone()[0],note['id'])

    def test_private_move_default_directory_and_crash_checkpoint_recovery(self):
        file=self.private_source()
        with connect(self.db) as db: db.execute('UPDATE notes SET category=? WHERE id=?',('私人笔记 / Developer',self.private['id']))
        sync_once(self.config)
        checkpoint=Path(self.config['state_path']).with_suffix('.v2.json'); before=checkpoint.read_text()
        renamed=self.source/'Developer/new-directory/new.md'; renamed.parent.mkdir(); file.rename(renamed)
        sync_once(self.config)
        self.assertEqual(self.current(self.private['id'])['category'],'私人笔记 / Developer / new-directory')
        checkpoint.write_text(before)
        self.assertEqual(sync_once(self.config)['conflicts'],0)
        self.assertIn('Developer/new-directory/new.md',json.loads(checkpoint.read_text())['notes'])
        self.assertNotIn('Developer/secret.md',json.loads(checkpoint.read_text())['notes'])

    def test_public_cross_directory_move_updates_default_category_without_duplicate(self):
        sync_once(self.config)
        renamed=self.source/'Backend/topic/renamed.md'; renamed.parent.mkdir(parents=True); self.file.rename(renamed)
        counts=sync_once(self.config)
        self.assertEqual(counts['conflicts'],0)
        self.assertEqual(self.current()['path'],'Backend/topic/renamed.md')
        self.assertEqual(self.current()['category'],'Backend / topic')

    def test_old_server_blocks_private_move_and_reports_upgrade_instead_of_duplicate(self):
        file=self.private_source(); sync_once(self.config)
        file.rename(self.source/'Developer/new-name.md')
        original=self.api.call
        def older(route,payload=None):
            result=original(route,payload)
            if route=='/api/sync/v2/state': result['capabilities']=['rename']
            return result
        self.api.call=older
        counts=sync_once(self.config)
        self.assertEqual(counts['conflicts'],1)
        with connect(self.db) as db:
            self.assertIsNone(db.execute("SELECT id FROM notes WHERE path='Developer/new-name.md'").fetchone())
            self.assertIn('服务器需升级',db.execute('SELECT message FROM sync_runs ORDER BY id DESC LIMIT 1').fetchone()[0])

    def test_private_move_keeps_concurrent_website_version_and_unknown_private_files_local(self):
        file=self.private_source(); sync_once(self.config)
        file.rename(self.source/'Developer/new-name.md')
        (self.source/'Developer/unrelated.md').write_text('Not authorized for import')
        with connect(self.db) as db: save_note(db,self.current(self.private['id']),'Concurrent website body','web','owner')
        counts=sync_once(self.config)
        self.assertEqual(counts['conflicts'],1)
        self.assertEqual(self.current(self.private['id'])['body'],'Concurrent website body')
        with connect(self.db) as db:
            self.assertIsNone(db.execute("SELECT id FROM notes WHERE path='Developer/unrelated.md'").fetchone())

    def test_ambiguous_content_matches_are_held_without_creating_public_duplicates(self):
        sync_once(self.config)
        body=self.file.read_text(); self.file.unlink()
        for name in ['one.md','two.md']: (self.source/'Frontend'/name).write_text(body)
        counts=sync_once(self.config)
        self.assertEqual(counts['conflicts'],1)
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM notes WHERE path LIKE 'Frontend/%'").fetchone()[0],1)
            self.assertIn('改名候选不唯一',db.execute('SELECT message FROM sync_runs ORDER BY id DESC LIMIT 1').fetchone()[0])

    def test_reused_inode_without_birth_time_does_not_hide_rename_ambiguity(self):
        from unittest.mock import patch
        from sync_v2 import rename_candidates
        body=self.file.read_text();self.file.unlink()
        names=['Frontend/one.md','Frontend/two.md']
        for name in names: (self.source/name).write_text(body)
        state={'Frontend/example.md':{'body_sha':digest(body),'file_identity':[1,2,None]}}
        notes={'Frontend/example.md':{'visibility':'public'}}
        ambiguous={}
        with patch('sync_v2.file_identity',side_effect=lambda path:[1,2,None] if path.name=='one.md' else [1,3,None]):
            result=rename_candidates(self.source.resolve(),dict.fromkeys(names),notes,state,{},ambiguous)
        self.assertEqual(result,{})
        self.assertEqual(set(ambiguous['Frontend/example.md']),set(names))

    def test_rename_rejects_privacy_boundary_and_tool_or_symlink_targets(self):
        file=self.private_source(); sync_once(self.config)
        headers={'Authorization':'Bearer test-token'}
        note=self.current(self.private['id'])
        for path in ['Frontend/published.md','WIKI/leak.md','knowledge-site/leak.md','Developer/../Frontend/leak.md','Developer/static/leak.md']:
            response=self.anon.post('/api/sync/v2/rename',headers=headers,json={
                'from_path':note['path'],'path':path,'base_sha':record_sha(note),'target_sha':'','body':note['body']})
            self.assertEqual(response.status_code,400,response.text)
        file.unlink(); (self.source/'Developer/link.md').symlink_to(self.file)
        self.assertEqual(sync_once(self.config)['conflicts'],1)
        self.assertEqual(self.current(self.private['id'])['path'],self.private['path'])

    def test_obsidian_static_embed_upload_render_download_and_code_isolation(self):
        body='![[思路.png|800]]\n\n`![[code.png]]`\n\n```md\n![[fence.png]]\n```\n\n\\![[escaped.png]]'
        self.file.write_text(body)
        with connect(self.db) as db: save_note(db,self.public,body,'local')
        asset=self.source/'Frontend/static/思路.png'; asset.parent.mkdir(); asset.write_bytes(b'image-one')
        sync_once(self.config)
        self.assertEqual((self.content/'Frontend/static/思路.png').read_bytes(),b'image-one')
        reading=self.anon.get(f"/n/{self.public['id']}")
        self.assertIn('<img src="/asset/',reading.text)
        self.assertIn('<code>![[code.png]]</code>',reading.text)
        self.assertEqual(self.anon.get(f"/asset/{self.public['id']}",params={'path':'static/思路.png'}).content,b'image-one')
        self.assertEqual(self.anon.get(f"/asset/{self.public['id']}",params={'path':'static/code.png'}).status_code,404)
        self.assertEqual(self.anon.get(f"/asset/{self.public['id']}",params={'path':'static/escaped.png'}).status_code,404)
        preview=self.owner.post(f"/n/{self.public['id']}/preview",data=self.form(body=body))
        self.assertIn('<img src="/asset/',preview.text)
        (self.content/'Frontend/static/思路.png').write_bytes(b'image-two')
        sync_once(self.config); self.assertEqual(asset.read_bytes(),b'image-two')

    def test_obsidian_uppercase_static_and_cross_root_attachment_guards(self):
        body='![[图.png]]\n\n![[../Developer/secret.png]]'
        self.file.write_text(body)
        with connect(self.db) as db: save_note(db,self.public,body,'local')
        asset=self.source/'Frontend/Static/图.png'; asset.parent.mkdir(); asset.write_bytes(b'uppercase-image')
        sync_once(self.config)
        self.assertEqual((self.content/'Frontend/Static/图.png').read_bytes(),b'uppercase-image')
        self.assertEqual(self.anon.get(f"/asset/{self.public['id']}",params={'path':'Static/图.png'}).content,b'uppercase-image')
        self.assertEqual(self.anon.get(f"/asset/{self.public['id']}",params={'path':'../Developer/secret.png'}).status_code,404)

    def test_missing_catalog_is_rebuilt_without_losing_website_metadata(self):
        self.prepare_catalog_workspace(); sync_once(self.config)
        note=self.current()
        self.owner.post(f"/n/{note['id']}/metadata",data=self.form(record_sha=record_sha(note),category='Frontend / 网站分类',summary='网站摘要'))
        sync_once(self.config)
        (self.source/'categories.json').unlink()
        (self.source/'Frontend/new.md').write_text('new public note')
        counts=sync_once(self.config)
        self.assertEqual(counts['uploaded'],1)
        self.assertEqual(self.current()['summary'],'网站摘要')
        self.assertEqual(self.current()['category'],'Frontend / 网站分类')
        result=json.loads((self.source/'categories.json').read_text())
        self.assertEqual(result['stats']['total_documents'],2)
        self.assertIn('new.md',(self.source/'WIKI/分类目录.md').read_text())

    def test_case_alias_uses_existing_catalog_path_and_does_not_create_another_note(self):
        if not (self.source/'Frontend/EXAMPLE.md').is_file():
            self.skipTest('Case-sensitive filesystem')
        self.prepare_catalog_workspace(); sync_once(self.config)
        path=self.source/'categories.json'; data=json.loads(path.read_text())
        frontend=next(node for node in data['children'] if node['path']=='Frontend')
        original=frontend['documents'][0]
        frontend['documents'].append({**original,'slug':'case-alias','path':'Frontend/EXAMPLE.md','title':'EXAMPLE'})
        path.write_text(json.dumps(data))
        counts=sync_once(self.config)
        self.assertEqual(counts['uploaded'],0)
        data=json.loads(path.read_text()); self.assertEqual(data['stats']['total_documents'],1)
        with connect(self.db) as db:
            self.assertIsNone(db.execute("SELECT id FROM notes WHERE path='Frontend/EXAMPLE.md'").fetchone())

    def test_failed_sync_reports_failure_without_token_or_body(self):
        sync_once(self.config)
        original=self.api.call
        def fail(route,payload=None):
            if route=='/api/sync/v2/note': raise RuntimeError('Sync request failed (503)')
            return original(route,payload)
        self.api.call=fail; self.file.write_text('private-test-body')
        with self.assertRaises(RuntimeError): sync_once(self.config)
        response=self.owner.get('/admin/sync/status').json()
        self.assertEqual(response['latest']['status'],'failed')
        self.assertIn('503',response['latest']['message'])
        self.assertNotIn('test-token',response['latest']['message']); self.assertNotIn('private-test-body',response['latest']['message'])


if __name__=='__main__': unittest.main()
