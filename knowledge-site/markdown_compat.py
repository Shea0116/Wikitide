"""Render a few Markdown conventions already used by the source notes.

Keep this on the display path only; the Markdown files and editor text retain
their original spelling.
"""

from __future__ import annotations

import re

from markdown_it.token import Token


# CommonMark leaves **注意：**后文 literal because the closing delimiter touches
# a word character. The notes use that spelling for bold labels.
ADJACENT_STRONG = re.compile(r"(?<!\\)\*\*([^*\n]+?)(?<!\\)\*\*(?=\w)")
TASK_MARKER = re.compile(r"^\[([ xX])\](?:\s+|$)")


def _text(content: str) -> Token:
    return Token("text", "", 0, content=content)


def _fix_adjacent_strong(tokens: list[Token]) -> None:
    for token in tokens:
        if token.type != "inline" or not token.children or not ADJACENT_STRONG.search(token.content):
            continue
        children: list[Token] = []
        for child in token.children:
            if child.type != "text":
                children.append(child)
                continue
            cursor = 0
            for match in ADJACENT_STRONG.finditer(child.content):
                if match.start() > cursor:
                    children.append(_text(child.content[cursor : match.start()]))
                children.extend(
                    [
                        Token("strong_open", "strong", 1, markup="**"),
                        _text(match.group(1)),
                        Token("strong_close", "strong", -1, markup="**"),
                    ]
                )
                cursor = match.end()
            if cursor:
                if cursor < len(child.content):
                    children.append(_text(child.content[cursor:]))
            else:
                children.append(child)
        token.children = children


def _fix_extended_tables(tokens: list[Token]) -> list[Token]:
    removed: set[int] = set()
    for index, token in enumerate(tokens):
        # -tx- is a table directive in the source notes, not visible prose.
        if (
            token.type == "paragraph_open"
            and index + 3 < len(tokens)
            and tokens[index + 1].type == "inline"
            and tokens[index + 1].content.strip() == "-tx-"
            and tokens[index + 2].type == "paragraph_close"
            and tokens[index + 3].type == "table_open"
        ):
            removed.update((index, index + 1, index + 2))

        if token.type != "tbody_open":
            continue
        anchors: dict[int, Token] = {}
        position = index + 1
        while position < len(tokens) and tokens[position].type != "tbody_close":
            if tokens[position].type != "tr_open":
                position += 1
                continue
            position += 1
            column = 0
            while position < len(tokens) and tokens[position].type != "tr_close":
                if tokens[position].type != "td_open":
                    position += 1
                    continue
                start = position
                while position < len(tokens) and tokens[position].type != "td_close":
                    position += 1
                if position >= len(tokens):
                    break
                end = position
                body = next((item for item in tokens[start + 1 : end] if item.type == "inline"), None)
                if body is not None and body.content.strip() == "^^" and column in anchors:
                    anchor = anchors[column]
                    anchor.attrSet("rowspan", int(anchor.attrGet("rowspan") or 1) + 1)
                    removed.update(range(start, end + 1))
                else:
                    anchors[column] = tokens[start]
                column += 1
                position = end + 1
            position += 1
    return [token for index, token in enumerate(tokens) if index not in removed]


def _fix_task_lists(tokens: list[Token]) -> None:
    for index, token in enumerate(tokens):
        if token.type != "list_item_open" or index + 2 >= len(tokens):
            continue
        if tokens[index + 1].type != "paragraph_open" or tokens[index + 2].type != "inline":
            continue
        inline = tokens[index + 2]
        match = TASK_MARKER.match(inline.content)
        if not match or not inline.children or inline.children[0].type != "text":
            continue
        first = inline.children[0]
        if not first.content.startswith(match.group(0)):
            continue
        checked = match.group(1).lower() == "x"
        checkbox = Token(
            "html_inline",
            "",
            0,
            content='<input class="task-checkbox" type="checkbox" disabled'
            + (' checked aria-label="已完成">' if checked else ' aria-label="未完成">'),
        )
        first.content = first.content[match.end() :]
        inline.content = inline.content[match.end() :]
        inline.children.insert(0, checkbox)
        token.attrSet("class", "task-list-item")


def enhance_markdown_tokens(tokens: list[Token]) -> list[Token]:
    _fix_adjacent_strong(tokens)
    _fix_task_lists(tokens)
    return _fix_extended_tables(tokens)
