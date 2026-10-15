#!/usr/bin/env python3
"""Record normal UI interaction with either provided APK in a dedicated emulator."""
import argparse
import io
import json
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET

import cv2
import numpy as np
from PIL import Image
import requests

parser = argparse.ArgumentParser()
parser.add_argument("--app", choices=("magic", "touch"), required=True)
parser.add_argument("--apk", required=True, type=Path)
parser.add_argument("--out", required=True, type=Path)
parser.add_argument("--abi", choices=("armeabi-v7a", "arm64-v8a"))
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=True)
package = "com.magic.retouch" if args.app == "magic" else "com.advasoft.touchretouch"
activity = "com.magic.retouch.ui.activity.external.ExternalRemoveActivity" if args.app == "magic" else "com.advasoft.touchretouch.EditActivityNBO"
report = {"app": args.app, "package": package, "actions": [], "status": "starting", "requested_abi": args.abi}


def adb(*values, timeout=60, check=True):
    result = subprocess.run(["adb", "-e", *map(str, values)], capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace")[:600] or result.stdout.decode(errors="replace")[:600])
    return result


def capture(name):
    png = adb("exec-out", "screencap", "-p").stdout
    (args.out / (name + ".png")).write_bytes(png)
    adb("shell", "uiautomator", "dump", "/sdcard/probe.xml", check=False)
    xml = adb("exec-out", "cat", "/sdcard/probe.xml", check=False).stdout
    (args.out / (name + ".xml")).write_bytes(xml)
    try:
        nodes = list(ET.fromstring(xml).iter("node"))
    except ET.ParseError:
        nodes = []
    return Image.open(io.BytesIO(png)).convert("RGB"), nodes


def tap_node(node):
    coords = list(map(int, re.findall(r"\d+", node.get("bounds", ""))))
    if len(coords) != 4:
        return False
    adb("shell", "input", "tap", (coords[0] + coords[2]) // 2, (coords[1] + coords[3]) // 2)
    report["actions"].append({"tap": node.get("text") or node.get("content-desc") or node.get("resource-id")})
    time.sleep(2)
    return True


def tap_label(nodes, labels):
    for label in labels:
        for node in nodes:
            values = (node.get("text", "").strip().casefold(), node.get("content-desc", "").strip().casefold())
            if label.casefold() in values and node.get("enabled") != "false":
                return tap_node(node)
    return False


def locate(screen, sample):
    image = cv2.cvtColor(np.array(screen), cv2.COLOR_RGB2GRAY)
    original = cv2.cvtColor(np.array(sample), cv2.COLOR_RGB2GRAY)
    best = (-1, None)
    for scale in np.linspace(0.2, 0.55, 90):
        w, h = int(sample.width * scale), int(sample.height * scale)
        if w > screen.width or h > screen.height:
            continue
        resized = cv2.resize(original, (w, h), interpolation=cv2.INTER_AREA)
        _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(image, resized, cv2.TM_CCOEFF_NORMED))
        if score > best[0]:
            best = (score, (point[0], point[1], scale))
    return best


try:
    adb("logcat", "-c", check=False)
    install_args = ["install", "-r", "-g"]
    if args.abi:
        install_args += ["--abi", args.abi]
    installed = adb(*install_args, str(args.apk), timeout=180)
    report["install"] = installed.stdout.decode(errors="replace").strip()
    report["status"] = "installed"
    adb("shell", "wm", "size", "480x800")
    adb("shell", "wm", "density", "160")
    for name in ("window_animation_scale", "transition_animation_scale", "animator_duration_scale"):
        adb("shell", "settings", "put", "global", name, "0")
    for permission in ("READ_EXTERNAL_STORAGE", "WRITE_EXTERNAL_STORAGE"):
        adb("shell", "pm", "grant", package, "android.permission." + permission, check=False)
    source = "https://cdn.asurascans.com/asura-images/chapters/nano-machine/334/904a20.webp?v=1791994334"
    response = requests.get(source, headers={"Referer": "https://asurascans.com/"}, timeout=40)
    response.raise_for_status()
    sample = Image.open(io.BytesIO(response.content)).convert("RGB").crop((0, 0, 900, 1150))
    sample_path = args.out / "input.png"
    sample.save(sample_path)
    report["sample_source"] = source
    remote = "/sdcard/Pictures/retouch-probe.png"
    adb("push", str(sample_path), remote)
    adb("shell", "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE", "-d", "file://" + remote, check=False)
    time.sleep(3)
    media = adb("shell", "content", "query", "--uri", "content://media/external/images/media", "--projection", "_id:_display_name", check=False).stdout.decode(errors="replace")
    media_id = None
    for row in media.splitlines():
        if "retouch-probe.png" in row:
            found = re.search(r"_id=(\d+)", row)
            if found:
                media_id = found.group(1)
                break
    content_uri = "content://media/external/images/media/" + media_id if media_id else "file://" + remote
    report["content_uri_available"] = bool(media_id)
    adb("shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1", timeout=30, check=False)
    time.sleep(8)
    # Only ordinary onboarding/permission controls. Purchase and subscription buttons are excluded.
    for index in range(6):
        screen, nodes = capture(f"onboarding-{index:02}")
        texts = " ".join(node.get("text", "") for node in nodes).casefold()
        if any(word in texts for word in ("subscription", "free trial", "$", "per month", "purchase")):
            report["onboarding_notice"] = "Offer or purchase screen observed; no purchase performed"
            break
        if not tap_label(nodes, ["Skip", "Not now", "Get started", "I agree", "Allow", "Continue", "Next", "Start"]):
            time.sleep(4)
    adb("shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1", timeout=30, check=False)
    time.sleep(4)
    started = adb("shell", "am", "start", "-n", package + "/" + activity, "-a", "android.intent.action.SEND", "-t", "image/png", "--eu", "android.intent.extra.STREAM", content_uri, "-f", "0x10000001", check=False)
    report["share_intent"] = (started.stdout + started.stderr).decode(errors="replace")[:1200]
    time.sleep(8)
    for index in range(12):
        screen, nodes = capture(f"editor-{index:02}")
        score, geometry = locate(screen, sample)
        if score > 0.72:
            report["sample_match"] = {"score": float(score), "geometry": geometry}
            break
        if not tap_label(nodes, ["Allow", "Not now", "Skip", "Next", "Continue", "OK"]):
            time.sleep(3)
    else:
        screen, nodes = capture("editor-final")
    score, geometry = locate(screen, sample)
    report["sample_match"] = {"score": float(score), "geometry": geometry}
    if score > 0.72 and geometry:
        tap_label(nodes, ["Object Removal", "Objects", "Brush"])
        screen, nodes = capture("before-selection")
        score, geometry = locate(screen, sample)
        if score > 0.72 and geometry:
            x0, y0, scale = geometry
            # Test the sky SFX region including its glow, adjacent to the helmet edge.
            for row in range(275, 432, 18):
                adb("shell", "input", "swipe", int(x0 + 336 * scale), int(y0 + row * scale),
                    int(x0 + 500 * scale), int(y0 + row * scale), 300)
            _, nodes = capture("selection")
            tapped = tap_label(nodes, ["GO", "Go", "Remove", "Erase", "Apply"])
            report["apply_button_found"] = tapped
            time.sleep(20)
            screen, nodes = capture("after-attempt")
            report["status"] = "removal_attempt_recorded"
            # Save locally only; never select an external share destination.
            if tap_label(nodes, ["Save", "Export"]):
                time.sleep(3)
                _, nodes = capture("export")
                tap_label(nodes, ["Save as Copy", "Save a copy", "Save", "Gallery"])
                time.sleep(3)
                capture("export-final")
            saved = adb("shell", "find", "/sdcard/Pictures", "/sdcard/DCIM", "-type", "f", check=False).stdout.decode(errors="replace").splitlines()
            for index, path in enumerate(saved):
                if path != remote and path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    adb("pull", path, str(args.out / (f"export-{index:02}" + Path(path).suffix)), check=False)
    else:
        report["status"] = "ui_recorded_sample_not_located"
except Exception as error:
    report["status"] = "error"
    report["error"] = str(error)[:800]
    try:
        capture("failure")
    except Exception:
        pass
finally:
    try:
        crash = adb("logcat", "-d", "-b", "crash", timeout=20, check=False).stdout
        (args.out / "android-crash.log").write_bytes(crash)
        report["abi_list"] = adb("shell", "getprop", "ro.product.cpu.abilist", timeout=10, check=False).stdout.decode(errors="replace").strip()
        activity = adb("shell", "dumpsys", "activity", "activities", timeout=15, check=False).stdout.decode(errors="replace")
        report["foreground_activity"] = [line.strip() for line in activity.splitlines()
                                         if "mResumedActivity" in line or "topResumedActivity" in line]
        runtime = adb("logcat", "-d", "-v", "threadtime", timeout=20, check=False).stdout.decode(errors="replace")
        diagnostic = []
        for line in runtime.splitlines():
            if not any(word in line for word in (package, "AndroidRuntime", "NativeBridge", "libc", "linker", "EGL", "OpenGL", "ActivityTaskManager")):
                continue
            line = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", line)
            line = re.sub(r"(?i)(token|uuid|pointid|android_id|decrypt|sign)([\s\"':=]+)[^\s,}]+", r"\1\2[redacted]", line)
            line = re.sub(r"\b[0-9a-fA-F]{16,}\b", "[identifier]", line)
            diagnostic.append(line)
        (args.out / "android-runtime-redacted.log").write_text("\n".join(diagnostic))
        details = adb("shell", "dumpsys", "package", package, timeout=15, check=False).stdout.decode(errors="replace")
        report["installed_native_abis"] = [line.strip() for line in details.splitlines()
                                            if "primaryCpuAbi=" in line or "secondaryCpuAbi=" in line]
    except Exception:
        pass
    (args.out / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
