"""Verify a prepared release, including rejection of tampered files."""
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from release_macos import PREFIX, ROOT, SIGNING_KEY
from setup_macos_updater import setup

folder = ROOT / 'artifacts/macos-updates'
feed = folder / 'appcast.xml'
tool = setup() / 'bin/sign_update'
ns = '{http://www.andymatuschak.org/xml-namespaces/sparkle}'


def verify(path, signature=None):
    return subprocess.run(['rtk', 'proxy', str(tool), '--ed-key-file', str(SIGNING_KEY),
        '--verify', str(path), *([signature] if signature else [])], capture_output=True).returncode == 0


assert verify(feed), 'Feed signature invalid'
items = ET.parse(feed).findall('./channel/item')
assert items, 'Feed empty'
with tempfile.TemporaryDirectory() as temporary:
    temporary = Path(temporary)
    for item in items:
        enclosure = item.find('enclosure')
        assert enclosure is not None
        assert enclosure.attrib['url'].startswith(PREFIX)
        archive = folder / enclosure.attrib['url'].removeprefix(PREFIX)
        signature = enclosure.attrib[ns+'edSignature']
        assert archive.stat().st_size == int(enclosure.attrib['length'])
        assert verify(archive, signature), 'Archive signature invalid'
        changed = temporary / archive.name
        data = bytearray(archive.read_bytes()); data[-1] ^= 1; changed.write_bytes(data)
        assert not verify(changed, signature), 'Tampered archive was accepted'
    changed = temporary/'appcast.xml'
    changed.write_bytes(feed.read_bytes().replace(b'<title>WikiTide</title>', b'<title>Modified</title>', 1))
    assert not verify(changed), 'Tampered update list was accepted'
print('Release signatures verified; tampered update archive and feed both rejected.')
