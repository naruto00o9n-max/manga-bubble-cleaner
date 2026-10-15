#!/usr/bin/env python3
"""Create one persistent guest profile using the user's own Android installation."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import zipfile

from androguard.core.apk import APK
from androguard.core.dex import DEX
from loguru import logger

from points import request_points


def extract_config(path):
    logger.remove()
    apk = APK(str(path))
    version = (apk.get_androidversion_name(), apk.get_androidversion_code())
    if apk.get_package() != "com.magic.retouch" or version not in (("2.3.8.0", "76"), ("2.2.4.2", "53")):
        raise ValueError("Supported inspected APKs: 2.2.4.2 (53) and 2.3.8.0 (76)")
    public_keys, seed = set(), None
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not re.fullmatch(r"classes\d*\.dex", name):
                continue
            dex = DEX(archive.read(name))
            # Locate the TX013 public transport key verified in both inspected APKs.
            # This is a public-key fingerprint, not a user credential.
            for value in dex.get_strings():
                if value.startswith("MIGf"):
                    try:
                        fingerprint = hashlib.sha256(base64.b64decode(value, validate=True)).hexdigest()
                    except ValueError:
                        continue
                    if fingerprint == "f95a3f2c19222c3db64616135353e797e6bc3033e55308eacd61ae77158eda3c":
                        public_keys.add(value)
            for cls in dex.get_classes():
                if cls.get_name() == "Lcom/energysh/points/api/b;":
                    for method in cls.get_methods():
                        if method.get_name() == "c" and "Ljava/lang/String;" in method.get_descriptor():
                            for instruction in method.get_instructions():
                                if instruction.get_name().startswith("const-string"):
                                    seed = dex.CM.get_string(instruction.get_ref_kind())
                                    break
    protocol = "v5" if version[0] == "2.3.8.0" else "v3"
    if len(public_keys) != 1 or (protocol == "v5" and not seed):
        raise ValueError("The APK protocol configuration differs from the inspected build")
    public_key = public_keys.pop()
    base64.b64decode(public_key, validate=True)
    return {"protocol": protocol, "base_url": "https://aicup-v2.magicutapp.com/", "app_id": "TX013",
            "channel": "GOOGLEPLAY", "version_name": version[0], "version_code": int(version[1]),
            "public_key_der_base64": public_key, "points_encryption_seed": seed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", required=True, type=Path, help="Base APK, not the XAPK container")
    parser.add_argument("--android-id", required=True, help="Stable ID of your own dedicated Android test installation")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-fA-F]{16}", args.android_id):
        parser.error("android-id must contain 16 hexadecimal characters")
    if args.output.exists():
        parser.error("Profile already exists: reuse it; do not reset the installation to obtain credits")
    profile = extract_config(args.apk)
    profile["android_id"] = args.android_id.lower()
    # Reserve the profile before registering, so a later run cannot reset this identity.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(profile, output, indent=2)
    if profile["protocol"] == "v3":
        print("Created private profile for the Drive APK's v3 protocol")
        return
    result = request_points(profile, "/point/user/getNewUserPointInfo.htm?osType=1", {
        "passivePointId": "", "passivePkgType": "",
    })
    if not result.get("pointId"):
        raise ValueError("Server did not issue a point ID; keep the profile for diagnosis")
    profile["point_id"] = result["pointId"]
    args.output.write_text(json.dumps(profile, indent=2))
    print(f"Created private guest profile. Server-issued balance: {result.get('pointNumber', 'unknown')} points")


if __name__ == "__main__":
    main()
