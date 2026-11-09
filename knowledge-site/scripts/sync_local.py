"""Safely sync website edits with the local Markdown source tree.

Run this periodically on the computer that owns the source files. The server
never connects to the computer. Local changes are sent only when the last
known server revision still matches; website edits are applied only when the
local file still matches the revision edited on the website.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

MAX_RESPONSE = 16 * 1024 * 1024


def digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def safe_file(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if relative.is_absolute() or not relative.parts or any(part in (".", "..") or part.startswith(".") for part in relative.parts):
        raise ValueError("unsafe note path")
    result = root.joinpath(*relative.parts)
    ancestors = [root.joinpath(*relative.parts[:index]) for index in range(1,len(relative.parts)+1)]
    if any(path.is_symlink() for path in ancestors) or not result.parent.resolve().is_relative_to(root) or (result.exists() and not result.resolve().is_relative_to(root)):
        raise ValueError("unsafe note target")
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise RuntimeError("Sync endpoint redirected; refusing to forward the token")


class SyncAPI:
    def __init__(self, base_url: str, token: str, *, allow_http_local: bool = False):
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme != "https" and not (allow_http_local and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}):
            raise ValueError("Sync URL must use HTTPS")
        if not token or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Invalid sync connection settings")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect)

    def call(self, route: str, payload: dict | None = None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + route,
            data=data,
            method="GET" if payload is None else "POST",
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=20) as response:
                body = response.read(MAX_RESPONSE + 1)
                if len(body) > MAX_RESPONSE:
                    raise ValueError("Sync response too large")
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                return {"conflict": True}
            if exc.code == 404:
                return {"unsupported": True}
            raise RuntimeError(f"Sync request failed ({exc.code})") from exc


def atomic_write(path: Path, body: str, expected_sha: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=".kb-sync-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            os.chmod(temp_name, path.stat().st_mode & 0o777)
        if expected_sha is not None:
            actual = digest(path.read_text(encoding='utf-8')) if path.is_file() else ''
            if actual != expected_sha:
                raise RuntimeError('Local file changed while preparing sync; retry next run')
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def read_state(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise ValueError("Invalid local sync state")
    return data


def save_state(path: Path, state: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    atomic_write(path, json.dumps(state, ensure_ascii=False, indent=2))
    os.chmod(path, 0o600)


def sync_once(config: dict, *, allow_http_local: bool = False) -> dict[str, int]:
    source_root = Path(config["source_root"]).resolve()
    if not source_root.is_dir():
        raise ValueError("Local source directory does not exist")
    state_path = Path(config["state_path"]).resolve()
    conflict_root = Path(config["conflict_root"]).resolve()
    api = SyncAPI(config["base_url"], config["token"], allow_http_local=allow_http_local)
    # Negotiate the enhanced protocol; old servers retain the original flow.
    try:
        snapshot = api.call('/api/sync/v2/state')
    except AssertionError:  # Legacy in-process test adapters.
        snapshot = {'unsupported': True}
    if not snapshot.get('unsupported'):
        from sync_v2 import sync_enhanced
        return sync_enhanced(config, api, snapshot)
    state = read_state(state_path)
    counts = {"downloaded": 0, "uploaded": 0, "conflicts": 0}
    server_notes = {row["path"]: row["sha"] for row in api.call("/api/sync/state")["notes"]}
    blocked: set[str] = set()

    # New installations establish a baseline only where the copied source
    # exactly matches the server. They never overwrite an unfamiliar file.
    for name, server_sha in server_notes.items():
        path = safe_file(source_root, name)
        if name not in state and path.is_file() and digest(path.read_text(encoding="utf-8")) == server_sha:
            state[name] = server_sha

    while True:
        events = api.call("/api/sync/events")["events"]
        if not events:
            break
        processed = 0
        for event in events:
            name = event["path"]
            if name in blocked:
                continue
            path = safe_file(source_root, name)
            current = digest(path.read_text(encoding="utf-8")) if path.is_file() else ""
            if current == event["new_sha"]:
                api.call(f"/api/sync/events/{event['id']}/ack", {"status": "applied"})
                state[name] = current
                processed += 1
            elif current == event["base_sha"] and state.get(name) in (None, event["base_sha"]):
                atomic_write(path, event["body"])
                if digest(path.read_text(encoding="utf-8")) != event["new_sha"]:
                    raise RuntimeError("Local write verification failed")
                api.call(f"/api/sync/events/{event['id']}/ack", {"status": "applied"})
                state[name] = event["new_sha"]
                counts["downloaded"] += 1
                processed += 1
            else:
                conflict = safe_file(conflict_root, name + ".server.md")
                conflict_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.chmod(conflict_root, 0o700)
                current_dir = conflict_root
                for part in PurePosixPath(name).parts[:-1]:
                    current_dir /= part
                    current_dir.mkdir(exist_ok=True, mode=0o700)
                    os.chmod(current_dir, 0o700)
                atomic_write(conflict, event["body"])
                os.chmod(conflict, 0o600)
                api.call(f"/api/sync/events/{event['id']}/ack", {"status": "conflict", "message": "本地文件已变化；服务器版本保存在本地冲突目录"})
                blocked.add(name)
                counts["conflicts"] += 1
                processed += 1
        if processed == 0:
            break

    # Local edits are uploaded only if the website still has the version the
    # computer last observed. A 409 response leaves both copies untouched.
    for name, server_sha in server_notes.items():
        if name in blocked:
            continue
        path = safe_file(source_root, name)
        if not path.is_file():
            continue
        body = path.read_text(encoding="utf-8")
        local_sha = digest(body)
        if local_sha == server_sha:
            state[name] = local_sha
            continue
        baseline = state.get(name)
        if baseline is None or baseline != server_sha:
            counts["conflicts"] += 1
            continue
        result = api.call("/api/sync/push", {"path": name, "base_sha": baseline, "body": body})
        if result.get("conflict"):
            counts["conflicts"] += 1
            continue
        state[name] = result["sha"]
        counts["uploaded"] += 1

    save_state(state_path, state)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument('--rename', action='append', default=[], metavar='OLD=NEW',
                        help='Explicit tracked note rename/move, including earlier duplicate imports')
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    renames=dict(config.get('renames',{}))
    for mapping in args.rename:
        old,separator,new=mapping.partition('=')
        if not separator or not old or not new: parser.error('--rename requires OLD=NEW')
        renames[old]=new
    config['renames']=renames
    import fcntl
    lock_path = Path(config['state_path']).with_suffix('.lock')
    lock_path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with lock_path.open('a') as lock:
        os.chmod(lock_path,0o600)
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print('已有同步任务正在运行，本轮跳过')
            return
        counts = sync_once(config)
    print("同步完成：网站到本地 {downloaded} 篇，本地到网站 {uploaded} 篇，冲突 {conflicts} 篇".format(**counts))


if __name__ == "__main__":
    main()
