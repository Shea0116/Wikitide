"""Loopback-only context UI fixture; synthetic accounts, sources and model output."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'scripts'))


def main():
    with tempfile.TemporaryDirectory(prefix='wikitide-agent-ui-') as folder:
        root = Path(folder)
        config = root / 'disabled.json'; config.write_text('{}')
        os.environ.update(KB_DB_PATH=str(root / 'site.db'), KB_CONTENT_ROOT=str(root / 'content'),
                          KB_EXPLORE_CONFIG=str(config), DEEPSEEK_API_KEY='', TAVILY_API_KEY='',
                          KB_EXPLORE_EMBEDDING_MODEL='', KB_AGENT_CONTEXT_ENABLED='true', KB_EXPLORE_SELECTED_ENABLED='true',
                          KB_AGENT_RETRIEVAL_ENABLED='true', KB_AGENT_LEARNING_ENABLED='true', KB_AGENT_ARTIFACTS_ENABLED='true')
        from app import app
        from features import add_note
        from storage import connect, create_user
        from evaluate_agent_memory import ReplayProvider
        from agent_models import ProjectInput,MemoryInput,RunInput

        class SyntheticProvider(ReplayProvider):
            ready = True
            def answer(self, messages, thinking=False, *, max_tokens=None, usage=None):
                if usage is not None:
                    usage.update(prompt_tokens=20, completion_tokens=10)
                return '这是合成演示回答，未调用收费模型。闭包保留词法作用域。[1]'
            def assess_learning(self, messages, *, usage):
                usage.update(prompt_tokens=30, completion_tokens=20)
                return json.dumps({'verdict': 'partial', 'answer_quote': json.loads(messages[-1]['content'])['answer'][:120], 'reference_status': 'usable', 'feedback': '你说明了词法作用域，可以补充外层函数返回后仍能访问变量。',
                                   'matched_points': ['保留词法作用域'], 'missing_points': ['外层函数返回后的变量访问']}, ensure_ascii=False)
            def stream_answer(self, messages, thinking=False, **options):
                yield self.answer(messages, thinking)
            def summarize_local(self,messages,usage=None):
                return json.dumps({'schema_version':1,'goal':'合成演示学习任务','constraints':['保留代码示例']},ensure_ascii=False)
            def plan_retrieval(self, messages, *, usage):
                payload = json.loads(messages[-1]['content'])
                usage.update(prompt_tokens=8, completion_tokens=4)
                if all(action.get('initial') for action in payload['actions']):
                    return json.dumps({'action': 'search_knowledge', 'query': '闭包 词法作用域'})
                return json.dumps({'action': 'finish'})

        app.state.explore_provider = SyntheticProvider()
        with connect(root / 'site.db') as db:
            create_user(db, 'tester', 'agent-preview-123', 'editor')
            user_id = db.execute("SELECT id FROM users WHERE username='tester'").fetchone()[0]
            note = add_note(db, 'Frontend/Agent演示闭包.md', '# 闭包\n\n## 状态更新\n\n闭包保留词法作用域。函数式更新用于读取最新状态。',
                            category='Frontend', visibility='public')
            selected = add_note(db, 'Developer/合成学习资料.md', '# 合成学习资料\n\n## 闭包\n\n这是一篇合成的私密演示笔记。闭包保留词法作用域，变量可以在外层函数返回后继续访问。',
                                category='演示资料', visibility='private')
            db.execute('INSERT INTO grants(user_id,note_id,can_view,can_edit) VALUES(?,?,1,1)', (user_id, selected['id']))
            target = add_note(db, 'Developer/合成知识整理.md', '# 合成知识整理\n\n保留已有的阅读心得。', category='演示资料', visibility='private')
            db.execute('INSERT INTO grants(user_id,note_id,can_view,can_edit) VALUES(?,?,1,1)', (user_id, target['id']))
            db.execute('INSERT INTO review_cards(user_id,note_id,question,answer,anchor,body_sha,due_at) VALUES(?,?,?,?,?,?,0)',
                       (user_id, selected['id'], '闭包为什么能在外层函数返回后访问变量？', '闭包保留词法作用域，外层函数返回后仍可访问变量。', '闭包', selected['body_sha']))
            sid = 'synthetic-agent-context-session'
            db.execute('''INSERT INTO explore_sessions(id,user_id,title,note_id,created_at,updated_at,version)
                VALUES(?,?,?,NULL,1,1,12)''', (sid, user_id, 'React 闭包演示'))
            refs = [{'kind': 'note', 'number': 1, 'note_id': note['id'], 'body_sha': note['body_sha'],
                     'url': f"/n/{note['id']}", 'title': note['title'], 'path': note['path'],
                     'start_line': 3, 'end_line': 5, 'section': '状态更新', 'excerpt': '合成资料'}]
            for turn in range(12):
                for role, text, sources in (
                    ('user', f'React 闭包 第{turn}轮 ' + ('CONSTRAINT_KEEP_CODE ' if turn == 0 else '') + '合成问题。' * 25, []),
                    ('assistant', '合成历史说明。[1]' + '合成回答。' * 20, refs),
                ):
                    db.execute('INSERT INTO explore_messages(session_id,role,content,sources,created_at) VALUES(?,?,?,?,1)',
                               (sid, role, text, json.dumps(sources, ensure_ascii=False)))
            project=app.state.agent_projects.create(db,user_id,ProjectInput(title='React 闭包演示项目',goal='先给代码示例，再解释适用条件'))
            app.state.agent_memory.create(db,user_id,MemoryInput(scope='project',project_id=project['id'],content='解释概念时保留短代码示例，并核对来源。'))
            db.execute('INSERT INTO agent_session_links(session_id,project_id) VALUES(?,?)',(sid,project['id']))
            db.execute('INSERT INTO agent_project_links VALUES(?,?,?,?)',(project['id'],'session',sid,1))
            db.execute('INSERT INTO agent_project_links VALUES(?,?,?,?)',(project['id'],'note',str(note['id']),1))
            app.state.agent_runtime.create(db,user_id,RunInput(kind='research',topic='React 闭包',project_id=project['id'],idempotency_key='synthetic-demo-run'))
        import uvicorn
        # Demo-only execution. Production uses the independent agent_worker.py.
        stop=threading.Event()
        def demo_worker():
            while not stop.wait(.5):
                app.state.agent_runtime.run_once()
        worker=threading.Thread(target=demo_worker,daemon=True);worker.start()
        try:uvicorn.run(app, host='127.0.0.1', port=8781)
        finally:stop.set();worker.join(timeout=3)


if __name__ == '__main__':
    main()
