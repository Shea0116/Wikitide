"""Account-scoped sibling ordering shared by navigation and browse results."""
from __future__ import annotations

import json
from collections import defaultdict

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from storage import connect, visible_where


def initialize_orders(db_path):
    with connect(db_path) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS navigation_orders (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK(kind IN ('category','note')),
            parent TEXT NOT NULL,
            keys_json TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(user_id,kind,parent)
        )""")


def saved_orders(db, user):
    if user is None:
        return {}
    return {(row['kind'], row['parent']): {
        'keys': json.loads(row['keys_json']), 'version': row['version']
    } for row in db.execute(
        'SELECT kind,parent,keys_json,version FROM navigation_orders WHERE user_id=?',
        (user['id'],)
    )}


def available_groups(db, user):
    where, params = visible_where(user)
    rows = db.execute(
        f'SELECT notes.id,notes.category,notes.title FROM notes WHERE {where} '
        'ORDER BY notes.category,notes.title,notes.id', params
    ).fetchall()
    categories = defaultdict(set)
    notes = defaultdict(list)
    for row in rows:
        parts = row['category'].split(' / ')
        for depth in range(len(parts)):
            categories[' / '.join(parts[:depth])].add(' / '.join(parts[:depth + 1]))
        notes[row['category']].append(str(row['id']))
    groups = {('category', parent): sorted(keys) for parent, keys in categories.items()}
    groups.update({('note', parent): keys for parent, keys in notes.items()})
    return groups


def ordered_keys(default, saved):
    allowed = set(default)
    chosen = [key for key in saved if key in allowed]
    selected = set(chosen)
    return chosen + [key for key in default if key not in selected]


def order_snapshot(db, user):
    saved = saved_orders(db, user)
    result = []
    for (kind, parent), default in available_groups(db, user).items():
        record = saved.get((kind, parent), {'keys': [], 'version': 0})
        result.append({'kind': kind, 'parent': parent, 'version': record['version'],
                       'keys': ordered_keys(default, record['keys'])})
    return result


def sort_category_tree(db, user, roots):
    saved = saved_orders(db, user)
    def visit(children, parent):
        rank = {key: i for i, key in enumerate(saved.get(('category', parent), {}).get('keys', []))}
        children.sort(key=lambda child: (rank.get(child['path'], len(rank)), child['path']))
        for child in children:
            visit(child['children'], child['path'])
    visit(roots, '')
    return roots


def sort_note_rows(db, user, rows):
    saved = saved_orders(db, user)
    ranks = {group: {key: i for i, key in enumerate(record['keys'])}
             for group, record in saved.items()}
    def key(row):
        parts = row['category'].split(' / ')
        path_rank = []
        for depth, name in enumerate(parts):
            rank = ranks.get(('category', ' / '.join(parts[:depth])), {})
            path_rank.append((rank.get(' / '.join(parts[:depth + 1]), len(rank)), name))
        rank = ranks.get(('note', row['category']), {})
        return (tuple(path_rank), rank.get(str(row['id']), len(rank)), row['title'], row['id'])
    return sorted(rows, key=key)


def sort_article_tree(db, user, root):
    saved = saved_orders(db, user)
    def visit(item):
        note_rank = {key: i for i, key in enumerate(saved.get(('note', item['path']), {}).get('keys', []))}
        item['notes'].sort(key=lambda row: (note_rank.get(str(row['id']), len(note_rank)), row['title'], row['id']))
        category_rank = {key: i for i, key in enumerate(saved.get(('category', item['path']), {}).get('keys', []))}
        item['children'].sort(key=lambda child: (category_rank.get(child['path'], len(category_rank)), child['path']))
        for child in item['children']:
            visit(child)
    visit(root)
    return root


def register_orders(app, db_path, database, current_user, require_csrf):
    initialize_orders(db_path)

    @app.get('/api/navigation-order')
    def get_orders(request: Request):
        with database(db_path) as db:
            user, _ = current_user(db, request)
            if user is None:
                raise HTTPException(401, '请先登录')
            response = JSONResponse({'groups': order_snapshot(db, user)})
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['Vary'] = 'Cookie'
        return response

    @app.put('/api/navigation-order')
    async def put_order(request: Request):
        # Bound input before parsing; note bodies and metadata never enter this API.
        raw = await request.body()
        if len(raw) > 1024 * 1024:
            raise HTTPException(413, '排序内容过大')
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, '排序内容无效')
        if not isinstance(data, dict):
            raise HTTPException(400, '排序内容无效')
        kind, parent, keys, version = (data.get(name) for name in ('kind', 'parent', 'keys', 'version'))
        if (kind not in ('category', 'note') or not isinstance(parent, str) or len(parent) > 240
                or not isinstance(keys, list) or len(keys) > 5000
                or not all(isinstance(key, str) and len(key) <= 240 for key in keys)
                or len(set(keys)) != len(keys) or type(version) is not int or version < 0):
            raise HTTPException(400, '排序内容无效')
        with database(db_path) as db:
            user, csrf = current_user(db, request)
            if user is None:
                raise HTTPException(401, '请先登录')
            require_csrf({'csrf': request.headers.get('X-CSRF-Token', '')}, csrf)
            db.execute('BEGIN IMMEDIATE')
            default = available_groups(db, user).get((kind, parent))
            if default is None or set(keys) != set(default):
                raise HTTPException(400, '目录已变化，请刷新后重试')
            prior = saved_orders(db, user).get((kind, parent), {'keys': [], 'version': 0})
            if prior['version'] != version:
                return JSONResponse({'error': 'conflict', 'kind': kind, 'parent': parent,
                                     'version': prior['version'],
                                     'keys': ordered_keys(default, prior['keys'])}, status_code=409)
            new_version = version + 1
            db.execute('''INSERT INTO navigation_orders(user_id,kind,parent,keys_json,version)
                          VALUES(?,?,?,?,?) ON CONFLICT(user_id,kind,parent) DO UPDATE
                          SET keys_json=excluded.keys_json,version=excluded.version''',
                       (user['id'], kind, parent, json.dumps(keys, ensure_ascii=False), new_version))
        return {'kind': kind, 'parent': parent, 'keys': keys, 'version': new_version}
