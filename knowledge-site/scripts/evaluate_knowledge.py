"""Reproduce a public-note retrieval baseline in a disposable database."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

from fastapi import HTTPException

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from explore_search import ExploreIndex, LocalEmbeddings, INDEX_VERSION, initialize_explore, public_path, split_article
from prepare_content import safe_path, walk_catalog
from storage import connect, digest, initialize


def percentile(values, fraction):
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * fraction))], 2) if ordered else None


def load_public_notes(db, source_root):
    versions = {}
    records = [record for record in walk_catalog(source_root, json.loads((source_root / 'categories.json').read_text()), [], [], skip_missing=True)
               if public_path(record['path'])]
    for record in records:
        path = source_root / record['path']
        if not safe_path(source_root, path):
            raise ValueError('Unsafe public source')
        body = path.read_text()
        versions[record['path']] = digest(body)
        db.execute('''INSERT INTO notes(path,title,summary,category,visibility,body,body_sha,updated_at)
            VALUES(?,?,?,?,?,?,?,0)''', (record['path'], record['title'], record['summary'], record['category'], 'public', body, digest(body)))
    return versions


def run(source_root, dataset, *, embedding_model=None, model_samples=0):
    data = json.loads(dataset.read_text())
    cases, results, source_versions = data['cases'], [], {}
    sample_positions = {int(i * len(cases) / model_samples) for i in range(model_samples)}
    if len({case['id'] for case in cases}) != len(cases):
        raise ValueError('Duplicate case IDs')
    with tempfile.TemporaryDirectory(prefix='wikitide-knowledge-eval-') as folder:
        root = Path(folder)
        db_path = root / 'site.db'
        initialize(db_path, root / 'content')
        with connect(db_path) as db:
            initialize_explore(db)
            source_versions = load_public_notes(db, source_root)
            for case in cases:
                for evidence in case['evidence']:
                    row = db.execute('SELECT body FROM notes WHERE path=?', (evidence['path'],)).fetchone()
                    if row is None or not any(evidence['section_contains'] in chunk['section'] for chunk in split_article(row['body'])):
                        raise ValueError('Missing evidence target in ' + case['id'])
            index = ExploreIndex(LocalEmbeddings(embedding_model))
            index.sync(db)
            db.commit()
            if embedding_model:
                index.build_vectors(db)
            chunks = db.execute('SELECT count(*) FROM explore_chunks').fetchone()[0]
            provider = None
            if model_samples:
                from explore import DeepSeek
                provider = DeepSeek()
                if not provider.ready:
                    raise ValueError('Model samples require a configured provider')
            for position, case in enumerate(cases):
                started = time.perf_counter()
                rows, mode = index.search(db, case['question'], limit=5)
                elapsed = (time.perf_counter() - started) * 1000
                hits = [any(row['path'] == evidence['path'] and evidence['section_contains'] in row['section'] for row in rows)
                        for evidence in case['evidence']]
                item = dict(id=case['id'], flow=case['flow'], split=data['topic_splits'][case['topic']], mode=mode,
                            evidence_found=sum(hits), evidence_total=len(hits), complete_evidence=all(hits), retrieval_ms=round(elapsed, 2),
                            retrieved=[dict(path=row['path'], section=row['section'], start_line=row['start_line'], end_line=row['end_line']) for row in rows])
                if provider and position in sample_positions:
                    from explore import SYSTEM, source_snapshot, render_answer
                    from agent_context import legacy_messages
                    sources = [source_snapshot(row, i + 1) for i, row in enumerate(rows)]
                    usage, model_started = {}, time.perf_counter()
                    try:
                        answer = provider.answer(legacy_messages(SYSTEM, [], case['question'], '公开技术笔记', sources), usage=usage)
                        _, cited = render_answer(answer, sources)
                        item['model'] = dict(status='completed', citation_resolved=bool(cited), answer=answer,
                                             duration_ms=round((time.perf_counter() - model_started) * 1000), usage=usage,
                                             semantic_quality='requires_human_review')
                    except HTTPException as exc:
                        item['model'] = dict(status='failed', error_type=type(exc).__name__, cause_type=type(exc.__cause__).__name__)
                results.append(item)
        durations = [item['retrieval_ms'] for item in results]
        model_results = [item['model'] for item in results if 'model' in item]
        return dict(schema_version=1, created_at=datetime.now(timezone.utc).isoformat(),
                    dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(), index_version=INDEX_VERSION,
                    corpus=dict(documents=len(source_versions), chunks=chunks, versions=source_versions),
                    summary=dict(cases=len(results), evidence_recall_at_5=round(sum(item['evidence_found'] for item in results) / sum(item['evidence_total'] for item in results), 4),
                                 complete_evidence_rate=round(sum(item['complete_evidence'] for item in results) / len(results), 4),
                                 retrieval_p50_ms=round(statistics.median(durations), 2), retrieval_p95_ms=percentile(durations, .95),
                                 modes=dict(Counter(item['mode'] for item in results)), model_samples=len(model_results),
                                 model_completed=sum(item['status'] == 'completed' for item in model_results)),
                    limits=['候选任务由现有公开笔记编写，真实用户标注待补充。', '检索使用全部可解析的公开分类笔记，私密权限由独立接口测试验证。',
                            '仅执行检索和显式请求的模型样例，研究、学习、草稿完整任务成功率待后续评测。',
                            '解析出有效引用的回答仍需人工检查事实与引用支持关系。', data['split_policy']], results=results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=BASE.parent)
    parser.add_argument('--dataset', type=Path, default=BASE / 'tests/fixtures/knowledge_tasks.json')
    parser.add_argument('--embedding-model', type=Path)
    parser.add_argument('--model-samples', type=int, choices=range(0, 31), default=0)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = run(args.source_root.resolve(), args.dataset, embedding_model=args.embedding_model, model_samples=args.model_samples)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report['summary'], ensure_ascii=False))
    return 1 if report['summary']['model_completed'] < report['summary']['model_samples'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
