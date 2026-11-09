"""Merge the live editor into a captured production baseline, preserving other features."""
import argparse
import ast
import hashlib
import json
from pathlib import Path


def replace_once(source, before, after):
    if source.count(before) != 1:
        raise ValueError('Baseline changed; review required at: ' + before[:90])
    return source.replace(before, after, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    base = Path(__file__).resolve().parent.parent
    baseline = json.loads(args.baseline.read_text())
    source = baseline['source']
    local = (base / 'app.py').read_text()
    app = source['app.py']
    app = replace_once(app, "asset_prefix='/asset') -> Markup:", "asset_prefix='/asset', editor_blocks=False):")
    old_return = '    return Markup(sanitize_html(MARKDOWN.renderer.render(tokens, MARKDOWN.options, {}), rewrite_url))'
    new_return = local[local.index('    def render_tokens(items):'):local.index('\n\ndef note_asset_path')].rstrip()
    app = replace_once(app, old_return, new_return)
    app = replace_once(app, '    def render_note(db, note, user):\n        return rendered_markdown(db, note, user, content_root)',
                       '    def render_note(db, note, user, **options):\n        return rendered_markdown(db, note, user, content_root, **options)')
    app = replace_once(app, '    async def security_headers(request: Request, call_next):\n        response = await call_next(request)',
                       '    async def security_headers(request: Request, call_next):\n        style_nonce = secrets.token_urlsafe(24) if re.fullmatch(r\'/n/\\d+/edit\', request.url.path) else \'\'\n        request.state.editor_style_nonce = style_nonce\n        response = await call_next(request)')
    old_csp = next(line for line in app.splitlines() if 'response.headers["Content-Security-Policy"] =' in line)
    new_csp = next(line for line in local.splitlines() if 'response.headers["Content-Security-Policy"] =' in line)
    app = replace_once(app, old_csp, '        trusted_style = f" \'nonce-{style_nonce}\'" if style_nonce else \'\'\n' + new_csp)
    feature_name = 'scripts/features.py' if 'scripts/features.py' in source else 'features.py'
    feature = source[feature_name]
    anchor = "            copy = dict(note); copy['body'] = form.get('body','')\n"
    feature = replace_once(feature, anchor, anchor + "            if form.get('blocks') == '1':\n                return JSONResponse({'blocks': render(db, copy, user, editor_blocks=True)},\n                                    headers={'Cache-Control': 'no-store'})\n")
    template = source['templates/base.html']
    import re
    template, count = re.subn(r'(/static/features\.js\?v=)[^"\s]+', r'\g<1>20261021a', template)
    assert count == 1
    assert '{% block extra_head %}' in template
    # These two files had only the known production fixes before this feature.
    known_hashes = {
        'templates/edit.html': {'9c916d8384956e60771f151b74af3a6b75e6709e5465df64a608384b77d65b79'},
        'static/features.js': {'e63daf29075b01b6c14d0a87fc219748e8baf77a3dc546b9e4c5b84ce5690d6b',
                               '74aeb535fdf1e6da1962c90e353cb4b34ab99b7dc6691658862135a6ec240a2e'},
    }
    for name, known in known_hashes.items():
        assert baseline['files'][name] in known, 'New production changes require merging: ' + name
    files = {'app.py': app, feature_name: feature, 'templates/base.html': template}
    for name in ['templates/edit.html', 'static/features.js', 'static/editor/editor.js', 'static/editor/editor.css']:
        files[name] = (base / name).read_text()
    for name, content in files.items():
        if name.endswith('.py'):
            ast.parse(content, filename=name)
        target = args.output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    payload = {'old_root': baseline['root'], 'environment': baseline['environment'],
               'expected': {name: baseline['files'].get(name) for name in files}, 'files': files,
               'hashes': {name: hashlib.sha256(content.encode()).hexdigest() for name, content in files.items()}}
    (args.output.parent / 'payload.json').write_text(json.dumps(payload, ensure_ascii=False))
    print(json.dumps({'files': list(files), 'candidate': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
