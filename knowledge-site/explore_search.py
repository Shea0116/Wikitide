"""Local, public-note-only retrieval for 知识求索. Never reads the source tree."""
from __future__ import annotations

import html
import json
import logging
import math
import re
import threading
import unicodedata
from collections import Counter
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from markdown_it import MarkdownIt

from storage import digest

LOG = logging.getLogger(__name__)
PARSER = MarkdownIt("default", {"html": True})
INDEX_VERSION = "2"
PUBLIC_SQL = """notes.visibility='public' AND
    (notes.path LIKE 'Backend/%' OR notes.path LIKE 'Frontend/%')"""


def public_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(parts and parts[0] in {"Backend", "Frontend"}
                and path.endswith(".md") and "\\" not in path
                and all(p not in {".", ".."} and not p.startswith(".") for p in parts)
                and not any(p.lower() == "static" for p in parts[:-1])
                and parts[-1] not in {"AGENTS.md", "CLAUDE.md", "LICENSE.md"})


def plain(text: str) -> str:
    text = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", text, flags=re.I | re.S)
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def excerpt_text(text: str) -> str:
    parts = []
    for token in PARSER.parse(text):
        if token.type == "inline":
            parts.append("".join(child.content if child.type in {"text", "code_inline"} else " " if child.type in {"softbreak", "hardbreak"} else ""
                                 for child in token.children or []))
        elif token.type in {"fence", "code_block"}:
            parts.append(token.content.strip())
    return "\n".join(parts).strip()


def terms(text: str) -> Counter:
    result = []
    for word in re.findall(r"[a-zA-Z0-9_$]+|[\u3400-\u9fff]+", plain(text).casefold()):
        if re.search(r"[\u3400-\u9fff]", word):
            result.extend(word[i:i + 2] for i in range(len(word) - 1))
            if len(word) == 1:
                result.append(word)
        else:
            result.append(word)
    return Counter(result)


def heading_slug(text: str, index: int) -> str:
    # Match ui.js, so references open the existing article's exact heading.
    text = "".join(c for c in text.strip().lower()
                   if unicodedata.category(c)[0] in {"L", "N", "M"} or c.isspace() or c in "_-")
    return re.sub(r"\s+", "-", text) or f"section-{index}"


def split_article(body: str) -> list[dict]:
    lines = body.splitlines(keepends=True)
    tokens = PARSER.parse(body)
    headings = {}
    used = set()
    count = 0
    for i, token in enumerate(tokens):
        if token.type == "heading_open" and token.map:
            count += 1
            inline = tokens[i + 1]
            title = "".join(child.content for child in inline.children or []
                            if child.type in {"text", "code_inline"})
            base = heading_slug(title, count)
            slug, suffix = base, 2
            while slug in used:
                slug, suffix = f"{base}-{suffix}", suffix + 1
            used.add(slug)
            headings[token.map[0]] = (int(token.tag[1:]), title, slug)
    chunks, stack = [], []
    start, collected, size = None, [], 0
    section, anchor = "正文", ""

    def flush():
        nonlocal start, collected, size
        if collected:
            text = "".join(collected).strip()
            meaningful = re.sub(r"(?m)^\s*(?:#{1,6}\s+.*|[-*_=\s]+)$", "", plain(text)).strip()
            if text and meaningful:
                chunks.append(dict(section=section, anchor=anchor, start_line=start + 1,
                                   end_line=start + len(collected), text=text))
        start, collected, size = None, [], 0

    fence = None
    for number, line in enumerate(lines):
        if number in headings:
            flush()
            level, title, anchor = headings[number]
            stack = [item for item in stack if item[0] < level] + [(level, title)]
            section = " / ".join(item[1] for item in stack)
        boundary = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if boundary:
            marker = boundary.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not line[boundary.end():].strip():
                fence = None
        if start is None:
            start = number
        collected.append(line)
        size += len(line)
        if size >= 850 and not fence and not line.strip():
            flush()
    flush()
    return chunks


def initialize_explore(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS explore_documents (
        note_id INTEGER PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
        fingerprint TEXT NOT NULL, body_sha TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS explore_chunks (
        id INTEGER PRIMARY KEY, note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL, section TEXT NOT NULL, anchor TEXT NOT NULL,
        start_line INTEGER NOT NULL, end_line INTEGER NOT NULL, text TEXT NOT NULL,
        tokens TEXT NOT NULL, embedding TEXT, embedding_model TEXT,
        UNIQUE(note_id, ordinal)
    );
    CREATE INDEX IF NOT EXISTS ix_explore_chunks_note ON explore_chunks(note_id);
    CREATE TABLE IF NOT EXISTS explore_sessions (
        id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL, note_id INTEGER, created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL, version INTEGER NOT NULL DEFAULT 0,
        busy_until INTEGER NOT NULL DEFAULT 0, busy_token TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS ix_explore_sessions_user ON explore_sessions(user_id,updated_at);
    CREATE TABLE IF NOT EXISTS explore_messages (
        id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES explore_sessions(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK(role IN ('user','assistant')), content TEXT NOT NULL,
        sources TEXT NOT NULL DEFAULT '[]', created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS explore_requests (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, created_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_explore_requests_user ON explore_requests(user_id,created_at);
    """)


class LocalEmbeddings:
    """Offline-only loading. Model installation is an explicit setup operation."""
    def __init__(self, path: Path | None):
        self.path = path
        self.model = None
        self.failed = False
        self.lock = threading.RLock()

    @property
    def available(self):
        return bool(self.path and (self.path / "modules.json").is_file() and not self.failed)

    @property
    def key(self):
        if not self.available:
            return ""
        marker = self.path / "model.safetensors"
        stamp = marker.stat().st_mtime_ns if marker.exists() else 0
        return digest(f"{self.path.resolve()}:{stamp}")

    def encode(self, texts: list[str], *, query=False):
        if not self.available:
            return None
        with self.lock:
            try:
                if self.model is None:
                    import torch
                    from sentence_transformers import SentenceTransformer
                    torch.set_num_threads(2)
                    self.model = SentenceTransformer(str(self.path), local_files_only=True,
                                                     trust_remote_code=False, device="cpu")
                prefix = "为这个句子生成表示以用于检索相关文章：" if query else ""
                return self.model.encode([prefix + t for t in texts], normalize_embeddings=True,
                                         show_progress_bar=False, batch_size=32).tolist()
            except Exception:
                # Search remains usable; never download a model from a web request.
                LOG.warning("知识求索本地向量模型不可用，改用关键词检索", exc_info=False)
                self.failed = True
                return None


class ExploreIndex:
    def __init__(self, embeddings=None):
        self.embeddings = embeddings or LocalEmbeddings(None)
        self.lock = threading.RLock()

    def sync(self, db):
        with self.lock:
            # Catch changes through older write paths. Reads still verify scope
            # and body_sha; a cached snapshot never grants note access.
            from agent_dependencies import reconcile
            reconcile(db)
            rows = db.execute(f"SELECT notes.* FROM notes WHERE {PUBLIC_SQL} ORDER BY id").fetchall()
            allowed = {row["id"] for row in rows if public_path(row["path"])}
            for row in db.execute("SELECT note_id FROM explore_documents").fetchall():
                if row[0] not in allowed:
                    db.execute("DELETE FROM explore_chunks WHERE note_id=?", (row[0],))
                    db.execute("DELETE FROM explore_documents WHERE note_id=?", (row[0],))
            for row in rows:
                if row["id"] not in allowed:
                    continue
                fingerprint = digest(json.dumps([INDEX_VERSION, row["body_sha"], row["title"], row["summary"], row["path"]]))
                existing = db.execute("SELECT fingerprint FROM explore_documents WHERE note_id=?", (row["id"],)).fetchone()
                if existing and existing[0] == fingerprint:
                    continue
                db.execute("DELETE FROM explore_chunks WHERE note_id=?", (row["id"],))
                for i, chunk in enumerate(split_article(row["body"])):
                    weights = terms(row["title"] + " " + row["summary"] + " " + chunk["section"])
                    counts = terms(chunk["text"]) + Counter({k: v * 3 for k, v in weights.items()})
                    db.execute("""INSERT INTO explore_chunks
                        (note_id,ordinal,section,anchor,start_line,end_line,text,tokens) VALUES(?,?,?,?,?,?,?,?)""",
                               (row["id"], i, chunk["section"], chunk["anchor"], chunk["start_line"],
                                chunk["end_line"], chunk["text"], json.dumps(counts, ensure_ascii=False)))
                db.execute("INSERT OR REPLACE INTO explore_documents VALUES(?,?,?)", (row["id"], fingerprint, row["body_sha"]))

    def build_vectors(self, db):
        """Run outside requests. Incremental; corpus text is never sent to a service."""
        self.sync(db)
        db.commit()
        key = self.embeddings.key
        if not key:
            raise ValueError("请先安装本地向量模型")
        rows = db.execute("""SELECT c.id,c.note_id,c.section,c.text,n.title FROM explore_chunks c
            JOIN notes n ON n.id=c.note_id WHERE c.embedding_model IS NULL OR c.embedding_model!=?""", (key,)).fetchall()
        for offset in range(0, len(rows), 64):
            batch = rows[offset:offset + 64]
            vectors = self.embeddings.encode([r["title"] + "\n" + r["section"] + "\n" + r["text"] for r in batch])
            if vectors is None:
                raise ValueError("本地向量模型加载失败，请检查可选依赖与模型目录")
            for row, vector in zip(batch, vectors):
                db.execute("""UPDATE explore_chunks SET embedding=?,embedding_model=?
                    WHERE id=? AND note_id=? AND section=? AND text=?
                    AND EXISTS(SELECT 1 FROM notes WHERE notes.id=explore_chunks.note_id AND notes.title=?)""",
                           (json.dumps(vector), key, row["id"], row["note_id"], row["section"], row["text"], row["title"]))
            db.commit()
        return len(rows)

    def search(self, db, query, *, note_id=None, limit=8, category='', since=0, per_note_limit=None):
        self.sync(db)
        db.commit()
        where = PUBLIC_SQL + (" AND notes.id=?" if note_id is not None else "")
        params = (note_id,) if note_id is not None else ()
        if category:
            where += " AND (notes.category=? OR substr(notes.category,1,length(?)+3)=?||' / ')"
            params += (category,category,category)
        if since:
            where += ' AND notes.updated_at>=?'; params += (since,)
        rows = db.execute(f"""SELECT c.*,notes.title,notes.path,notes.category,notes.body_sha,notes.updated_at
            FROM explore_chunks c JOIN notes ON notes.id=c.note_id
            JOIN explore_documents d ON d.note_id=notes.id WHERE {where} AND d.body_sha=notes.body_sha""",
                          params).fetchall()
        rows = [dict(r) for r in rows if public_path(r["path"])]
        if not rows:
            return [], "keyword"
        return rank_chunks(rows, query, self.embeddings, note_id=note_id, limit=limit, per_note_limit=per_note_limit)


def rank_chunks(rows, query, embeddings, *, note_id=None, limit=8, per_note_limit=None):
    """Rank an already authorized set of chunks using the shared hybrid scorer."""
    if not rows:
        return [], "keyword"
    counters = [json.loads(r["tokens"]) for r in rows]
    wanted = terms(query)
    frequency = Counter()
    for counter in counters:
        frequency.update(set(counter) & wanted.keys())
    average = sum(sum(c.values()) for c in counters) / len(counters) or 1
    lexical = []
    for i, counts in enumerate(counters):
        score = 0
        length = sum(counts.values())
        for word in wanted:
            tf = counts.get(word, 0)
            if tf:
                idf = math.log(1 + (len(rows) - frequency[word] + .5) / (frequency[word] + .5))
                score += idf * tf * 2.2 / (tf + 1.2 * (.25 + .75 * length / average))
        if score:
            lexical.append((i, score))
    lexical.sort(key=lambda item: item[1], reverse=True)
    ranks = {i: 1 / (60 + rank) for rank, (i, _) in enumerate(lexical[:80], 1)}
    matches = {i: "关键词匹配" for i, _ in lexical[:80]}
    if not ranks and note_id is not None:
        ranks = {i: 1 / (61 + i) for i in range(min(4, len(rows)))}
        matches = {i: "所选文章内容" for i in ranks}
    mode = "keyword"
    key = embeddings.key
    vector_rows = [(i, r) for i, r in enumerate(rows) if key and r["embedding_model"] == key and r["embedding"]]
    if vector_rows:
        encoded = embeddings.encode([query], query=True)
        if encoded:
            mode = "hybrid"
            vector = encoded[0]
            semantic = sorted(((i, sum(a * b for a, b in zip(vector, json.loads(r["embedding"]))))
                               for i, r in vector_rows), key=lambda item: item[1], reverse=True)
            # Similarity isn't a probability. Keep keyword evidence and let the answer abstain.
            cutoff = max(.4, semantic[0][1] - .08) if semantic else 1
            for rank, (i, score) in enumerate(semantic[:40], 1):
                if score < cutoff:
                    continue
                ranks[i] = ranks.get(i, 0) + 1 / (60 + rank)
                matches[i] = "关键词与语义匹配" if i in matches else "语义相关"
    # Explicit names such as React, Go, or Promise should constrain broad semantic matches.
    anchors = {word for word in wanted if word.isascii() and len(word) >= 2}
    if anchors and any(anchors & counts.keys() for counts in counters):
        for i in ranks:
            coverage = len(anchors & counters[i].keys()) / len(anchors)
            ranks[i] *= .25 + .75 * coverage
    total_weight = sum(math.log(1 + (len(rows) + .5) / (frequency[word] + .5)) for word in wanted)
    if total_weight:
        for i in ranks:
            matched_weight = sum(math.log(1 + (len(rows) + .5) / (frequency[word] + .5))
                                 for word in wanted if word in counters[i])
            ranks[i] *= .4 + .6 * matched_weight / total_weight
    result, per_note = [], Counter()
    for i in sorted(ranks, key=ranks.get, reverse=True):
        row = rows[i]
        if per_note[row["note_id"]] >= (per_note_limit or (4 if note_id is not None else 2)):
            continue
        per_note[row["note_id"]] += 1
        row["reason"] = matches[i]
        row["url"] = f"/n/{row['note_id']}" + ("#" + quote(row["anchor"]) if row["anchor"] else "")
        row.pop("tokens", None)
        row.pop("embedding", None)
        row.pop("embedding_model", None)
        result.append(row)
        if len(result) == limit:
            break
    return result, mode
