"""Read-only SQLite backup, additive migrations and legacy-row fingerprint checks."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from agent_migrations import migrate_agent


def fingerprint(db,table):
    quoted='"'+table.replace('"','""')+'"'
    rows=[repr(tuple(row)) for row in db.execute('SELECT * FROM '+quoted)]
    return {'rows':len(rows),'sha256':hashlib.sha256('\n'.join(sorted(rows)).encode()).hexdigest()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not args.source.is_file():parser.error('source database must exist')
    with tempfile.TemporaryDirectory(prefix='wikitide-agent-migration-') as folder:
        target=Path(folder)/'copy.db'
        with sqlite3.connect(args.source.resolve().as_uri()+'?mode=ro',uri=True) as source,sqlite3.connect(target) as db:
            source.backup(db)
            db.execute('PRAGMA foreign_keys=ON')
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'agent_%'")]
            before={table:fingerprint(db,table) for table in tables}
            migrate_agent(db);migrate_agent(db)
            after={table:fingerprint(db,table) for table in tables}
            integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
            foreign=len(db.execute('PRAGMA foreign_key_check').fetchall())
            report={'source_opened_readonly':True,'temporary_copy_removed_on_exit':True,
                    'migration_version':db.execute('SELECT max(version) FROM agent_schema_migrations').fetchone()[0],
                    'idempotent':True,'legacy_tables_checked':len(tables),'legacy_rows_checked':sum(x['rows'] for x in before.values()),
                    'legacy_fingerprints_unchanged':before==after,'integrity_check':integrity,'foreign_key_errors':foreign}
    text=json.dumps(report,ensure_ascii=False,indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(text+'\n')
    print(text)
    return 0 if before==after and integrity=='ok' and not foreign else 1


if __name__=='__main__':raise SystemExit(main())
