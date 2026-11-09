"""Build the client and prepare a signed Sparkle feed. Does not upload anything."""
import argparse
import hashlib
from pathlib import Path
import plistlib
import re
import shutil
import subprocess

from setup_macos_updater import ROOT, setup

PUBLIC_KEY = 'lhtdEFUDVRCR6wRLOE68wlwZLjhDMq2TDgtlr2KbYzw='
SIGNING_KEY = ROOT / '.tools/update-signing/ed25519.key'
PREFIX = 'https://wikitide.soulshea.vip/static/updates/macos/'


def run(*args, **kwargs):
    return subprocess.run(['rtk', 'proxy', *map(str, args)], check=True, **kwargs)


def release(*, build=True):
    tools = setup()
    # Never generate replacement keys during releases: existing clients trust
    # the original key. The private bytes never leave this process's memory.
    if not SIGNING_KEY.exists() or SIGNING_KEY.stat().st_mode & 0o077:
        raise SystemExit('Restore the original signing key with mode 600 before releasing.')
    public = run('xcrun', 'swift', ROOT / 'scripts/update_public_key.swift', SIGNING_KEY, capture_output=True, text=True).stdout.strip()
    if public != PUBLIC_KEY:
        raise SystemExit('The signing key does not match this client. Restore the original key before releasing.')
    if build: run('flutter', 'build', 'macos', '--release', cwd=ROOT)
    app = ROOT / 'build/macos/Build/Products/Release/WikiTide.app'
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    version, build_number = info['CFBundleShortVersionString'], info['CFBundleVersion']
    expected = re.search(r'^version:\s*(\S+)', (ROOT / 'pubspec.yaml').read_text(), re.M)[1]
    if version + '+' + build_number != expected:
        raise SystemExit('Built app version differs from pubspec.yaml; rebuild before packaging.')
    if info.get('SUPublicEDKey') != PUBLIC_KEY or info.get('SUFeedURL') != PREFIX + 'appcast.xml':
        raise SystemExit('Built app update settings do not match this release channel.')
    if not info.get('SURequireSignedFeed') or not info.get('SUVerifyUpdateBeforeExtraction'):
        raise SystemExit('Built app must verify the feed and update archive.')
    run('codesign', '--verify', '--deep', '--strict', app)
    folder = ROOT / 'artifacts/macos-updates'
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / f'WikiTide-{version}-macOS.zip'
    if archive.exists():
        raise SystemExit('This version is already packaged. Increment version/build, or explicitly remove an unpublished local package.')
    run('ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', app, archive)
    notes = ROOT / f'release-notes/macos-{version}.html'
    if not notes.exists(): raise SystemExit('Add release notes before publishing this version.')
    shutil.copyfile(notes, archive.with_suffix('.html'))
    run(tools / 'bin/generate_appcast', '--ed-key-file', SIGNING_KEY,
        '--download-url-prefix', PREFIX, '--maximum-deltas', '0',
        '--embed-release-notes', folder)
    (folder / 'SHA256SUMS').write_text(''.join(
        hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n'
        for p in sorted(folder.glob('*.zip'))))
    shutil.copyfile(archive, ROOT / f'artifacts/WikiTide-{version}-macOS.zip')
    print('Prepared signed macOS release ' + version + ' (' + build_number + ') in ' + str(folder))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-build', action='store_true', help='Package the already verified current build')
    args = parser.parse_args()
    release(build=not args.no_build)
