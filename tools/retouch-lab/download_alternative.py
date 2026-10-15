"""Download the selected research APK and require its recorded SHA-256."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import re
from urllib.parse import urlparse

import requests

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("app")
parser.add_argument("output", type=Path)
args = parser.parse_args()
specs = json.loads(Path(__file__).with_name("alternative-apps.json").read_text())
spec = next(row for row in specs if row["name"] == args.app)
session = requests.Session()
page = session.get(spec["source_page"], timeout=(20, 60))
page.raise_for_status()
links = [html.unescape(value) for value in re.findall(r"href=[\"']([^\"']+)", page.text)
         if ".apk" in value.lower() and urlparse(html.unescape(value)).hostname in ("files.5modapk.com", "cloud.leeapks.com")]
if not links:
    raise SystemExit("No APK file link found on the recorded download page")
digest = hashlib.sha256()
size = 0
try:
    with session.get(links[0], headers={"Referer": spec["source_page"]}, stream=True, timeout=(20, 120)) as response:
        response.raise_for_status()
        with args.output.open("wb") as target:
            for chunk in response.iter_content(1024 * 1024):
                if not size and not chunk.startswith(b"PK"):
                    raise ValueError("The download is not an APK/ZIP")
                size += len(chunk)
                if size > 250 * 1024 * 1024:
                    raise ValueError("APK exceeds the download limit")
                digest.update(chunk)
                target.write(chunk)
    if digest.hexdigest() != spec["sha256"]:
        raise ValueError("APK SHA-256 differs from the inspected file")
except Exception:
    args.output.unlink(missing_ok=True)
    raise
print(json.dumps({"app": args.app, "bytes": size, "sha256": digest.hexdigest()}))
