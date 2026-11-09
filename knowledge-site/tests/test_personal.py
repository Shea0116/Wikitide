"""Reading/learning isolation, stale versions and search scopes on temporary data."""
import sys
import tempfile
import time
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
from app import create_app
from features import add_note
from storage import connect, create_user, create_session, save_note


class PersonalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.db=self.root/'site.db'
        self.app=create_app(self.db,self.root/'content'); self.client=TestClient(self.app)
        with connect(self.db) as db:
            create_user(db,'reader','test-personal-123','reader')
            self.uid=db.execute('SELECT id FROM users').fetchone()[0]
            token,self.csrf=create_session(db,self.uid); self.client.cookies.set('kb_session',token)
            self.note=add_note(db,'Frontend/example.md','# 示例\n\n## 章节\n\n这是复习的关键知识，足够生成一个本地摘录题目。',category='Frontend',visibility='public')
            self.secret=add_note(db,'Developer/secret.md','classified-secret',category='私人',visibility='private')
    def tearDown(self): self.client.close(); self.temp.cleanup()
    def post(self,path,**values): return self.client.post(path,data={'csrf':self.csrf,**values},follow_redirects=False)

    def test_library_paginates_and_filters_beyond_twenty(self):
        with connect(self.db) as db:
            for i in range(25):
                note=add_note(db,f'Frontend/item{i:02}.md','text',category='Frontend',visibility='public')
                db.execute('INSERT INTO reading VALUES(?,?,1,0,?)',(self.uid,note['id'],i+1))
        first=self.client.get('/library?tab=bookmarks'); second=self.client.get('/library?tab=bookmarks&page_number=2')
        self.assertIn('共 25 条',first.text); self.assertIn('下一页',first.text)
        self.assertNotIn('item00',first.text); self.assertIn('item00',second.text)
        self.assertIn('item03',self.client.get('/library?q=item03').text)

    def test_organization_annotation_and_revocation(self):
        path=f"/n/{self.note['id']}"
        self.assertEqual(self.post(path+'/organize',folder='路线甲',later='1',ordinal='2').status_code,303)
        self.assertIn('路线甲',self.client.get('/library?tab=later').text)
        self.assertEqual(self.post(path+'/annotate',body_sha=self.note['body_sha'],quote='<script>quote</script>',comment='仅自己可见').status_code,303)
        html=self.client.get('/library?tab=annotations').text
        self.assertIn('&lt;script&gt;quote',html); self.assertNotIn('<script>quote',html)
        with connect(self.db) as db: db.execute("UPDATE notes SET visibility='private' WHERE id=?",(self.note['id'],))
        self.assertNotIn('路线甲',self.client.get('/library').text)
        self.assertNotIn('仅自己可见',self.client.get('/library?tab=annotations').text)
        self.assertEqual(self.client.get(f"/api/reading/{self.note['id']}").status_code,404)

    def test_cards_require_confirmation_and_use_versioned_review(self):
        suggestion=self.client.get(f"/learn?note_id={self.note['id']}")
        self.assertIn('确认加入复习',suggestion.text)
        with connect(self.db) as db: self.assertEqual(db.execute('SELECT count(*) FROM review_cards').fetchone()[0],0)
        self.assertEqual(self.post('/learn/cards',note_id=str(self.note['id']),body_sha=self.note['body_sha'],question='问题',answer='答案',anchor='章节').status_code,303)
        self.assertEqual(self.post('/learn/cards/1/review',version='1',grade='again').status_code,303)
        self.assertEqual(self.post('/learn/cards/1/review',version='1',grade='good').status_code,409)
        self.assertIn('问题',self.client.get('/learn?filter=mistakes').text)
        with connect(self.db) as db:
            card=db.execute('SELECT * FROM review_cards').fetchone()
            self.assertEqual(card['mistakes'],1); self.assertGreater(card['due_at'],time.time()+500)
            save_note(db,self.note,'new body','web','reader')
        self.assertEqual(self.post('/learn/cards/1/review',version='2',grade='good').status_code,409)

    def test_private_note_cannot_generate_or_create_cards(self):
        self.assertEqual(self.client.get(f"/learn?note_id={self.secret['id']}").status_code,404)
        self.assertEqual(self.post('/learn/cards',note_id=str(self.secret['id']),body_sha=self.secret['body_sha'],question='q',answer='a').status_code,404)
        self.assertEqual(self.client.post(f"/n/{self.note['id']}/organize",data={'folder':'x'}).status_code,403)

    def test_search_filters_and_chapter_links(self):
        result=self.client.get('/search?q=关键&scope=public&days=7')
        self.assertEqual(result.status_code,200); self.assertIn('#%E7%AB%A0%E8%8A%82',result.text)
        self.assertNotIn('classified-secret',self.client.get('/search?q=classified&scope=all').text)
        self.assertEqual(self.client.get('/search?q=x&scope=team:999').status_code,404)
        self.assertEqual(self.client.get('/search?q=关键&mode=semantic&scope=public').status_code,200)

    def test_reference_cache_updates_only_changed_sources(self):
        with connect(self.db) as db:
            linking=add_note(db,'Frontend/linking.md','[[example]]',category='Frontend',visibility='public')
        self.assertIn('linking',self.client.get(f"/n/{self.note['id']}").text)
        with connect(self.db) as db:
            cached=db.execute('SELECT targets FROM note_reference_index WHERE note_id=?',(linking['id'],)).fetchone()[0]
            self.assertIn('example',cached)
            save_note(db,linking,'no links','web','test')
        self.client.get(f"/n/{self.note['id']}")
        with connect(self.db) as db:
            self.assertNotIn('example',db.execute('SELECT targets FROM note_reference_index WHERE note_id=?',(linking['id'],)).fetchone()[0])

    def test_search_chapter_anchor_matches_duplicate_heading_in_reading_html(self):
        with connect(self.db) as db:
            save_note(db,self.note,'## Same\n\nfirst\n\n## Same\n\nneedle-second','web','reader')
        result=self.client.get('/search?q=needle-second').text
        self.assertIn('#same-2',result)
        self.assertIn('id="same-2"',self.client.get(f"/n/{self.note['id']}").text)


if __name__=='__main__': unittest.main()
