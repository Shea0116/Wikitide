"""Permissioned knowledge library with server-side Markdown rendering."""

from __future__ import annotations

import hmac
import mimetypes
import os
import posixpath
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import FileSystemLoader
from markdown_it import MarkdownIt
from markupsafe import Markup
from starlette.middleware.trustedhost import TrustedHostMiddleware
from i18n import LocalizedEnvironment, register_i18n, ui, LOCALE

from navigation_order import register_orders, sort_category_tree, sort_note_rows, sort_article_tree
from html_compat import sanitize_html
from client_api import register_client_api
from markdown_compat import enhance_markdown_tokens
from note_assets import enable_embeds, parse_note
from sharing import register_sharing, shared_note
from explore import register_explore
from teams import register_teams
from imports import register_imports
from accounts import register_accounts, update_account, audit, safe_next
from features import (register, search_notes, wiki_tokens, note_extras, personal_notes,
                      admin_context, record_sha)
from storage import (
    SESSION_LIFETIME,
    can_edit,
    can_view,
    connect,
    create_session,
    create_user,
    digest,
    initialize,
    password_hash,
    password_matches,
    save_note,
    session_user,
    visible_where,
)

BASE = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(env=LocalizedEnvironment(loader=FileSystemLoader(str(BASE / 'templates')), autoescape=True))
TEMPLATES.env.globals['ui'] = ui
MARKDOWN = enable_embeds(MarkdownIt("default", {"html": True}))
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".pdf"}
WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
MAX_BODY = 8 * 1024 * 1024


@contextmanager
def database(path: Path):
    db = connect(path)
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def form_values(raw: bytes) -> dict[str, str]:
    if len(raw) > MAX_BODY:
        raise HTTPException(413, "内容过大")
    try:
        parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "表单编码无效") from exc
    return {key: values[-1] for key, values in parsed.items()}


def page(request: Request, name: str, *, status_code: int = 200, **data):
    workspace = getattr(request.app.state, 'agent_memory', None)
    context = {"request": request, 'locale': LOCALE.get(), 'workspace_enabled': bool(workspace and workspace.enabled and data.get('user')),
               'selected_knowledge_enabled': bool(getattr(request.app.state, 'explore_selected_enabled', False) and data.get('user')), **data}
    context['learning_enabled'] = bool(getattr(request.app.state, 'learning_enabled', False) and data.get('user'))
    context['artifacts_enabled'] = bool(getattr(request.app.state, 'artifacts_enabled', False) and data.get('user'))
    if isinstance(context.get('error'), str): context['error'] = ui(context['error'])
    return HTMLResponse(TEMPLATES.get_template(name).render(context), status_code=status_code)


def current_user(db, request: Request):
    if request.url.path.startswith('/api/client/'):
        authorization = request.headers.get('Authorization', '')
        if authorization:
            scheme, _, token = authorization.partition(' ')
            if scheme.lower() != 'bearer' or not token:
                raise HTTPException(401, '登录已过期，请重新登录')
            user, csrf = session_user(db, token)
            if user is None:
                raise HTTPException(401, '登录已过期，请重新登录')
            return user, csrf
        return None, None
    user,csrf=session_user(db, request.cookies.get("kb_session"))
    return user,csrf


def record_device(db,request,user):
    now=int(time.time())
    previous=db.execute('SELECT last_seen,label FROM session_devices WHERE token_hash=?',(user['token_hash'],)).fetchone()
    if previous and previous['last_seen']>now-300: return
    agent=request.headers.get('user-agent','')
    platform='iOS' if any(name in agent for name in ['iPhone','iPad']) else 'Android' if 'Android' in agent else 'macOS' if 'Macintosh' in agent else 'Windows' if 'Windows' in agent else '客户端'
    db.execute('''INSERT INTO session_devices VALUES(?,?,?,?) ON CONFLICT(token_hash) DO UPDATE SET
        label=excluded.label,last_seen=excluded.last_seen WHERE session_devices.last_seen<?''',(user['token_hash'],platform+' · WikiTide',now,now,now-300))


def require_csrf(form: dict[str, str], expected: str | None) -> None:
    if not expected or not hmac.compare_digest(form.get("csrf", ""), expected):
        raise HTTPException(403, "表单已过期，请刷新页面")


def require_admin(user) -> None:
    if user is None or user["role"] != "admin":
        raise HTTPException(403, "无权访问")


def visible_notes(db, user, extra_where: str = "", params: tuple = (), limit: int = 100):
    where, access_params = visible_where(user)
    clause = f"WHERE {where}" + (f" AND ({extra_where})" if extra_where else "")
    return db.execute(
        f"SELECT notes.id,notes.path,notes.title,notes.summary,notes.category,notes.visibility,notes.updated_at FROM notes {clause} ORDER BY notes.category, notes.title LIMIT ?",
        (*access_params, *params, limit),
    ).fetchall()


def categories(db, user):
    where, params = visible_where(user)
    rows = db.execute(
        f"SELECT notes.category,COUNT(*) AS count FROM notes WHERE {where} GROUP BY notes.category ORDER BY notes.category",
        params,
    ).fetchall()
    roots: list[dict] = []
    lookup: dict[str, dict] = {}
    for row in rows:
        parts = row["category"].split(" / ")
        for depth, name in enumerate(parts):
            path = " / ".join(parts[: depth + 1])
            if path not in lookup:
                item = {"name": name, "path": path, "count": 0, "total": 0, "children": []}
                lookup[path] = item
                if depth:
                    lookup[" / ".join(parts[:depth])]["children"].append(item)
                else:
                    roots.append(item)
            lookup[path]["total"] += row["count"]
        lookup[row["category"]]["count"] += row["count"]
    return sort_category_tree(db, user, roots)


def rewrite_wikilinks(body: str, lookup: dict[str, int]) -> str:
    def replace(match):
        target, _, label = match.group(1).partition("|")
        key = Path(target.split("#", 1)[0]).stem.casefold()
        note_id = lookup.get(key)
        if not note_id:
            return match.group(0)
        display = label or target
        return f"[{display}](/n/{note_id})"

    return WIKILINK.sub(replace, body)


def rendered_markdown(db, note, user, content_root=None, *, visible_rows=None, note_prefix='/n', asset_prefix='/asset', editor_blocks=False):
    if visible_rows is not None:
        all_visible=visible_rows
    else:
        access,params=visible_where(user)
        all_visible=db.execute(f'SELECT id,path,title,category FROM notes WHERE {access} LIMIT 10000',params).fetchall()
    by_path = {row["path"]: row["id"] for row in all_visible}
    tokens = enhance_markdown_tokens(wiki_tokens(MARKDOWN, parse_note(MARKDOWN, note['body'], note['path'], content_root), note['path'], all_visible))
    from explore_search import heading_slug
    used=set();heading_count=0
    for index,token in enumerate(tokens):
        if token.type!='heading_open': continue
        heading_count+=1
        inline=tokens[index+1]
        title=''.join(child.content for child in inline.children or [] if child.type in {'text','code_inline'})
        base=heading_slug(title,heading_count);slug=base;suffix=2
        while slug in used: slug=f'{base}-{suffix}';suffix+=1
        used.add(slug);token.attrSet('id',slug)
    def rewrite_url(tag, attr, original):
        parsed = urlsplit(original)
        if parsed.scheme or original.startswith("#"):
            return original
        path = posixpath.normpath(posixpath.join(posixpath.dirname(note["path"]), unquote(parsed.path)))
        if attr == "href":
            target = by_path.get(path)
            if target:
                return f"{note_prefix}/{target}" + (f"#{quote(parsed.fragment)}" if parsed.fragment else "")
        if note_asset_path(note, original):
            return f"{asset_prefix}/{note['id']}?path={quote(original, safe='')}"
        # Local HTML images follow the same authorized asset route as Markdown images.
        return original if attr == "href" else None

    # wiki_tokens emits /n/<id> links. Keep them inside the same share scope.
    for block in tokens:
        for token in block.children or []:
            href = token.attrGet('href') if token.type == 'link_open' else None
            if note_prefix != '/n' and href and re.match(r'^/n/\d+(?:#.*)?$', href):
                token.attrSet('href', note_prefix + href[2:])
    def render_tokens(items):
        return sanitize_html(MARKDOWN.renderer.render(items, MARKDOWN.options, {}), rewrite_url)

    if editor_blocks:
        # CodeMirror positions count UTF-16 code units, including for emoji.
        # Token maps use source lines; rendering never rewrites the saved source.
        offsets = [0]
        for line in note['body'].split('\n'):
            offsets.append(offsets[-1] + len(line.encode('utf-16-le')) // 2 + 1)
        blocks, group, start, end, depth = [], [], None, None, 0
        for token in tokens:
            if not group and token.map:
                start, end = token.map
            group.append(token)
            depth += token.nesting
            if depth == 0:
                if start is not None and end is not None:
                    blocks.append({'from': offsets[start], 'to': offsets[end] - 1,
                                   'html': render_tokens(group)})
                group, start, end = [], None, None
        return blocks
    return Markup(render_tokens(tokens))


def note_asset_path(note, original: str) -> str | None:
    parsed = urlsplit(original)
    if parsed.scheme or parsed.netloc or original.startswith(("//", "#")) or not parsed.path:
        return None
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(note["path"]), unquote(parsed.path)))
    if resolved.split("/", 1)[0] == note["path"].split("/", 1)[0] and Path(resolved).suffix.lower() in ASSET_SUFFIXES:
        return resolved
    return None


def linked_assets(note, content_root=None) -> set[str]:
    """Limit an asset URL to files actually linked by its authorized note."""
    targets = set()

    def collect_url(tag, attr, original):
        resolved = note_asset_path(note, original)
        if resolved:
            targets.add(resolved)
        return original

    tokens = parse_note(MARKDOWN, note['body'], note['path'], content_root)
    for block in tokens:
        for token in block.children or []:
            for url in token.meta.get('embed_urls', []):
                collect_url('img', 'src', url)
    sanitize_html(MARKDOWN.renderer.render(tokens, MARKDOWN.options, {}), collect_url)
    return targets


def create_app(db_path: Path | None = None, content_root: Path | None = None, *, cookie_secure: bool | None = None, sync_token_hash: str | None = None) -> FastAPI:
    db_path = Path(db_path or os.getenv("KB_DB_PATH", BASE / "work" / "site.db")).resolve()
    content_root = Path(content_root or os.getenv("KB_CONTENT_ROOT", BASE / "content")).resolve()
    secure = cookie_secure if cookie_secure is not None else os.getenv("KB_COOKIE_SECURE", "0") == "1"
    sync_hash = sync_token_hash if sync_token_hash is not None else os.getenv("KB_SYNC_TOKEN_SHA256", "")
    initialize(db_path, content_root)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db_path = db_path
    app.state.content_root = content_root
    app.state.cookie_secure = secure
    app.state.sync_token_hash = sync_hash
    def render_note(db, note, user, **options):
        return rendered_markdown(db, note, user, content_root, **options)
    allowed_hosts = [item.strip() for item in os.getenv("KB_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if item.strip()]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
    register_orders(app, db_path, database, current_user, require_csrf)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        # CodeMirror installs its own trusted styles. Authorize only those
        # style elements on editor responses, keeping arbitrary styles blocked.
        style_nonce = secrets.token_urlsafe(24) if re.fullmatch(r'/n/\d+/edit', request.url.path) or request.url.path=='/teams' else ''
        request.state.editor_style_nonce = style_nonce
        response = await call_next(request)
        # Optional device metadata uses a separate connection after the route's
        # transaction. Authentication itself must remain read-only: some callers
        # begin their write transaction only after checking the current user.
        token=request.cookies.get('kb_session')
        if request.url.path.startswith('/api/client/'):
            scheme,_,bearer=request.headers.get('Authorization','').partition(' ')
            token=bearer if scheme.lower()=='bearer' else None
        if token and not request.url.path.startswith('/static/'):
            try:
                with database(db_path) as db:
                    user,_=session_user(db,token)
                    if user: record_device(db,request,user)
            except sqlite3.OperationalError:
                pass  # Metadata must not turn a successful content request into a failure.
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        pdf_frame = bool(re.fullmatch(r'(?:/api/imports/\d+/preview-original|/n/\d+/pdf|/s/[^/]+/pdf/\d+|/api/teams/\d+/documents/\d+/pdf)', request.url.path))
        if pdf_frame: response.headers['X-Frame-Options']='SAMEORIGIN'
        response.headers["Referrer-Policy"] = "no-referrer"
        trusted_style = f" 'nonce-{style_nonce}'" if style_nonce else ''
        ancestors="'self'" if pdf_frame else "'none'"
        response.headers["Content-Security-Policy"] = f"default-src 'self'; img-src 'self' data:; style-src 'self'{trusted_style}; style-src-attr 'unsafe-inline'; script-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors {ancestors}"
        if (request.cookies.get('kb_session') and not request.url.path.startswith('/static/')) or request.url.path.startswith(('/api/', '/admin', '/login', '/register', '/recover', '/account', '/s/')):
            response.headers['Cache-Control'] = 'no-store'
        if request.url.path.startswith('/s/'):
            response.headers['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
        return response

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        with database(db_path) as db:
            user, csrf = current_user(db, request)
            guides = visible_notes(db, user, "notes.category='知识导览'", limit=20)
            recent = db.execute(
                f"SELECT notes.id,notes.title,notes.category,notes.summary,notes.visibility,EXISTS(SELECT 1 FROM document_pdfs p WHERE p.note_id=notes.id AND p.note_format='pdf') AS is_pdf FROM notes WHERE {visible_where(user)[0]} AND notes.category!='知识导览' ORDER BY notes.updated_at DESC, notes.id DESC LIMIT 12",
                visible_where(user)[1],
            ).fetchall()
            return page(request, "home.html", user=user, csrf=csrf, categories=categories(db, user), guides=guides, recent=recent, continue_reading=personal_notes(db,user)[:4])

    @app.get("/browse", response_class=HTMLResponse)
    def browse(request: Request, category: str = "", page_number: int = 1):
        if page_number < 1 or page_number > 1000 or len(category) > 240:
            raise HTTPException(400, "页码无效")
        with database(db_path) as db:
            user, csrf = current_user(db, request)
            where, params = visible_where(user)
            if category:
                prefix = category.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                where += " AND (notes.category=? OR notes.category LIKE ? ESCAPE '\\')"
                params += (category, prefix + " / %")
            rows = db.execute(
                f"SELECT notes.id,notes.title,notes.summary,notes.category,notes.visibility,EXISTS(SELECT 1 FROM document_pdfs p WHERE p.note_id=notes.id AND p.note_format='pdf') AS is_pdf FROM notes WHERE {where} ORDER BY notes.category,notes.title,notes.id",
                params,
            ).fetchall()
            offset = (page_number - 1) * 50
            rows = sort_note_rows(db, user, rows)[offset:offset + 51]
            return page(request, "browse.html", user=user, csrf=csrf, categories=categories(db, user), notes=rows[:50], has_more=len(rows) > 50, category=category, page_number=page_number)

    @app.get("/search", response_class=HTMLResponse)
    def search(request: Request, q: str = "", category: str = '', page_number: int = 1, scope: str = 'all', mode: str = 'keyword', days: int = 0):
        if not 1 <= page_number <= 10000 or len(category)>240:
            raise HTTPException(400)
        term = q.strip()[:100]
        with database(db_path) as db:
            from unified_search import search_page
            return search_page(request,db,app,globals(),q=term,category=category,page_number=page_number,scope=scope,mode=mode,days=days)

    @app.get("/n/{note_id}", response_class=HTMLResponse)
    def note_page(request: Request, note_id: int):
        with database(db_path) as db:
            user, csrf = current_user(db, request)
            note = db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
            if note is None:
                alias = db.execute('SELECT notes.* FROM note_redirects JOIN notes ON notes.id=note_redirects.note_id WHERE old_id=?', (note_id,)).fetchone()
                if alias is not None and can_view(db, alias, user):
                    return RedirectResponse(f"/n/{alias['id']}", status_code=308)
            if note is None or not can_view(db, note, user):
                raise HTTPException(404, "笔记不存在")
            html = render_note(db, note, user)
            from pdf_notes import pdf_info
            return page(request, "note.html", user=user, csrf=csrf, categories=categories(db, user), note=note, html=html, pdf=pdf_info(db, note_id=note_id), editable=can_edit(db, note, user), **note_extras(db,user,note,MARKDOWN))

    @app.get('/api/client/notes/{note_id}/asset')
    @app.get("/asset/{note_id}")
    @app.get('/s/{token}/asset/{note_id}')
    def asset(request: Request, note_id: int, path: str):
        with database(db_path) as db:
            user, _ = current_user(db, request)
            # Only the share route can use a token; query parameters cannot bypass normal access.
            token = request.path_params.get('token')
            note = shared_note(db, token, note_id) if token else db.execute("SELECT id,path,body,visibility FROM notes WHERE id=?", (note_id,)).fetchone()
            if note is None or (not token and not can_view(db, note, user)):
                raise HTTPException(404)
            parsed = urlsplit(path)
            if parsed.scheme or parsed.netloc or not parsed.path:
                raise HTTPException(404)
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(note["path"]), unquote(parsed.path)))
            if resolved.startswith("../") or resolved == ".." or resolved.split("/", 1)[0] != note["path"].split("/", 1)[0]:
                raise HTTPException(404)
            if resolved not in linked_assets(note, content_root):
                raise HTTPException(404)
            file = content_root / resolved
            if file.suffix.lower() not in ASSET_SUFFIXES or file.is_symlink() or not file.is_file() or not file.resolve().is_relative_to(content_root / note['path'].split('/',1)[0]):
                raise HTTPException(404)
            media_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            response = FileResponse(file, media_type=media_type)
            response.headers["Cache-Control"] = "private, max-age=300" if note["visibility"] == "private" else "public, max-age=300"
            return response

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/"):
        token = secrets.token_urlsafe(24)
        response = page(request, "login.html", user=None, csrf=token, next=safe_next(next, '/'), error="")
        response.set_cookie("kb_login_csrf", token, max_age=600, httponly=True, secure=secure, samesite="lax")
        return response

    @app.post("/login")
    async def login(request: Request):
        form = form_values(await request.body())
        require_csrf(form, request.cookies.get("kb_login_csrf"))
        username = form.get("username", "").strip()
        password = form.get("password", "")
        next_path = safe_next(form.get("next", "/"), '/')
        with database(db_path) as db:
            key = digest((request.client.host if request.client else "") + ":" + username)
            now = int(time.time())
            db.execute("DELETE FROM login_attempts WHERE happened_at<?", (now - 900,))
            attempts = db.execute("SELECT COUNT(*) FROM login_attempts WHERE key=?", (key,)).fetchone()[0]
            if attempts >= 10:
                raise HTTPException(429, "尝试次数过多，请稍后再试")
            user = db.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
            if not user or not password_matches(password, user["password_hash"]):
                db.execute("INSERT INTO login_attempts(key,happened_at) VALUES(?,?)", (key, now))
                return page(request, "login.html", user=None, csrf=form["csrf"], next=next_path, error="用户名或密码错误", status_code=401)
            db.execute("DELETE FROM login_attempts WHERE key=?", (key,))
            token, _ = create_session(db, user["id"])
        response = RedirectResponse(next_path, status_code=303)
        response.set_cookie("kb_session", token, max_age=SESSION_LIFETIME, httponly=True, secure=secure, samesite="lax")
        response.delete_cookie("kb_login_csrf")
        return response

    @app.post("/logout")
    async def logout(request: Request):
        form = form_values(await request.body())
        with database(db_path) as db:
            user, csrf = current_user(db, request)
            if user:
                require_csrf(form, csrf)
                db.execute("DELETE FROM sessions WHERE token_hash=?", (digest(request.cookies["kb_session"]),))
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie("kb_session")
        return response

    @app.get("/n/{note_id}/edit", response_class=HTMLResponse)
    def edit_page(request: Request, note_id: int):
        with database(db_path) as db:
            user, csrf = current_user(db, request)
            note = db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
            if note is None or not can_edit(db, note, user):
                raise HTTPException(403, "无编辑权限")
            return page(request, "edit.html", user=user, csrf=csrf, note=note, error="",categories=categories(db,user),record_sha=record_sha(note))

    @app.post("/n/{note_id}/edit")
    async def edit_note(request: Request, note_id: int):
        form = form_values(await request.body())
        with database(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            user, csrf = current_user(db, request)
            require_csrf(form, csrf)
            note = db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
            if note is None or not can_edit(db, note, user):
                raise HTTPException(403, "无编辑权限")
            if form.get("base_sha") != note["body_sha"]:
                return page(request, "edit.html", user=user, csrf=csrf, note=note, draft_body=form.get('body',''),server_body=note['body'],error="正文已更新。你的输入已保留，请对照最新正文合并后再保存。", status_code=409,categories=categories(db,user),record_sha=record_sha(note))
            body = form.get("body", "")
            if not body.strip():
                return page(request, "edit.html", user=user, csrf=csrf, note=note,draft_body=body,error="笔记内容不能为空", status_code=400,categories=categories(db,user),record_sha=record_sha(note))
            try:
                save_note(db, note, body, "web",user['username'])
            except ValueError:
                latest=db.execute('SELECT * FROM notes WHERE id=?',(note_id,)).fetchone()
                return page(request,'edit.html',status_code=409,user=user,csrf=csrf,note=latest,draft_body=body,server_body=latest['body'],error='保存期间正文已变化，你的输入已保留。',categories=categories(db,user),record_sha=record_sha(latest))
        return RedirectResponse(f"/n/{note_id}", status_code=303)

    @app.get("/admin/permissions", response_class=HTMLResponse)
    def admin_page(request: Request, q: str = '', category: str = '', page_number: int = 1):
        if not 1<=page_number<=10000 or len(category)>240: raise HTTPException(400)
        with database(db_path) as db:
            user, csrf = current_user(db, request)
            require_admin(user)
            return page(request, "admin.html", user=user, csrf=csrf,categories=categories(db,user),**admin_context(db,q[:100],category,page_number))

    @app.post("/admin/users")
    async def admin_users(request: Request):
        form = form_values(await request.body())
        with database(db_path) as db:
            db.execute('BEGIN IMMEDIATE')
            user, csrf = current_user(db, request)
            require_admin(user)
            require_csrf(form, csrf)
            action = form.get("action", "")
            try:
                if action == "create":
                    create_user(db, form.get("username", "").strip(), form.get("password", ""), form.get("role", "reader"))
                    audit(db, user['id'], 'account.created', form.get('username', '').strip(), '管理员创建账户')
                elif action == "update":
                    target_id = int(form.get("user_id", "0"))
                    role = form.get("role", "")
                    update_account(db, user, target_id, role=role, active=form.get('active') == 'on',
                                   version=int(form.get('version', '0')), password=form.get('password', ''))
                else:
                    raise ValueError("操作无效")
            except (sqlite3.IntegrityError, ValueError) as exc:
                raise HTTPException(400, str(exc)) from exc
        return RedirectResponse("/admin/permissions", status_code=303)

    @app.get("/admin/n/{note_id}", response_class=HTMLResponse)
    def admin_note_page(request: Request, note_id: int):
        with database(db_path) as db:
            user, csrf = current_user(db, request)
            require_admin(user)
            note = db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
            if not note:
                raise HTTPException(404)
            users = db.execute("SELECT u.id,u.username,u.role,u.active,COALESCE(g.can_view,0) AS can_view,COALESCE(g.can_edit,0) AS can_edit FROM users u LEFT JOIN grants g ON g.user_id=u.id AND g.note_id=? WHERE u.role!='admin' ORDER BY u.username", (note_id,)).fetchall()
            return page(request, "admin_note.html", user=user, csrf=csrf, note=note, users=users)

    @app.post("/admin/n/{note_id}")
    async def admin_note_save(request: Request, note_id: int):
        form = form_values(await request.body())
        with database(db_path) as db:
            user, csrf = current_user(db, request)
            require_admin(user)
            require_csrf(form, csrf)
            visibility = form.get("visibility")
            if visibility not in {"public", "private"}:
                raise HTTPException(400, "公开范围无效")
            note = db.execute("SELECT id FROM notes WHERE id=?", (note_id,)).fetchone()
            if not note:
                raise HTTPException(404)
            db.execute("UPDATE notes SET visibility=? WHERE id=?", (visibility, note_id))
            from agent_dependencies import source_changed
            source_changed(db, note_id)
            db.execute("DELETE FROM grants WHERE note_id=?", (note_id,))
            users = db.execute("SELECT id,role FROM users WHERE role!='admin' AND active=1").fetchall()
            for target in users:
                can_view_value = form.get(f"view_{target['id']}") == "on"
                can_edit_value = target["role"] == "editor" and form.get(f"edit_{target['id']}") == "on"
                if can_view_value or can_edit_value:
                    db.execute("INSERT INTO grants(user_id,note_id,can_view,can_edit) VALUES(?,?,?,?)", (target["id"], note_id, int(can_view_value or can_edit_value), int(can_edit_value)))
        return RedirectResponse(f"/admin/n/{note_id}", status_code=303)

    def require_sync(request: Request) -> None:
        auth = request.headers.get("Authorization", "")
        prefix = "Bearer "
        if not sync_hash or not auth.startswith(prefix) or not hmac.compare_digest(digest(auth[len(prefix):]), sync_hash):
            raise HTTPException(401, "未授权")

    @app.get("/api/sync/state")
    def sync_state(request: Request):
        require_sync(request)
        with database(db_path) as db:
            rows = db.execute("SELECT path,body_sha FROM notes ORDER BY path").fetchall()
            return {"notes": [{"path": row["path"], "sha": row["body_sha"]} for row in rows]}

    @app.get("/api/sync/events")
    def sync_events(request: Request):
        require_sync(request)
        with database(db_path) as db:
            rows = db.execute("SELECT e.id,n.path,e.base_sha,e.new_sha,e.body FROM edit_events e JOIN notes n ON n.id=e.note_id WHERE e.origin='web' AND e.status='pending' ORDER BY e.id LIMIT 100").fetchall()
            return {"events": [dict(row) for row in rows]}

    @app.post("/api/sync/events/{event_id}/ack")
    async def sync_ack(request: Request, event_id: int):
        require_sync(request)
        raw = await request.body()
        if len(raw) > 4096:
            raise HTTPException(413)
        try:
            data = await request.json()
        except ValueError as exc:
            raise HTTPException(400) from exc
        status = data.get("status")
        if status not in {"applied", "conflict"}:
            raise HTTPException(400)
        with database(db_path) as db:
            changed = db.execute("UPDATE edit_events SET status=?,message=? WHERE id=? AND status='pending' AND origin='web'", (status, str(data.get("message", ""))[:500], event_id)).rowcount
            if changed != 1:
                raise HTTPException(404)
        return {"ok": True}

    @app.post("/api/sync/push")
    async def sync_push(request: Request):
        require_sync(request)
        raw = await request.body()
        if len(raw) > MAX_BODY:
            raise HTTPException(413)
        try:
            data = await request.json()
        except ValueError as exc:
            raise HTTPException(400) from exc
        path = data.get("path")
        base_sha = data.get("base_sha")
        body = data.get("body")
        if not isinstance(path, str) or not isinstance(base_sha, str) or not isinstance(body, str) or not body.strip():
            raise HTTPException(400)
        with database(db_path) as db:
            note = db.execute("SELECT * FROM notes WHERE path=?", (path,)).fetchone()
            if note is None:
                raise HTTPException(404)
            if note["body_sha"] != base_sha:
                return JSONResponse({"error": "conflict", "server_sha": note["body_sha"]}, status_code=409)
            new_sha = save_note(db, note, body, "local")
        return {"sha": new_sha}

    @app.get("/health")
    def health():
        return {"ok": True}

    TEMPLATES.env.filters['datetime'] = lambda value: datetime.fromtimestamp(value,timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S') if value else ui('暂无记录')
    register(app,db_path,content_root,{**globals(),'require_sync':require_sync,'rendered_markdown':render_note})
    register_client_api(app, db_path, {**globals(), 'rendered_markdown':render_note})
    register_sharing(app, db_path, globals())
    register_explore(app, db_path, {**globals(), 'rendered_markdown': render_note})
    from knowledge_artifacts import register_artifacts
    register_artifacts(app, db_path, globals())
    register_teams(app, db_path, globals())
    from note_creation import register_note_creation
    register_note_creation(app, db_path, globals())
    register_accounts(app, db_path, globals())
    register_imports(app, db_path, globals())
    from pdf_notes import register_pdfs
    register_pdfs(app, db_path, globals())
    from personal import register_personal
    register_personal(app, db_path, globals())
    from learning_routes import register_learning
    register_learning(app, db_path, globals())
    from offline import register_offline
    register_offline(app,db_path,globals())
    from drafts import register_drafts
    register_drafts(app,db_path,globals())
    from team_workflow import register_team_workflow
    register_team_workflow(app,db_path,globals())
    from explore_quality import register_quality
    register_quality(app,db_path,globals())
    from account_self_service import register_self_service
    register_self_service(app,db_path,globals())
    from updates import register_updates
    register_updates(app,db_path,globals())
    register_i18n(app, secure)
    return app


app = create_app()
