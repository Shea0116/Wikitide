"""Permanent PDF sources scoped to the permissions of their destination document."""
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import Response


def initialize_pdfs(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS document_pdfs (
            id INTEGER PRIMARY KEY,
            note_id INTEGER UNIQUE REFERENCES notes(id) ON DELETE CASCADE,
            document_id INTEGER UNIQUE REFERENCES team_documents(id) ON DELETE CASCADE,
            created_by INTEGER NOT NULL REFERENCES users(id),
            filename TEXT NOT NULL, file_sha TEXT NOT NULL, original BLOB NOT NULL,
            note_format TEXT NOT NULL CHECK(note_format IN ('pdf','markdown')),
            CHECK ((note_id IS NOT NULL) != (document_id IS NOT NULL))
        );
        CREATE INDEX IF NOT EXISTS ix_document_pdf_owner ON document_pdfs(created_by);
    ''')


def pdf_info(db, *, note_id=None, document_id=None, team_id=None, share_prefix=None):
    column, identity = ('note_id', note_id) if note_id is not None else ('document_id', document_id)
    row = db.execute(f'SELECT filename,length(original) AS size,note_format FROM document_pdfs WHERE {column}=?', (identity,)).fetchone()
    if row is None:
        return None
    if note_id is not None:
        url = f'{share_prefix}/pdf/{note_id}' if share_prefix else f'/n/{note_id}/pdf'
    else:
        url = f'/api/teams/{team_id}/documents/{document_id}/pdf'
    return {**dict(row), 'url': url, 'download_url': url + '?download=true'}


def team_storage_usage(db, team_id):
    size, count = db.execute('''SELECT coalesce(sum(length(p.original)),0),count(*) FROM document_pdfs p
        JOIN team_documents d ON d.id=p.document_id WHERE d.team_id=?''', (team_id,)).fetchone()
    attachments, attached_count = db.execute('SELECT coalesce(sum(length(content)),0),count(*) FROM team_attachments WHERE team_id=?', (team_id,)).fetchone()
    return size + attachments, count + attached_count


def save_pdf(db, row, user_id, note_format, *, note_id=None, document_id=None, team_id=None):
    """Copy the source in the same transaction as insertion; import cleanup is independent."""
    if team_id:
        size, count = team_storage_usage(db, team_id)
    else:
        size, count = db.execute('SELECT coalesce(sum(length(original)),0),count(*) FROM document_pdfs WHERE note_id IS NOT NULL AND created_by=?', (user_id,)).fetchone()
    if size + row['size'] > 100 * 1024 * 1024 or count >= 500:
        raise HTTPException(409, 'PDF 原文件空间不足（最多 100 MB、500 份），请先清理不需要的正式文档')
    db.execute('''INSERT INTO document_pdfs(note_id,document_id,created_by,filename,file_sha,original,note_format)
        VALUES(?,?,?,?,?,?,?)''', (note_id, document_id, user_id, row['filename'], row['file_sha'], row['original'], note_format))


def register_pdfs(app, db_path, g):
    def response(db, column, identity, download):
        row = db.execute(f'SELECT filename,original FROM document_pdfs WHERE {column}=?', (identity,)).fetchone()
        if row is None:
            raise HTTPException(404, 'PDF 原文件不存在')
        disposition = 'attachment' if download else 'inline'
        return Response(row['original'], media_type='application/pdf', headers={
            'Content-Disposition': disposition + "; filename*=UTF-8''" + quote(row['filename'], safe=''),
            'Cache-Control': 'no-store',
        })

    @app.get('/n/{note_id}/pdf')
    @app.get('/s/{token}/pdf/{note_id}')
    def note_pdf(request: Request, note_id: int, download: bool = False):
        with g['database'](db_path) as db:
            token = request.path_params.get('token')
            if token:
                note = g['shared_note'](db, token, note_id)
            else:
                user, _ = g['current_user'](db, request)
                note = db.execute('SELECT * FROM notes WHERE id=?', (note_id,)).fetchone()
                if note is None or not g['can_view'](db, note, user):
                    raise HTTPException(404, '笔记不存在')
            return response(db, 'note_id', note['id'], download)

    @app.get('/api/teams/{team_id}/documents/{document_id}/pdf')
    def team_pdf(request: Request, team_id: int, document_id: int, download: bool = False):
        with g['database'](db_path) as db:
            user, _ = g['current_user'](db, request)
            if user is None:
                raise HTTPException(401, '请先登录')
            if not db.execute('''SELECT 1 FROM team_documents d JOIN team_members m ON m.team_id=d.team_id
                WHERE d.id=? AND d.team_id=? AND m.user_id=?''', (document_id, team_id, user['id'])).fetchone():
                raise HTTPException(404, '文档不存在')
            return response(db, 'document_id', document_id, download)
