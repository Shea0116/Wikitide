"""Public release notes and account-scoped, monotonic read receipts."""
from datetime import date
import json
from pathlib import Path
import re
import time

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from storage import connect
from i18n import ui

RELEASES_FILE = Path(__file__).with_name('release-notes.json')


def load_releases(path=RELEASES_FILE):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict) or set(data) != {'releases'}:
        raise ValueError('更新说明必须包含 releases 列表')
    releases = data['releases']
    if not isinstance(releases, list) or not 1 <= len(releases) <= 50:
        raise ValueError('请保留 1–50 条更新记录')
    ids, sequences = set(), set()
    for release in releases:
        if not isinstance(release, dict) or set(release) != {'id','sequence','date','title','summary','highlights'}:
            raise ValueError('更新记录字段无效')
        if not isinstance(release['id'], str) or not re.fullmatch(r'\d{8}\.\d+', release['id']):
            raise ValueError('更新编号使用 YYYYMMDD.序号')
        sequence = release['sequence']
        if type(sequence) is not int or sequence < 1 or sequence in sequences or release['id'] in ids:
            raise ValueError('更新编号和 sequence 必须唯一')
        if not isinstance(release['date'], str): raise ValueError('更新日期无效')
        date.fromisoformat(release['date'])
        for key, limit in [('title',80),('summary',240)]:
            if not isinstance(release[key], str) or not release[key].strip() or len(release[key]) > limit:
                raise ValueError('更新标题或摘要无效')
        bullets = release['highlights']
        if not isinstance(bullets, list) or not 1 <= len(bullets) <= 8 or any(
                not isinstance(item, str) or not item.strip() or len(item) > 180 for item in bullets):
            raise ValueError('更新说明应有 1–8 条简短变化')
        ids.add(release['id']); sequences.add(sequence)
    if [r['sequence'] for r in releases] != sorted(sequences, reverse=True):
        raise ValueError('更新记录必须按 sequence 从新到旧排列')
    return releases


def validate_upgrade(current, previous):
    if current[0]['sequence'] <= previous[0]['sequence']:
        raise ValueError('每次上线须新增 sequence 更大的更新说明，不能复用旧版本')
    old = {item['id']:item for item in previous}
    retained = {item['id']:item for item in current}
    if previous[0]['id'] not in retained:
        raise ValueError('请保留上一次更新记录')
    for release_id in old.keys() & retained.keys():
        if old[release_id] != retained[release_id]:
            raise ValueError('已发布更新记录不能改写，请新增一条记录')


def register_updates(app, db_path, api):
    releases = load_releases(RELEASES_FILE)
    latest = releases[0]
    with connect(db_path) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS release_reads(
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL CHECK(sequence>0), release_id TEXT NOT NULL,
            read_at INTEGER NOT NULL)''')

    @app.get('/updates')
    def history(request: Request):
        with api['database'](db_path) as db:
            user, csrf = api['current_user'](db, request)
            return api['page'](request, 'updates.html', user=user, csrf=csrf,
                               categories=api['categories'](db, user), releases=releases)

    @app.get('/api/updates')
    def current(request: Request):
        with api['database'](db_path) as db:
            user, _ = api['current_user'](db, request)
            receipt = db.execute('SELECT sequence FROM release_reads WHERE user_id=?',
                                 (user['id'],)).fetchone() if user else None
            seen = receipt['sequence'] if receipt else 0
            display = {**latest, 'title':ui(latest['title']), 'summary':ui(latest['summary']),
                       'highlights':[ui(value) for value in latest['highlights']]}
            return JSONResponse({'latest':display, 'account_id':user['id'] if user else None,
                                 'unread':seen < latest['sequence']},
                                headers={'Cache-Control':'no-store','Vary':'Cookie, Accept-Language'})

    @app.post('/api/updates/seen')
    async def acknowledge(request: Request):
        raw = await request.body()
        if len(raw) > 1024: raise HTTPException(413)
        try: data = json.loads(raw)
        except (ValueError, UnicodeDecodeError): raise HTTPException(400, '请求格式无效')
        if not isinstance(data, dict) or not isinstance(data.get('release_id'), str):
            raise HTTPException(400, '请求格式无效')
        with api['database'](db_path) as db:
            user, csrf = api['current_user'](db, request)
            if not user: raise HTTPException(401, '请先登录')
            api['require_csrf']({'csrf':request.headers.get('X-CSRF-Token','')}, csrf)
            if data['release_id'] != latest['id']:
                raise HTTPException(409, '更新版本已变化，请刷新后查看')
            db.execute('''INSERT INTO release_reads VALUES(?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET sequence=excluded.sequence,
                release_id=excluded.release_id,read_at=excluded.read_at
                WHERE release_reads.sequence<=excluded.sequence''',
                (user['id'],latest['sequence'],latest['id'],int(time.time())))
        return {'ok':True}
