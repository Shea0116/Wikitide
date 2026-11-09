"""Public-note version snapshots and private artifact dependency invalidation.

Every read rechecks the current public scope. A historical snapshot is never an
authorization grant, and snapshots are erased when a note leaves that scope.
"""
import difflib
import hashlib
import json
import re
import time

from agent_context import compact_json
from agent_memory import public_note


def available(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_note_versions'").fetchone() is not None


def sections(body):
    """Heading text is a match candidate, not an ordinal-based stable identity."""
    result, current, lines = [], '', []
    fence = None
    for line in body.splitlines(keepends=True):
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if marker:
            if fence is None: fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence): fence = None
        heading = re.match(r'^#{1,6}\s+(.+?)\s*$', line) if fence is None else None
        if heading:
            if lines: result.append({'heading': current, 'text': ''.join(lines)})
            current, lines = re.sub(r'<[^>]*>', '', heading[1]), []
        lines.append(line)
    if lines: result.append({'heading': current, 'text': ''.join(lines)})
    for item in result: item['sha'] = hashlib.sha256(item['text'].encode()).hexdigest()
    return result


def snapshot_note(db, note_id):
    if not available(db): return
    note = public_note(db, note_id)
    if not note:
        db.execute('DELETE FROM agent_note_versions WHERE note_id=?', (note_id,))
        return
    if db.execute('SELECT 1 FROM agent_note_versions WHERE note_id=? AND body_sha=?', (note_id, note['body_sha'])).fetchone(): return
    db.execute('INSERT OR IGNORE INTO agent_note_versions VALUES(?,?,?,?)',
               (note_id, note['body_sha'], compact_json(sections(note['body'])), int(time.time())))
    # Finite retention. Existing dependency metadata remains useful after pruning.
    db.execute('''DELETE FROM agent_note_versions WHERE note_id=? AND body_sha NOT IN
        (SELECT body_sha FROM agent_note_versions WHERE note_id=? ORDER BY created_at DESC,rowid DESC LIMIT 10)''', (note_id, note_id))


def source_changed(db, note_id):
    """Call in the same transaction as a note mutation; no independent commit."""
    if not available(db): return
    note = public_note(db, note_id)
    current_sha = note['body_sha'] if note else None
    snapshot_note(db, note_id)
    affected_users = set()
    for item in db.execute("SELECT * FROM agent_artifact_dependencies WHERE source_kind='note' AND source_id=?", (str(note_id),)).fetchall():
        if current_sha and item['source_revision'] == current_sha: continue
        affected_users.add(item['user_id'])
        if item['target_kind'] == 'memory':
            db.execute("UPDATE agent_memory_items SET status='stale' WHERE id=? AND user_id=? AND status NOT IN ('deleted','disabled')", (item['target_id'], item['user_id']))
        elif item['target_kind'] == 'answer':
            db.execute('UPDATE agent_answer_context SET invalidated=1 WHERE message_id=?', (item['target_id'],))
    # Some older artifacts predate the explicit dependency index.
    for row in db.execute("SELECT id,user_id,source_json FROM agent_memory_items WHERE status='active'").fetchall():
        source = json.loads(row['source_json'])
        if source.get('kind') == 'note' and source.get('note_id') == note_id and source.get('body_sha') != current_sha:
            db.execute("UPDATE agent_memory_items SET status='stale' WHERE id=?", (row['id'],))
            affected_users.add(row['user_id'])
    for row in db.execute('SELECT id,user_id,sources_json FROM agent_context_snapshots WHERE status=\'valid\'').fetchall():
        if any(ref.get('kind') == 'note' and ref.get('note_id') == note_id and ref.get('body_sha') != current_sha for ref in json.loads(row['sources_json'])):
            db.execute("UPDATE agent_context_snapshots SET status='invalid',summary_json='',sources_json='[]' WHERE id=?", (row['id'],))
            affected_users.add(row['user_id'])
    # No epoch bump is needed: strict source checks fence in-flight commits while
    # leaving unrelated tasks alone. Memory mutation still bumps its own epoch.
    return len(affected_users)


def reconcile(db):
    if not available(db): return
    ids = {row[0] for row in db.execute('SELECT DISTINCT note_id FROM agent_note_versions')}
    ids.update(row[0] for row in db.execute('SELECT id FROM notes'))
    for note_id in ids:
        note = public_note(db, note_id)
        indexed = db.execute('SELECT body_sha FROM explore_documents WHERE note_id=?', (note_id,)).fetchone()
        snapshotted = bool(note and db.execute('SELECT 1 FROM agent_note_versions WHERE note_id=? AND body_sha=?', (note_id, note['body_sha'])).fetchone())
        if note and snapshotted and indexed and indexed[0] == note['body_sha']: continue
        source_changed(db, note_id)


def record_dependencies(db, user_id, refs, target_kind, target_id):
    for ref in refs:
        kind = ref.get('kind')
        if kind not in {'note', 'memory'}: continue
        source_id = str(ref.get('note_id') if kind == 'note' else ref['id'])
        revision = str(ref.get('body_sha', ref.get('revision', '')))
        db.execute('INSERT OR IGNORE INTO agent_artifact_dependencies VALUES(?,?,?,?,?,?)',
                   (user_id, kind, source_id, revision, target_kind, str(target_id)))


def affected_artifacts(db, user_id, note_id, current_sha):
    """Only return IDs of artifacts still owned by the requesting account."""
    result, seen = [], set()
    mappings = {'draft': ('explore_drafts', 'id'), 'memory': ('agent_memory_items', 'id'),
                'run': ('agent_runs', 'id'), 'snapshot': ('agent_context_snapshots', 'id')}
    for row in db.execute("SELECT * FROM agent_artifact_dependencies WHERE user_id=? AND source_kind='note' AND source_id=? AND source_revision!=?", (user_id, str(note_id), current_sha)):
        kind, rid = row['target_kind'], row['target_id']
        if kind in mappings:
            table, key = mappings[kind]
            owned = db.execute(f'SELECT 1 FROM {table} WHERE {key}=? AND user_id=?', (rid, user_id)).fetchone()
        elif kind == 'answer':
            owned = db.execute('SELECT 1 FROM explore_messages m JOIN explore_sessions s ON s.id=m.session_id WHERE m.id=? AND s.user_id=?', (rid, user_id)).fetchone()
        else: owned = None
        if owned and (kind, rid) not in seen:
            seen.add((kind, rid)); result.append({'kind': kind, 'id': rid, 'source_revision': row['source_revision']})
    # Older write paths stored provenance without an explicit dependency record.
    for row in db.execute('SELECT d.id,d.sources,c.sources_json FROM explore_drafts d LEFT JOIN agent_draft_context c ON c.draft_id=d.id WHERE d.user_id=?', (user_id,)):
        refs = json.loads(row['sources']) + json.loads(row['sources_json'] or '[]')
        for ref in refs:
            if ref.get('note_id') == note_id and ref.get('body_sha') != current_sha and ('draft', row['id']) not in seen:
                seen.add(('draft', row['id'])); result.append({'kind': 'draft', 'id': row['id'], 'source_revision': ref.get('body_sha', '')})
    return result[:200]


def analyze_change(db, user_id, note_id):
    note = public_note(db, note_id)
    if not note:
        # Intentionally omit title, old text, old hashes and artifact excerpts.
        return {'note_id': note_id, 'available': False, 'reason': 'source_unavailable', 'affected': [], 'changes': []}
    snapshot_note(db, note_id)
    current = sections(note['body'])
    previous = db.execute('SELECT * FROM agent_note_versions WHERE note_id=? AND body_sha!=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (note_id, note['body_sha'])).fetchone()
    affected = affected_artifacts(db, user_id, note_id, note['body_sha'])
    if not previous:
        return {'note_id': note_id, 'available': True, 'body_sha': note['body_sha'], 'classification': 'unknown', 'affected': affected, 'changes': [], 'reason': 'previous_version_unavailable'}
    old = json.loads(previous['sections_json'])
    old_body, new_body = ''.join(s['text'] for s in old), note['body']
    normalized = lambda value: re.sub(r'\s+', '', re.sub(r'<[^>]*>|[*_`#]', '', value))
    classification = 'format_only' if normalized(old_body) == normalized(new_body) else 'review_required'
    changes = []
    for heading in dict.fromkeys([s['heading'] for s in old] + [s['heading'] for s in current]):
        before = [s for s in old if s['heading'] == heading]
        after = [s for s in current if s['heading'] == heading]
        if len(before) != 1 or len(after) != 1:
            changes.append({'heading': heading, 'classification': 'uncertain_match'}); continue
        if before[0]['sha'] != after[0]['sha']:
            changes.append({'heading': heading, 'classification': 'review_required'})
    diff = ''.join(difflib.unified_diff(old_body.splitlines(keepends=True), new_body.splitlines(keepends=True), fromfile='previous-public-version', tofile='current-public-version'))[:40000]
    return {'note_id': note_id, 'available': True, 'body_sha': note['body_sha'], 'previous_sha': previous['body_sha'],
            'classification': classification, 'affected': affected, 'changes': changes[:100], 'source_diff': diff,
            'suggestion': '复核受影响资料中的结论与引用；修订建议需人工确认。'}
