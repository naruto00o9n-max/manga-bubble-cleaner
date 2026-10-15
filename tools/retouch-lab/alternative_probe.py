"""Exercise ordinary erasing controls in a disposable Android emulator."""
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

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--app", required=True)
parser.add_argument("--snapedit-super", action="store_true", help="Select the existing Super Erase UI switch; stop if it is unavailable")
parser.add_argument("--block-network", action="store_true", help="Block emulator non-loopback IPv4 and IPv6 before drawing the mask")
parser.add_argument("--apk", required=True, type=Path)
parser.add_argument("--out", required=True, type=Path)
args = parser.parse_args()
spec = next(row for row in json.loads(Path(__file__).with_name("alternative-apps.json").read_text()) if row["name"] == args.app)
args.out.mkdir(parents=True, exist_ok=True)
package = spec["package"]
report = {"app": args.app, "package": package, "version": spec["version"], "sha256": spec["sha256"], "actions": [], "status": "starting"}
sample = None
existing_images = set()
report["requested_mode"] = "super" if args.snapedit_super else "ordinary"


def adb(*values, check=True, timeout=60):
    result = subprocess.run(["adb", "-e", *map(str, values)], capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError((result.stdout + result.stderr).decode(errors="replace")[:600])
    return result


def capture(name):
    data = adb("exec-out", "screencap", "-p").stdout
    (args.out / (name + ".png")).write_bytes(data)
    try:
        adb("shell", "uiautomator", "dump", "/sdcard/alternative-ui.xml", check=False, timeout=18)
    except subprocess.TimeoutExpired:
        pass
    data_xml = adb("exec-out", "cat", "/sdcard/alternative-ui.xml", check=False).stdout
    (args.out / (name + ".xml")).write_bytes(data_xml)
    try:
        nodes = list(ET.fromstring(data_xml).iter("node"))
    except ET.ParseError:
        nodes = []
    return Image.open(io.BytesIO(data)).convert("RGB"), nodes


def bounds(node):
    values = list(map(int, re.findall(r"\d+", node.get("bounds", ""))))
    return values if len(values) == 4 else None


def tap(node):
    b = bounds(node)
    if not b:
        return False
    adb("shell", "input", "tap", (b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
    report["actions"].append({"tap": node.get("text") or node.get("content-desc") or node.get("resource-id")})
    time.sleep(2)
    return True


def tap_label(nodes, labels):
    for label in labels:
        for node in nodes:
            if node.get("enabled") == "false":
                continue
            values = [re.sub(r"\s+", " ", node.get(key, "")).strip().casefold() for key in ("text", "content-desc")]
            if label.casefold() in values:
                if args.app == "picwish" and node.get("resource-id", "").endswith("/menuTv"):
                    label_bounds = bounds(node)
                    if label_bounds:
                        cx = (label_bounds[0] + label_bounds[2]) // 2
                        for icon in nodes:
                            b = bounds(icon)
                            if icon.get("resource-id", "").endswith("/menuItemView") and b and b[0] <= cx <= b[2] and 0 <= label_bounds[1] - b[3] <= 40:
                                return tap(icon)
                return tap(node)
    return False


def tap_id(nodes, words):
    for word in words:
        for node in nodes:
            value = node.get("resource-id", "").split("/")[-1].casefold()
            if word in value and node.get("enabled") != "false":
                return tap(node)
    return False


def text_of(nodes):
    return " ".join(node.get("text", "") + " " + node.get("content-desc", "") for node in nodes).casefold()


def paywall(nodes):
    value = text_of(nodes)
    return bool(re.search(r"\$\s*\d|\d[.,]\d{2}\s*(?:usd|/week|/month)|start free trial|start your 3-day trial|subscribe now|buy credits|purchase credits", value))


def locate(screen):
    target = cv2.cvtColor(np.array(sample), cv2.COLOR_RGB2GRAY)
    screenshot = cv2.cvtColor(np.array(screen), cv2.COLOR_RGB2GRAY)
    best = (-1, None)
    for scale in np.linspace(0.25, 0.70, 70):
        w, h = int(sample.width * scale), int(sample.height * scale)
        if w > screen.width or h > screen.height or h < 380:
            continue
        resized = cv2.resize(target, (w, h), interpolation=cv2.INTER_AREA)
        _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(screenshot, resized, cv2.TM_CCOEFF_NORMED))
        if score > best[0]:
            best = (float(score), (point[0], point[1], float(scale)))
    return best


def gallery_choice(nodes):
    if args.app == "picwish" and any(n.get("resource-id", "").endswith("/albumCtv") for n in nodes):
        thumbnails = [n for n in nodes if n.get("clickable") == "true" and bounds(n)
                      and 90 <= bounds(n)[2] - bounds(n)[0] <= 250
                      and 90 <= bounds(n)[3] - bounds(n)[1] <= 250
                      and 100 <= bounds(n)[1] and bounds(n)[3] < 800]
        if thumbnails:
            return tap(thumbnails[0])
    if args.app == "removeit" and "demo images" in text_of(nodes) and "recents" in text_of(nodes):
        # The custom picker mixes downloaded samples with actual media. Pick only
        # the clickable recent image, then confirm its selection on the next pass.
        recent = [n for n in nodes if n.get("resource-id", "").endswith("/ivImage")
                  and n.get("clickable") == "true"]
        if not report.get("recent_chosen") and recent:
            report["recent_chosen"] = True
            return tap(recent[-1])
        return tap_id(nodes, ["ic_select"])
    for node in nodes:
        if "alternative-probe" in (node.get("text", "") + node.get("content-desc", "")):
            return tap(node)
    gallery_packages = ("documentsui", "providers.media", "google.android.apps.photos", "gallery3d")
    gallery_screen = any(any(name in node.get("package", "") for name in gallery_packages) for node in nodes)
    gallery_screen = gallery_screen or any(label in text_of(nodes) for label in ("select photo", "select image", "select a photo", "all photos", "recent photos"))
    if gallery_screen:
        candidates = [n for n in nodes if n.get("clickable") == "true" and bounds(n)
                      and bounds(n)[2] - bounds(n)[0] > 65 and bounds(n)[3] - bounds(n)[1] > 65
                      and bounds(n)[1] > 70 and bounds(n)[3] < 930]
        if candidates:
            return tap(candidates[0])
        if tap_label(nodes, ["Images", "Pictures", "Photos", "Recent"]):
            return True
    return False


try:
    adb("logcat", "-c", check=False)
    report["install"] = adb("install", "-r", "-g", str(args.apk), timeout=180).stdout.decode(errors="replace").strip()
    adb("shell", "wm", "size", "540x960")
    adb("shell", "wm", "density", "200")
    for setting in ("window_animation_scale", "transition_animation_scale", "animator_duration_scale"):
        adb("shell", "settings", "put", "global", setting, "1")
    for permission in ("READ_EXTERNAL_STORAGE", "WRITE_EXTERNAL_STORAGE"):
        adb("shell", "pm", "grant", package, "android.permission." + permission, check=False)
    url = "https://cdn.asurascans.com/asura-images/chapters/infinite-mage/100/003.webp?v=1770499638"
    response = requests.get(url, headers={"Referer": "https://asurascans.com/"}, timeout=45)
    response.raise_for_status()
    sample = Image.open(io.BytesIO(response.content)).convert("RGB").crop((0, 7800, 800, 9000))
    sample.save(args.out / "input.png")
    report["sample_source"] = url
    remote = "/sdcard/Pictures/alternative-probe.png"
    adb("push", str(args.out / "input.png"), remote)
    adb("shell", "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE", "-d", "file://" + remote, check=False)
    time.sleep(3)
    media = adb("shell", "content", "query", "--uri", "content://media/external/images/media", "--projection", "_id:_display_name", check=False).stdout.decode(errors="replace")
    match = re.search(r"_id=(\d+)[^\n]*alternative-probe", media)
    uri = "content://media/external/images/media/" + match.group(1) if match else "file://" + remote
    report["content_uri_available"] = bool(match)
    adb("shell", "am", "start", "-n", package + "/" + spec["main"], check=False)
    time.sleep(8)
    for index in range(10):
        screen, nodes = capture(f"onboarding-{index:02}")
        if paywall(nodes):
            report["offer_observed"] = True
            if not (tap_label(nodes, ["Not now", "Skip", "Close", "Maybe later"]) or tap_id(nodes, ["close", "skip", "dismiss"])):
                adb("shell", "input", "keyevent", "4")
            continue
        if not tap_label(nodes, ["Reject all", "Do not consent", "Reject", "Confirm choices", "Save choices", "Close", "Skip", "Not now", "Get started", "I agree", "Agree", "Agree and Continue", "Accept", "Continue", "Next", "Start", "Allow", "Allow all photos", "While using the app", "OK", "Consent"]):
            break
    if spec.get("share_activity"):
        result = adb("shell", "am", "start", "-n", package + "/" + spec["share_activity"], "-a", "android.intent.action.SEND", "-t", "image/png", "--eu", "android.intent.extra.STREAM", uri, "-f", "0x10000001", check=False)
        report["share_intent"] = (result.stdout + result.stderr).decode(errors="replace")[:900]
        time.sleep(6)
    geometry = None
    editor_requested = False
    for index in range(22):
        screen, nodes = capture(f"navigate-{index:02}")
        score, geometry = locate(screen)
        report["sample_match"] = {"score": score, "geometry": geometry}
        if score > 0.70 and not paywall(nodes):
            if args.app == "snapedit" and not editor_requested and tap_label(nodes, ["Remove Objects"]):
                editor_requested = True
                time.sleep(4)
                continue
            if args.app == "removeit" and not editor_requested and tap_id(nodes, ["btn_remover"]):
                editor_requested = True
                time.sleep(4)
                continue
            report["status"] = "sample_opened"
            break
        if paywall(nodes):
            report["offer_observed"] = True
            if not (tap_label(nodes, ["Not now", "Skip", "Close"]) or tap_id(nodes, ["close", "skip", "dismiss"])):
                adb("shell", "input", "keyevent", "4")
        elif tap_label(nodes, ["Reject all", "Do not consent", "Reject", "Confirm choices", "Close", "Agree and Continue", "Allow", "Allow all photos", "While using the app", "OK", "Not now", "Skip", "Next", "Continue", "Got it", "Consent"]):
            pass
        elif args.app == "picwish" and "try now" in text_of(nodes):
            adb("shell", "input", "keyevent", "4")
            time.sleep(2)
        elif args.app == "picwish" and tap_id(nodes, ["closeiv"]):
            pass
        elif gallery_choice(nodes):
            pass
        elif tap_label(nodes, ["Remove objects", "Remove Objects", "Object Remover", "Object Removal", "Remove Object", "AI Retouch", "Remove Unwanted Objects", "Retouch", "Photo Retouch", "Remove", "Manual", "Photo Eraser", "Start editing", "Start Editing Now", "Gallery", "Photos"]):
            pass
        elif tap_id(nodes, ["remove_object", "object_remov", "retouch", "eraser", "gallery", "import", "photo_picker"]):
            pass
        else:
            time.sleep(4)
    else:
        report["status"] = "ui_recorded_sample_not_opened"
        geometry = None
    if geometry:
        paths_before = adb("shell", "find", "/sdcard/", "-maxdepth", "4", "-type", "f", check=False).stdout.decode(errors="replace").splitlines()
        existing_images = set(paths_before)
        if args.snapedit_super:
            if args.app != "snapedit":
                raise RuntimeError("The Super Erase flag applies only to SnapEdit")
            switch = next((n for n in nodes if n.get("resource-id", "").endswith("/super_erase_switch")), None)
            if switch is None:
                raise RuntimeError("Super Erase switch is not available in this screen")
            if switch.get("checked") != "true":
                tap(switch)
            time.sleep(3)
            screen, nodes = capture("super-mode-selected")
            report["super_switch_checked"] = any(n.get("resource-id", "").endswith("/super_erase_switch") and n.get("checked") == "true" for n in nodes)
            if paywall(nodes) or not report["super_switch_checked"]:
                report["mode_status"] = "not_enabled"
                raise RuntimeError("Normal UI did not enable Super Erase; no purchase attempted")
        if args.block_network:
            report["network_test"] = "requested"
            adb("root")
            adb("wait-for-device", timeout=30)
            if adb("shell", "id", "-u").stdout.strip() != b"0":
                raise RuntimeError("Cannot verify offline mode: emulator did not provide root")
            for firewall in ("iptables", "ip6tables"):
                adb("shell", firewall, "-I", "OUTPUT", "1", "!", "-o", "lo", "-j", "REJECT")
            report["network_test"] = "non_loopback_ipv4_and_ipv6_blocked_before_mask"
        tap_label(nodes, ["Manual", "Brush", "Draw", "Eraser"])
        screen, nodes = capture("before-selection")
        score, geometry = locate(screen)
        if score > 0.70:
            x0, y0, scale = geometry
            # One difficult glowing SFX region on foliage; no other regions requested.
            for y in range(670, 1041, 24):
                adb("shell", "input", "swipe", int(x0 + 12 * scale), int(y0 + y * scale), int(x0 + 330 * scale), int(y0 + y * scale), 280)
            screen, nodes = capture("selection")
            # Apply through the visible UI; never activate a purchase or trial control.
            apply_labels = ["Remove", "Remove Now", "Erase", "GO", "Go", "Apply", "Done"]
            if paywall(nodes):
                report["status"] = "payment_required_before_processing"
            elif (args.app == "removeit" and tap_id(nodes, ["buttonremove", "ibaccept"])) or tap_label(nodes, apply_labels) or tap_id(nodes, ["btn_remove", "btn_erase", "btn_apply", "action_apply", "ibaccept"]):
                report["apply_button_found"] = True
                capture("processing-start")
                time.sleep(25)
                screen, nodes = capture("after-erase")
                if paywall(nodes):
                    report["status"] = "payment_required_after_selection"
                else:
                    report["status"] = "erase_attempt_recorded"
                    if tap_label(nodes, ["Done"]) or (args.app == "removeit" and tap_id(nodes, ["ibaccept"])):
                        time.sleep(3)
                        _, nodes = capture("tool-finalized")
                    if tap_label(nodes, ["Save", "Export", "Download"]) or tap_id(nodes, ["btn_save", "action_save", "iv_save", "save_image", "btn_export"]):
                        time.sleep(3)
                        _, nodes = capture("export")
                        if not paywall(nodes):
                            tap_label(nodes, ["Save to gallery", "Save image", "Save", "Save and Exit", "Save as copy", "Save a copy", "Gallery"])
                        time.sleep(3)
                        capture("export-final")
            else:
                report["status"] = "selection_recorded_no_apply_button"
            paths = adb("shell", "find", "/sdcard/", "-maxdepth", "4", "-type", "f", check=False).stdout.decode(errors="replace").splitlines()
            exported = []
            for index, path in enumerate(paths):
                if path != remote and path not in existing_images and path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    destination = args.out / (f"export-{index:02}" + Path(path).suffix)
                    adb("pull", path, str(destination), check=False)
                    if destination.exists():
                        exported.append(destination.name)
            report["exported_files"] = exported
except Exception as error:
    report["status"] = "error"
    report["error"] = str(error)[:700]
    try:
        capture("failure")
    except Exception:
        pass
finally:
    try:
        if args.block_network and report.get("network_test") == "non_loopback_ipv4_and_ipv6_blocked_before_mask":
            for firewall in ("iptables", "ip6tables"):
                report[firewall + "_after_test"] = adb("shell", firewall, "-L", "OUTPUT", "-n", "-v", check=False).stdout.decode(errors="replace")
                adb("shell", firewall, "-D", "OUTPUT", "!", "-o", "lo", "-j", "REJECT", check=False)
        report["abi_list"] = adb("shell", "getprop", "ro.product.cpu.abilist", check=False).stdout.decode().strip()
        activity = adb("shell", "dumpsys", "activity", "activities", check=False, timeout=15).stdout.decode(errors="replace")
        report["foreground_activity"] = [line.strip() for line in activity.splitlines() if "mResumedActivity" in line or "topResumedActivity" in line]
        (args.out / "crash.log").write_bytes(adb("logcat", "-d", "-b", "crash", check=False, timeout=15).stdout)
    except Exception:
        pass
    (args.out / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
