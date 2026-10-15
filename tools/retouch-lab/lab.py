#!/usr/bin/env python3
"""Small, local ADB controller for a dedicated Retouch test emulator."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


def inspect_apk(path):
    from androguard.core.apk import APK
    from loguru import logger
    logger.remove()
    apk = APK(str(path))
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    return {
        "filename": path.name,
        "sha256": hashlib.file_digest(path.open("rb"), "sha256").hexdigest(),
        "package": apk.get_package(),
        "version": apk.get_androidversion_name(),
        "activity": apk.get_main_activity(),
        "permissions": sorted(apk.get_permissions()),
        "abis": sorted({name.split("/")[1] for name in names if name.startswith("lib/")}),
        "models": [name for name in names if name.endswith((".tflite", ".onnx", ".mnn", ".jpack"))],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", default=os.environ.get("ANDROID_SERIAL", "emulator-5554"))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "install", "import-image"):
        commands.add_parser(name).add_argument("path", type=Path)
    commands.add_parser("open").add_argument("package", choices=("com.magic.retouch", "com.advasoft.touchretouch"))
    commands.add_parser("screenshot").add_argument("path", type=Path)
    commands.add_parser("ui").add_argument("path", type=Path)
    tap = commands.add_parser("tap")
    tap.add_argument("x", type=int)
    tap.add_argument("y", type=int)
    swipe = commands.add_parser("swipe")
    for value in ("x1", "y1", "x2", "y2"):
        swipe.add_argument(value, type=int)
    swipe.add_argument("--duration", type=int, default=350)
    commands.add_parser("network").add_argument("state", choices=("on", "off"))
    args = parser.parse_args()
    if args.command == "inspect":
        print(json.dumps(inspect_apk(args.path), indent=2, ensure_ascii=False))
        return
    sdk = os.environ.get("ANDROID_SDK_ROOT", "")
    adb = shutil.which("adb") or str(Path(sdk) / "platform-tools/adb")
    prefix = [adb, "-s", args.serial]

    def run(*values, **kwargs):
        return subprocess.run(prefix + list(values), check=True, timeout=120, **kwargs)

    if args.command == "install":
        run("install", "-r", str(args.path))
    elif args.command == "open":
        run("shell", "monkey", "-p", args.package, "-c", "android.intent.category.LAUNCHER", "1")
    elif args.command == "import-image":
        # Fixed destination avoids interpreting a user-supplied filename in Android's shell.
        suffix = args.path.suffix.lower()
        if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
            parser.error("Use a PNG, JPEG, or WebP image")
        remote = "/sdcard/Pictures/retouch-test" + suffix
        run("push", str(args.path), remote)
        run("shell", "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE", "-d", "file://" + remote)
    elif args.command == "screenshot":
        args.path.parent.mkdir(parents=True, exist_ok=True)
        with args.path.open("wb") as output:
            run("exec-out", "screencap", "-p", stdout=output)
    elif args.command == "ui":
        run("shell", "uiautomator", "dump", "/sdcard/retouch-ui.xml")
        args.path.parent.mkdir(parents=True, exist_ok=True)
        run("pull", "/sdcard/retouch-ui.xml", str(args.path))
    elif args.command == "tap":
        run("shell", "input", "tap", str(args.x), str(args.y))
    elif args.command == "swipe":
        run("shell", "input", "swipe", *[str(getattr(args, name)) for name in ("x1", "y1", "x2", "y2")], str(args.duration))
    elif args.command == "network":
        state = "enable" if args.state == "on" else "disable"
        run("shell", "svc", "wifi", state)
        run("shell", "svc", "data", state)


if __name__ == "__main__":
    try:
        main()
    except (subprocess.SubprocessError, OSError, zipfile.BadZipFile) as error:
        sys.exit(str(error))
