"""Run the import and version-diff demo using synthetic data only, on loopback."""
import os
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parent.parent
ROOT = BASE/'work/imports-preview'
ROOT.mkdir(parents=True, exist_ok=True)
(ROOT/'content').mkdir(exist_ok=True)
os.environ['KB_DB_PATH'] = str(ROOT/'site.db')
os.environ['KB_CONTENT_ROOT'] = str(ROOT/'content')
os.environ['KB_COOKIE_SECURE'] = '0'
os.environ['KB_ALLOWED_HOSTS'] = '127.0.0.1,localhost,testserver'
os.environ['KB_EXPLORE_CONFIG'] = str(ROOT/'disabled-config.json')
sys.path.insert(0, str(BASE))
from app import app
from storage import connect, create_user, digest, save_note
from features import add_note
import time

with connect(ROOT/'site.db') as db:
    if not db.execute("SELECT 1 FROM users WHERE username='importer'").fetchone():
        create_user(db, 'importer', 'imports-preview-123', 'admin')
        user_id = db.execute("SELECT id FROM users WHERE username='importer'").fetchone()[0]
        old = '# 导入说明\n\n原始段落：检查文件格式。\n\n- Markdown\n- PDF\n'
        note = add_note(db, 'Developer/demo/imports.md', old, '用于检查 Git 风格差异的虚构示例', '私人笔记 / 演示', 'private', 'importer')
        save_note(db, note, '# 导入说明\n\n更新段落：先预览，再确认入库。\n\n- Markdown\n- PDF\n- DOCX\n\n保存失败时保留输入。\n', 'web', 'importer')
        now = int(time.time())
        team = db.execute("INSERT INTO teams(name,description,created_at) VALUES('导入演示空间','仅包含虚构资料',?)", (now,)).lastrowid
        db.execute("INSERT INTO team_members VALUES(?,?,'owner',?)", (team, user_id, now))
        source = '# 示例资料\n\n这是一份用于预览工作台的虚构文档。\n\n| 格式 | 状态 |\n| --- | --- |\n| Markdown | 可预览 |\n'
        raw = source.encode()
        import hashlib
        db.execute("""INSERT INTO document_imports(user_id,filename,file_sha,size,original,status,title,body,warnings,created_at,updated_at)
                      VALUES(?,?,?,?,?,'ready',?,?,?, ?,?)""", (user_id, '示例资料.md', hashlib.sha256(raw).hexdigest(), len(raw), raw,
                      '示例资料', source, '["此为演示资料，不包含真实笔记。"]', now, now))

if __name__ == '__main__':
    import uvicorn
    print('预览：http://127.0.0.1:8772/imports；虚构管理员 importer / imports-preview-123', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=8772)
