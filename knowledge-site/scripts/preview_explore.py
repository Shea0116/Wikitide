"""Loopback preview of 知识求索; import only public Backend/Frontend Markdown."""
import argparse
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8768)
    parser.add_argument('--root', type=Path, default=BASE / 'work/explore-preview')
    args = parser.parse_args()
    root = args.root.resolve()
    marker = root / '.explore-preview'
    if (root / 'site.db').exists() and not marker.exists():
        parser.error('Refusing to reuse a database not created by this preview')
    root.mkdir(parents=True, exist_ok=True); marker.touch()
    content = root / 'content'; content.mkdir(exist_ok=True)
    os.environ['KB_DB_PATH'] = str(root / 'site.db')
    os.environ['KB_CONTENT_ROOT'] = str(content)
    os.environ['KB_ALLOWED_HOSTS'] = '127.0.0.1,localhost'
    from app import app
    from storage import connect, create_user, save_note
    from features import add_note
    from explore_search import public_path
    with connect(root / 'site.db') as db:
        if not db.execute('SELECT 1 FROM users').fetchone():
            create_user(db, 'explorer', 'explorer-preview-123', 'admin')
        source_root = BASE.parent
        count = 0
        for name in ('Backend', 'Frontend'):
            for current, dirs, files in os.walk(source_root / name, followlinks=False):
                dirs[:] = [d for d in dirs if not d.startswith('.') and d.lower() != 'static'
                           and not (Path(current) / d).is_symlink()]
                for filename in files:
                    source = Path(current) / filename
                    path = source.relative_to(source_root).as_posix()
                    if source.is_symlink() or not source.resolve().is_relative_to(source_root / name) or not public_path(path):
                        continue
                    body = source.read_text(encoding='utf-8')
                    note = db.execute('SELECT * FROM notes WHERE path=?', (path,)).fetchone()
                    if note:
                        save_note(db, note, body, 'local')
                    else:
                        add_note(db, path, body, category=' / '.join(source.relative_to(source_root).parts[:-1]), visibility='public')
                    count += 1
    print(f'知识求索: http://127.0.0.1:{args.port}/explore ({count} public notes only)', flush=True)
    print('Local preview account: explorer / explorer-preview-123', flush=True)
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
