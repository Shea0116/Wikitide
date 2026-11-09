"""Configure DeepSeek locally without printing or putting its key in shell history."""
import getpass
import argparse
import json
import os
import tempfile
import sys
from pathlib import Path

def main(argv=()):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--web-search', action='store_true', help='Configure Tavily; keep the existing DeepSeek key')
    args = parser.parse_args(argv)
    path = Path(os.getenv('KB_EXPLORE_CONFIG', Path.home() / '.config/wikitide/explore-config.json')).expanduser()
    try:
        config = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(config, dict): raise ValueError('Invalid config')
    except (OSError, ValueError) as exc:
        raise SystemExit('现有配置无法读取，未修改，请先检查配置文件。') from exc
    service = 'Tavily' if args.web_search else 'DeepSeek'
    key = getpass.getpass(service + ' API Key（输入不显示）: ').strip()
    if not key:
        raise SystemExit('未输入密钥，现有配置未修改。')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    config['tavily_api_key' if args.web_search else 'api_key'] = key
    config.setdefault('model', 'deepseek-flash')
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.explore-config-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(config, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    print(f'密钥已保存：{path}')
    print('重启站点后生效；不要提交或分享该配置文件。')


if __name__ == '__main__':
    main(sys.argv[1:])
