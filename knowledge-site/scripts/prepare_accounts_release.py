"""Merge the accounts/workspaces feature into a captured production source baseline.

Only selected runtime files are emitted; notes, settings and preview data are excluded.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path


def replace_once(source, before, after):
    if source.count(before) != 1:
        raise ValueError('Production baseline changed at: ' + before[:90])
    return source.replace(before, after, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    base = Path(__file__).resolve().parent.parent
    baseline = json.loads(args.baseline.read_text())
    sources = baseline['source']
    files = {}
    app = sources['app.py']
    app = replace_once(app, 'from explore import register_explore',
                       'from explore import register_explore\nfrom teams import register_teams\nfrom accounts import register_accounts, update_account, audit, safe_next')
    app = replace_once(app, "('/api/', '/admin', '/login')", "('/api/', '/admin', '/login', '/register')")
    app = replace_once(app, 'next=next if next.startswith("/") and not next.startswith("//") else "/"', "next=safe_next(next, '/')")
    app = replace_once(app, '''        next_path = form.get("next", "/")
        if not next_path.startswith("/") or next_path.startswith("//"):
            next_path = "/"''', '''        next_path = safe_next(form.get("next", "/"), '/')''')
    app = replace_once(app, '@app.get("/admin", response_class=HTMLResponse)', '@app.get("/admin/permissions", response_class=HTMLResponse)')
    start = '    @app.post("/admin/users")'
    end = '    @app.get("/admin/n/{note_id}", response_class=HTMLResponse)'
    local_app = (base / 'app.py').read_text()
    old_block = app[app.index(start):app.index(end)]
    new_block = local_app[local_app.index(start):local_app.index(end)]
    app = replace_once(app, old_block, new_block)
    app = replace_once(app, '    return app\n', '    register_teams(app, db_path, globals())\n    register_accounts(app, db_path, globals())\n    return app\n')
    files['app.py'] = app
    storage = sources['storage.py']
    storage = replace_once(storage, '    if len(password) < 12:\n        raise ValueError("密码至少需要 12 个字符")', '    if not 12 <= len(password) <= 1024:\n        raise ValueError("密码需要 12–1024 个字符")')
    files['storage.py'] = storage
    feature_name = 'scripts/features.py' if 'scripts/features.py' in sources else 'features.py'
    files[feature_name] = replace_once(sources[feature_name], 'SELECT id,username,role,active FROM users ORDER BY username', 'SELECT id,username,role,active,version FROM users ORDER BY username')
    template = sources['templates/base.html']
    template = replace_once(template, '<span>我的</span></a>{% endif %}', '<span>管理员平台</span></a>{% endif %}')
    graph = '      <a {% if request.url.path == \'/graph\' %}aria-current="page"{% endif %} href="/graph">关系图谱</a>'
    template = replace_once(template, graph, graph + '''
      {% if user %}<a {% if request.url.path == '/teams' %}aria-current="page"{% endif %} href="/teams">团队空间</a>{% endif %}
      {% if user and user.role == 'admin' %}<a {% if request.url.path.startswith('/admin') %}aria-current="page"{% endif %} href="/admin">管理员平台</a>{% endif %}''')
    files['templates/base.html'] = template
    for name in ['teams.py', 'accounts.py', 'scripts/admin_user.py', 'templates/login.html', 'templates/admin.html',
                 'templates/register.html', 'templates/admin_dashboard.html', 'templates/teams.html',
                 'static/teams/app.js', 'static/teams/app.css', 'static/admin/app.js', 'static/admin/app.css',
                 'TEAMS.md', 'ACCOUNTS.md', 'tests/test_teams.py', 'tests/test_accounts.py']:
        files[name] = (base / name).read_text()
    if feature_name.startswith('scripts/'):
        for name in ['accounts.py', 'tests/test_teams.py', 'tests/test_accounts.py']:
            files[name] = files[name].replace('from features import ', 'from scripts.features import ')
    for name, content in files.items():
        if name.endswith('.py'):
            ast.parse(content, filename=name)
        target = args.output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    payload = {'old_root': baseline['root'], 'environment': baseline['environment'],
               'expected': {name: baseline['files'].get(name) for name in files}, 'files': files,
               'hashes': {name: hashlib.sha256(content.encode()).hexdigest() for name, content in files.items()}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    (args.output.parent / 'payload.json').write_text(json.dumps(payload, ensure_ascii=False))
    print(json.dumps({'files': list(files), 'candidate': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
