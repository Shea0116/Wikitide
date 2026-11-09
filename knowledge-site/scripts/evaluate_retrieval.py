"""Compare fixed retrieval and real bounded retrieval decisions on public notes."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from agent_migrations import migrate_agent
from evaluate_knowledge import load_public_notes
from explore import DeepSeek, source_snapshot, sources_valid
from explore_search import ExploreIndex, LocalEmbeddings, initialize_explore
from knowledge_retrieval import (RetrievalRun, RULES, ACTIONS, PROMPT_VERSION, TOOL_VERSION,
                                 MAX_DECISIONS, MAX_SEARCHES, MAX_READS, initialize_retrieval, combined_usage)
from knowledge_scope import read_source_chunk, selection_version
from storage import connect, create_user, initialize

DEFAULT_CASES = ['read-05', 'research-03', 'research-04', 'research-07', 'research-09', 'draft-07', 'draft-10']


def offered(rows):
    result, seen, remaining = [], set(), 7000
    for row in rows:
        key = (row['note_id'], row['start_line'], row['end_line'])
        length = len(row['text'][:2200])
        if key in seen or length > remaining:
            continue
        seen.add(key)
        result.append(row)
        remaining -= length
        if len(result) >= 8:
            break
    return result


def score(case, rows):
    hits = [any(row['path'] == evidence['path'] and evidence['section_contains'] in row['section'] for row in rows)
            for evidence in case['evidence']]
    return dict(found=sum(hits), total=len(hits), complete=all(hits),
                sources=[dict(path=row['path'], section=row['section'], start_line=row['start_line'], end_line=row['end_line']) for row in rows])


def run(args):
    dataset = json.loads(args.dataset.read_text())
    ids = args.case_id or DEFAULT_CASES
    cases = [case for case in dataset['cases'] if case['id'] in ids]
    if {case['id'] for case in cases} != set(ids):
        raise ValueError('Unknown case ID')
    provider = DeepSeek()
    if not provider.ready:
        raise ValueError('Configure the existing provider before running real decisions')
    results, usages = [], []
    with tempfile.TemporaryDirectory(prefix='wikitide-retrieval-eval-') as folder:
        root = Path(folder)
        db_path = root / 'site.db'
        initialize(db_path, root / 'content')
        with connect(db_path) as db:
            initialize_explore(db)
            initialize_retrieval(db)
            versions = load_public_notes(db, args.source_root)
            create_user(db, 'evaluation', 'temporary-evaluation-password', 'reader')
            uid = db.execute('SELECT id FROM users').fetchone()[0]
            migrate_agent(db)
            index = ExploreIndex(LocalEmbeddings(args.embedding_model))
            index.sync(db)
            db.commit()
            if args.embedding_model:
                index.build_vectors(db)
            chunk_count = db.execute('SELECT count(*) FROM explore_chunks').fetchone()[0]

        def search(query):
            with connect(db_path) as db:
                rows, _ = index.search(db, query, limit=6, per_note_limit=6)
                return rows

        def guard(refs):
            with connect(db_path) as db:
                if not sources_valid(db, refs):
                    raise ValueError('Public source changed during evaluation')

        def read(row, direction):
            with connect(db_path) as db:
                return read_source_chunk(db, row, direction)

        for case in cases:
            with connect(db_path) as db:
                sid = 'evaluation-' + case['id']
                db.execute('INSERT INTO explore_sessions(id,user_id,title,created_at,updated_at) VALUES(?,?,?,0,0)', (sid, uid, case['id']))
                seed, mode = index.search(db, case['question'], limit=4)
                single, _ = index.search(db, case['question'], limit=8)
            fixed, fixed_calls = list(seed), 1
            for suffix in (' 原理 限制', ' 示例 版本 差异'):
                if len(offered(fixed)) >= 8:
                    break
                fixed.extend(search(case['question'] + suffix))
                fixed_calls += 1
            started = time.perf_counter()
            runner = RetrievalRun(connect, db_path, uid, sid, selection_version([]), provider, seed,
                                  lambda row, number: source_snapshot(row, number), guard, search, read)
            try:
                adaptive = runner.execute(case['question'])
                runner.finish('succeeded')
            except BaseException:
                runner.finish('failed')
                raise
            usages.extend(runner.usages)
            item = dict(id=case['id'], mode=mode, single=score(case, offered(single)),
                        fixed=score(case, offered(fixed)), fixed_search_calls=fixed_calls,
                        adaptive=score(case, offered(adaptive)), trace=runner.info(), duration_ms=round((time.perf_counter() - started) * 1000))
            results.append(item)
            print(json.dumps(dict(id=item['id'], single=item['single']['found'], fixed=item['fixed']['found'],
                                  adaptive=item['adaptive']['found'], total=item['adaptive']['total'],
                                  stop_reason=runner.stop_reason, model_calls=runner.model_calls), ensure_ascii=False), flush=True)
    summary = {}
    for name in ('single', 'fixed', 'adaptive'):
        summary[name] = dict(found=sum(item[name]['found'] for item in results), total=sum(item[name]['total'] for item in results),
                             complete=sum(item[name]['complete'] for item in results), cases=len(results))
    summary.update(model_calls=sum(item['trace']['model_calls'] for item in results), usage=combined_usage(usages))
    return dict(schema_version=1, created_at=datetime.now(timezone.utc).isoformat(),
                dataset_sha256=hashlib.sha256(args.dataset.read_bytes()).hexdigest(), model=provider.model,
                prompt_version=PROMPT_VERSION, prompt_sha256=hashlib.sha256(RULES.encode()).hexdigest(),
                tool_version=TOOL_VERSION, tool_schema_sha256=hashlib.sha256(json.dumps(ACTIONS.json_schema(), sort_keys=True).encode()).hexdigest(),
                limits=dict(decisions=MAX_DECISIONS, searches=MAX_SEARCHES, reads=MAX_READS, final_sources=8, excerpt_characters=7000),
                corpus=dict(documents=len(versions), chunks=chunk_count, versions=versions),
                summary=summary, results=results,
                limitations=['选择了已知漏检开发案例，独立保留集尚待补充。', '三种方案均按最多八条片段、7000 字符摘录评测章节覆盖，语义支持度仍需人工评估。',
                             '单次检索使用一次搜索，固定补查与动态补查均最多三次搜索；动态方案额外使用模型决策和最多两次原文读取。',
                             '本报告评测检索执行器，完整学习和写作任务的成功率另行验证。'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real', action='store_true', help='Explicitly permit billable model decisions on public notes')
    parser.add_argument('--source-root', type=Path, default=BASE.parent)
    parser.add_argument('--dataset', type=Path, default=BASE / 'tests/fixtures/knowledge_tasks.json')
    parser.add_argument('--embedding-model', type=Path)
    parser.add_argument('--case-id', action='append')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.real:
        parser.error('Use --real to run billable model decisions')
    report = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report['summary'], ensure_ascii=False))
    return int(any(item['trace']['stop_reason'] == 'provider_or_budget_error' for item in report['results']))


if __name__ == '__main__':
    raise SystemExit(main())
