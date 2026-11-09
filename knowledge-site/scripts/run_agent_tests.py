"""Run tests with a disposable bootstrap DB before any app module is imported."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import unittest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pattern', default='test_agent*.py')
    args = parser.parse_args()
    base = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix='wikitide-agent-tests-') as folder:
        root = Path(folder)
        config = root / 'disabled.json'
        config.write_text('{}')
        os.environ.update(KB_DB_PATH=str(root / 'site.db'), KB_CONTENT_ROOT=str(root / 'content'),
                          KB_EXPLORE_CONFIG=str(config), KB_EXPLORE_EMBEDDING_MODEL='',
                          DEEPSEEK_API_KEY='', TAVILY_API_KEY='', KB_AGENT_CONTEXT_ENABLED='false',
                          KB_AGENT_MEMORY_ENABLED='false',KB_AGENT_TRANSIENT_ENABLED='false',KB_AGENT_RUNTIME_ENABLED='false',
                          KB_EXPLORE_SELECTED_ENABLED='false',
                          KB_AGENT_RETRIEVAL_ENABLED='false',
                          KB_AGENT_LEARNING_ENABLED='false',
                          KB_AGENT_ARTIFACTS_ENABLED='false',
                          HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
        sys.path.insert(0, str(base))
        suite = unittest.defaultTestLoader.discover(str(base / 'tests'), pattern=args.pattern)
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
