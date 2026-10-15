"""Normal guest registration and read-only credit queries used by the app SDK."""
import base64
import json
import time

from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, modes
import requests

from cloud import ServiceError


def request_points(profile, action, extra):
    android_id = profile["android_id"]
    uid = "jrxc_" + android_id
    payload = {
        "deviceUuid": android_id, "deviceUuId": android_id, "uuId": uid,
        "userId": "", "pkgName": "com.magic.retouch", "osType": "1",
        "lang": "en-US", "versionName": profile["version_name"],
        "versionCode": str(profile["version_code"]), "channelName": profile["channel"],
        "requestId": str(time.monotonic_ns()), **extra,
    }
    # Transport encoding required by this SDK; the seed is read from the user's APK.
    key = base64.b64encode(profile["points_encryption_seed"].encode())
    key = key.ljust(24, b"X") if len(key) < 24 else key[:12] + key[-12:]
    padder = padding.PKCS7(64).padder()
    raw = json.dumps(payload, separators=(",", ":")).encode()
    raw = padder.update(raw) + padder.finalize()
    encryptor = Cipher(TripleDES(key), modes.ECB()).encryptor()
    encoded = encryptor.update(raw) + encryptor.finalize()
    headers = {
        "Content-Type": "application/wxt;charset=UTF-8", "x-uuid": uid,
        "x-deviceuuid": android_id, "x-userid": "", "x-openid": "",
        "x-appinfo": f"{profile['channel']}/com.magic.retouch/{profile['version_name']}/Android 11/Pixel 2/Google",
    }
    try:
        response = requests.post("https://apis.videoshowapp.com/zone/1.0.1" + action,
                                 data=encoded, headers=headers, timeout=(20, 30))
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError) as error:
        raise ServiceError("تعذر الاتصال بنظام رصيد Retouch") from error
    if not isinstance(result, dict) or result.get("retCode") != 1:
        raise ServiceError("رفض نظام رصيد Retouch الطلب")
    return result


def balance(profile):
    return request_points(profile, "/point/user/getPointBalanceInfo.htm?osType=1", {
        "pointId": profile["point_id"], "enableViolationBan": "false",
    })["pointNumber"]


def costs(profile):
    result = request_points(profile, "/point/consume/getConsumePointConfig.htm?osType=1", {
        "pointId": profile["point_id"], "isVIP": "0",
    })
    return {entry["consumeType"]: entry["pointNumber"] for entry in result.get("consumeList", [])
            if entry.get("consumeType") in ("Advanced_Elimination", "Ordinary_Elimination")}
