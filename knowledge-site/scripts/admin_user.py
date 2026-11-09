"""Create or reset an administrator without putting the password in a command."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage import connect, create_user, initialize, password_hash  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--content", type=Path, required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument('--promote-only', action='store_true', help='Promote an existing active account without changing its password')
    args = parser.parse_args()
    if args.promote_only:
        # Do not initialize/import notes or reset credentials during a role-only change.
        if not args.db.is_file():
            raise SystemExit('Database does not exist')
        from accounts import initialize_accounts, audit
        initialize_accounts(args.db)
        with connect(args.db) as db:
            db.execute('BEGIN IMMEDIATE')
            matches = db.execute('SELECT id,username,active FROM users WHERE lower(username)=lower(?)', (args.username,)).fetchall()
            if len(matches) != 1 or not matches[0]['active']:
                raise SystemExit('Expected exactly one existing active account; no account or credential was changed')
            account = matches[0]
            db.execute("UPDATE users SET role='admin',version=version+1 WHERE id=?", (account['id'],))
            db.execute('DELETE FROM sessions WHERE user_id=?', (account['id'],))
            audit(db, None, 'account.promoted', account['username'], '站点维护命令配置管理员；保留原密码')
        print('Administrator role assigned; password preserved; sign in again')
        return
    password = getpass.getpass("Admin password (at least 12 characters): ")
    again = getpass.getpass("Repeat password: ")
    if password != again:
        raise SystemExit("Passwords do not match")
    initialize(args.db, args.content)
    from accounts import initialize_accounts, audit
    initialize_accounts(args.db)
    with connect(args.db) as db:
        existing = db.execute("SELECT id FROM users WHERE username=?", (args.username,)).fetchone()
        if existing:
            db.execute("UPDATE users SET password_hash=?,role='admin',active=1,version=version+1 WHERE id=?", (password_hash(password), existing["id"]))
            db.execute("DELETE FROM sessions WHERE user_id=?", (existing["id"],))
            print("Administrator password reset")
        else:
            create_user(db, args.username, password, "admin")
            print("Administrator created")
        audit(db, None, 'account.promoted', args.username, '站点维护命令配置管理员及密码')


if __name__ == "__main__":
    main()

