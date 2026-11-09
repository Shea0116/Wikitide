"""Small, allow-list HTML sanitizer for Markdown notes.

The notes use HTML for visual emphasis (mostly ``span`` and ``div`` with
inline styles). Markdown-It can parse that HTML, but raw note content must not
be allowed to inject scripts or arbitrary attributes into the site.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit


ALLOWED_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "code",
    "del",
    "details",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "input",
    "ins",
    "kbd",
    "li",
    "mark",
    "ol",
    "p",
    "pre",
    "s",
    "small",
    "span",
    "strong",
    "sub",
    "summary",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
    "u",
}
SKIP_TAGS = {"audio", "canvas", "embed", "form", "iframe", "object", "script", "select", "style", "svg", "template", "textarea", "video"}
VOID_TAGS = {"br", "hr", "img", "input"}
HTML_VOID_TAGS = VOID_TAGS | {"area", "base", "col", "embed", "link", "meta", "param", "source", "track", "wbr"}
GLOBAL_ATTRS = {"class", "id", "title"}
TAG_ATTRS = {
    "a": {"href", "target", "rel"},
    "img": {"alt", "height", "src", "width"},
    "input": {"aria-label", "checked", "disabled", "type"},
    "ol": {"start"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}
STYLE_PROPS = {
    "background-color",
    "border",
    "border-left",
    "border-radius",
    "color",
    "font-size",
    "font-style",
    "font-weight",
    "line-height",
    "margin",
    "margin-bottom",
    "margin-top",
    "padding",
    "padding-left",
    "padding-right",
    "text-align",
    "text-decoration",
    "white-space",
}
SAFE_STYLE_VALUE = re.compile(r"^[\w\s#(),.%+\-./]+$")


def safe_url(value: str) -> bool:
    if any(ord(char) < 32 or ord(char) == 127 for char in value) or "\\" in value:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme:
        return parsed.scheme.lower() in {"http", "https", "mailto"}
    return not parsed.netloc and not value.startswith("//")


def _style(value: str) -> str:
    declarations: list[str] = []
    for declaration in value.split(";"):
        if ":" not in declaration:
            continue
        prop, raw_value = (part.strip().lower() for part in declaration.split(":", 1))
        if prop in STYLE_PROPS and raw_value and SAFE_STYLE_VALUE.fullmatch(raw_value) and "url" not in raw_value.lower() and "expression" not in raw_value.lower():
            declarations.append(f"{prop}:{raw_value}")
    return ";".join(declarations)


class SafeHTML(HTMLParser):
    def __init__(self, url_transform=None) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.stack: list[str] = []
        self.skip_stack: list[str] = []
        self.url_transform = url_transform

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        if self.skip_stack:
            if tag in SKIP_TAGS and tag not in HTML_VOID_TAGS:
                self.skip_stack.append(tag)
            return
        if tag in SKIP_TAGS:
            if tag not in HTML_VOID_TAGS:
                self.skip_stack.append(tag)
            return
        if tag not in ALLOWED_TAGS:
            return
        if tag == "input":
            # Only the renderer's read-only task checkbox is allowed through.
            attrs_by_name = {name.lower(): value or "" for name, value in attrs}
            if attrs_by_name.get("type", "").lower() != "checkbox":
                return
            attrs_by_name["disabled"] = ""
            clean = []
            if attrs_by_name.get("class") == "task-checkbox":
                clean.append(' class="task-checkbox"')
            clean.extend([' type="checkbox"', " disabled"])
            if "checked" in attrs_by_name:
                clean.append(" checked")
            if attrs_by_name.get("aria-label"):
                clean.append(f' aria-label="{html.escape(attrs_by_name["aria-label"], quote=True)}"')
            self.output.append("<input" + "".join(clean) + " />")
            return
        allowed = set(GLOBAL_ATTRS) | TAG_ATTRS.get(tag, set())
        clean: list[str] = []
        for name, value in attrs:
            name = name.lower()
            if name == "style":
                value = _style(value or "")
                if value:
                    clean.append(f' style="{html.escape(value, quote=True)}"')
            elif name in allowed and value is not None:
                if name in {"href", "src"}:
                    value = value.strip()
                    if not safe_url(value):
                        continue
                    if self.url_transform:
                        value = self.url_transform(tag, name, value)
                        if value is None or not safe_url(value):
                            continue
                if name in {"rowspan", "colspan", "width", "height"} and not re.fullmatch(r"[1-9][0-9]{0,3}", value):
                    continue
                if name == "start" and not re.fullmatch(r"-?[0-9]+", value):
                    continue
                clean.append(f' {name}="{html.escape(value, quote=True)}"')
        if tag == "a":
            clean = [attr for attr in clean if not attr.startswith(' rel=')]
            clean.append(' rel="noopener noreferrer"')
        self.output.append("<" + tag + "".join(clean) + (" />" if tag in VOID_TAGS else ">"))
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs):
        # A self-closing forbidden element must not swallow following text.
        if tag in SKIP_TAGS:
            return
        self.handle_starttag(tag, attrs)
        if tag not in HTML_VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if self.skip_stack:
            if tag in self.skip_stack:
                del self.skip_stack[self.skip_stack.index(tag):]
            return
        if tag in self.stack:
            while self.stack:
                current = self.stack.pop()
                self.output.append(f"</{current}>")
                if current == tag:
                    break

    def handle_data(self, data: str):
        if not self.skip_stack:
            self.output.append(html.escape(data))

    def handle_entityref(self, name: str):
        if not self.skip_stack:
            self.output.append(f"&{name};")

    def handle_charref(self, name: str):
        if not self.skip_stack:
            self.output.append(f"&#{name};")

    def handle_comment(self, data: str):
        return


def sanitize_html(value: str, url_transform=None) -> str:
    parser = SafeHTML(url_transform)
    parser.feed(value)
    parser.close()
    while parser.stack:
        parser.output.append(f"</{parser.stack.pop()}>")
    return "".join(parser.output)
