"""Interface localization. Note bodies and interpolated user data are never translated.

Template literals are localized before interpolation; the same reviewed catalog is
used by browser interactions. Chinese remains the source/fallback language.
"""
from contextvars import ContextVar
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from jinja2 import Environment, nodes
from jinja2.visitor import NodeTransformer

LOCALE = ContextVar('wikitide_locale', default='zh-CN')
COOKIE = 'kb_language'
CATALOG = json.loads((Path(__file__).parent / 'static/i18n/en.json').read_text())
HAN = re.compile(r'[\u3400-\u9fff]')
UI_PUNCTUATION = re.compile(r'[。！？：；，（）《》、]')
DYNAMIC_KEYS = (
    '{0} 内容无效或过长', '{0} 不能为空', '创建空间「{0}」',
    '{0} 加入空间', '{0} 拒绝邀请', '邀请 {0}，角色 {1}', '撤销邀请 #{0}',
    '账户 #{0} 的角色调整为 {1}', '账户 #{0} 已移出空间', '{0} 退出空间',
    '所有权转让给账户 #{0}', '创建文档「{0}」', '更新文档「{0}」至 v{1}',
    '在「{0}」添加讨论', '提交「{0}」v{1} 审核', '审核「{0}」v{1}',
    '当前 v{0}', '历史版本 #{0}', '未导入不支持的图片：{0}',
    'DOCX 已提取段落、标题、基础表格与 {0} 张图片；批注、页眉页脚和复杂版式请对照原文件。',
    'PDF 仅提取文字，不保留图片和原排版。 本地 OCR 识别了 {0} 页，请逐页核对文字。',
)
DYNAMIC_PATTERNS = [(re.compile('^'+re.sub(r'\\\{\d+\\\}', '(.*?)', re.escape(key))+'$'), key)
                    for key in DYNAMIC_KEYS]
PARTS = re.compile(r'(<!--.*?-->|<[^>]*>)', re.S)
ATTRS = re.compile(r'''\b(aria-label|aria-description|title|placeholder|alt|data-confirm|content)=("[^"]*"|'[^']*')''')


def language(request):
    explicit = request.query_params.get('lang')
    if explicit in {'en', 'zh-CN'}:
        return explicit
    saved = request.cookies.get(COOKIE)
    if saved in {'en', 'zh-CN'}:
        return saved
    preferences = []
    for index, entry in enumerate(request.headers.get('accept-language', '').split(',')):
        tag, *parameters = entry.strip().lower().split(';')
        quality = 1.0
        try:
            for parameter in parameters:
                if parameter.strip().startswith('q='):
                    quality = float(parameter.strip()[2:])
        except ValueError:
            continue
        if not 0 < quality <= 1:
            continue
        if tag == 'en' or tag.startswith('en-'):
            preferences.append((-quality, index, 'en'))
        elif tag == 'zh' or tag.startswith('zh-'):
            preferences.append((-quality, index, 'zh-CN'))
    return min(preferences)[2] if preferences else 'zh-CN'


def ui(value):
    if not isinstance(value, str) or LOCALE.get() != 'en':
        return value
    key = value.strip()
    translated = CATALOG.get(key)
    if translated is None:
        for pattern, template in DYNAMIC_PATTERNS if len(key) <= 2048 else ():
            match = pattern.fullmatch(key)
            if match and template in CATALOG:
                translated = CATALOG[template].format(*match.groups())
                break
    if translated is None:
        if HAN.search(key):
            return value
        translated = key.translate(str.maketrans({'。':'.','！':'!','？':'?', '：':':', '；':';', '，':',', '（':'(', '）':')', '《':'“', '》':'”', '、':','}))
    return value[:len(value)-len(value.lstrip())] + translated + value[len(value.rstrip()):]


def safe_return(value):
    """Preference changes may return only to an ordinary same-site page."""
    if not value or any(ord(char) < 32 for char in value) or '\\' in value:
        return '/'
    try:
        parsed = urlsplit(value)
    except ValueError:
        return '/'
    if parsed.scheme or parsed.netloc or not value.startswith('/') or value.startswith('//'):
        return '/'
    if parsed.path.startswith('/language/'):
        return '/'
    # A previous explicit locale must not override the newly selected cookie.
    from urllib.parse import parse_qsl, urlencode, urlunsplit
    query = urlencode([(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != 'lang'])
    return urlunsplit(('', '', parsed.path, query, parsed.fragment))


def call(value):
    return nodes.Call(nodes.Name('ui', 'load'), [nodes.Const(value)], [], None, None)


def preprocess_template(source):
    # Mask Jinja first so tags remain whole even when an attribute contains a
    # dynamic URL or a conditional. User values never enter this translation.
    expressions = []
    def mask(match):
        expressions.append(match.group())
        return '\x00J' + str(len(expressions)-1) + '\x00'
    masked = re.sub(r'({{.*?}}|{%.*?%}|{#.*?#})', mask, source, flags=re.S)
    def text(value):
        return ''.join('{{ ui(' + json.dumps(piece, ensure_ascii=False) + ') }}'
                       if HAN.search(piece) or UI_PUNCTUATION.search(piece) else piece
                       for piece in re.split(r'(\x00J\d+\x00)', value))
    output = []
    for part in PARTS.split(masked):
        if part.startswith('<!--'):
            output.append(part)
        elif part.startswith('<'):
            output.append(ATTRS.sub(lambda m: m.group(1)+'='+m.group(2)[0]+text(m.group(2)[1:-1])+m.group(2)[-1], part))
        else:
            output.append(text(part))
    return re.sub(r'\x00J(\d+)\x00', lambda m: expressions[int(m.group(1))], ''.join(output))


class OutputLiterals(NodeTransformer):
    def visit_Const(self, node, *args, **kwargs):
        return call(node.value) if isinstance(node.value, str) and HAN.search(node.value) else node

    def visit_CondExpr(self, node, *args, **kwargs):
        # Tests and dictionary keys are application data, never display text.
        node.expr1 = self.visit(node.expr1)
        if node.expr2 is not None:
            node.expr2 = self.visit(node.expr2)
        return node

    def visit_Compare(self, node, *args, **kwargs):
        return node

    def visit_Pair(self, node, *args, **kwargs):
        node.value = self.visit(node.value)
        return node

    def visit_Getitem(self, node, *args, **kwargs):
        node.node = self.visit(node.node)
        return node

    def visit_Filter(self, node, *args, **kwargs):
        if node.name not in {'urlencode', 'tojson', 'replace'}:
            node.node = self.visit(node.node)
        return node

    def visit_Call(self, node, *args, **kwargs):
        # Explicit ui() is already marked; method parameters can be data.
        if isinstance(node.node, nodes.Name) and node.node.name == 'ui':
            return node
        node.node = self.visit(node.node)
        if isinstance(node.node, nodes.Getattr) and node.node.attr == 'get':
            node.args = [node.args[0], *[self.visit(arg) for arg in node.args[1:]]]
        return node


class TemplateLiterals(NodeTransformer):
    def visit_Output(self, node, *args, **kwargs):
        result = []
        for child in node.nodes:
            result.append(child if isinstance(child, nodes.TemplateData) else OutputLiterals().visit(child))
        node.nodes = result
        return node


class LocalizedEnvironment(Environment):
    def _parse(self, source, name, filename):
        return TemplateLiterals().visit(super()._parse(preprocess_template(source), name, filename))


def register_i18n(app, secure=False):
    from fastapi import Request, HTTPException
    from fastapi.exception_handlers import http_exception_handler
    from fastapi.responses import RedirectResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.middleware('http')
    async def locale_context(request: Request, call_next):
        selected = language(request)
        request.state.locale = selected
        token = LOCALE.set(selected)
        try:
            response = await call_next(request)
            if response.headers.get('content-type', '').startswith('text/html'):
                response.headers['Content-Language'] = selected
                existing = response.headers.get('Vary', '')
                response.headers['Vary'] = ', '.join(dict.fromkeys(
                    [v.strip() for v in existing.split(',') if v.strip()] + ['Cookie', 'Accept-Language']))
                response.headers['Cache-Control'] = 'no-store'
            if request.query_params.get('lang') in {'en', 'zh-CN'}:
                response.set_cookie(COOKIE, selected, max_age=31536000, secure=secure, httponly=True, samesite='lax')
            return response
        finally:
            LOCALE.reset(token)

    @app.get('/language/{locale}')
    def select_language(request: Request, locale: str, next: str = '/'):
        if locale not in {'en', 'zh-CN'}:
            raise HTTPException(404)
        response = RedirectResponse(safe_return(next), status_code=303)
        response.set_cookie(COOKIE, locale, max_age=31536000, secure=secure, httponly=True, samesite='lax')
        response.headers['Cache-Control'] = 'no-store'
        return response

    previous_error = app.exception_handlers.get(HTTPException, http_exception_handler)
    @app.exception_handler(HTTPException)
    @app.exception_handler(StarletteHTTPException)
    async def localized_error(request, exc):
        localized = HTTPException(status_code=exc.status_code, detail=ui(exc.detail), headers=exc.headers)
        return await previous_error(request, localized)
