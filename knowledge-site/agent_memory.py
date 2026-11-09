"""Explicit, account/project memory with source validation and forgetting propagation."""
import hashlib
import json
import secrets
import time

from fastapi import HTTPException

from agent_context import compact_json, estimate_tokens
from explore_search import PUBLIC_SQL, public_path, terms


def identifier():
    return secrets.token_urlsafe(24)


def text_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def public_note(db, note_id):
    row = db.execute(f'SELECT * FROM notes WHERE id=? AND {PUBLIC_SQL}', (note_id,)).fetchone()
    return row if row and public_path(row['path']) else None


def valid_reference(db, ref):
    """Refs here are created by our server; transient clients cannot provide them."""
    kind = ref.get('kind')
    if kind == 'memory':
        memory = db.execute('SELECT * FROM agent_memory_items WHERE id=? AND user_id=?',
                            (ref.get('id'), ref.get('user_id'))).fetchone()
        return bool(memory and memory['status'] == 'active' and memory['revision'] == ref.get('revision')
                    and (memory['expires_at'] is None or memory['expires_at'] > int(time.time()))
                    and valid_memory_source(db, memory))
    if kind == 'note':
        note = public_note(db, ref.get('note_id'))
        return bool(note and note['body_sha'] == ref.get('body_sha'))
    if kind == 'web':
        from explore_web import public_web_url
        return public_web_url(ref.get('url'))
    return False


def valid_memory_source(db, memory):
    source = json.loads(memory['source_json'])
    if source.get('kind') == 'manual':
        return True
    if source.get('kind') == 'note':
        return valid_reference(db, source)
    if source.get('kind') == 'message':
        row = db.execute('''SELECT m.content,m.sources,a.sources_json,a.invalidated
            FROM explore_messages m JOIN explore_sessions s ON s.id=m.session_id
            LEFT JOIN agent_answer_context a ON a.message_id=m.id WHERE m.id=? AND s.user_id=?''',
            (source.get('id'), memory['user_id'])).fetchone()
        if not row or row['invalidated'] or text_hash(row['content']) != source.get('sha'):
            return False
        if db.execute('SELECT 1 FROM agent_memory_suppressions WHERE user_id=? AND source_message_id=?',
                      (memory['user_id'], source['id'])).fetchone():
            return False
        # Disallow remembering a memory-derived answer: keeps provenance acyclic.
        refs = [*json.loads(row['sources']), *json.loads(row['sources_json'] or '[]')]
        return all(ref.get('kind') != 'memory' and valid_reference(db, ref) for ref in refs)
    return False


def bump_epoch(db, user_id):
    db.execute('INSERT OR IGNORE INTO agent_user_state(user_id) VALUES(?)', (user_id,))
    db.execute('UPDATE agent_user_state SET context_epoch=context_epoch+1 WHERE user_id=?', (user_id,))
    db.execute("UPDATE agent_context_snapshots SET status='invalid',summary_json='',sources_json='[]' WHERE user_id=?", (user_id,))


def epoch(db, user_id):
    row = db.execute('SELECT context_epoch FROM agent_user_state WHERE user_id=?', (user_id,)).fetchone()
    return row[0] if row else 0


class MemoryService:
    def __init__(self, database, path, enabled=False):
        self.database, self.path, self.enabled = database, path, enabled

    @staticmethod
    def own(db, user_id, memory_id):
        row = db.execute("SELECT * FROM agent_memory_items WHERE id=? AND user_id=? AND status!='deleted'", (memory_id, user_id)).fetchone()
        if not row:
            raise HTTPException(404, '记忆不存在。')
        return dict(row)

    @staticmethod
    def source(db, user_id, source):
        if source.kind == 'manual':
            return {'kind': 'manual'}
        if source.kind == 'note':
            note = public_note(db, source.id)
            if not note:
                raise HTTPException(404, '来源不在公开技术资料范围。')
            return {'kind': 'note', 'note_id': note['id'], 'body_sha': note['body_sha']}
        row = db.execute('''SELECT m.* FROM explore_messages m JOIN explore_sessions s ON s.id=m.session_id
            WHERE m.id=? AND s.user_id=?''', (source.id, user_id)).fetchone()
        if not row:
            raise HTTPException(404, '来源消息不存在。')
        result = {'kind': 'message', 'id': row['id'], 'sha': text_hash(row['content'])}
        if not valid_memory_source(db, {'source_json': compact_json(result), 'user_id': user_id}):
            raise HTTPException(409, '来源已失效、已被遗忘或包含记忆派生内容，请改为手动输入。')
        result['candidate'] = row['role'] == 'assistant'
        return result

    @staticmethod
    def event(db, user_id, memory_id, action, revision):
        db.execute('INSERT INTO agent_memory_events VALUES(?,?,?,?,?,?)',
                   (identifier(), user_id, memory_id, action, revision, int(time.time())))
        db.execute('DELETE FROM agent_memory_events WHERE created_at<?', (int(time.time()) - 90 * 86400,))

    def create(self, db, user_id, data):
        from agent_projects import own_project
        if data.scope == 'project':
            own_project(db, user_id, data.project_id)
        elif data.project_id:
            raise HTTPException(400, '账户记忆不能关联项目。')
        content = data.content.strip()
        if not content:
            raise HTTPException(400, '记忆内容不能为空。')
        if db.execute("SELECT count(*) FROM agent_memory_items WHERE user_id=? AND status!='deleted'", (user_id,)).fetchone()[0] >= 1000:
            raise HTTPException(429, '记忆已达到 1000 条，请先整理。')
        source = self.source(db, user_id, data.source)
        mid, now = identifier(), int(time.time())
        status = 'candidate' if source.pop('candidate', False) else 'active'
        db.execute('''INSERT INTO agent_memory_items(id,user_id,project_id,scope,kind,content,status,source_json,
            conflict_key,expires_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
            (mid,user_id,data.project_id,data.scope,data.kind,content,status,compact_json(source),data.conflict_key.strip(),data.expires_at,now,now))
        if source['kind'] != 'manual':
            sid = str(source.get('id', source.get('note_id')))
            db.execute('INSERT OR IGNORE INTO agent_artifact_dependencies VALUES(?,?,?,?,?,?)',
                       (user_id,source['kind'],sid,source.get('body_sha',source.get('sha','')),'memory',mid))
        bump_epoch(db,user_id); self.event(db,user_id,mid,'create',1)
        return self.present(db, self.own(db,user_id,mid))

    @staticmethod
    def present(db, row):
        result = {k: row[k] for k in ('id','project_id','scope','kind','content','status','revision','expires_at','conflict_key','updated_at')}
        result['source'] = json.loads(row['source_json'])
        result['source_valid'] = valid_memory_source(db,row)
        if not result['source_valid']:
            result['content'] = ''
            result['source'] = {'kind': result['source'].get('kind'), 'available': False}
        result['expired'] = row['expires_at'] is not None and row['expires_at'] <= int(time.time())
        return result

    def list(self, db, user_id, project_id=None, status=None, before=0, limit=20):
        where, params = ["user_id=?", "status!='deleted'"], [user_id]
        if project_id:
            from agent_projects import own_project
            own_project(db,user_id,project_id); where.append('project_id=?'); params.append(project_id)
        if status:
            where.append('status=?'); params.append(status)
        if before:
            where.append('rowid<?'); params.append(before)
        rows = db.execute('SELECT rowid AS cursor,* FROM agent_memory_items WHERE ' + ' AND '.join(where) + ' ORDER BY rowid DESC LIMIT ?', (*params,limit+1)).fetchall()
        return {'memories':[self.present(db,dict(row)) for row in rows[:limit]],
                'next_cursor': rows[limit-1]['cursor'] if len(rows)>limit else None}

    def mutate(self, db, user_id, mid, revision, data=None, delete=False, activate=False):
        row = self.own(db,user_id,mid)
        if row['revision'] != revision:
            raise HTTPException(409, '记忆已在其他窗口修改，请重新读取。')
        if delete:
            source = json.loads(row['source_json'])
            if source.get('kind') == 'message':
                db.execute('INSERT OR IGNORE INTO agent_memory_suppressions VALUES(?,?,?)', (user_id,source['id'],int(time.time())))
            db.execute("UPDATE agent_memory_items SET content='',source_json='{}',status='deleted',revision=revision+1,updated_at=? WHERE id=?", (int(time.time()),mid))
        else:
            if not valid_memory_source(db,row):
                raise HTTPException(409,'来源已失效，请重新核实后创建记忆。')
            if activate:
                if row['status'] != 'candidate': raise HTTPException(409,'这条记忆不是待确认候选。')
                db.execute("UPDATE agent_memory_items SET status='active',revision=revision+1,updated_at=? WHERE id=?",(int(time.time()),mid))
            else:
                if not data.content.strip(): raise HTTPException(400,'记忆内容不能为空。')
                db.execute('''UPDATE agent_memory_items SET content=?,status=?,conflict_key=?,expires_at=?,
                    revision=revision+1,updated_at=? WHERE id=?''',
                    (data.content.strip(),data.status,data.conflict_key.strip(),data.expires_at,int(time.time()),mid))
        # All answer provenance is flattened, so this also catches indirect descendants.
        affected = 0
        for answer in db.execute('''SELECT a.message_id,a.sources_json FROM agent_answer_context a
            JOIN explore_messages m ON m.id=a.message_id JOIN explore_sessions s ON s.id=m.session_id WHERE s.user_id=?''',(user_id,)).fetchall():
            if any(ref.get('kind')=='memory' and ref.get('id')==mid for ref in json.loads(answer['sources_json'])):
                db.execute('UPDATE agent_answer_context SET invalidated=1 WHERE message_id=?',(answer['message_id'],)); affected += 1
        bump_epoch(db,user_id); self.event(db,user_id,mid,'forget' if delete else ('activate' if activate else 'update'),revision+1)
        return {'ok':True,'affected_answers':affected} if delete else self.present(db,self.own(db,user_id,mid))

    def select(self, db, user_id, project_id, question, budget=1000):
        rows = [dict(row) for row in db.execute('''SELECT * FROM agent_memory_items WHERE user_id=? AND status='active'
            AND (expires_at IS NULL OR expires_at>?) AND (scope='account' OR project_id=?) ORDER BY updated_at DESC,id''',
            (user_id,int(time.time()),project_id)) if valid_memory_source(db,row)]
        query = set(terms(question))
        conflicts = set()
        grouped = {}
        for row in rows:
            if row['conflict_key']: grouped.setdefault((row['scope'],row['conflict_key']),[]).append(row)
        for key, group in grouped.items():
            if len({r['content'] for r in group})>1: conflicts.update(r['id'] for r in group)
        project_keys = {r['conflict_key'] for r in rows if r['scope']=='project' and r['conflict_key']}
        rows = [r for r in rows if r['id'] not in conflicts and not (r['scope']=='account' and r['conflict_key'] in project_keys)]
        rows.sort(key=lambda r:(r['scope']=='project',len(query & set(terms(r['content']))),r['updated_at']),reverse=True)
        selected, refs, used = [], [], 0
        for row in rows:
            entry = {k:row[k] for k in ('id','revision','kind','scope','content')}
            cost = estimate_tokens(entry)
            if len(selected)>=5 or used+cost>budget: continue
            selected.append(entry); used += cost
            refs.append({'kind':'memory','id':row['id'],'user_id':user_id,'revision':row['revision']})
        return selected, refs, sorted(conflicts)
