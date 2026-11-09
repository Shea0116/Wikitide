"""Build an Android APK using a project-local SDK when available."""
import argparse
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', action='store_true', help='Requires android/key.properties')
    parser.add_argument('--server', help='Override the default HTTPS WikiTide server')
    args = parser.parse_args()
    environment = dict(os.environ)
    sdk = ROOT / '.tools/android'
    if sdk.is_dir():
        environment.update(ANDROID_HOME=str(sdk), ANDROID_SDK_ROOT=str(sdk))
    if args.release and not (ROOT / 'android/key.properties').exists():
        parser.error('Configure android/key.properties before building a release APK')
    command = ['rtk', 'flutter', '--no-version-check', 'build', 'apk', '--release' if args.release else '--debug', '--no-pub']
    if args.server:
        command.append('--dart-define=WIKITIDE_SERVER=' + args.server)
    raise SystemExit(subprocess.call(command, cwd=ROOT, env=environment))


if __name__ == '__main__':
    main()
