import concurrent.futures
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
from fastapi import HTTPException
from app import create_app
from document_parser import extract
from imports import parse_job
from storage import connect, create_session, create_user, digest
from version_diff import make_diff


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root/'content').mkdir()
        self.db = self.root/'site.db'
        self.app = create_app(self.db, self.root/'content', sync_token_hash=digest('test-sync'))
        self.clients, self.csrf, self.ids = {}, {}, {}
        with connect(self.db) as db:
            for name, role in [('alice', 'reader'), ('bob', 'reader'), ('admin', 'admin')]:
                create_user(db, name, 'import-tests-password', role)
                self.ids[name] = db.execute('SELECT id FROM users WHERE username=?', (name,)).fetchone()[0]
                token, self.csrf[name] = create_session(db, self.ids[name])
                client = TestClient(self.app)
                client.cookies.set('kb_session', token)
                self.clients[name] = client
        self.anon = TestClient(self.app)
        self.team = self.clients['alice'].post('/api/teams', json={'name': '资料空间'}, headers=self.headers('alice')).json()['id']

    def tearDown(self):
        self.app.state.import_executor.shutdown(wait=True, cancel_futures=True)
        for client in [*self.clients.values(), self.anon]:
            client.close()
        self.temp.cleanup()

    def headers(self, who):
        return {'X-CSRF-Token': self.csrf[who]}

    def upload(self, who='alice', filename='资料.md', raw=b'# Import\n\nOriginal body', note_format='markdown'):
        return self.clients[who].post('/api/imports', params={'filename': filename, 'note_format': note_format}, content=raw,
                                      headers={**self.headers(who), 'Content-Type': 'application/octet-stream'})

    def ready(self, who='alice', **kwargs):
        response = self.upload(who, **kwargs)
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()['id']
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            job = self.clients[who].get(f'/api/imports/{job_id}').json()
            if job['status'] not in {'queued', 'parsing'}:
                return job
            time.sleep(.02)
        self.fail('import worker did not finish')

    def post(self, who, job, suffix, **values):
        return self.clients[who].post(f'/api/imports/{job["id"]}/{suffix}', json={'version': job['version'], **values}, headers=self.headers(who))

    @staticmethod
    def pdf_bytes(text=b'PDF source text', encrypted=False):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer = PdfWriter()
        page = writer.add_blank_page(612, 792)
        if text:
            font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(b'BT /F1 12 Tf 72 720 Td (' + text + b') Tj ET')
            page[NameObject('/Contents')] = writer._add_object(stream)
        if encrypted:
            writer.encrypt('test-password')
        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()

    def wait_job(self, who, job_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = self.clients[who].get(f'/api/imports/{job_id}').json()
            if job['status'] not in {'queued', 'parsing'}:
                return job
            time.sleep(.02)
        self.fail('PDF import worker did not finish')

    def test_scanned_pdf_note_survives_cleanup_and_share_revocation(self):
        raw = self.pdf_bytes(text=None)
        job = self.ready('admin', filename='扫描.pdf', raw=raw, note_format='pdf')
        self.assertEqual(job['status'], 'ready', job['error'])
        self.assertEqual(job['body'], '')
        edited = self.clients['admin'].put(f'/api/imports/{job["id"]}', json={
            'version': job['version'], 'title': '扫描讲义', 'body': ''}, headers=self.headers('admin')).json()
        result = self.post('admin', edited, 'commit', team_id=0, path='Developer/Imports/scanned.md')
        self.assertEqual(result.status_code, 200, result.text)
        url = result.json()['url']
        pdf_url = url + '/pdf'
        self.assertIn('PDF 原文阅读', self.clients['admin'].get(url).text)
        self.assertIn('扫描讲义', self.clients['admin'].get('/browse').text)
        original = self.clients['admin'].get(pdf_url)
        self.assertEqual(original.content, raw)
        self.assertEqual(original.headers['x-frame-options'], 'SAMEORIGIN')
        self.assertIn("frame-ancestors 'self'", original.headers['content-security-policy'])
        self.assertEqual(original.headers['cache-control'], 'no-store')
        self.assertIn('attachment;', self.clients['admin'].get(pdf_url+'?download=true').headers['content-disposition'])
        for client in [self.anon, self.clients['bob']]:
            self.assertEqual(client.get(pdf_url).status_code, 404)
        note_id = int(url.rsplit('/', 1)[-1])
        shared = self.clients['admin'].post('/api/sharing', json={
            'note_id': note_id, 'version': 0, 'audience': 'link', 'members': []}, headers=self.headers('admin')).json()
        shared_pdf = shared['url'] + f'/pdf/{note_id}'
        self.assertIn(shared_pdf, self.anon.get(shared['url']).text)
        self.assertEqual(self.anon.get(shared_pdf).content, raw)
        self.assertEqual(self.anon.get(pdf_url+'?token='+shared['url'].split('/')[-1]).status_code, 404)
        self.clients['admin'].delete(f'/api/imports/{job["id"]}', headers=self.headers('admin'))
        self.assertEqual(self.clients['admin'].get(pdf_url).content, raw)
        self.assertEqual(self.clients['admin'].post(pdf_url+'/convert', data={}).status_code, 403)
        self.assertEqual(self.clients['bob'].post(pdf_url+'/convert', data={'csrf': self.csrf['bob']}).status_code, 404)
        conversion = self.clients['admin'].post(pdf_url+'/convert', data={'csrf': self.csrf['admin']}, follow_redirects=False)
        self.assertEqual(conversion.status_code, 303)
        self.assertTrue(conversion.headers['location'].startswith('/imports?job='))
        duplicate = self.ready('admin', filename='再次上传.pdf', raw=raw, note_format='pdf')
        self.assertEqual(self.post('admin', duplicate, 'commit', team_id=0, path='Developer/Imports/duplicate.md').status_code, 409)
        self.clients['admin'].post('/api/sharing', json={
            'note_id': note_id, 'version': shared['version'], 'audience': 'restricted', 'members': []}, headers=self.headers('admin'))
        self.assertEqual(self.anon.get(shared_pdf).status_code, 404)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM notes').fetchone()[0], 1)

    def test_pdf_conversion_exports_and_team_permission_retention(self):
        raw = self.pdf_bytes()
        source = self.ready(filename='讲义.pdf', raw=raw, note_format='pdf')
        committed = self.post('alice', source, 'commit', team_id=self.team)
        self.assertEqual(committed.status_code, 200, committed.text)
        doc_id = int(committed.json()['url'].split('document=')[1])
        route = f'/api/teams/{self.team}/documents/{doc_id}'
        detail = self.clients['alice'].get(route).json()
        self.assertEqual(detail['note_format'], 'pdf')
        self.assertEqual(detail['pdf']['filename'], '讲义.pdf')
        pdf_url = detail['pdf']['url']
        self.assertEqual(self.clients['alice'].get(pdf_url).content, raw)
        for who in ['bob', 'admin']:
            self.assertEqual(self.clients[who].get(pdf_url).status_code, 404)
        self.assertEqual(self.anon.get(pdf_url).status_code, 401)
        conversion = self.post('alice', source, 'convert')
        self.assertEqual(conversion.status_code, 202, conversion.text)
        converted = self.wait_job('alice', conversion.json()['id'])
        self.assertNotEqual(source['id'], converted['id'])
        self.assertEqual(converted['note_format'], 'markdown')
        self.assertIn('PDF source text', converted['body'])
        repeated = self.post('alice', source, 'convert')
        self.assertEqual(repeated.json()['id'], converted['id'])
        export = f'/api/imports/{converted["id"]}/export'
        plain = self.clients['alice'].get(export+'?format=txt')
        self.assertIn('PDF source text', plain.text)
        self.assertNotIn('## 第', plain.text)
        self.assertEqual(plain.headers['cache-control'], 'no-store')
        self.assertEqual(self.clients['bob'].get(export).status_code, 404)
        self.assertEqual(self.clients['alice'].get(export+'?format=html').status_code, 400)
        self.assertEqual(self.clients['alice'].get(f'/api/imports/{source["id"]}/export').status_code, 409)
        edited = self.clients['alice'].put(f'/api/imports/{converted["id"]}', json={
            'version': converted['version'], 'title': '修正讲义', 'body': '# Corrected text'}, headers=self.headers('alice')).json()
        self.assertEqual(self.clients['alice'].get(export).text, '# Corrected text')
        self.assertIn('PDF source text', self.clients['alice'].get(export+'?format=txt').text)
        markdown = self.post('alice', edited, 'commit', team_id=self.team)
        self.assertEqual(markdown.status_code, 200, markdown.text)
        md_id = int(markdown.json()['url'].split('document=')[1])
        md_detail = self.clients['alice'].get(f'/api/teams/{self.team}/documents/{md_id}').json()
        self.assertEqual(md_detail['note_format'], 'markdown')
        self.assertIsNotNone(md_detail['pdf'])
        with connect(self.db) as db:
            db.execute("INSERT INTO team_members VALUES(?,?,'reader',?)", (self.team, self.ids['bob'], int(time.time())))
        self.assertEqual(self.clients['bob'].get(pdf_url).content, raw)
        with connect(self.db) as db:
            db.execute('DELETE FROM team_members WHERE team_id=? AND user_id=?', (self.team, self.ids['bob']))
        self.assertEqual(self.clients['bob'].get(pdf_url).status_code, 404)
        self.clients['alice'].delete(f'/api/imports/{source["id"]}', headers=self.headers('alice'))
        self.clients['alice'].delete(f'/api/imports/{converted["id"]}', headers=self.headers('alice'))
        self.assertEqual(self.clients['alice'].get(pdf_url).content, raw)
        self.assertEqual(self.clients['alice'].get(md_detail['pdf']['url']).content, raw)
        from pdf_notes import team_storage_usage
        with connect(self.db) as db:
            self.assertEqual(team_storage_usage(db, self.team), (len(raw) * 2, 2))
        from unittest.mock import patch
        with patch('pdf_notes.team_storage_usage', return_value=(100 * 1024 * 1024, 2)):
            blocked = self.clients['alice'].post(f'/teams/{self.team}/documents/{doc_id}/attachments',
                data={'csrf': self.csrf['alice']}, files={'file': ('image.png', b'\x89PNG\r\n\x1a\nsynthetic', 'image/png')})
        self.assertEqual(blocked.status_code, 409)
        saved_conversion = self.clients['alice'].post(f'/teams/{self.team}/documents/{doc_id}/pdf/convert', data={'csrf': self.csrf['alice']}, follow_redirects=False)
        self.assertEqual(saved_conversion.status_code, 303)
        self.assertEqual(self.clients['bob'].post(f'/teams/{self.team}/documents/{doc_id}/pdf/convert', data={'csrf': self.csrf['bob']}).status_code, 404)

    def test_pdf_validation_and_transactional_storage_limit(self):
        self.assertEqual(self.upload(note_format='pdf').status_code, 400)
        self.assertEqual(self.upload(note_format='other').status_code, 400)
        self.assertEqual(self.ready(filename='bad.pdf', raw=b'%PDF-invalid', note_format='pdf')['status'], 'failed')
        encrypted = self.ready(filename='encrypted.pdf', raw=self.pdf_bytes(encrypted=True), note_format='pdf')
        self.assertEqual(encrypted['status'], 'failed')
        self.assertIn('加密', encrypted['error'])
        scan = self.ready(filename='scan.pdf', raw=self.pdf_bytes(text=None))
        self.assertEqual(scan['status'], 'failed')
        job = self.ready(filename='valid.pdf', raw=self.pdf_bytes(), note_format='pdf')
        from unittest.mock import patch
        with patch('imports.save_pdf', side_effect=HTTPException(409, 'PDF 空间不足')):
            result = self.post('alice', job, 'commit', team_id=self.team)
        self.assertEqual(result.status_code, 409)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM team_documents').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM document_pdfs').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT status FROM document_imports WHERE id=?', (job['id'],)).fetchone()[0], 'ready')

    def test_note_consolidation_preserves_pdf_and_rejects_different_sources(self):
        from features import add_note
        from note_renames import merge_duplicate
        job = self.ready('admin', filename='source.pdf', raw=self.pdf_bytes(), note_format='pdf')
        created = self.post('admin', job, 'commit', team_id=0, path='Developer/source.md')
        self.assertEqual(created.status_code, 200, created.text)
        source_id = int(created.json()['url'].rsplit('/', 1)[-1])
        with connect(self.db) as db:
            duplicate = db.execute('SELECT * FROM notes WHERE id=?', (source_id,)).fetchone()
            original = add_note(db, 'Developer/original.md', duplicate['body'])
            merge_duplicate(db, original, duplicate)
            original_id = original['id']
            self.assertEqual(db.execute('SELECT note_id FROM document_pdfs').fetchone()[0], original_id)
        self.assertEqual(self.clients['admin'].get(f'/n/{original_id}/pdf').content, self.pdf_bytes())
        another = self.ready('admin', filename='different.pdf', raw=self.pdf_bytes(text=b'Other source'), note_format='pdf')
        second = self.post('admin', another, 'commit', team_id=0, path='Developer/other.md')
        self.assertEqual(second.status_code, 200, second.text)
        second_id = int(second.json()['url'].rsplit('/', 1)[-1])
        with self.assertRaises(HTTPException), connect(self.db) as db:
            merge_duplicate(db, db.execute('SELECT * FROM notes WHERE id=?', (original_id,)).fetchone(), db.execute('SELECT * FROM notes WHERE id=?', (second_id,)).fetchone())
        self.assertEqual(self.clients['admin'].get(f'/n/{second_id}/pdf').content, self.pdf_bytes(text=b'Other source'))

    def test_auth_csrf_original_and_account_isolation(self):
        self.assertEqual(self.anon.get('/imports', follow_redirects=False).status_code, 303)
        self.assertEqual(self.anon.get('/api/imports').status_code, 401)
        self.assertEqual(self.clients['alice'].post('/api/imports?filename=a.md', content=b'x').status_code, 403)
        job = self.ready()
        self.assertEqual(job['status'], 'ready')
        listing = self.clients['alice'].get('/api/imports').json()
        self.assertNotIn('body', listing['jobs'][0])
        self.assertNotIn('original', listing['jobs'][0])
        self.assertFalse(listing['personal_allowed'])
        for who in ['bob', 'admin']:
            for suffix in ['', '/original']:
                self.assertEqual(self.clients[who].get(f'/api/imports/{job["id"]}'+suffix).status_code, 404)
        self.assertEqual(self.clients['alice'].get(f'/api/imports/{job["id"]}/original').content, b'# Import\n\nOriginal body')
        self.assertIn('no-store', self.clients['alice'].get('/api/imports').headers['cache-control'])

    def test_dedupe_edit_version_commit_and_repeated_commit(self):
        job = self.ready()
        again = self.upload()
        self.assertEqual(again.json()['id'], job['id'])
        self.assertTrue(again.json()['duplicate'])
        route = f'/api/imports/{job["id"]}'
        response = self.clients['alice'].put(route, json={'version': job['version'], 'title': '导入说明', 'body': '# Corrected\n<script>alert(1)</script>'}, headers=self.headers('alice'))
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        self.assertNotIn('<script>', updated['html'])
        self.assertEqual(self.post('alice', job, 'commit', team_id=self.team).status_code, 409)
        first = self.post('alice', updated, 'commit', team_id=self.team)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertIn('document=', first.json()['url'])
        repeat = self.post('alice', updated, 'commit', team_id=self.team)
        self.assertEqual(repeat.json()['url'], first.json()['url'])
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM team_documents').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM team_document_revisions').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM notes').fetchone()[0], 0)
        duplicate = self.ready(filename='same.md', raw=b'# Corrected\n<script>alert(1)</script>')
        self.assertEqual(self.post('alice', duplicate, 'commit', team_id=self.team).status_code, 409)

    def test_team_role_rechecked_and_cross_tenant_denied(self):
        job = self.ready()
        other = self.clients['bob'].post('/api/teams', json={'name': 'Other'}, headers=self.headers('bob')).json()['id']
        self.assertEqual(self.post('alice', job, 'commit', team_id=other).status_code, 404)
        self.assertEqual(self.post('alice', job, 'commit', team_id=0, path='Developer/x.md').status_code, 403)
        with connect(self.db) as db:
            db.execute("UPDATE team_members SET role='reader' WHERE team_id=?", (self.team,))
        self.assertEqual(self.post('alice', job, 'commit', team_id=self.team).status_code, 403)
        with connect(self.db) as db:
            db.execute('DELETE FROM team_members WHERE team_id=?', (self.team,))
        self.assertEqual(self.post('alice', job, 'commit', team_id=self.team).status_code, 404)

    def test_personal_private_default_public_confirmation_and_no_overwrite(self):
        job = self.ready('admin')
        self.assertEqual(self.post('admin', job, 'commit', team_id=0, path='Backend/Imports/a.md', visibility='public').status_code, 400)
        private = self.post('admin', job, 'commit', team_id=0, path='Developer/Imports/a.md')
        self.assertEqual(private.status_code, 200, private.text)
        self.assertEqual(self.anon.get(private.json()['url']).status_code, 404)
        with connect(self.db) as db:
            note = db.execute('SELECT * FROM notes').fetchone()
            self.assertEqual(note['visibility'], 'private')
            self.assertEqual(db.execute('SELECT status FROM edit_events').fetchone()[0], 'pending')
        next_job = self.ready('admin', raw=b'new content')
        self.assertEqual(self.post('admin', next_job, 'commit', team_id=0, path='Developer/Imports/a.md').status_code, 409)
        self.assertEqual(self.post('admin', next_job, 'commit', team_id=0, path='Backend/Imports/a.md', visibility='public', confirm_public=True).status_code, 200)
        bad = self.ready('admin', raw=b'bad path body')
        for path in ['Developer/../Frontend/x.md', 'work/a.md', 'Developer/static/a.md']:
            self.assertEqual(self.post('admin', bad, 'commit', team_id=0, path=path).status_code, 400)

    def test_private_import_rejects_existing_category_shares(self):
        job = self.ready('admin')
        with connect(self.db) as db:
            share = db.execute('''INSERT INTO shares(scope_key,category,token,audience,version)
                VALUES('category:私人笔记 / Shared','私人笔记 / Shared','test-category-share-token','link',1)''').lastrowid
        for category in ['私人笔记 / Shared', '私人笔记 / Shared / Child']:
            result = self.post('admin', job, 'commit', team_id=0, path='Developer/secret.md', category=category)
            self.assertEqual(result.status_code, 409, result.text)
        with connect(self.db) as db:
            db.execute("UPDATE shares SET audience='restricted' WHERE id=?", (share,))
            db.execute('INSERT INTO share_members(share_id,user_id,can_edit) VALUES(?,?,0)', (share, self.ids['bob']))
        self.assertEqual(self.post('admin', job, 'commit', team_id=0, path='Developer/secret.md', category='私人笔记 / Shared / Child').status_code, 409)
        with connect(self.db) as db:
            db.execute('DELETE FROM share_members WHERE share_id=?', (share,))
        result = self.post('admin', job, 'commit', team_id=0, path='Developer/secret.md', category='私人笔记 / Shared / Child')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.clients['bob'].get(result.json()['url']).status_code, 404)

    def test_failed_retry_cancel_delete_and_size_guards(self):
        job = self.ready(filename='broken.pdf', raw=b'not a pdf')
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(self.post('alice', job, 'retry').status_code, 200)
        self.app.state.import_parse = lambda _: None
        # Canceled/deleted rows cannot be resurrected by a late parser completion.
        queued = self.upload(filename='queued.md', raw=b'queued content').json()
        self.assertEqual(self.post('alice', queued, 'cancel').status_code, 200)
        parse_job(self.db, queued['id'])
        self.assertEqual(self.clients['alice'].get(f'/api/imports/{queued["id"]}').json()['status'], 'cancelled')
        self.assertEqual(self.clients['alice'].delete(f'/api/imports/{queued["id"]}', headers=self.headers('alice')).status_code, 200)
        self.assertEqual(self.upload(filename='../bad.md').status_code, 400)
        self.assertEqual(self.upload(filename='bad.exe').status_code, 400)
        self.assertEqual(self.upload(raw=b'').status_code, 400)
        self.assertEqual(self.upload(raw=b'x'*(10*1024*1024+1)).status_code, 413)

    def test_docx_tables_and_pdf_text_are_extracted(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>导入标题</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
        job = self.ready(filename='word.docx', raw=raw.getvalue())
        self.assertEqual(job['status'], 'ready', job['error'])
        self.assertIn('# 导入标题', job['body'])
        self.assertIn('| A |', job['body'])
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer = PdfWriter(); page = writer.add_blank_page(612, 792)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject(); stream.set_data(b'BT /F1 12 Tf 72 720 Td (Import PDF text) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
        pdf = io.BytesIO(); writer.write(pdf)
        job = self.ready(filename='text.pdf', raw=pdf.getvalue())
        self.assertEqual(job['status'], 'ready', job['error'])
        self.assertIn('Import PDF text', job['body'])

    def test_double_commit_is_transactional_and_original_cleanup_keeps_document(self):
        job = self.ready()
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.post('alice', job, 'commit', team_id=self.team), range(2)))
        self.assertEqual([r.status_code for r in results], [200, 200])
        self.assertEqual(results[0].json()['url'], results[1].json()['url'])
        self.clients['alice'].delete(f'/api/imports/{job["id"]}', headers=self.headers('alice'))
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM team_documents').fetchone()[0], 1)
        parse_job(self.db, job['id'])
        self.assertEqual(self.clients['alice'].get(f'/api/imports/{job["id"]}').status_code, 404)

    def test_team_diff_rechecks_membership_and_document_scope(self):
        route = f'/api/teams/{self.team}/documents'
        doc = self.clients['alice'].post(route, json={'title': 'v1', 'body': 'old\n'}, headers=self.headers('alice')).json()
        self.clients['alice'].put(route+'/'+str(doc['id']), json={'title': 'v2', 'body': 'new\nadded\n', 'version': 1}, headers=self.headers('alice'))
        endpoint = route+'/'+str(doc['id'])+'/diff?base=1&head=2'
        diff = self.clients['alice'].get(endpoint)
        self.assertEqual(diff.status_code, 200, diff.text)
        self.assertEqual((diff.json()['added'], diff.json()['deleted']), (2, 1))
        self.assertEqual(diff.json()['new_title'], 'v2')
        for who in ['bob', 'admin']:
            self.assertEqual(self.clients[who].get(endpoint).status_code, 404)
        self.assertEqual(self.clients['alice'].get(route+'/'+str(doc['id'])+'/diff?base=999').status_code, 404)
        with connect(self.db) as db:
            db.execute('DELETE FROM team_members WHERE team_id=?', (self.team,))
        self.assertEqual(self.clients['alice'].get(endpoint).status_code, 404)

    def test_personal_diff_escapes_html_and_rejects_foreign_revision(self):
        from storage import save_note
        job = self.ready('admin', raw=b'<script>old()</script>\n')
        url = self.post('admin', job, 'commit', team_id=0, path='Developer/diff.md').json()['url']
        note_id = int(url.rsplit('/',1)[1])
        with connect(self.db) as db:
            note = db.execute('SELECT * FROM notes WHERE id=?',(note_id,)).fetchone()
            revision = db.execute('SELECT id FROM revisions WHERE note_id=?',(note_id,)).fetchone()[0]
            save_note(db,note,'<script>new()</script>\n','web','admin')
        page = self.clients['admin'].get(url+'/history', params={'revision': revision})
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn('data-diff-view',page.text)
        self.assertNotIn('<script>old()',page.text)
        self.assertIn('&lt;script&gt;',page.text)
        patch = self.clients['admin'].get(url+'/diff.patch', params={'revision': revision})
        self.assertIn('-<script>old()</script>',patch.text)
        self.assertIn('+<script>new()</script>',patch.text)
        self.assertEqual(self.clients['bob'].get(url+'/diff.patch', params={'revision': revision}).status_code,404)
        self.assertEqual(self.clients['admin'].get(url+'/diff.patch', params={'revision': revision+999}).status_code,404)


class DiffTests(unittest.TestCase):
    def test_lines_inline_patch_and_missing_final_newline(self):
        value = make_diff('alpha\nold text\n', 'alpha\nnew text\nextra\n')
        self.assertEqual((value['added'], value['deleted']), (2, 1))
        self.assertIn('-old text\n+new text\n+extra\n', value['patch'])
        changed = value['hunks'][0]['split'][1]
        self.assertEqual((changed['old_no'], changed['new_no']), (2, 2))
        self.assertTrue(any(s['changed'] for s in changed['old']))
        self.assertTrue(make_diff('same', 'same\n')['newline_changed'])
        self.assertIn('\\ No newline at end of file\n', make_diff('old', 'new')['patch'])
        self.assertTrue(make_diff('same\n', 'same\n')['identical'])

    def test_bound_and_unchanged_context_folding(self):
        lines = [str(i) for i in range(100)]
        other = lines.copy(); other[50] = 'change'
        diff = make_diff('\n'.join(lines), '\n'.join(other))
        self.assertEqual(len(diff['hunks'][0]['split']), 7)
        from fastapi import HTTPException
        with self.assertRaises(HTTPException): make_diff('x'*1_000_001, '')
