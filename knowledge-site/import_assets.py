"""Bounded archive/image handling shared by import extraction and commit."""
import base64
import hashlib
import io
import posixpath
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit
from markdown_it import MarkdownIt
from html_compat import sanitize_html
from note_assets import enable_embeds, parse_note

MAX_ASSETS=10*1024*1024
PARSER=enable_embeds(MarkdownIt('default',{'html':True}))


def archive_members(archive):
    entries=archive.infolist()
    if len(entries)>2000 or sum(item.file_size for item in entries)>30*1024*1024:
        raise ValueError('压缩包解压体积或文件数超过限制')
    seen=set()
    for item in entries:
        path=PurePosixPath(item.filename)
        if item.filename in seen or path.is_absolute() or '..' in path.parts or '\\' in item.filename or ((item.external_attr>>16)&0o170000)==0o120000:
            raise ValueError('压缩包包含重复、不安全路径或符号链接')
        seen.add(item.filename)
    return seen


def asset(path,raw):
    from PIL import Image
    if len(raw)>4*1024*1024: raise ValueError('单个附件不得超过 4 MB')
    suffix=PurePosixPath(path).suffix.lower()
    if suffix=='.pdf' and raw.startswith(b'%PDF-'): mime='application/pdf'
    else:
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.width*image.height>25000000: raise ValueError('图片像素过大')
                mime={'PNG':'image/png','JPEG':'image/jpeg','GIF':'image/gif','WEBP':'image/webp'}.get(image.format)
                image.verify()
            expected={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.gif':'image/gif','.webp':'image/webp'}
            if not mime or expected.get(suffix)!=mime: raise ValueError('图片扩展名与格式不一致')
        except ValueError: raise
        except Exception as exc: raise ValueError('图片格式无效') from exc
    return dict(path=path,mime=mime,size=len(raw),data=base64.b64encode(raw).decode())


def referenced_paths(body,exists=None):
    result=set()
    def collect(tag,attr,url):
        parsed=urlsplit(url)
        if not parsed.scheme and not parsed.netloc and parsed.path:
            result.add(unquote(parsed.path))
        return url
    tokens=parse_note(PARSER,body,'note.md',exists=exists)
    sanitize_html(PARSER.renderer.render(tokens,PARSER.options,{}),collect)
    return result


def markdown_package(archive):
    names=archive_members(archive)
    markdown=[name for name in names if name.lower().endswith('.md') and not name.startswith('__MACOSX/')]
    if len(markdown)!=1: raise ValueError('Markdown 附件包需要且只能包含一份 .md 正文')
    note=markdown[0]
    try: body=archive.read(note).decode('utf-8-sig')
    except UnicodeDecodeError as exc: raise ValueError('Markdown 请使用 UTF-8 编码') from exc
    assets=[];warnings=[]
    exists=lambda url:posixpath.normpath(posixpath.join(posixpath.dirname(note),url)) in names
    for source in sorted(referenced_paths(body,exists)):
        name=posixpath.normpath(posixpath.join(posixpath.dirname(note),source))
        if name.startswith('../') or name not in names or PurePosixPath(name).suffix.lower() not in {'.png','.jpg','.jpeg','.webp','.gif','.pdf'}:
            warnings.append('缺失或不支持的附件：'+source[:160]);continue
        assets.append(asset(source,archive.read(name)))
    if len(assets)>100 or sum(item['size'] for item in assets)>MAX_ASSETS: raise ValueError('附件最多 100 个，合计不得超过 10 MB')
    return body,assets,warnings


def preview_body(body,assets,job_id):
    paths={item['path']:index for index,item in enumerate(assets)}
    def urls(tag,attr,url):
        parsed=urlsplit(url)
        if not parsed.scheme and not parsed.netloc and unquote(parsed.path) in paths:
            return f"/api/imports/{job_id}/assets/{paths[unquote(parsed.path)]}"
        return url if tag=='a' and attr=='href' and (url.startswith('#') or parsed.scheme in {'http','https','mailto'}) else None
    tokens=parse_note(PARSER,body,'note.md',exists=lambda url:unquote(url) in paths)
    return sanitize_html(PARSER.renderer.render(tokens,PARSER.options,{}),urls)


def write_note_assets(db,content_root,note_path,assets):
    root=(content_root/note_path.split('/')[0]).resolve()
    # Production keeps the content tree immutable and stores attachment updates
    # transactionally in SQLite. Preserve that storage mode for new imports.
    overrides=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='asset_overrides'").fetchone()
    for item in assets:
        parsed=urlsplit(item['path'])
        relative=posixpath.normpath(posixpath.join(posixpath.dirname(note_path),unquote(parsed.path)))
        path=content_root/relative
        if not path.resolve().is_relative_to(root) or path.suffix.lower() not in {'.png','.jpg','.jpeg','.gif','.webp','.pdf'}:
            raise ValueError('附件相对路径越出了目标笔记范围，请调整入库路径')
        raw=base64.b64decode(item['data'],validate=True)
        if overrides:
            import time
            previous=db.execute('SELECT body FROM asset_overrides WHERE path=?',(relative,)).fetchone()
            if previous:
                if bytes(previous['body'])!=raw: raise ValueError('目标附件已存在且内容不同，请更换笔记目录')
                continue
            if path.exists() and (path.is_symlink() or not path.is_file() or path.read_bytes()!=raw):
                raise ValueError('目标附件已存在且内容不同，请更换笔记目录')
            db.execute('INSERT INTO asset_overrides(path,body,sha,updated_at) VALUES(?,?,?,?)',
                       (relative,raw,hashlib.sha256(raw).hexdigest(),int(time.time())))
            continue
        if path.exists():
            if path.is_symlink() or not path.is_file() or path.read_bytes()!=raw: raise ValueError('目标附件已存在且内容不同，请更换笔记目录')
            continue
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as stream: stream.write(raw)
        db.pending_import_assets.append(path)


def team_asset_body(db,team_id,document_id,user_id,body,assets,now):
    # Copy import assets into the team's independent attachment table. Rewrite
    # only links and images, leaving code examples and unrelated text intact.
    urls={}
    for item in assets:
        aid=db.execute('INSERT INTO team_attachments(team_id,document_id,name,mime,content,created_by,created_at) VALUES(?,?,?,?,?,?,?)',
                       (team_id,document_id,PurePosixPath(item['path']).name,item['mime'],base64.b64decode(item['data']),user_id,now)).lastrowid
        urls[item['path']]=f'/api/teams/{team_id}/attachments/{aid}'
    return replace_links(body,urls)


def replace_links(body,urls):
    """Patch Markdown link destinations outside fenced/inline code, never reserialize."""
    import re
    lines=body.splitlines(keepends=True);protected=set()
    for token in PARSER.parse(body):
        if token.type in {'fence','code_block'} and token.map: protected.update(range(*token.map))
    for index,line in enumerate(lines):
        if index in protected: continue
        parts=re.split(r'(`+[^`]*(?:`(?!`)[^`]*)*`+)',line)
        # Destinations are replaced only where a Markdown/HTML reference names
        # the exact imported path. Percent-encoded variants are handled too.
        for p in range(0,len(parts),2):
            def destination(match):
                old=match.group(2); return match.group(1)+urls.get(unquote(old),old)+match.group(3)
            parts[p]=re.sub(r'(\]\(<?)([^\s)>]+)(>?)(?=[\s)])',destination,parts[p])
            parts[p]=re.sub(r'((?:src|href)=["\'])([^"\']+)(["\'])',destination,parts[p])
            parts[p]=re.sub(r'(!\[\[)([^\]|]+)(\]\])',lambda m: '![]('+urls[m[2]]+')' if m[2] in urls else m[0],parts[p])
            parts[p]=re.sub(r'(^\s*\[[^]]+\]:\s*<?)([^\s>]+)(>?)(?=\s|$)',destination,parts[p])
        lines[index]=''.join(parts)
    return ''.join(lines)
