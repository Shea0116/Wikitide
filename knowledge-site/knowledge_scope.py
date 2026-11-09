"""Explicit account-selected note retrieval, separate from the public corpus."""
import json
from urllib.parse import quote

from fastapi import HTTPException

from explore_search import INDEX_VERSION, PUBLIC_SQL, public_path, rank_chunks, split_article, terms
from storage import can_view, digest


def selected_note(db, user_id, note_id):
    user = db.execute('SELECT * FROM users WHERE id=? AND active=1', (user_id,)).fetchone()
    note = db.execute('SELECT * FROM notes WHERE id=?', (note_id,)).fetchone()
    return note if user and note and can_view(db, note, user) else None


def session_scope(session):
    return session['scope'] if 'scope' in session.keys() else 'public'


def selection_ids(note_id=None, note_ids=None):
    ids = sorted(set(note_ids or []))
    if note_id is not None:
        if ids and ids != [note_id]:
            raise HTTPException(400, '单篇与多篇资料范围不一致，请重新选择。')
        ids = [note_id]
    if len(ids) > 12 or any(type(value) is not int or value <= 0 for value in ids):
        raise HTTPException(400, '每次请选择 1～12 篇笔记。')
    return ids


def selected_notes(db, user_id, note_ids):
    notes = [selected_note(db, user_id, note_id) for note_id in note_ids]
    if not notes or any(note is None for note in notes):
        raise HTTPException(404, '所选笔记不可访问，请重新选择。')
    return notes


def session_note_ids(db, session):
    ids = [row[0] for row in db.execute('SELECT note_id FROM explore_session_notes WHERE session_id=? ORDER BY note_id', (session['id'],))]
    return ids or ([session['note_id']] if session['note_id'] is not None else [])


def selection_version(note_ids):
    return digest(json.dumps(sorted(note_ids)))


def read_source_chunk(db, row, direction, *, user_id=None, note_ids=None, public_note_id=None):
    if note_ids is not None:
        if row['note_id'] not in note_ids:
            raise HTTPException(404, '资料不在本次选择范围内。')
        note = selected_note(db, user_id, row['note_id'])
    else:
        if public_note_id is not None and row['note_id'] != public_note_id:
            raise HTTPException(404, '资料不在本次选择范围内。')
        note = db.execute(f'SELECT * FROM notes WHERE id=? AND {PUBLIC_SQL}', (row['note_id'],)).fetchone()
        if note and not public_path(note['path']):
            note = None
    if note is None or note['body_sha'] != row['body_sha']:
        raise HTTPException(409, '资料或访问权限已变化，请重新提问。')
    chunks = split_article(note['body'])
    position = next((index for index, chunk in enumerate(chunks) if chunk['start_line'] == row['start_line'] and chunk['end_line'] == row['end_line']), None)
    if position is None:
        raise HTTPException(409, '资料或访问权限已变化，请重新提问。')
    position += {'current': 0, 'previous': -1, 'next': 1}[direction]
    if not 0 <= position < len(chunks):
        return []
    chunk = chunks[position]
    return [{**chunk, 'ordinal': position, 'note_id': note['id'], 'title': note['title'], 'path': note['path'],
             'category': note['category'], 'body_sha': note['body_sha'],
             'url': f"/n/{note['id']}" + ('#' + quote(chunk['anchor']) if chunk['anchor'] else ''), 'reason': '原文相邻片段'}]


def initialize_selected(db):
    if 'scope' not in {row['name'] for row in db.execute('PRAGMA table_info(explore_sessions)')}:
        db.execute("ALTER TABLE explore_sessions ADD COLUMN scope TEXT NOT NULL DEFAULT 'public' CHECK(scope IN ('public','selected'))")
    db.executescript('''
        CREATE TABLE IF NOT EXISTS explore_session_notes (
            session_id TEXT NOT NULL REFERENCES explore_sessions(id) ON DELETE CASCADE,
            note_id INTEGER NOT NULL,
            PRIMARY KEY(session_id,note_id)
        );
        CREATE TABLE IF NOT EXISTS explore_selected_documents (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
            fingerprint TEXT NOT NULL, body_sha TEXT NOT NULL,
            PRIMARY KEY(user_id,note_id)
        );
        CREATE TABLE IF NOT EXISTS explore_selected_chunks (
            user_id INTEGER NOT NULL, note_id INTEGER NOT NULL, ordinal INTEGER NOT NULL,
            section TEXT NOT NULL, anchor TEXT NOT NULL, start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL, text TEXT NOT NULL, tokens TEXT NOT NULL,
            embedding TEXT, embedding_model TEXT,
            PRIMARY KEY(user_id,note_id,ordinal),
            FOREIGN KEY(user_id,note_id) REFERENCES explore_selected_documents(user_id,note_id) ON DELETE CASCADE
        );
    ''')


class SelectedIndex:
    def __init__(self, embeddings):
        self.embeddings = embeddings

    def chunks(self, db, user_id, note):
        note_id = note['id']
        fingerprint = digest(json.dumps([INDEX_VERSION, note['body_sha'], note['title'], note['summary'], note['path']]))
        indexed = db.execute('SELECT fingerprint FROM explore_selected_documents WHERE user_id=? AND note_id=?',
                             (user_id, note_id)).fetchone()
        if not indexed or indexed['fingerprint'] != fingerprint:
            db.execute('DELETE FROM explore_selected_documents WHERE user_id=? AND note_id=?', (user_id, note_id))
            db.execute('INSERT INTO explore_selected_documents VALUES(?,?,?,?)',
                       (user_id, note_id, fingerprint, note['body_sha']))
            for ordinal, chunk in enumerate(split_article(note['body'])):
                weights = terms(note['title'] + ' ' + note['summary'] + ' ' + chunk['section'])
                counts = terms(chunk['text'])
                counts.update({key: value * 3 for key, value in weights.items()})
                db.execute('''INSERT INTO explore_selected_chunks
                    (user_id,note_id,ordinal,section,anchor,start_line,end_line,text,tokens)
                    VALUES(?,?,?,?,?,?,?,?,?)''',
                           (user_id, note_id, ordinal, chunk['section'], chunk['anchor'],
                            chunk['start_line'], chunk['end_line'], chunk['text'], json.dumps(counts, ensure_ascii=False)))
        db.commit()
        rows = [dict(row) for row in db.execute('''SELECT c.*,n.title,n.path,n.category,n.body_sha,n.updated_at
            FROM explore_selected_chunks c JOIN notes n ON n.id=c.note_id
            JOIN explore_selected_documents d ON d.user_id=c.user_id AND d.note_id=c.note_id
            WHERE c.user_id=? AND c.note_id=? AND d.body_sha=n.body_sha ORDER BY c.ordinal''', (user_id, note_id))]
        # Only the explicitly selected, currently readable note is embedded.
        key = self.embeddings.key
        missing = [row for row in rows if key and (row['embedding_model'] != key or not row['embedding'])]
        if missing:
            vectors = self.embeddings.encode([row['title'] + '\n' + row['section'] + '\n' + row['text'] for row in missing])
            if vectors is not None:
                for row, vector in zip(missing, vectors):
                    row['embedding'], row['embedding_model'] = json.dumps(vector), key
                    db.execute('''UPDATE explore_selected_chunks SET embedding=?,embedding_model=?
                        WHERE user_id=? AND note_id=? AND ordinal=? AND text=?''',
                               (row['embedding'], key, user_id, note_id, row['ordinal'], row['text']))
                db.commit()
        current = selected_note(db, user_id, note_id)
        if current is None or current['body_sha'] != note['body_sha']:
            raise HTTPException(409, '所选笔记或访问权限已变化，请重新提问。')
        return rows

    def search(self, db, user_id, query, *, note_id=None, note_ids=None, limit=8, per_note_limit=None):
        ids = selection_ids(note_id, note_ids)
        notes = selected_notes(db, user_id, ids)
        rows = [row for note in notes for row in self.chunks(db, user_id, note)]
        current = selected_notes(db, user_id, ids)
        if any(before['body_sha'] != after['body_sha'] for before, after in zip(notes, current)):
            raise HTTPException(409, '所选笔记或访问权限已变化，请重新提问。')
        return rank_chunks(rows, query, self.embeddings, note_id=ids[0] if len(ids) == 1 else None,
                           limit=limit, per_note_limit=per_note_limit)
