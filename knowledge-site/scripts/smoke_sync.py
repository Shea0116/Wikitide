"""Verify two-way sync and conflict protection using disposable files."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sync_local  # noqa: E402


class FakeAPI:
    def __init__(self, path: str, body: str):
        self.path = path
        self.body = body
        self.events: list[dict] = []
        self.acks: list[tuple[int, str]] = []

    def call(self, route: str, payload: dict | None = None):
        if route == "/api/sync/state":
            return {"notes": [{"path": self.path, "sha": sync_local.digest(self.body)}]}
        if route == "/api/sync/events":
            return {"events": list(self.events)}
        if route.endswith("/ack"):
            event_id = int(route.split("/")[-2])
            self.acks.append((event_id, payload["status"]))
            self.events = [event for event in self.events if event["id"] != event_id]
            return {"ok": True}
        if route == "/api/sync/push":
            if payload["base_sha"] != sync_local.digest(self.body):
                return {"conflict": True}
            self.body = payload["body"]
            return {"sha": sync_local.digest(self.body)}
        raise AssertionError(route)

    def web_edit(self, body: str, event_id: int):
        event = {"id": event_id, "path": self.path, "base_sha": sync_local.digest(self.body), "new_sha": sync_local.digest(body), "body": body}
        self.events.append(event)
        self.body = body


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "source"
        path = source / "Frontend" / "example.md"
        path.parent.mkdir(parents=True)
        path.write_text("first\n", encoding="utf-8")
        fake = FakeAPI("Frontend/example.md", "first\n")
        original = sync_local.SyncAPI
        sync_local.SyncAPI = lambda *_args, **_kwargs: fake
        config = {"source_root": str(source), "state_path": str(root / "state.json"), "conflict_root": str(root / "conflicts"), "base_url": "https://example.test", "token": "test"}
        try:
            assert sync_local.sync_once(config) == {"downloaded": 0, "uploaded": 0, "conflicts": 0}
            path.write_text("local edit\n", encoding="utf-8")
            assert sync_local.sync_once(config)["uploaded"] == 1
            assert fake.body == "local edit\n"
            fake.web_edit("web edit\n", 1)
            assert sync_local.sync_once(config)["downloaded"] == 1
            assert path.read_text(encoding="utf-8") == "web edit\n"
            path.write_text("second local edit\n", encoding="utf-8")
            fake.web_edit("second web edit\n", 2)
            assert sync_local.sync_once(config)["conflicts"] >= 1
            assert path.read_text(encoding="utf-8") == "second local edit\n"
            conflict = root / "conflicts" / "Frontend" / "example.md.server.md"
            assert conflict.read_text(encoding="utf-8") == "second web edit\n"
            assert fake.acks[-1] == (2, "conflict")
        finally:
            sync_local.SyncAPI = original
    print("Sync checks passed: local upload, website download and conflict preservation")


if __name__ == "__main__":
    main()
