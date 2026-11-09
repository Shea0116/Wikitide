"""Local synthetic data for editor, reading and learning UI regression checks."""
import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


def main():
    with tempfile.TemporaryDirectory(prefix='wikitide-improvements-') as directory:
        root = Path(directory)
        os.environ.update(KB_DB_PATH=str(root / 'site.db'), KB_CONTENT_ROOT=str(root / 'content'),
                          KB_COOKIE_SECURE='0', KB_EXPLORE_CONFIG=str(root / 'disabled.json'),
                          KB_EXPLORE_EMBEDDING_MODEL='', DEEPSEEK_API_KEY='', TAVILY_API_KEY='')
        from app import app
        from features import add_note
        from storage import connect, create_user
        import time
        with connect(root / 'site.db') as db:
            create_user(db, 'tester', 'test-editor-123', 'admin')
            create_user(db,'writer','test-editor-123','editor')
            uid=db.execute("SELECT id FROM users WHERE username='tester'").fetchone()[0]
            add_note(db, 'Frontend/章节折叠测试.md',
                     '# 编辑器体验\n\n## <span style="color:#DC143C;">**问题一：如何折叠章节？**</span>\n\n第一章正文，应当随着 H2 折叠。\n\n'
                     '### 子问题 A\n\n子章节内容，同样包含在 H2 中。\n\n```md\n## 这是代码，不是章节\n```\n\n'
                     '### 子问题 B\n\n另一段正文。\n\n## 问题二：如何恢复？\n\n点击箭头或全部展开，正文保持原样。\n\n'
                     '| 操作 | 结果 |\n| --- | --- |\n| 折叠 | 仅改变显示 |\n', category='Frontend', visibility='public')
            for index in range(25):
                note=add_note(db,f'Frontend/示例{index+1:02}.md',f'# 示例 {index+1}\n\n## 学习章节\n\n用于验证分页、学习与全文检索的合成资料。闭包与词法作用域示例 {index+1}。',category='Frontend',visibility='public')
                db.execute('INSERT INTO reading VALUES(?,?,1,0,?)',(uid,note['id'],index+1))
            now=int(time.time())
            tid=db.execute('INSERT INTO teams(name,description,created_at) VALUES(?,?,?)',('测试协作空间','全部为合成资料',now)).lastrowid
            for name,role in [('tester','owner'),('writer','editor')]:
                member=db.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()[0]
                db.execute('INSERT INTO team_members VALUES(?,?,?,?)',(tid,member,role,now))
            body='# 团队文档\n\n## 第一章\n\n团队实时编辑与标题折叠。\n\n### 子章节\n\n这里是子章节内容。\n\n## 第二章\n\n评论与版本审核。'
            from storage import digest
            did=db.execute('INSERT INTO team_documents(team_id,title,body,body_sha,updated_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(tid,'协作测试文档',body,digest(body),uid,now,now)).lastrowid
            db.execute('INSERT INTO team_document_revisions(document_id,version,title,body,actor_id,created_at) VALUES(?,1,?,?,?,?)',(did,'协作测试文档',body,uid,now))
        import uvicorn
        uvicorn.run(app, host='127.0.0.1', port=8777)


if __name__ == '__main__':
    main()
