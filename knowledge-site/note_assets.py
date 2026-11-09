"""Obsidian embeds shared by display, authorization and attachment sync."""
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it.token import Token

ASSET_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}


def embed_urls(target):
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return []
    path = unquote(parsed.path)
    if Path(path).suffix.lower() not in ASSET_SUFFIXES or '\\' in path:
        return []
    if '/' in path:
        return [target]
    return ['static/' + target, 'Static/' + target, target]


def obsidian_embed(state, silent):
    start = state.pos
    if not state.src.startswith('![[', start):
        return False
    end = state.src.find(']]', start + 3, state.posMax)
    if end < 0:
        return False
    target, _, label = state.src[start + 3:end].partition('|')
    target = target.strip()
    if '\n' in target or not embed_urls(target):
        return False
    if not silent:
        token = state.push('image', 'img', 0)
        token.markup = '![['
        token.attrSet('src', target)
        token.content = label if label and not label.replace('x', '').isdigit() else Path(target).name
        token.children = [Token('text', '', 0, content=token.content)]
    state.pos = end + 2
    return True


def enable_embeds(parser):
    parser.inline.ruler.before('image', 'obsidian_embed', obsidian_embed)
    return parser


def parse_note(parser, body, note_path, root=None, exists=None):
    tokens = parser.parse(body)
    for block in tokens:
        for token in block.children or []:
            if token.type != 'image' or token.markup != '![[':
                continue
            urls = embed_urls(token.attrGet('src'))
            chosen = urls[0]
            if root is not None or exists is not None:
                base = Path(root) / Path(note_path).parent if root is not None else None
                for url in urls:
                    file = base / unquote(urlsplit(url).path) if base is not None else None
                    if (file is not None and file.is_file()) or (exists is not None and exists(url)):
                        chosen = url
                        break
            # Keep all candidates so authorization also works before upload.
            token.meta['embed_urls'] = urls
            token.attrSet('src', chosen)
    return tokens
