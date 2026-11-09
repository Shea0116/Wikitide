"""Fail a release when its public update notice is absent, stale or rewritten."""
import argparse
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from updates import load_releases, validate_upgrade


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=BASE/'release-notes.json')
    parser.add_argument('--previous', type=Path, help='Captured live release-notes.json, when one exists')
    args = parser.parse_args()
    try:
        current = load_releases(args.manifest)
        if args.previous is not None:
            validate_upgrade(current, load_releases(args.previous))
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc)+'\n')
    print(f'更新说明通过：{current[0]["id"]}，{len(current[0]["highlights"])} 条变化。')


if __name__ == '__main__': main()
