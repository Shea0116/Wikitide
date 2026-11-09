"""Explicit public-note downloads. No session, private note or team content is exported."""
import base64
import mimetypes
import re
import time
from urllib.parse import parse_qs, urlsplit
from fastapi import HTTPException, Request
from explore_search import PUBLIC_SQL, public_path


def register_offline(app, db_path, api):
    def public_note(db,note_id):
        note=db.execute(f'SELECT notes.* FROM notes WHERE notes.id=? AND {PUBLIC_SQL}',(note_id,)).fetchone()
        if not note or not public_path(note['path']): raise HTTPException(404,'此文章不在公开离线范围')
        return note

    @app.get('/api/offline/{note_id}/status')
    def status(note_id:int):
        with api['database'](db_path) as db:
            note=public_note(db,note_id)
            return dict(id=note_id,sha=note['body_sha'],updated_at=note['updated_at'])

    @app.get('/api/offline/{note_id}')
    def download(request:Request,note_id:int):
        with api['database'](db_path) as db:
            note=public_note(db,note_id)
            html=str(api['rendered_markdown'](db,note,None,app.state.content_root))
            allowed=api['linked_assets'](note,app.state.content_root)
            total=len(html.encode()); missing=[]
            def image(match):
                nonlocal total
                original=match.group(1)
                parsed=urlsplit(original.replace('&amp;','&'))
                if parsed.path!=f'/asset/{note_id}':
                    missing.append('外部图片'); return 'src=""'
                source=parse_qs(parsed.query).get('path',[''])[0]
                path=api['note_asset_path'](note,source)
                file=app.state.content_root/(path or '')
                root=(app.state.content_root/note['path'].split('/')[0]).resolve()
                if path not in allowed or file.is_symlink() or not file.resolve().is_relative_to(root):
                    missing.append('缺失图片'); return 'src=""'
                if api.get('asset_bytes'):
                    raw=api['asset_bytes'](db,app.state.content_root,note,path,api['linked_assets'])
                else:
                    raw=file.read_bytes() if file.is_file() and file.stat().st_size<=4*1024*1024 else None
                if raw is None:
                    missing.append('缺失图片'); return 'src=""'
                size=len(raw)
                if size>4*1024*1024 or total+size*4//3>8*1024*1024:
                    missing.append('超出离线包大小的图片'); return 'src=""'
                data=base64.b64encode(raw).decode(); total+=len(data)
                mime=mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
                return f'src="data:{mime};base64,{data}"'
            html=re.sub(r'src="([^"]*)"',image,html)
            if len(html.encode())>8*1024*1024: raise HTTPException(413,'文章过大，无法保存离线副本')
            return dict(id=note_id,title=note['title'],html=html,sha=note['body_sha'],updated_at=note['updated_at'],downloaded_at=int(time.time()),missing=sorted(set(missing)))
