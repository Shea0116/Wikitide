"""Check interface translation coverage and keep the browser catalog in sync."""
import argparse
import json
from pathlib import Path
import re
import sys

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from i18n import CATALOG, DYNAMIC_KEYS, HAN, LocalizedEnvironment
from jinja2 import nodes


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-browser',action='store_true')
    args=parser.parse_args()
    keys=set()
    env=LocalizedEnvironment()
    for path in (BASE/'templates').glob('*.html'):
        tree=env.parse(path.read_text())
        for call in tree.find_all(nodes.Call):
            if isinstance(call.node,nodes.Name) and call.node.name=='ui' and call.args and isinstance(call.args[0],nodes.Const):
                if isinstance(call.args[0].value,str):keys.add(call.args[0].value.strip())
        for loop in tree.find_all(nodes.For):
            if any(isinstance(node,nodes.Name) and node.name=='label' for node in loop.target.find_all(nodes.Name)):
                for value in loop.iter.find_all(nodes.Const):
                    if isinstance(value.value,str) and HAN.search(value.value):keys.add(value.value.strip())
    literal=re.compile(r'(?:ui|window\.WikitideI18n)\.t\(\s*("(?:\\.|[^"\\])*")')
    for path in [*(BASE/'static').glob('*.js'),*(BASE/'team-ui/src').rglob('*.tsx'),*(BASE/'team-ui/src').rglob('*.ts')]:
        if '.test.' in path.name:continue
        source = path.read_text()
        keys.update(json.loads(match.group(1)).strip() for match in literal.finditer(source))
        if re.search(r'const t\s*=.*window\.WikitideI18n\.t', source):
            keys.update(json.loads(match.group(1)).strip() for match in re.finditer(r'\bt\(\s*("(?:\\.|[^"\\])*")', source))
    releases=json.loads((BASE/'release-notes.json').read_text())['releases']
    for release in releases:
        keys.update([release['title'],release['summary'],*release['highlights']])
    keys.update(DYNAMIC_KEYS)
    missing=sorted(key for key in keys if HAN.search(key) and key not in CATALOG and key not in {'中文','切换到中文'})
    errors=[]
    if missing:errors.append('Missing translations: '+json.dumps(missing,ensure_ascii=False))
    for key,value in CATALOG.items():
        if key!=key.strip() or not isinstance(value,str) or not value.strip():errors.append('Invalid entry: '+key)
        if set(re.findall(r'\{\d+\}',key))!=set(re.findall(r'\{\d+\}',value)):
            errors.append('Placeholder mismatch: '+key)
    browser=BASE/'static/i18n/en.js'
    generated='window.WikitideEnglish = '+json.dumps(CATALOG,ensure_ascii=False)+';\nwindow.WikitideEnglishPatterns = '+json.dumps(DYNAMIC_KEYS,ensure_ascii=False)+';\n'
    if args.write_browser:browser.write_text(generated)
    if not browser.exists() or browser.read_text()!=generated:errors.append('Browser catalog is out of date; run with --write-browser')
    if errors:
        print('\n'.join(errors));return 1
    print(f'Interface catalog checked: {len(CATALOG)} entries, {len(keys)} source labels; browser catalog matches.')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
