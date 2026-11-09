"""Offline, synthetic context-availability replay. Does not call a model or import app."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time
import statistics

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from agent_context import ContextPolicy, compact_json, legacy_messages
from agent_migrations import migrate_agent
from agent_service import ContextService


@contextmanager
def database(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


class ReplayProvider:
    """Deterministically preserve marked synthetic constraints, not an LLM evaluator."""
    model = 'synthetic-replay'
    calls = 0

    def summarize_context(self, payload, *, usage=None):
        self.calls += 1
        data = json.loads(payload)
        previous = data['previous_summary']
        constraints = list(previous['constraints']) if previous else []
        for message in data['messages']:
            if message['role'] == 'user':
                for marker in re.findall(r'CONSTRAINT_[A-Z_]+', message['content']):
                    if not any(marker == fact['text'] for fact in constraints):
                        constraints.append({'text': marker, 'source_message_ids': [message['id']]})
        goal = previous['goal'] if previous else {
            'text': 'React 闭包合成学习任务', 'source_message_ids': [data['messages'][0]['id']]}
        return compact_json({'schema_version': 1, 'goal': goal, 'constraints': constraints})


def replay(case,with_memory=False):
    with tempfile.TemporaryDirectory(prefix='agent-replay-') as folder:
        db_path = Path(folder) / 'replay.db'
        with database(db_path) as db:
            db.executescript('''CREATE TABLE users(id INTEGER PRIMARY KEY,active INTEGER);
                INSERT INTO users VALUES(1,1);
                CREATE TABLE explore_sessions(id TEXT PRIMARY KEY,user_id INTEGER,version INTEGER,busy_token TEXT);
                INSERT INTO explore_sessions VALUES('replay',1,0,'synthetic-lease');
                CREATE TABLE explore_messages(id INTEGER PRIMARY KEY,session_id TEXT REFERENCES explore_sessions(id)
                  ON DELETE CASCADE,role TEXT,content TEXT,sources TEXT);''')
            migrate_agent(db)
        service = ContextService(ContextPolicy(enabled=True), database, db_path, lambda db, refs, *, user_id=None: True)
        if with_memory:
            from agent_memory import MemoryService
            service.memory=MemoryService(database,db_path,True)
        provider = ReplayProvider()
        history = []
        final = legacy = None
        timings=[]
        for turn in range(case['rounds']):
            markers = ' '.join(item['marker'] for item in case['requirements'] if item['round'] == turn)
            if with_memory and markers:
                from agent_models import MemoryInput
                with database(db_path) as db:service.memory.create(db,1,MemoryInput(content=markers))
            question = f'React 闭包：第 {turn} 轮。{markers} ' + '解释示例、作用域和状态更新。' * 6
            started=time.perf_counter()
            final = service.prepare(1, 'replay', turn, 'synthetic-lease', provider,
                                    '这是合成上下文回放。', question, '公开技术笔记', [])
            timings.append((time.perf_counter()-started)*1000)
            legacy = legacy_messages('这是合成上下文回放。', history, question, '公开技术笔记', [])
            answer = '这是合成历史回答；不代表真实模型结论。' * 4
            with database(db_path) as db:
                for role, content in (('user', question), ('assistant', answer)):
                    db.execute('INSERT INTO explore_messages(session_id,role,content,sources) VALUES(?,?,?,\'[]\')',
                               ('replay', role, content))
                    history.append({'role': role, 'content': content})
                db.execute('UPDATE explore_sessions SET version=version+1 WHERE id=\'replay\'')
        expected = [item['marker'] for item in case['requirements']]
        current_text, legacy_text = compact_json(final.messages), compact_json(legacy)
        return {'id': case['id'], 'rounds': case['rounds'], 'expected_constraints': len(expected),
                'legacy_available': sum(marker in legacy_text for marker in expected),
                'summary_available': sum(marker in current_text for marker in expected),
                'summary_calls': provider.calls, 'final_degraded': final.info['degraded'],
                'estimated_input_tokens': final.info['estimated_input_tokens'],
                'synthetic_p50_ms':round(statistics.median(timings),2),
                'synthetic_p95_ms':round(sorted(timings)[max(0,int(len(timings)*.95)-1)],2),
                'estimator': final.info['estimator']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cases', type=Path, default=BASE / 'tests/fixtures/agent_cases.json')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    data = json.loads(args.cases.read_text())
    if data.get('schema_version') != 1 or not 1 <= len(data['cases']) <= 100:
        parser.error('Invalid replay dataset')
    for case in data['cases']:
        if not 2 <= case['rounds'] <= 50:
            parser.error('Replay rounds must be 2–50')
    results = [replay(case) for case in data['cases']]
    for case,row in zip(data['cases'],results):
        memory=replay(case,True)
        row.update(summary_memory_available=memory['summary_available'],summary_memory_calls=memory['summary_calls'],
                   summary_memory_estimated_input_tokens=memory['estimated_input_tokens'])
    report = {'evaluation': 'synthetic_context_availability', 'real_model_called': False,
              'limitation': 'Measures whether marked requirements reach the prompt; does not measure model understanding, truthfulness or billable tokens.',
              'cases': results}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n')
    print(text)
    return 0 if all(row['summary_available'] == row['expected_constraints'] == row['summary_memory_available'] and not row['final_degraded'] for row in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
