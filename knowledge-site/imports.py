"""Account-owned import drafts, durable parsing jobs, and explicit transactional insertion."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, asynccontextmanager
import json
import re
from pathlib import Path, PurePosixPath
import sqlite3
import subprocess
import sys
import time
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from features import add_note, safe_note_path
from storage import connect, digest
from teams import WRITERS, rendered, text, version
from pdf_notes import initialize_pdfs, save_pdf, team_storage_usage

MAX_FILE = 10 * 1024 * 1024
MAX_STORAGE = 50 * 1024 * 1024
FIELDS = 'id,filename,file_sha,size,status,title,warnings,error,version,attempts,result_url,created_at,updated_at,note_format'


def initialize_imports(path):
    with connect(path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS document_imports (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            filename TEXT NOT NULL, file_sha TEXT NOT NULL, size INTEGER NOT NULL, original BLOB NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','parsing','ready','failed','committed','cancelled')),
            title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', warnings TEXT NOT NULL DEFAULT '[]',
            error TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1, attempts INTEGER NOT NULL DEFAULT 0,
            result_url TEXT NOT NULL DEFAULT '', target_team INTEGER,
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_import_user ON document_imports(user_id,id DESC);
        CREATE INDEX IF NOT EXISTS ix_import_sha ON document_imports(user_id,file_sha);
        ''')
        columns={row['name'] for row in db.execute('PRAGMA table_info(document_imports)')}
        for name,definition in [('assets',"TEXT NOT NULL DEFAULT '[]'"),('original_body',"TEXT NOT NULL DEFAULT ''"),('ocr','INTEGER NOT NULL DEFAULT 0'),('asset_size','INTEGER NOT NULL DEFAULT 0'),('note_format',"TEXT NOT NULL DEFAULT 'markdown'")]:
            if name not in columns: db.execute(f'ALTER TABLE document_imports ADD COLUMN {name} {definition}')
        initialize_pdfs(db)


def parse_job(db_path, job_id):
    with connect(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT * FROM document_imports WHERE id=? AND status='queued'", (job_id,)).fetchone()
        if row is None:
            return
        db.execute("UPDATE document_imports SET status='parsing',attempts=attempts+1,updated_at=? WHERE id=?", (int(time.time()), job_id))
        raw, suffix, attempt = row['original'], Path(row['filename']).suffix.lower(), row['attempts']+1
        ocr=bool(row['ocr'])
        pdf_note = row['note_format'] == 'pdf'
    try:
        process = subprocess.run([sys.executable, str(Path(__file__).with_name('document_parser.py')), suffix, *(['--ocr'] if ocr else []), *(['--pdf-note'] if pdf_note else [])],
                                 input=raw, capture_output=True, timeout=120 if ocr else 30)
        result = json.loads(process.stdout)
        if process.returncode or result.get('error'):
            raise ValueError(result.get('error') or '解析进程未正常完成')
        body = result['body']
        if not isinstance(body, str) or (not pdf_note and not body.strip()) or len(body) > 100000:
            raise ValueError('提取正文无效或过长')
        status, error = 'ready', ''
    except subprocess.TimeoutExpired:
        result, body, status, error = {}, '', 'failed', '解析超过时间限制，请拆分文件后重试'
    except Exception as exc:
        result, body, status = {}, '', 'failed'
        error = str(exc)[:240] if isinstance(exc, ValueError) else '解析失败，请检查文档后重试'
    with connect(db_path) as db:
        # Cancel/delete can win while the child process is running.
        db.execute('BEGIN IMMEDIATE')
        assets_json=json.dumps(result.get('assets',[]),ensure_ascii=False)
        asset_size=len(assets_json.encode()) if result.get('assets') else 0
        used=db.execute('SELECT coalesce(sum(size+asset_size),0) FROM document_imports WHERE user_id=?',(row['user_id'],)).fetchone()[0]
        if used+asset_size-row['asset_size']>MAX_STORAGE:
            status,error,body,assets_json,asset_size='failed','提取附件后超过账户 50 MB 暂存额度，请清理旧记录后重试','','[]',0
        db.execute('''UPDATE document_imports SET status=?,body=?,original_body=?,assets=?,asset_size=?,warnings=?,error=?,version=version+1,updated_at=?
                      WHERE id=? AND status='parsing' AND attempts=? ''',
                   (status, body, body,assets_json,asset_size,json.dumps(result.get('warnings', []), ensure_ascii=False), error, int(time.time()), job_id, attempt))


def register_imports(app, db_path, g):
    initialize_imports(db_path)
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='document-import')
    app.state.import_executor = executor
    app.state.import_parse = lambda job_id: parse_job(db_path, job_id)

    def submit(job_id):
        executor.submit(app.state.import_parse, job_id)

    def recover():
        with connect(db_path) as db:
            db.execute("UPDATE document_imports SET status='failed',error='服务重启中断了解析，请重试',version=version+1 WHERE status='parsing'")
            pending = [r[0] for r in db.execute("SELECT id FROM document_imports WHERE status='queued'")]
        for job_id in pending:
            submit(job_id)

    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with previous_lifespan(application):
            recover()
            try:
                yield
            finally:
                import asyncio
                await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)

    app.router.lifespan_context = lifespan

    @contextmanager
    def authorized(request, write=False):
        pending=[]
        try:
            with g['database'](db_path) as db:
                db.pending_import_assets=pending
                if write: db.execute('BEGIN IMMEDIATE')
                user, csrf = g['current_user'](db, request)
                if user is None: raise HTTPException(401, '请先登录')
                if write: g['require_csrf']({'csrf': request.headers.get('X-CSRF-Token', '')}, csrf)
                yield db, user
        except BaseException:
            for path in pending:
                try: path.unlink()
                except FileNotFoundError: pass
            raise

    def owned(db, user, job_id):
        row = db.execute('SELECT * FROM document_imports WHERE id=? AND user_id=?', (job_id, user['id'])).fetchone()
        if row is None:
            raise HTTPException(404, '导入记录不存在')
        return row

    def info(row, full=False):
        item = {k: row[k] for k in FIELDS.split(',')}
        item['warnings'] = json.loads(item['warnings'])
        if full:
            from import_assets import preview_body
            assets=json.loads(row['assets'])
            item.update(body=row['body'],html=preview_body(row['body'],assets,row['id']),original_body=row['original_body'],
                        assets=[{k:v for k,v in asset.items() if k!='data'} for asset in assets])
        return item

    def enqueue(db, user, filename, raw, ocr, note_format):
        import hashlib
        sha = hashlib.sha256(raw).hexdigest()
        existing = db.execute("SELECT * FROM document_imports WHERE user_id=? AND file_sha=? AND ocr=? AND note_format=? AND status!='committed' ORDER BY id DESC LIMIT 1", (user['id'], sha, int(ocr), note_format)).fetchone()
        if existing:
            return {**info(existing), 'duplicate': True}
        if db.execute("SELECT count(*) FROM document_imports WHERE status IN ('queued','parsing')").fetchone()[0] >= 100:
            raise HTTPException(429, '解析队列已满，请稍后重试')
        count, size = db.execute('SELECT count(*),coalesce(sum(size+asset_size),0) FROM document_imports WHERE user_id=?', (user['id'],)).fetchone()
        if count >= 100 or size+len(raw) > MAX_STORAGE:
            raise HTTPException(429, '导入记录最多 100 份、原文件合计 50 MB；请先清理旧记录')
        now = int(time.time())
        job_id = db.execute('''INSERT INTO document_imports(user_id,filename,file_sha,size,original,title,created_at,updated_at,ocr,note_format)
            VALUES(?,?,?,?,?,?,?,?,?,?)''', (user['id'], filename, sha, len(raw), bytes(raw), Path(filename).stem, now, now, int(ocr), note_format)).lastrowid
        return info(owned(db, user, job_id))

    async def data(request):
        if request.headers.get('content-type', '').split(';')[0] != 'application/json':
            raise HTTPException(415, '请提交 JSON 内容')
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 512 * 1024:
                raise HTTPException(413, '正文过大')
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise HTTPException(400, 'JSON 无效')
        if not isinstance(result, dict):
            raise HTTPException(400, '请求无效')
        return result

    @app.get('/imports')
    def page(request: Request):
        with g['database'](db_path) as db:
            user, csrf = g['current_user'](db, request)
            if user is None:
                return RedirectResponse('/login?next=/imports', 303)
            return g['page'](request, 'imports.html', user=user, csrf=csrf, categories=g['categories'](db, user))

    @app.get('/api/imports')
    def listing(request: Request, page: int = 1):
        if not 1 <= page <= 10000:
            raise HTTPException(400, '页码无效')
        with authorized(request) as (db, user):
            jobs = db.execute(f'SELECT {FIELDS} FROM document_imports WHERE user_id=? ORDER BY id DESC LIMIT 20 OFFSET ?',
                              (user['id'], (page-1)*20)).fetchall()
            targets = [dict(r) for r in db.execute('''SELECT t.id,t.name FROM teams t JOIN team_members m ON m.team_id=t.id
                          WHERE m.user_id=? AND m.role IN ('owner','admin','editor') ORDER BY t.id DESC''', (user['id'],))]
            from local_ocr import available
            return {'jobs': [info(r) for r in jobs], 'page': page, 'ocr_available':available(),
                    'total': db.execute('SELECT count(*) FROM document_imports WHERE user_id=?', (user['id'],)).fetchone()[0],
                    'storage': db.execute('SELECT coalesce(sum(size+asset_size),0) FROM document_imports WHERE user_id=?', (user['id'],)).fetchone()[0],
                    'personal_allowed': user['role'] == 'admin', 'teams': targets}

    @app.post('/api/imports', status_code=202)
    async def upload(request: Request, filename: str, ocr: bool=False, note_format: str='markdown'):
        with authorized(request, True):
            pass
        if (not filename or len(filename) > 160 or PurePosixPath(filename).name != filename or '\\' in filename
                or any(ord(c) < 32 for c in filename) or Path(filename).suffix.lower() not in {'.md', '.pdf', '.docx','.zip'}):
            raise HTTPException(400, '请选择有效的 Markdown、PDF 或 DOCX 文件名')
        if note_format not in {'pdf', 'markdown'} or (note_format == 'pdf' and Path(filename).suffix.lower() != '.pdf'):
            raise HTTPException(400, 'PDF 笔记只支持 PDF 原文件')
        if note_format == 'pdf':
            ocr = False
        if ocr:
            from local_ocr import available
            if not available(): raise HTTPException(503,'本地 OCR 尚未配置，请联系管理员')
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_FILE:
                raise HTTPException(413, '单个文件不能超过 10 MB')
        if not raw:
            raise HTTPException(400, '文件为空')
        with authorized(request, True) as (db, user):
            result = enqueue(db, user, filename, raw, ocr, note_format)
        if not result.get('duplicate'):
            submit(result['id'])
        return result

    @app.post('/api/imports/{job_id}/convert', status_code=202)
    async def convert(request: Request, job_id: int):
        values = await data(request)
        ocr = values.get('ocr', False)
        if type(ocr) is not bool:
            raise HTTPException(400, 'OCR 选项无效')
        with authorized(request, True) as (db, user):
            row = owned(db, user, job_id)
            if not row['filename'].lower().endswith('.pdf') or row['status'] not in {'ready', 'committed', 'failed'}:
                raise HTTPException(409, '请选择已完成检查的 PDF 资料')
            if ocr:
                from local_ocr import available
                if not available():
                    raise HTTPException(503, '本地 OCR 尚未配置，请联系管理员')
            result = enqueue(db, user, row['filename'], row['original'], ocr, 'markdown')
        if not result.get('duplicate'):
            submit(result['id'])
        return result

    async def convert_saved_pdf(request, *, note_id=None, team_id=None, document_id=None):
        raw = await request.body()
        if len(raw) > 4096:
            raise HTTPException(413, '请求过大')
        form = g['form_values'](raw)
        with g['database'](db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            user, csrf = g['current_user'](db, request)
            if user is None:
                raise HTTPException(401, '请先登录')
            g['require_csrf'](form, csrf)
            if note_id is not None:
                note = db.execute('SELECT * FROM notes WHERE id=?', (note_id,)).fetchone()
                if note is None or not g['can_view'](db, note, user):
                    raise HTTPException(404, '笔记不存在')
                source = db.execute('SELECT * FROM document_pdfs WHERE note_id=?', (note_id,)).fetchone()
            else:
                if not db.execute('''SELECT 1 FROM team_documents d JOIN team_members m ON m.team_id=d.team_id
                    WHERE d.id=? AND d.team_id=? AND m.user_id=?''', (document_id, team_id, user['id'])).fetchone():
                    raise HTTPException(404, '文档不存在')
                source = db.execute('SELECT * FROM document_pdfs WHERE document_id=?', (document_id,)).fetchone()
            if source is None:
                raise HTTPException(404, 'PDF 原文件不存在')
            result = enqueue(db, user, source['filename'], source['original'], False, 'markdown')
        if not result.get('duplicate'):
            submit(result['id'])
        return RedirectResponse('/imports?job=' + str(result['id']), 303)

    @app.post('/n/{note_id}/pdf/convert')
    async def convert_saved_note(request: Request, note_id: int):
        return await convert_saved_pdf(request, note_id=note_id)

    @app.post('/teams/{team_id}/documents/{document_id}/pdf/convert')
    async def convert_saved_team(request: Request, team_id: int, document_id: int):
        return await convert_saved_pdf(request, team_id=team_id, document_id=document_id)

    @app.get('/api/imports/{job_id}/export')
    def export(request: Request, job_id: int, format: str = 'md'):
        if format not in {'md', 'txt'}:
            raise HTTPException(400, '请选择 Markdown 或纯文本格式')
        with authorized(request) as (db, user):
            row = owned(db, user, job_id)
            if row['status'] not in {'ready', 'committed'} or row['note_format'] != 'markdown':
                raise HTTPException(409, '请先提取文字并检查解析结果')
            body = row['body'] if format == 'md' else re.sub(r'^## (第 \d+ 页)$', r'\1', row['original_body'] or row['body'], flags=re.M)
            filename = Path(row['filename']).stem + '.' + format
            return Response(body.encode('utf-8'), media_type='text/markdown' if format == 'md' else 'text/plain', headers={
                'Content-Disposition': "attachment; filename*=UTF-8''" + quote(filename, safe=''), 'Cache-Control': 'no-store'})

    @app.get('/api/imports/{job_id}')
    def get_job(request: Request, job_id: int):
        with authorized(request) as (db, user):
            return info(owned(db, user, job_id), True)

    @app.get('/api/imports/{job_id}/original')
    def original(request: Request, job_id: int):
        with authorized(request) as (db, user):
            row = owned(db, user, job_id)
            return Response(row['original'], media_type='application/octet-stream',
                            headers={'Content-Disposition': "attachment; filename*=UTF-8''"+quote(row['filename'], safe=''),
                                     'Cache-Control': 'no-store'})

    @app.get('/api/imports/{job_id}/preview-original')
    def original_preview(request:Request,job_id:int):
        with authorized(request) as (db,user):
            row=owned(db,user,job_id)
            if not row['filename'].lower().endswith('.pdf'): raise HTTPException(400,'此文件请下载原件对照')
            return Response(row['original'],media_type='application/pdf',headers={'Content-Disposition':"inline; filename*=UTF-8''"+quote(row['filename'], safe=''),'Cache-Control':'no-store'})

    @app.get('/api/imports/{job_id}/assets/{index}')
    def imported_asset(request:Request,job_id:int,index:int):
        with authorized(request) as (db,user):
            assets=json.loads(owned(db,user,job_id)['assets'])
            if not 0<=index<len(assets): raise HTTPException(404)
            import base64
            item=assets[index]
            return Response(base64.b64decode(item['data']),media_type=item['mime'],headers={'Cache-Control':'no-store'})

    @app.put('/api/imports/{job_id}')
    async def edit(request: Request, job_id: int):
        values = await data(request)
        with authorized(request, True) as (db, user):
            row = owned(db, user, job_id)
            if row['status'] != 'ready' or row['version'] != version(values):
                raise HTTPException(409, '导入版本已变化，请重新打开；当前输入仍可复制保留')
            title, body = text(values, 'title', 160), text(values, 'body', 100000, required=row['note_format'] != 'pdf')
            db.execute('UPDATE document_imports SET title=?,body=?,version=version+1,updated_at=? WHERE id=?', (title, body, int(time.time()), job_id))
            return info(owned(db, user, job_id), True)

    @app.post('/api/imports/{job_id}/retry')
    async def retry(request: Request, job_id: int):
        with authorized(request, True) as (db, user):
            row = owned(db, user, job_id)
            if row['status'] not in {'failed', 'cancelled'} or row['attempts'] >= 3:
                raise HTTPException(409, '当前状态不可重试，或已达到三次解析上限')
            if db.execute("SELECT count(*) FROM document_imports WHERE status IN ('queued','parsing')").fetchone()[0] >= 100:
                raise HTTPException(429, '解析队列已满')
            db.execute("UPDATE document_imports SET status='queued',error='',version=version+1,updated_at=? WHERE id=?", (int(time.time()), job_id))
        submit(job_id)
        return {'ok': True}

    @app.post('/api/imports/{job_id}/cancel')
    async def cancel(request: Request, job_id: int):
        with authorized(request, True) as (db, user):
            row = owned(db, user, job_id)
            if row['status'] not in {'queued', 'parsing'}:
                raise HTTPException(409, '此任务已不在解析队列中')
            db.execute("UPDATE document_imports SET status='cancelled',version=version+1,updated_at=? WHERE id=?", (int(time.time()), job_id))
        return {'ok': True}

    @app.delete('/api/imports/{job_id}')
    async def delete(request: Request, job_id: int):
        with authorized(request, True) as (db, user):
            owned(db, user, job_id)
            db.execute('DELETE FROM document_imports WHERE id=?', (job_id,))
        return {'ok': True}

    @app.post('/api/imports/{job_id}/commit')
    async def commit(request: Request, job_id: int):
        values = await data(request)
        with authorized(request, True) as (db, user):
            row = owned(db, user, job_id)
            team_id = values.get('team_id')
            if type(team_id) is not int or team_id < 0:
                raise HTTPException(400, '请选择有效的目标空间')
            if team_id:
                member = db.execute('SELECT role FROM team_members WHERE team_id=? AND user_id=?', (team_id, user['id'])).fetchone()
                if member is None:
                    raise HTTPException(404, '空间不存在或你已不在该空间')
                if member['role'] not in WRITERS:
                    raise HTTPException(403, '当前空间角色不能导入文档')
            elif user['role'] != 'admin':
                raise HTTPException(403, '个人笔记库入库沿用管理员权限；你可以导入有编辑权限的团队')
            if row['status'] == 'committed':
                if row['target_team'] != team_id:
                    raise HTTPException(409, '此记录已导入另一个目标')
                return {'url': row['result_url'], 'already_committed': True}
            if row['status'] != 'ready' or row['version'] != version(values):
                raise HTTPException(409, '请保存预览修正并重新核对版本后再入库')
            now, body = int(time.time()), row['body']
            pdf_note = row['note_format'] == 'pdf'
            is_pdf = row['filename'].lower().endswith('.pdf')
            if pdf_note and not body.strip():
                body = '# ' + row['title'] + '\n'
            assets=json.loads(row['assets'])
            if team_id:
                if db.execute("SELECT 1 FROM document_imports WHERE user_id=? AND file_sha=? AND target_team=? AND note_format=? AND status='committed' AND id!=?",(user['id'],row['file_sha'],team_id,row['note_format'],job_id)).fetchone(): raise HTTPException(409,'相同原文件已导入此团队，请检查已有文档')
                if is_pdf and db.execute('''SELECT 1 FROM document_pdfs p JOIN team_documents d ON d.id=p.document_id
                    WHERE d.team_id=? AND p.file_sha=? AND p.note_format=?''', (team_id, row['file_sha'], row['note_format'])).fetchone():
                    raise HTTPException(409, '此空间已有相同 PDF 与笔记类型，请检查已有文档')
                size,count=team_storage_usage(db, team_id)
                if size+sum(item['size'] for item in assets)>100*1024*1024 or count+len(assets)>500: raise HTTPException(409,'团队附件空间不足')
                duplicate = db.execute('SELECT id FROM team_documents WHERE team_id=? AND body_sha=?', (team_id, digest(body))).fetchone()
                if duplicate and not pdf_note:
                    raise HTTPException(409, '该团队已有相同正文的文档，请从团队列表检查，未创建重复记录')
                doc_id = db.execute('''INSERT INTO team_documents(team_id,title,body,body_sha,updated_by,created_at,updated_at)
                                      VALUES(?,?,?,?,?,?,?)''', (team_id, row['title'], body, digest(body), user['id'], now, now)).lastrowid
                if assets:
                    from import_assets import team_asset_body
                    body=team_asset_body(db,team_id,doc_id,user['id'],body,assets,now)
                    db.execute('UPDATE team_documents SET body=?,body_sha=? WHERE id=?',(body,digest(body),doc_id))
                if is_pdf:
                    save_pdf(db, row, user['id'], row['note_format'], document_id=doc_id, team_id=team_id)
                db.execute('INSERT INTO team_document_revisions(document_id,version,title,body,actor_id,created_at) VALUES(?,1,?,?,?,?)', (doc_id, row['title'], body, user['id'], now))
                db.execute("INSERT INTO team_events(team_id,actor_id,action,detail,created_at) VALUES(?,?,'document.imported',?,?)", (team_id, user['id'], '导入文档「'+row['title']+'」', now))
                db.execute('UPDATE teams SET version=version+1 WHERE id=?', (team_id,))
                url = f'/teams?space={team_id}&document={doc_id}'
            else:
                path = values.get('path', '')
                parsed = safe_note_path(path)
                visibility = values.get('visibility', 'private')
                if parsed.parts[0] not in {'Backend', 'Frontend', 'Developer'}:
                    raise HTTPException(400, '请选择 Backend/、Frontend/ 或 Developer/ 下的 Markdown 路径')
                public = parsed.parts[0] in {'Backend', 'Frontend'}
                if visibility != ('public' if public else 'private') or (public and values.get('confirm_public') is not True):
                    raise HTTPException(400, '公开目录需明确确认公开；Developer/ 仅允许私密')
                if db.execute('SELECT 1 FROM notes WHERE path=?', (path,)).fetchone():
                    raise HTTPException(409, '目标路径已有笔记，不会覆盖，请调整路径')
                if not pdf_note and db.execute('SELECT 1 FROM notes WHERE body_sha=? AND visibility=?', (digest(body), visibility)).fetchone():
                    raise HTTPException(409, '目标笔记库已有相同正文，请先检查重复资料')
                if is_pdf and db.execute('''SELECT 1 FROM document_pdfs p JOIN notes n ON n.id=p.note_id
                    WHERE n.visibility=? AND p.file_sha=? AND p.note_format=?''', (visibility, row['file_sha'], row['note_format'])).fetchone():
                    raise HTTPException(409, '此笔记库已有相同 PDF 与笔记类型，请检查已有笔记')
                summary, category = text(values, 'summary', 2000, required=False), text(values, 'category', 240, required=False)
                category = category or (' / '.join(parsed.parts[:-1]) if public else '私人笔记 / '+' / '.join(parsed.parts[:-1]))
                if not public and db.execute('''SELECT 1 FROM shares s WHERE s.note_id IS NULL AND (s.expires_at IS NULL OR s.expires_at>unixepoch())
                        AND (?=s.category OR substr(?,1,length(s.category)+3)=s.category||' / ')
                        AND (s.audience='link' OR EXISTS (
                            SELECT 1 FROM share_members m JOIN users u ON u.id=m.user_id
                            WHERE m.share_id=s.id AND u.active=1)) LIMIT 1''', (category, category)).fetchone():
                    raise HTTPException(409, '目标目录已有生效的分享，私密导入会被共享；请选择未分享的目录后重试')
                note = add_note(db, path, body, summary, category, visibility, user['username'], 'web')
                if is_pdf:
                    db.execute('UPDATE notes SET title=? WHERE id=?', (row['title'], note['id']))
                    save_pdf(db, row, user['id'], row['note_format'], note_id=note['id'])
                try:
                    from import_assets import write_note_assets
                    write_note_assets(db,app.state.content_root,path,assets)
                except ValueError as exc: raise HTTPException(409,str(exc)) from exc
                url = f'/n/{note["id"]}'
            db.execute("UPDATE document_imports SET status='committed',result_url=?,target_team=?,version=version+1,updated_at=? WHERE id=?", (url, team_id, now, job_id))
            return {'url': url}
