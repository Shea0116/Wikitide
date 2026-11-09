#!/usr/bin/env python3
"""Validate categories.json and maintain the derived Markdown catalog."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote


ROOT = Path(__file__).resolve().parent.parent
CATEGORIES = ROOT / "categories.json"
CATALOG = ROOT / "WIKI" / "分类目录.md"
MARKER = "<!-- 由 SCHEMA/wiki.py build 生成；请勿手工编辑 -->"
PUBLIC_ROOTS = {"Backend", "Frontend"}


def is_static_directory(parts: tuple[str, ...]) -> bool:
    return any(part.casefold() == "static" for part in parts)


def safe_relative(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and all(
        part not in (".", "..") for part in value.split("/")
    )


def resolve_document(doc_path: str, ancestors: list[str]) -> Path | None:
    if not safe_relative(doc_path):
        return None
    bases = [ROOT / path for path in ancestors if path != "/Private"] + [ROOT]
    for base in bases:
        candidate = base / doc_path
        if not candidate.is_file() or not candidate.is_relative_to(ROOT):
            continue
        relative = candidate.relative_to(ROOT)
        if (
            candidate.resolve().is_relative_to(ROOT)
            and relative.parts[0] in PUBLIC_ROOTS
            and not is_static_directory(relative.parts[:-1])
        ):
            return candidate
    return None


def character_count(path: Path) -> int:
    return sum(not character.isspace() for character in path.read_text(encoding="utf-8"))


def source_notes() -> set[str]:
    files: set[str] = set()
    for public_root in PUBLIC_ROOTS:
        for base, dirs, names in os.walk(ROOT / public_root):
            dirs[:] = [name for name in dirs if not name.startswith(".") and name != "node_modules" and name.casefold() != "static"]
            for name in names:
                path = Path(base) / name
                if path.suffix.lower() == ".md":
                    files.add(path.relative_to(ROOT).as_posix())
    return files


def check_public_links(*, exclude_catalog: bool = False) -> list[str]:
    errors: list[str] = []
    public_pages = [ROOT / "WIKI.md", *(ROOT / "WIKI").glob("*.md")]
    for page in public_pages:
        if exclude_catalog and page == CATALOG:
            continue
        for raw in re.findall(r"(?<!!)\[[^\]]+\]\((<[^>]+>|[^)]+)\)", page.read_text(encoding="utf-8")):
            link = raw[1:-1] if raw.startswith("<") else raw
            link = unquote(link.split("#", 1)[0])
            if not link or "://" in link:
                continue
            target = (page.parent / link).resolve()
            if not target.is_file() or not target.is_relative_to(ROOT):
                errors.append(f"{page.relative_to(ROOT)}: 无效链接 {link}")
                continue
            relative = target.relative_to(ROOT)
            if relative.parts[0] not in PUBLIC_ROOTS | {"WIKI", "SCHEMA"} and relative.as_posix() not in {"WIKI.md", "categories.json"}:
                errors.append(f"{page.relative_to(ROOT)}: 链接超出公开范围 {link}")
    return errors


def inspect(data: dict):
    errors: list[str] = []
    warnings: list[str] = []
    records: list[tuple[dict, list[str], int, list[tuple[dict, Path]]]] = []
    indexed: set[str] = set()
    slugs: set[tuple[str, str]] = set()
    declared_size = 0

    if not isinstance(data, dict):
        return ["根节点必须是对象"], warnings, records, indexed
    if data.get("documents") != []:
        errors.append("根节点 documents 必须为空；公开目录仅收录 Backend/ 与 Frontend/")
    children = data.get("children")
    if not isinstance(children, list) or {child.get("path") for child in children if isinstance(child, dict)} != PUBLIC_ROOTS or len(children) != 2:
        errors.append("根节点 children 必须且只能包含 Backend 与 Frontend")

    def visit(node: dict, ancestors: list[str], depth: int):
        nonlocal declared_size
        if not isinstance(node, dict):
            errors.append("分类节点必须是对象")
            return
        for key in ("name", "slug", "path"):
            if not isinstance(node.get(key), str) or not node[key]:
                errors.append(f"分类缺少有效的 {key}: {node!r}")
                return
        path = node["path"]
        if depth == 0:
            if path != "/Private":
                errors.append(f"根分类路径应为 /Private，实际为 {path}")
        elif not safe_relative(path):
            errors.append(f"分类路径不安全: {path}")
        elif path.split("/", 1)[0] not in PUBLIC_ROOTS:
            errors.append(f"分类不在公开范围: {path}")
        elif is_static_directory(PurePosixPath(path).parts):
            errors.append(f"静态资源目录不能作为分类: {path}")
        current = [path, *ancestors]
        documents = node.get("documents")
        children = node.get("children", [])
        if not isinstance(documents, list) or not isinstance(children, list):
            errors.append(f"{path}: documents 和 children 必须是数组")
            return
        resolved: list[tuple[dict, Path]] = []
        for doc in documents:
            if not isinstance(doc, dict):
                errors.append(f"{path}: 文档条目必须是对象")
                continue
            if any(not isinstance(doc.get(key), str) or not doc[key] for key in ("slug", "title", "path")):
                errors.append(f"{path}: 文档缺少 slug、title 或 path")
                continue
            if not isinstance(doc.get("summary"), str) or not doc["summary"].strip():
                errors.append(f"{path}/{doc['path']}: summary 必须是非空摘要")
            if not isinstance(doc.get("size"), int) or doc["size"] < 0:
                errors.append(f"{path}/{doc['path']}: size 必须是非负整数")
            else:
                declared_size += doc["size"]
            slug_key = (path, doc["slug"])
            if slug_key in slugs:
                errors.append(f"{path}: 重复的文档 slug {doc['slug']}")
            slugs.add(slug_key)
            target = resolve_document(doc["path"], current)
            if target is None:
                errors.append(f"找不到或路径不安全: {path} / {doc['path']}")
                continue
            relative = target.relative_to(ROOT).as_posix()
            if doc["title"] != target.stem:
                errors.append(f"{relative}: title 应为文件名 {target.stem}")
            if isinstance(doc.get("size"), int) and doc["size"] != character_count(target):
                errors.append(f"{relative}: size 与非空白字符数不一致")
            if relative in indexed:
                errors.append(f"同一文件重复登记: {relative}")
            indexed.add(relative)
            resolved.append((doc, target))
        records.append((node, current, depth, resolved))
        for child in children:
            visit(child, current, depth + 1)

    visit(data, [], 0)
    stats = data.get("stats", {})
    if isinstance(stats, dict):
        if stats.get("total_documents") != len(indexed):
            errors.append(f"stats.total_documents={stats.get('total_documents')}；已登记文件={len(indexed)}")
        if stats.get("total_size") != declared_size:
            errors.append(f"stats.total_size={stats.get('total_size')}；已登记 size 合计={declared_size}")
    else:
        errors.append("stats 必须是对象")
    indexed_filenames = {path.casefold() for path in indexed}
    unlisted = sorted(path for path in source_notes() if path.casefold() not in indexed_filenames)
    if unlisted:
        warnings.append(f"未纳入 categories.json 的公开笔记 {len(unlisted)} 篇: " + "、".join(unlisted))
    return errors, warnings, records, indexed


def render(records, count: int) -> str:
    lines = [
        MARKER,
        "# 完整分类目录",
        "",
        f"根据 [categories.json](../categories.json) 生成，当前有 **{count}** 篇已登记的公开笔记。只收录 Backend/ 与 Frontend/；规则见 [收录范围](补充资料.md)。",
        "",
    ]
    for node, _ancestors, depth, docs in records:
        if depth == 0:
            continue
        heading = "#" * min(depth + 1, 6)
        title = node["name"]
        lines += [f"{heading} {title}", ""]
        if docs:
            for doc, target in docs:
                relative = "../" + target.relative_to(ROOT).as_posix()
                link = quote(relative, safe="/.")
                label = doc["title"].replace("[", "\\[").replace("]", "\\]")
                summary = doc["summary"].replace("\n", " ").strip()
                lines.append(f"- [{label}]({link})" + (f"：{summary}" if summary else ""))
        else:
            lines.append("此分类目前没有直接登记的笔记。")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "build"))
    args = parser.parse_args()
    try:
        data = json.loads(CATEGORIES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(f"无法读取 categories.json: {exc}")
    errors, warnings, records, indexed = inspect(data)
    for warning in warnings:
        print(f"提示: {warning}")
    if errors:
        for error in errors:
            print(f"错误: {error}")
        return 1
    expected = render(records, len(indexed))
    if args.command == "build":
        if CATALOG.exists() and not CATALOG.read_text(encoding="utf-8").startswith(MARKER):
            print(f"错误: 拒绝覆盖非生成文件 {CATALOG}")
            return 1
        CATALOG.parent.mkdir(parents=True, exist_ok=True)
        CATALOG.write_text(expected, encoding="utf-8")
        print(f"已生成 {CATALOG.relative_to(ROOT)}：{len(indexed)} 篇笔记")
        return 0
    link_errors = check_public_links()
    if link_errors:
        for error in link_errors:
            print(f"错误: {error}")
        return 1
    if not CATALOG.exists() or CATALOG.read_text(encoding="utf-8") != expected:
        print("错误: WIKI/分类目录.md 缺失或与 categories.json 不同步；运行 build 更新")
        return 1
    print(f"检查通过：{len(indexed)} 篇已登记笔记，{len(records)} 个分类节点")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
