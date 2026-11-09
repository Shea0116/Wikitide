"""Prepare an isolated Mac App Store project; optionally build a local preview.

No upload, developer-account changes, signing-key access or payment is performed.
The website/Sparkle project and its published artifacts are left untouched.
"""
import argparse
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    return subprocess.run(['rtk', 'proxy', *map(str, args)], check=True, **kwargs)


def store_project(project):
    """Remove Sparkle build references and enable native store compilation."""
    objects = project['objects']
    removed = {key for key, obj in objects.items()
               if obj.get('path', '').endswith('Sparkle.framework')}
    removed |= {key for key, obj in objects.items()
                if obj.get('fileRef') in removed}
    for key in removed:
        del objects[key]
    for obj in objects.values():
        for key, value in list(obj.items()):
            if isinstance(value, list):
                obj[key] = [item for item in value
                            if not isinstance(item, str) or item not in removed]
        if obj.get('isa') == 'XCBuildConfiguration':
            settings = obj.setdefault('buildSettings', {})
            flags = settings.get('SWIFT_ACTIVE_COMPILATION_CONDITIONS', '$(inherited)')
            if isinstance(flags, list):
                flags = ' '.join(flags)
            settings['SWIFT_ACTIVE_COMPILATION_CONDITIONS'] = flags + ' APP_STORE'
    return project


def prepare():
    parent = ROOT / '.tools/mac-app-store'
    parent.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix='source-', dir=parent))
    # Copy only build inputs, excluding website packages, signing keys, SDKs,
    # published artifacts and generated Xcode/package caches.
    ignored = shutil.ignore_patterns('ephemeral', '.build', 'build', '.swiftpm',
                                     'xcuserdata', '.DS_Store')
    for name in ('lib', 'assets', 'macos'):
        shutil.copytree(ROOT / name, destination / name, ignore=ignored)
    for name in ('pubspec.yaml', 'pubspec.lock', 'analysis_options.yaml'):
        shutil.copy2(ROOT / name, destination / name)

    pbx = destination / 'macos/Runner.xcodeproj/project.pbxproj'
    converted = run('plutil', '-convert', 'xml1', '-o', '-', pbx,
                    capture_output=True).stdout
    pbx.write_bytes(plistlib.dumps(store_project(plistlib.loads(converted))))

    runner = destination / 'macos/Runner'
    info_path = runner / 'Info.plist'
    info = plistlib.loads(info_path.read_bytes())
    for key in list(info):
        if key.startswith('SU'):
            del info[key]
    info['LSApplicationCategoryType'] = 'public.app-category.productivity'
    info['CFBundleDisplayName'] = '识汐 WikiTide'
    info_path.write_bytes(plistlib.dumps(info))

    for name in ('DebugProfile.entitlements', 'Release.entitlements'):
        path = runner / name
        entitlements = plistlib.loads(path.read_bytes())
        key = 'com.apple.security.temporary-exception.mach-lookup.global-name'
        remaining = [value for value in entitlements.get(key, [])
                     if value not in ('$(PRODUCT_BUNDLE_IDENTIFIER)-spks',
                                      '$(PRODUCT_BUNDLE_IDENTIFIER)-spki')]
        if remaining:
            entitlements[key] = remaining
        else:
            entitlements.pop(key, None)
        path.write_bytes(plistlib.dumps(entitlements))

    config = runner / 'Configs/AppInfo.xcconfig'
    config.write_text(''.join(line for line in config.read_text().splitlines(True)
                              if 'sparkle' not in line.lower()
                              and 'WIKITIDE_UPDATE_PUBLIC_KEY' not in line))
    menu_path = runner / 'Base.lproj/MainMenu.xib'
    menu = ET.parse(menu_path)
    for items in menu.iter('items'):
        for item in list(items):
            if item.find(".//action[@selector='checkForUpdates:']") is not None:
                items.remove(item)
    menu.write(menu_path, encoding='utf-8', xml_declaration=True)
    return destination


def verify(app):
    """Check actual bundle contents, linkage, sandbox and signature."""
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    if any(key.startswith('SU') for key in info):
        raise SystemExit('Store bundle still contains Sparkle configuration.')
    if info.get('LSApplicationCategoryType') != 'public.app-category.productivity':
        raise SystemExit('Store bundle is missing its application category.')
    # Flutter's ink_sparkle.frag is a Material shader, unrelated to the updater.
    if any(path.name.lower() == 'sparkle.framework' for path in app.rglob('*')):
        raise SystemExit('Store bundle still embeds Sparkle files.')
    executable = app / 'Contents/MacOS' / info['CFBundleExecutable']
    linkage = run('otool', '-L', executable, capture_output=True, text=True).stdout
    if 'sparkle' in linkage.lower():
        raise SystemExit('Store executable still links Sparkle.')
    run('codesign', '--verify', '--deep', '--strict', app)
    signed = run('codesign', '-d', '--entitlements', ':-', app,
                 capture_output=True).stdout
    entitlements = plistlib.loads(signed)
    for key in ('com.apple.security.app-sandbox',
                'com.apple.security.network.client',
                'com.apple.security.files.user-selected.read-write'):
        if entitlements.get(key) is not True:
            raise SystemExit('Required sandbox entitlement missing: ' + key)
    if any('-spks' in str(value) or '-spki' in str(value)
           for value in entitlements.values()):
        raise SystemExit('Store bundle still requests Sparkle helper exceptions.')
    print('Verified local store preview: signature, sandbox, no Sparkle linkage/files/settings.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true',
                        help='Generate a configured Xcode project without compiling')
    parser.add_argument('--check-app', type=Path,
                        help='Verify an existing store preview or signed app')
    args = parser.parse_args()
    if args.check_app:
        verify(args.check_app.resolve())
        return
    destination = prepare()
    print('Isolated source: ' + str(destination), flush=True)
    lock = (destination / 'pubspec.lock').read_bytes()
    run('flutter', 'pub', 'get', '--offline', cwd=destination)
    if (destination / 'pubspec.lock').read_bytes() != lock:
        raise SystemExit('Dependency lock changed; review dependencies before preparing a store build.')
    command = ['flutter', 'build', 'macos', '--release', '--no-pub',
               '--dart-define=WIKITIDE_APP_STORE=true']
    if args.prepare_only:
        command.append('--config-only')
    run(*command, cwd=destination)
    if not args.prepare_only:
        verify(destination / 'build/macos/Build/Products/Release/WikiTide.app')
    print('Workspace for later distribution signing/archive: ' +
          str(destination / 'macos/Runner.xcworkspace'))
    print('Local preview only. Apple membership, distribution signing and App Store review are still required.')


if __name__ == '__main__':
    main()
