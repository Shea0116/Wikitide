"""Install the pinned official Sparkle distribution, verified before extraction."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '2.9.6'
SHA256 = '52bf9e88cdd972fc0c81501377a880e90d47031bd8ca5462488f843e2609e192'
ASSET = 'https://api.github.com/repos/sparkle-project/Sparkle/releases/assets/517328209'
TOOLS = ROOT / '.tools/sparkle'


def setup(archive=None):
    marker = TOOLS / '.verified-version'
    if marker.exists() and marker.read_text().strip() == VERSION + ':' + SHA256 and (TOOLS / 'Sparkle.framework/Sparkle').exists():
        return TOOLS
    TOOLS.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=TOOLS.parent, prefix='sparkle-') as temporary:
        temporary = Path(temporary)
        source = Path(archive) if archive else temporary / 'Sparkle.tar.xz'
        if not archive:
            subprocess.run(['rtk', 'proxy', 'curl', '--fail', '--location', '--silent', '--show-error',
                '--connect-timeout', '10', '--max-time', '120', '--header', 'Accept: application/octet-stream',
                ASSET, '--output', str(source)], check=True)
        if hashlib.sha256(source.read_bytes()).hexdigest() != SHA256:
            raise SystemExit('Sparkle archive checksum does not match the pinned official release.')
        extracted = temporary / 'distribution'
        extracted.mkdir()
        subprocess.run(['rtk', 'proxy', 'tar', '-xf', str(source), '-C', str(extracted)], check=True)
        if TOOLS.exists(): shutil.rmtree(TOOLS)
        extracted.rename(TOOLS)
        marker.write_text(VERSION + ':' + SHA256 + '\n')
    print('Verified official Sparkle ' + VERSION)
    return TOOLS


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', help='Use an already downloaded official archive; checksum is still required')
    setup(parser.parse_args().archive)
