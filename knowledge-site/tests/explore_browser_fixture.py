"""Loopback-only UI regression fixture. All articles and model answers are synthetic."""
import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


def main():
    temporary = tempfile.TemporaryDirectory(prefix='explore-ui-')
    root = Path(temporary.name)
    os.environ['KB_DB_PATH'] = str(root / 'site.db')
    os.environ['KB_CONTENT_ROOT'] = str(root / 'content')
    os.environ['KB_EXPLORE_EMBEDDING_MODEL'] = ''
    from fastapi import HTTPException
    from app import app
    from features import add_note
    from storage import connect, create_user

    class SyntheticProvider:
        ready = True
        def answer(self, messages, thinking=False):
            import json
            question = json.loads(messages[-1]['content'])['question']
            if '故障' in question:
                raise HTTPException(504, '测试：上游超时，问题已保留。')
            web = [source for source in json.loads(messages[-1]['content'])['sources'] if source.get('kind') == 'web']
            if web:
                return '这是 **模拟联网回归测试**，未实际联网或调用 DeepSeek。来源应显示网页标记。[' + str(web[0]['number']) + ']'
            return '这是 **UI 回归测试响应**，未调用 DeepSeek。\n\n闭包保留词法作用域，可以使用函数式更新。[1]\n\n```js\nsetCount(previous => previous + 1);\n```'

        def stream_answer(self, messages, thinking=False):
            import time
            text = self.answer(messages, thinking)
            for index in range(0, len(text), 8):
                yield text[index:index + 8]
                time.sleep(.03)

    class SyntheticSearch:
        ready = True
        def search(self, query):
            return [dict(kind='web', title='模拟网页来源 · React 官方文档', url='https://react.dev/reference/react',
                         section='react.dev', excerpt='模拟网页摘要，不代表实际检索结果。', retrieved_at='2026-10-18T00:00:00+00:00')]

    app.state.explore_provider = SyntheticProvider()
    app.state.explore_web = SyntheticSearch()
    with connect(root / 'site.db') as db:
        create_user(db, 'tester', 'test-explore-123', 'reader')
        add_note(db, 'Frontend/React测试.md', '# React 测试文章\n\n## 闭包与状态\n\n闭包保留词法作用域，可以使用函数式更新读取最新状态。\n\n```js\nsetCount(previous => previous + 1);\n```',
                 category='Frontend / React', visibility='public')
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=int(os.getenv('EXPLORE_FIXTURE_PORT', '8769')))
    temporary.cleanup()


if __name__ == '__main__':
    main()
