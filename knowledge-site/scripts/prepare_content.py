"""Copy an explicit allowlist of knowledge files into a deployable content bundle.

The public catalog remains exactly the one in categories.json. Other Markdown
documents are copied as private records; repository metadata and tool sources
are never copied. This script only reads the source knowledge base.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path, PurePosixPath

PUBLIC_ROOTS = {"Backend", "Frontend"}
PRIVATE_ROOTS = ("Developer", "HowToLiveBetter", "运维")
SKIP_DIRS = {".git", ".github", ".claude", ".claudian", ".obsidian", "node_modules", "skills", "tools", "scripts", "test", "tests", "__pycache__"}
SKIP_FILES = {"AGENTS.md", "CLAUDE.md", "LICENSE.md"}
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".pdf"}


def safe_path(root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return (
        path.is_file()
        and not path.is_symlink()
        and not any((root.joinpath(*relative.parts[:index])).is_symlink() for index in range(1,len(relative.parts)))
        and path.resolve().is_relative_to(root)
        and all(part not in (".", "..") and not part.startswith(".") for part in relative.parts)
    )


def resolve_public(root: Path, doc_path: str, ancestors: list[str]) -> Path:
    parsed = PurePosixPath(doc_path)
    if parsed.is_absolute() or any(part in (".", "..") for part in parsed.parts):
        raise ValueError("Unsafe catalog path")
    for base in [root / name for name in reversed(ancestors) if name != "/Private"] + [root]:
        candidate = base / doc_path
        if not safe_path(root, candidate):
            continue
        relative = candidate.relative_to(root)
        if relative.parts[0] in PUBLIC_ROOTS and not any(part.lower() == "static" for part in relative.parts[:-1]):
            return candidate
    raise FileNotFoundError(f"Catalog entry does not resolve: {doc_path}")


def walk_catalog(root: Path, node: dict, ancestors: list[str], labels: list[str], *, skip_missing: bool = False):
    current = ancestors + [node.get("path", "")]
    trail = labels + ([] if node.get("path") == "/Private" else [node["name"]])
    for doc in node.get("documents", []):
        try:
            source = resolve_public(root, doc["path"], current)
        except FileNotFoundError:
            if skip_missing:
                continue
            raise
        yield {
            "path": source.relative_to(root).as_posix(),
            "title": doc["title"],
            "summary": doc.get("summary", ""),
            "category": " / ".join(trail),
            "visibility": "public",
        }
    for child in node.get("children", []):
        yield from walk_catalog(root, child, current, trail, skip_missing=skip_missing)


def iter_private_markdown(root: Path):
    for dirname in PRIVATE_ROOTS:
        base = root / dirname
        if not base.is_dir():
            continue
        for current, dirs, files in os.walk(base):
            dirs[:] = [name for name in dirs if name not in SKIP_DIRS and not name.startswith(".")]
            for name in files:
                if name in SKIP_FILES or not name.lower().endswith(".md"):
                    continue
                path = Path(current) / name
                if safe_path(root, path):
                    yield path
    for path in root.glob("*.md"):
        if path.name not in SKIP_FILES | {"WIKI.md"} and safe_path(root, path):
            yield path


def iter_assets(root: Path):
    for dirname in (*PUBLIC_ROOTS, *PRIVATE_ROOTS):
        base = root / dirname
        if not base.is_dir():
            continue
        for current, dirs, files in os.walk(base):
            dirs[:] = [name for name in dirs if name not in SKIP_DIRS and not name.startswith(".")]
            for name in files:
                path = Path(current) / name
                if path.suffix.lower() in ASSET_SUFFIXES and safe_path(root, path):
                    yield path


def copy_file(source_root: Path, target_root: Path, path: Path) -> None:
    relative = path.relative_to(source_root)
    target = target_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--target-root", type=Path, default=Path(__file__).resolve().parent.parent / "content")
    args = parser.parse_args()
    source_root = args.source_root.resolve()
    target_root = args.target_root.resolve()
    if target_root == source_root or target_root in source_root.parents:
        raise SystemExit("Target must not contain the source tree")
    catalog = json.loads((source_root / "categories.json").read_text(encoding="utf-8"))
    records = list(walk_catalog(source_root, catalog, [], []))
    public_count = len(records)
    for path in sorted((source_root / "WIKI").glob("*.md")):
        if safe_path(source_root, path):
            records.append({"path": path.relative_to(source_root).as_posix(), "title": path.stem, "summary": "公开知识导览", "category": "知识导览", "visibility": "public"})
    for path in iter_private_markdown(source_root):
        relative = path.relative_to(source_root)
        category = "私人笔记" if len(relative.parts) == 1 else "私人笔记 / " + " / ".join(relative.parts[:-1])
        records.append({"path": relative.as_posix(), "title": path.stem, "summary": "", "category": category, "visibility": "private"})
    paths = [record["path"] for record in records]
    if len(paths) != len(set(paths)):
        raise SystemExit("Duplicate note path")
    target_root.mkdir(parents=True, exist_ok=True)
    for record in records:
        path = source_root / record["path"]
        copy_file(source_root, target_root, path)
        record["sha256"] = hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    asset_count = 0
    for path in iter_assets(source_root):
        copy_file(source_root, target_root, path)
        asset_count += 1
    (target_root / "manifest.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Prepared {public_count} catalog notes, {len(records) - public_count} additional notes, {asset_count} assets")


if __name__ == "__main__":
    main()
