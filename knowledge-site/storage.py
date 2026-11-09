"""SQLite storage and access rules for the knowledge site."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from pathlib import Path

ROLES = {"admin", "editor", "reader"}
SESSION_LIFETIME = 7 * 24 * 3600


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def password_hash(password: str) -> str:
    if not 12 <= len(password) <= 1024:
        raise ValueError("密码需要 12–1024 个字符")
    salt = secrets.token_bytes(16)
    hashed = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${hashed.hex()}"


def password_matches(password: str, stored: str) -> bool:
    try:
        method, salt_hex, expected_hex = stored.split("$")
        if method != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, bytes.fromhex(expected_hex))
    except (ValueError, TypeError):
        return False


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15, factory=ClosingConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 15000")
    return db


def initialize(path: Path, content_root: Path) -> None:
    with connect(path) as db:
        db.executescript("""
        PRAGMA journal_mode = WAL;
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin','editor','reader')),
            active INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            csrf TEXT NOT NULL,
            expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL,
            visibility TEXT NOT NULL CHECK (visibility IN ('public','private')),
            body TEXT NOT NULL,
            body_sha TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS session_devices (
            token_hash TEXT PRIMARY KEY REFERENCES sessions(token_hash) ON DELETE CASCADE,
            label TEXT NOT NULL,created_at INTEGER NOT NULL,last_seen INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS grants (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
            can_view INTEGER NOT NULL DEFAULT 0,
            can_edit INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, note_id)
        );
        CREATE TABLE IF NOT EXISTS shares (
            id INTEGER PRIMARY KEY,
            scope_key TEXT NOT NULL UNIQUE,
            note_id INTEGER REFERENCES notes(id) ON DELETE CASCADE,
            category TEXT,
            token TEXT NOT NULL UNIQUE,
            audience TEXT NOT NULL CHECK (audience IN ('restricted','link')),
            version INTEGER NOT NULL DEFAULT 1,
            CHECK ((note_id IS NOT NULL AND category IS NULL) OR
                   (note_id IS NULL AND category IS NOT NULL AND category != ''))
        );
        CREATE TABLE IF NOT EXISTS share_members (
            share_id INTEGER NOT NULL REFERENCES shares(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            can_edit INTEGER NOT NULL DEFAULT 0 CHECK (can_edit IN (0,1)),
            PRIMARY KEY (share_id,user_id)
        );
        CREATE INDEX IF NOT EXISTS ix_share_members_user ON share_members(user_id,share_id);
        CREATE TABLE IF NOT EXISTS edit_events (
            id INTEGER PRIMARY KEY,
            note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
            base_sha TEXT NOT NULL,
            new_sha TEXT NOT NULL,
            body TEXT NOT NULL,
            origin TEXT NOT NULL CHECK (origin IN ('web','local')),
            status TEXT NOT NULL CHECK (status IN ('pending','applied','conflict')),
            created_at INTEGER NOT NULL,
            message TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS ix_notes_category ON notes(category);
        CREATE TABLE IF NOT EXISTS note_reference_index (
            note_id INTEGER PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
            body_sha TEXT NOT NULL, targets TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS note_redirects (
            old_id INTEGER PRIMARY KEY,
            note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS note_moves (
            id INTEGER PRIMARY KEY, note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
            from_path TEXT NOT NULL, to_path TEXT NOT NULL, metadata_json TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_edit_events_status ON edit_events(status, id);
        CREATE TABLE IF NOT EXISTS login_attempts (
            key TEXT NOT NULL,
            happened_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_login_attempts ON login_attempts(key, happened_at);
        """)
        manifest_file = content_root / "manifest.json"
        if 'expires_at' not in {row['name'] for row in db.execute('PRAGMA table_info(shares)')}:
            db.execute('ALTER TABLE shares ADD COLUMN expires_at INTEGER')
        if manifest_file.is_file():
            records = json.loads(manifest_file.read_text(encoding="utf-8"))
            for record in records:
                # Old deployment manifests must not resurrect a renamed article.
                if db.execute('SELECT 1 FROM note_moves WHERE from_path=?', (record['path'],)).fetchone():
                    continue
                relative = Path(record["path"])
                if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                    raise ValueError("Unsafe content manifest path")
                source = content_root / relative
                if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(content_root.resolve()):
                    raise ValueError("Content file missing or unsafe")
                body = source.read_text(encoding="utf-8")
                db.execute(
                    "INSERT OR IGNORE INTO notes(id,path,title,summary,category,visibility,body,body_sha,updated_at) VALUES((SELECT max(value)+1 FROM (SELECT coalesce(max(id),0) AS value FROM notes UNION ALL SELECT coalesce(max(old_id),0) FROM note_redirects)),?,?,?,?,?,?,?,?)",
                    (record["path"], record["title"], record["summary"], record["category"], record["visibility"], body, digest(body), int(time.time())),
                )
        db.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
        db.execute("DELETE FROM login_attempts WHERE happened_at < ?", (int(time.time()) - 3600,))
        db.executescript("""
        CREATE TABLE IF NOT EXISTS revisions (
            id INTEGER PRIMARY KEY, note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
            body TEXT NOT NULL, body_sha TEXT NOT NULL, actor TEXT NOT NULL,
            origin TEXT NOT NULL, created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_revisions_note ON revisions(note_id,id);
        CREATE TABLE IF NOT EXISTS reading (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
            bookmarked INTEGER NOT NULL DEFAULT 0, position REAL NOT NULL DEFAULT 0,
            read_at INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id,note_id)
        );
        CREATE TABLE IF NOT EXISTS sync_runs (
            id INTEGER PRIMARY KEY, started_at INTEGER NOT NULL, finished_at INTEGER,
            status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
            uploaded INTEGER NOT NULL DEFAULT 0, downloaded INTEGER NOT NULL DEFAULT 0,
            conflicts INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sync_conflicts (
            id INTEGER PRIMARY KEY, note_id INTEGER NOT NULL UNIQUE REFERENCES notes(id) ON DELETE CASCADE,
            local_body TEXT NOT NULL, local_sha TEXT NOT NULL, server_sha TEXT NOT NULL,
            base_body TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open',
            resolved_body TEXT NOT NULL DEFAULT '', resolved_sha TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        );
        """)
        # Preserve existing event snapshots. The pre-upgrade original is not recoverable.
        for note in db.execute("SELECT * FROM notes").fetchall():
            if db.execute("SELECT 1 FROM revisions WHERE note_id=?", (note['id'],)).fetchone():
                continue
            for event in db.execute("SELECT * FROM edit_events WHERE note_id=? ORDER BY id", (note['id'],)).fetchall():
                db.execute("INSERT INTO revisions(note_id,body,body_sha,actor,origin,created_at) VALUES(?,?,?,?,?,?)",
                           (note['id'],event['body'],event['new_sha'],'历史记录（修改者未知）',event['origin'],event['created_at']))
            last = db.execute("SELECT body_sha FROM revisions WHERE note_id=? ORDER BY id DESC LIMIT 1", (note['id'],)).fetchone()
            if not last or last['body_sha'] != note['body_sha']:
                db.execute("INSERT INTO revisions(note_id,body,body_sha,actor,origin,created_at) VALUES(?,?,?,?,?,?)",
                           (note['id'],note['body'],note['body_sha'],'导入快照','import',note['updated_at']))


def create_user(db: sqlite3.Connection, username: str, password: str, role: str) -> None:
    if role not in ROLES or not username or len(username) > 64:
        raise ValueError("无效的用户名或角色")
    db.execute(
        "INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)",
        (username, password_hash(password), role, int(time.time())),
    )


def create_session(db: sqlite3.Connection, user_id: int) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    db.execute(
        "INSERT INTO sessions(token_hash,user_id,csrf,expires_at) VALUES(?,?,?,?)",
        (digest(token), user_id, csrf, int(time.time()) + SESSION_LIFETIME),
    )
    return token, csrf


def session_user(db: sqlite3.Connection, token: str | None):
    if not token:
        return None, None
    row = db.execute(
        "SELECT users.*, sessions.csrf, sessions.token_hash FROM sessions JOIN users ON users.id=sessions.user_id WHERE sessions.token_hash=? AND sessions.expires_at>? AND users.active=1",
        (digest(token), int(time.time())),
    ).fetchone()
    return (row, row["csrf"]) if row else (None, None)


def grant_for(db: sqlite3.Connection, note_id: int, user_id: int | None):
    if user_id is None:
        return None
    return db.execute("""SELECT max(can_view) AS can_view,max(can_edit) AS can_edit FROM (
        SELECT can_view,can_edit FROM grants WHERE note_id=? AND user_id=?
        UNION ALL
        SELECT 1,m.can_edit FROM shares s JOIN share_members m ON m.share_id=s.id
        JOIN notes n ON n.id=? WHERE m.user_id=? AND (s.expires_at IS NULL OR s.expires_at>unixepoch()) AND
        (s.note_id=n.id OR (s.category IS NOT NULL AND
        (n.category=s.category OR substr(n.category,1,length(s.category)+3)=s.category||' / ')))
    )""", (note_id, user_id, note_id, user_id)).fetchone()


def can_view(db: sqlite3.Connection, note, user) -> bool:
    if note["visibility"] == "public":
        return True
    if user is None:
        return False
    if user["role"] == "admin":
        return True
    grant = grant_for(db, note["id"], user["id"])
    return bool(grant and (grant["can_view"] or grant["can_edit"]))


def can_edit(db: sqlite3.Connection, note, user) -> bool:
    if user is None:
        return False
    if user["role"] == "admin":
        return True
    if user["role"] != "editor":
        return False
    grant = grant_for(db, note["id"], user["id"])
    return bool(grant and grant["can_edit"])


def visible_where(user) -> tuple[str, tuple]:
    if user is not None and user["role"] == "admin":
        return "1=1", ()
    if user is None:
        return "notes.visibility='public'", ()
    return """(notes.visibility='public' OR EXISTS (SELECT 1 FROM grants g
        WHERE g.note_id=notes.id AND g.user_id=? AND (g.can_view=1 OR g.can_edit=1))
        OR EXISTS (SELECT 1 FROM shares s JOIN share_members m ON m.share_id=s.id
        WHERE m.user_id=? AND (s.expires_at IS NULL OR s.expires_at>unixepoch()) AND (s.note_id=notes.id OR (s.category IS NOT NULL AND
        (notes.category=s.category OR substr(notes.category,1,length(s.category)+3)=s.category||' / ')))))""", (user["id"], user["id"])


def save_note(db: sqlite3.Connection, note, body: str, origin: str, actor: str = '') -> str:
    if origin not in {"web", "local"}:
        raise ValueError("Unknown origin")
    new_sha = digest(body)
    if new_sha == note["body_sha"]:
        return new_sha
    # Preserve only versions that are currently public before the content moves.
    from agent_dependencies import snapshot_note, source_changed
    snapshot_note(db, note['id'])
    now = int(time.time())
    if not db.execute('SELECT 1 FROM revisions WHERE note_id=?',(note['id'],)).fetchone():
        db.execute('INSERT INTO revisions(note_id,body,body_sha,actor,origin,created_at) VALUES(?,?,?,?,?,?)',
                   (note['id'],note['body'],note['body_sha'],'初始快照','import',note['updated_at']))
    changed = db.execute(
        "UPDATE notes SET body=?, body_sha=?, revision=revision+1, updated_at=? WHERE id=? AND body_sha=?",
        (body, new_sha, now, note["id"], note["body_sha"]),
    ).rowcount
    if changed != 1:
        raise ValueError("版本已变化，请重新载入")
    db.execute(
        "INSERT INTO edit_events(note_id,base_sha,new_sha,body,origin,status,created_at) VALUES(?,?,?,?,?,?,?)",
        (note["id"], note["body_sha"], new_sha, body, origin, "pending" if origin == "web" else "applied", now),
    )
    db.execute("INSERT INTO revisions(note_id,body,body_sha,actor,origin,created_at) VALUES(?,?,?,?,?,?)",
               (note['id'],body,new_sha,actor or ('本地同步' if origin == 'local' else '网站编辑'),origin,now))
    source_changed(db, note['id'])
    return new_sha
