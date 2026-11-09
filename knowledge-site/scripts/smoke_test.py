"""Focused access-control and sync checks against a temporary database."""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import create_app  # noqa: E402
from storage import connect, create_user, digest  # noqa: E402

BASE = Path(__file__).resolve().parent.parent


def login(client: TestClient, username: str, password: str) -> None:
    response = client.get("/login")
    assert response.status_code == 200
    csrf = re.search(r'name="csrf" value="([^"]+)"', response.text).group(1)
    response = client.post("/login", data={"csrf": csrf, "username": username, "password": password, "next": "/"}, follow_redirects=False)
    assert response.status_code == 303


def main() -> None:
    with tempfile.TemporaryDirectory(dir=BASE / "work") as directory:
        db_path = Path(directory) / "test.db"
        token = "test-sync-token"
        app = create_app(db_path, BASE / "content", sync_token_hash=digest(token))
        with connect(db_path) as db:
            public = db.execute("SELECT * FROM notes WHERE visibility='public' ORDER BY id LIMIT 1").fetchone()
            private = db.execute("SELECT * FROM notes WHERE visibility='private' ORDER BY id LIMIT 1").fetchone()
            total = db.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            assert public and private and total >= 340
            create_user(db, "owner", "local-test-password-123", "admin")
            create_user(db, "reader", "local-test-password-123", "reader")
            create_user(db, "editor", "local-test-password-123", "editor")
            reader_id = db.execute("SELECT id FROM users WHERE username='reader'").fetchone()[0]
            editor_id = db.execute("SELECT id FROM users WHERE username='editor'").fetchone()[0]
            db.execute("INSERT INTO grants(user_id,note_id,can_view,can_edit) VALUES(?,?,1,0)", (reader_id, private["id"]))
            db.execute("INSERT INTO grants(user_id,note_id,can_view,can_edit) VALUES(?,?,1,1)", (editor_id, private["id"]))

        anonymous = TestClient(app)
        assert anonymous.get("/").status_code == 200
        assert anonymous.get(f"/n/{public['id']}").status_code == 200
        assert anonymous.get(f"/n/{private['id']}").status_code == 404
        assert "私人笔记" not in anonymous.get("/").text
        assert "Frontend / Flutter" in anonymous.get("/browse?category=Frontend").text
        assert "<table>" in anonymous.get("/n/29").text
        assert anonymous.get("/api/sync/state").status_code == 401

        owner = TestClient(app)
        login(owner, "owner", "local-test-password-123")
        assert owner.get(f"/n/{private['id']}").status_code == 200
        assert owner.get("/admin").status_code == 200

        reader = TestClient(app)
        login(reader, "reader", "local-test-password-123")
        assert reader.get(f"/n/{private['id']}").status_code == 200
        assert reader.get(f"/n/{private['id']}/edit").status_code == 403
        assert reader.get("/admin").status_code == 403

        editor = TestClient(app)
        login(editor, "editor", "local-test-password-123")
        assert editor.get(f"/n/{private['id']}/edit").status_code == 200
        with connect(db_path) as db:
            before = db.execute("SELECT body,body_sha,path FROM notes WHERE id=?", (private["id"],)).fetchone()
            session = db.execute("SELECT csrf FROM sessions JOIN users ON users.id=sessions.user_id WHERE users.username='editor'").fetchone()
        changed_body = before["body"] + "\n测试编辑。\n"
        saved = editor.post(f"/n/{private['id']}/edit", data={"csrf": session["csrf"], "base_sha": before["body_sha"], "body": changed_body}, follow_redirects=False)
        assert saved.status_code == 303
        assert editor.post(f"/n/{private['id']}/edit", data={"csrf": "wrong", "base_sha": before["body_sha"], "body": changed_body}).status_code == 403
        headers = {"Authorization": "Bearer " + token}
        events = anonymous.get("/api/sync/events", headers=headers).json()["events"]
        assert len(events) == 1 and events[0]["path"] == before["path"] and events[0]["body"] == changed_body
        assert anonymous.post("/api/sync/push", headers=headers, json={"path": before["path"], "base_sha": before["body_sha"], "body": "stale"}).status_code == 409
        assert anonymous.post(f"/api/sync/events/{events[0]['id']}/ack", headers=headers, json={"status": "applied"}).status_code == 200
        assert anonymous.get("/api/sync/events", headers=headers).json()["events"] == []
        print("Smoke checks passed: visibility, roles, Markdown tables, CSRF, edit queue and sync conflict")


if __name__ == "__main__":
    main()
