"""Version-checked path changes and consolidation of prior sync duplicates."""
import json
import time
from pathlib import PurePosixPath

from fastapi import HTTPException

PUBLIC_ROOTS = {'Frontend', 'Backend'}
PRIVATE_ROOTS = {'Developer', 'HowToLiveBetter', '运维'}


def default_category(path, visibility):
    parts = PurePosixPath(path).parts[:-1]
    if visibility == 'private':
        return ' / '.join(('私人笔记', *parts))
    return ' / '.join(parts)


def active_share(db, share):
    if share and 'expires_at' in share.keys() and share['expires_at'] is not None and share['expires_at']<=int(time.time()):
        return False
    return bool(share and (share['audience'] == 'link' or db.execute(
        'SELECT 1 FROM share_members WHERE share_id=? LIMIT 1', (share['id'],)).fetchone()))


def missing_file_conflict(db, note):
    conflict = db.execute("SELECT * FROM sync_conflicts WHERE note_id=? AND status IN ('open','resolved')", (note['id'],)).fetchone()
    return not conflict or (conflict['status'] == 'open' and conflict['local_body'] == '' and conflict['server_sha'] == note['sha'])


def merge_duplicate(db, original, duplicate):
    old_id, new_id = original['id'], duplicate['id']
    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'document_pdfs' in tables:
        source = db.execute('SELECT id,file_sha,note_format FROM document_pdfs WHERE note_id=?', (old_id,)).fetchone()
        incoming = db.execute('SELECT id,file_sha,note_format FROM document_pdfs WHERE note_id=?', (new_id,)).fetchone()
        if source and incoming:
            if (source['file_sha'], source['note_format']) != (incoming['file_sha'], incoming['note_format']):
                raise HTTPException(409, '两篇笔记的 PDF 原文件或类型不同，请保留各自资料')
        elif incoming:
            db.execute('UPDATE document_pdfs SET note_id=? WHERE id=?', (old_id, incoming['id']))
    for table in ('revisions', 'edit_events'):
        db.execute(f'UPDATE {table} SET note_id=? WHERE note_id=?', (old_id, new_id))
    db.execute('UPDATE note_moves SET note_id=? WHERE note_id=?', (old_id, new_id))
    # Keep the active share URL; an already revoked share must not block consolidation.
    original_share = db.execute('SELECT * FROM shares WHERE note_id=?', (old_id,)).fetchone()
    duplicate_share = db.execute('SELECT * FROM shares WHERE note_id=?', (new_id,)).fetchone()
    if original_share and duplicate_share:
        discard = original_share if active_share(db, duplicate_share) else duplicate_share
        db.execute('DELETE FROM shares WHERE id=?', (discard['id'],))
    db.execute('UPDATE shares SET note_id=?,scope_key=? WHERE note_id=?', (old_id, f'note:{old_id}', new_id))
    for row in db.execute('SELECT * FROM grants WHERE note_id=?', (new_id,)).fetchall():
        db.execute('''INSERT INTO grants(user_id,note_id,can_view,can_edit) VALUES(?,?,?,?)
            ON CONFLICT(user_id,note_id) DO UPDATE SET can_view=max(can_view,excluded.can_view),can_edit=max(can_edit,excluded.can_edit)''',
            (row['user_id'], old_id, row['can_view'], row['can_edit']))
    for row in db.execute('SELECT * FROM reading WHERE note_id=?', (new_id,)).fetchall():
        db.execute('''INSERT INTO reading(user_id,note_id,bookmarked,position,read_at) VALUES(?,?,?,?,?)
            ON CONFLICT(user_id,note_id) DO UPDATE SET bookmarked=max(bookmarked,excluded.bookmarked),
            position=CASE WHEN excluded.read_at>read_at THEN excluded.position ELSE position END,
            read_at=max(read_at,excluded.read_at)''',
            (row['user_id'], old_id, row['bookmarked'], row['position'], row['read_at']))
    if 'reading_items' in tables:
        for row in db.execute('SELECT * FROM reading_items WHERE note_id=?',(new_id,)).fetchall():
            db.execute('''INSERT INTO reading_items VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id,note_id) DO UPDATE SET
                folder=excluded.folder,later=excluded.later,completed=excluded.completed,ordinal=excluded.ordinal,updated_at=excluded.updated_at
                WHERE excluded.updated_at>reading_items.updated_at''',(row['user_id'],old_id,row['folder'],row['later'],row['completed'],row['ordinal'],row['updated_at']))
    for table in ('annotations','review_cards'):
        if table in tables: db.execute(f'UPDATE {table} SET note_id=? WHERE note_id=?',(old_id,new_id))
    if 'editor_drafts' in tables:
        for row in db.execute("SELECT * FROM editor_drafts WHERE kind='note' AND resource_id=? AND deleted=0",(new_id,)).fetchall():
            previous=db.execute("SELECT * FROM editor_drafts WHERE user_id=? AND kind='note' AND resource_id=? AND team_id=0",(row['user_id'],old_id)).fetchone()
            if previous and not previous['deleted']:
                raise HTTPException(409,'两篇文章都有独立云草稿，请先整理后再合并')
            version=max(row['version'],previous['version'] if previous else 0)+1
            db.execute("DELETE FROM editor_drafts WHERE user_id=? AND kind='note' AND resource_id=? AND team_id=0",(row['user_id'],old_id))
            db.execute("UPDATE editor_drafts SET resource_id=?,version=? WHERE user_id=? AND kind='note' AND resource_id=? AND team_id=0",(old_id,version,row['user_id'],new_id))
        db.execute("DELETE FROM editor_drafts WHERE kind='note' AND resource_id=?",(new_id,))
    db.execute('UPDATE note_redirects SET note_id=? WHERE note_id=?', (old_id, new_id))
    db.execute('INSERT INTO note_redirects(old_id,note_id) VALUES(?,?)', (new_id, old_id))
    for row in db.execute("SELECT * FROM navigation_orders WHERE kind='note'").fetchall():
        keys = json.loads(row['keys_json'])
        if str(new_id) in keys:
            keys = list(dict.fromkeys(str(old_id) if key == str(new_id) else key for key in keys))
            db.execute("UPDATE navigation_orders SET keys_json=?,version=version+1 WHERE user_id=? AND kind='note' AND parent=?",
                       (json.dumps(keys), row['user_id'], row['parent']))
    db.execute('DELETE FROM notes WHERE id=?', (new_id,))


def rename_note(db, data, safe_note_path, record_sha, save_note):
    source = data.get('from_path')
    target = data.get('path')
    old_path, new_path = safe_note_path(source), safe_note_path(target)
    if source == target:
        raise HTTPException(400, '改名前后路径相同')
    note = db.execute('SELECT * FROM notes WHERE path=?', (source,)).fetchone()
    if not note or record_sha(note) != data.get('base_sha'):
        return None
    if note['visibility'] == 'public':
        if old_path.parts[0] not in PUBLIC_ROOTS or new_path.parts[0] not in PUBLIC_ROOTS:
            raise HTTPException(400, '公开笔记只能移动到 Backend 或 Frontend')
    elif ((len(new_path.parts) > 1 and new_path.parts[0] not in PRIVATE_ROOTS)
          or new_path.parts[0] in PUBLIC_ROOTS | {'WIKI', 'WIKI.md'}):
        raise HTTPException(400, '私密笔记不能通过改名进入公开或工具目录')
    original = dict(note, sha=record_sha(note))
    if not missing_file_conflict(db, original):
        return None
    duplicate = db.execute('SELECT * FROM notes WHERE path=?', (target,)).fetchone()
    if duplicate:
        if (duplicate['visibility'] != note['visibility'] or record_sha(duplicate) != data.get('target_sha')
                or db.execute("SELECT 1 FROM sync_conflicts WHERE note_id=? AND status IN ('open','resolved')", (duplicate['id'],)).fetchone()):
            return None
        # Only consolidate a duplicate whose current body matches the submitted file.
        if duplicate['body'] != data.get('body'):
            return None
        # Independently configured shares need an explicit choice, never silently widen either link.
        if (active_share(db, db.execute('SELECT * FROM shares WHERE note_id=?', (note['id'],)).fetchone())
                and active_share(db, db.execute('SELECT * FROM shares WHERE note_id=?', (duplicate['id'],)).fetchone())):
            return None
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='editor_drafts'").fetchone() and db.execute('''SELECT 1 FROM editor_drafts a JOIN editor_drafts b ON a.user_id=b.user_id
            WHERE a.kind='note' AND b.kind='note' AND a.resource_id=? AND b.resource_id=? AND a.team_id=0 AND b.team_id=0 AND a.deleted=0 AND b.deleted=0 LIMIT 1''',(note['id'],duplicate['id'])).fetchone():
            return None
    elif data.get('target_sha'):
        return None
    body = data.get('body')
    if not isinstance(body, str) or not body.strip():
        raise HTTPException(400, '正文无效')
    now = int(time.time())
    category = note['category']
    summary = note['summary']
    if duplicate:
        category = duplicate['category']
        summary = duplicate['summary'] or summary
    elif category == default_category(source, note['visibility']):
        category = default_category(target, note['visibility'])
    audit={'source':dict(note),'duplicate':dict(duplicate) if duplicate else None,'base_sha':record_sha(note)}
    move_id=db.execute('INSERT INTO note_moves(note_id,from_path,to_path,metadata_json,created_at) VALUES(?,?,?,?,?)',
               (note['id'], source, target, json.dumps(audit, ensure_ascii=False), now)).lastrowid
    if duplicate:
        merge_duplicate(db, note, duplicate)
    # A conflict caused solely by a missing old filename is resolved by this move.
    db.execute("UPDATE sync_conflicts SET status='applied',updated_at=? WHERE note_id=? AND status='open'", (now, note['id']))
    db.execute("UPDATE edit_events SET status='applied' WHERE note_id=? AND status IN ('pending','conflict')", (note['id'],))
    save_note(db, note, body, 'local')
    db.execute('UPDATE notes SET path=?,title=?,summary=?,category=?,revision=revision+1,updated_at=? WHERE id=?',
               (target, new_path.stem, summary, category, now, note['id']))
    from agent_dependencies import source_changed
    source_changed(db,note['id'])
    if category != note['category']:
        # Keep manual article order with the same note when its directory changes.
        for row in db.execute("SELECT * FROM navigation_orders WHERE kind='note'").fetchall():
            keys = json.loads(row['keys_json'])
            key = str(note['id'])
            if row['parent'] != category and key in keys:
                db.execute("UPDATE navigation_orders SET keys_json=?,version=version+1 WHERE user_id=? AND kind='note' AND parent=?",
                           (json.dumps([item for item in keys if item != key]), row['user_id'], row['parent']))
    renamed = db.execute('SELECT * FROM notes WHERE id=?', (note['id'],)).fetchone()
    audit['sha']=record_sha(renamed)
    db.execute('UPDATE note_moves SET metadata_json=? WHERE id=?',(json.dumps(audit,ensure_ascii=False),move_id))
    return dict(renamed, sha=record_sha(renamed))
