"""Publish verified macOS artifacts over an existing authenticated SSH connection."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import uuid
import xml.etree.ElementTree as ET

from release_macos import PREFIX, ROOT, SIGNING_KEY, run
from setup_macos_updater import setup


REMOTE = r'''
import datetime, fcntl, hashlib, json, pathlib, re, shutil, subprocess, sys, xml.etree.ElementTree as ET
stage = pathlib.Path(sys.argv[1]); manifest = json.loads(sys.argv[2])
assert re.fullmatch(r'/tmp/wikitide-client-update-[a-f0-9]{32}', str(stage))
lock = open('/opt/wikitide/.deployment.lock','a'); fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
base = pathlib.Path('/opt/wikitide/client-updates/macos'); base.mkdir(parents=True,exist_ok=True)
backup = pathlib.Path('/opt/wikitide/backups') / ('client-updates-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
backup.mkdir(mode=0o700)
config = pathlib.Path('/etc/nginx/sites-available/wikitide')
original = config.read_bytes(); shutil.copy2(config,backup/'nginx.conf')
old_feed = (base/'appcast.xml').read_bytes() if (base/'appcast.xml').exists() else None
if old_feed is not None: (backup/'appcast.xml').write_bytes(old_feed)
changed = False
try:
    if old_feed is not None:
        ns='{http://www.andymatuschak.org/xml-namespaces/sparkle}'
        def newest(raw):
            return max(tuple(int(part) for part in node.text.split('.')) for node in ET.fromstring(raw).findall('./channel/item/'+ns+'version'))
        assert newest((stage/'appcast.xml').read_bytes())>=newest(old_feed),'Refusing to downgrade the public update feed'
    for name, expected in manifest.items():
        assert re.fullmatch(r'(?:WikiTide-[0-9.]+-macOS.zip|appcast.xml|SHA256SUMS)',name)
        raw = (stage/name).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==expected, 'Uploaded artifact checksum mismatch'
        target = base/name
        if name.endswith('.zip') and target.exists():
            assert hashlib.sha256(target.read_bytes()).hexdigest()==expected,'Published version is immutable'
        temp = base/('.'+name+'.next'); temp.write_bytes(raw);temp.chmod(0o644)
        if name != 'appcast.xml':temp.replace(target)
    # Switch the signed feed last, after every referenced archive exists.
    (base/'.appcast.xml.next').replace(base/'appcast.xml')
    text=original.decode()
    if 'location ^~ /static/updates/macos/' not in text:
        assert 'server_name wikitide.soulshea.vip;' in text and '    location / {' in text
        location="""    # WikiTide client artifacts live outside application code releases.
    location ^~ /static/updates/macos/ {
        alias /opt/wikitide/client-updates/macos/;
        autoindex off;
        add_header Cache-Control "no-cache" always;
        add_header X-Content-Type-Options "nosniff" always;
    }

"""
        config.write_text(text.replace('    location / {',location+'    location / {',1));changed=True
        subprocess.run(['nginx','-t'],check=True)
        subprocess.run(['systemctl','reload','nginx'],check=True)
    print(json.dumps({'published':True,'backup':str(backup),'files':len(manifest)}))
except BaseException:
    if old_feed is None: (base/'appcast.xml').unlink(missing_ok=True)
    else: (base/'appcast.xml').write_bytes(old_feed)
    if changed:
        config.write_bytes(original)
        subprocess.run(['nginx','-t'],check=True)
        subprocess.run(['systemctl','reload','nginx'],check=True)
    raise
finally:
    shutil.rmtree(stage)
'''


def publish(host, control, known_hosts):
    tools = setup()
    folder = ROOT / 'artifacts/macos-updates'
    feed = folder / 'appcast.xml'
    run(tools/'bin/sign_update', '--ed-key-file', SIGNING_KEY, '--verify', feed)
    names = ['appcast.xml', 'SHA256SUMS']
    tree = ET.parse(feed)
    ns = '{http://www.andymatuschak.org/xml-namespaces/sparkle}'
    for enclosure in tree.findall('./channel/item/enclosure'):
        url = enclosure.attrib['url']
        if not url.startswith(PREFIX): raise SystemExit('Unexpected update origin')
        name = url[len(PREFIX):]
        if not re.fullmatch(r'WikiTide-[0-9.]+-macOS.zip', name): raise SystemExit('Unexpected archive path')
        archive = folder/name
        if int(enclosure.attrib['length']) != archive.stat().st_size: raise SystemExit('Archive length mismatch')
        run(tools/'bin/sign_update', '--ed-key-file', SIGNING_KEY, '--verify', archive, enclosure.attrib[ns+'edSignature'])
        names.append(name)
    if len(names) == 2: raise SystemExit('Feed contains no installable update')
    manifest = {name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in names}
    options = ['-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(known_hosts)]
    if control: options += ['-o','ControlMaster=no','-o','ControlPath='+str(control)]
    stage = '/tmp/wikitide-client-update-'+uuid.uuid4().hex
    run('ssh', *options, host, shlex.join(['mkdir','-m','700',stage]))
    run('scp', *options, *(folder/name for name in names), host+':'+stage+'/')
    run('ssh', *options, host, shlex.join(['sudo','-n','python3','-c',REMOTE,stage,json.dumps(manifest)]))
    # Public endpoint must return the exact signed feed without session cookies.
    import tempfile
    with tempfile.TemporaryDirectory() as temporary:
        downloaded = Path(temporary)/'appcast.xml'
        run('curl','--fail','--silent','--show-error','--max-time','30',PREFIX+'appcast.xml','-o',downloaded)
        if downloaded.read_bytes()!=feed.read_bytes(): raise SystemExit('Public feed differs from the signed local feed; inspect before announcing release.')
    print('Public signed update feed verified: '+PREFIX+'appcast.xml')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',required=True)
    parser.add_argument('--control',type=Path)
    parser.add_argument('--known-hosts',required=True,type=Path)
    args=parser.parse_args()
    publish(args.host,args.control,args.known_hosts)
