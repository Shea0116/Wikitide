"""Loopback-only team demo with synthetic accounts; never imports the personal notebook."""
import argparse
import os
from pathlib import Path
import sys
import time

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8769)
    parser.add_argument('--root', type=Path, default=BASE / 'work/teams-preview')
    args = parser.parse_args()
    root = args.root.resolve()
    marker = root / '.teams-preview'
    if (root / 'site.db').exists() and not marker.exists():
        parser.error('Refusing to use a database not created by this preview')
    if not (BASE / 'static/teams/app.js').is_file():
        parser.error('Build team-ui with npm run build first')
    root.mkdir(parents=True, exist_ok=True)
    marker.touch()
    content = root / 'content'
    content.mkdir(exist_ok=True)
    os.environ.update(KB_DB_PATH=str(root / 'site.db'), KB_CONTENT_ROOT=str(content), KB_ALLOWED_HOSTS='127.0.0.1,localhost', KB_COOKIE_SECURE='0',
                      KB_EXPLORE_CONFIG=str(root / 'disabled-explore-config.json'), DEEPSEEK_API_KEY='', TAVILY_API_KEY='')
    from app import app
    from storage import connect, create_user, digest
    with connect(root / 'site.db') as db:
        if not db.execute('SELECT 1 FROM users').fetchone():
            for name in ['alice', 'bob', 'carol']:
                create_user(db, name, 'teams-preview-123', 'reader')
            alice, bob, carol = [db.execute('SELECT id FROM users WHERE username=?', (name,)).fetchone()[0] for name in ['alice', 'bob', 'carol']]
            now = int(time.time())
            tid = db.execute('INSERT INTO teams(name,description,created_at) VALUES(?,?,?)', ('产品研发手记', '把讨论变成共识，把经验留给下一位同伴。', now)).lastrowid
            db.executemany('INSERT INTO team_members VALUES(?,?,?,?)', [(tid, alice, 'owner', now), (tid, bob, 'editor', now)])
            body = '# 欢迎来到团队空间\n\n这里是由示例数据组成的本地演示。\n\n## 我们的协作约定\n\n- 每次分享写清背景与结论。\n- 修改前先阅读，保存时尊重其他成员的新版本。\n- 邀请同伴一起完善知识。\n\n> 用 alice 管理团队，用 bob 体验编辑，用 carol 接受邀请。'
            doc = db.execute('''INSERT INTO team_documents(team_id,title,body,body_sha,updated_by,created_at,updated_at)
                                VALUES(?,?,?,?,?,?,?)''', (tid, '从一次共同的记录开始', body, digest(body), alice, now, now)).lastrowid
            db.execute('INSERT INTO team_document_revisions(document_id,version,title,body,actor_id,created_at) VALUES(?,1,?,?,?,?)', (doc, '从一次共同的记录开始', body, alice, now))
            db.execute('INSERT INTO team_invitations(team_id,user_id,invited_by,role,created_at,expires_at) VALUES(?,?,?,?,?,?)', (tid, carol, alice, 'reader', now, now + 7 * 86400))
            db.execute('INSERT INTO team_events(team_id,actor_id,action,detail,created_at) VALUES(?,?,?,?,?)', (tid, alice, 'team.created', '创建产品研发手记，共同的第一步', now))
        # A separate synthetic site administrator; never import/reset the real shea account.
        if not db.execute("SELECT 1 FROM users WHERE username='shea'").fetchone():
            create_user(db, 'shea', 'teams-preview-123', 'admin')
    print(f'Team preview: http://127.0.0.1:{args.port}/teams', flush=True)
    print('Synthetic accounts: alice / bob / carol / shea (site admin); password: teams-preview-123', flush=True)
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
