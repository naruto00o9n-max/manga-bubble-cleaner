"""Prepare the pinned, publisher-signed original 7.8 release and observer."""
import argparse
import hashlib
import json
import lzma
from pathlib import Path
import zipfile
import requests

ROOT = Path(__file__).parent
RELEASES = {
    'original.xapk': ('https://d.apkpure.com/b/XAPK/snapedit.app.remove?versionCode=463&nc=armeabi-v7a&sv=24', '172f15732fb9cb70bab2043b179a6e38f5da5e913de82f143710223a2ce25905'),
    'frida-server.xz': ('https://github.com/frida/frida/releases/download/16.7.19/frida-server-16.7.19-android-x86_64.xz', '4bc938db27cbe1fec859e28f6bd34d269995a719de2f10e97e28c1bdcd9416c7'),
}


def prepare(destination):
    destination.mkdir(parents=True, exist_ok=True)
    for name, (url, expected) in RELEASES.items():
        path = destination / name
        if not path.exists():
            with requests.get(url, stream=True, timeout=(20, 90)) as response:
                response.raise_for_status()
                total = 0
                with path.open('wb') as output:
                    for block in response.iter_content(1024 * 1024):
                        total += len(block)
                        if total > 250 * 1024 * 1024:
                            raise ValueError('Release exceeds download limit')
                        output.write(block)
        with path.open('rb') as downloaded:
            if hashlib.file_digest(downloaded, 'sha256').hexdigest() != expected:
                raise ValueError('Release SHA-256 mismatch: ' + name)
    with zipfile.ZipFile(destination / 'original.xapk') as archive:
        manifest = json.loads(archive.read('manifest.json'))
        if manifest['package_name'] != 'snapedit.app.remove' or str(manifest['version_code']) != '463':
            raise ValueError('Unexpected original APK identity')
        for name in ('snapedit.app.remove.apk', 'config.armeabi_v7a.apk', 'config.en.apk', 'config.mdpi.apk'):
            (destination / name).write_bytes(archive.read(name))
    spec = next(row for row in json.loads((ROOT / 'alternative-apps.json').read_text()) if row['name'] == 'snapedit')
    spec['sha256'] = hashlib.sha256((destination / 'snapedit.app.remove.apk').read_bytes()).hexdigest()
    spec['source_page'] = 'https://apkpure.com/snapedit-ai-photo-editor/snapedit.app.remove/download/7.8'
    (destination / 'original-spec.json').write_text(json.dumps([spec], indent=2))
    with lzma.open(destination / 'frida-server.xz', 'rb') as compressed:
        (destination / 'frida-server').write_bytes(compressed.read())
    print('Pinned original release and read-only observer verified')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    prepare(parser.parse_args().destination)
