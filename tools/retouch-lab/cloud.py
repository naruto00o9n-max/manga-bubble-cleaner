"""Client for the guest, non-VIP Retouch image + mask workflow.

This is an unofficial client. A profile must describe the user's own app
installation. Server errors are surfaced; no local inpainting fallback is used.
"""
import base64
import hashlib
import io
import json
from pathlib import Path
import time
from urllib.parse import quote, urlparse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from PIL import Image
import requests

MAX_BYTES = 20 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 25_000_000


class ServiceError(RuntimeError):
    pass


def decode_image(data, mode="RGB"):
    if len(data) > MAX_BYTES:
        raise ValueError("الملف أكبر من 20 MB")
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > 25_000_000:
            raise ValueError("الصورة أكبر من 25 مليون بكسل؛ أرسل لوحة أو قصاصة")
        if image.format not in ("JPEG", "PNG", "WEBP"):
            raise ValueError("استخدم PNG أو JPEG أو WebP")
        image.load()
        return image.convert(mode)


def encode_image(image, fmt="WEBP"):
    output = io.BytesIO()
    image.save(output, format=fmt, **({"lossless": True} if fmt == "WEBP" else {}))
    return output.getvalue()


class RetouchClient:
    def __init__(self, profile_path):
        profile = json.loads(Path(profile_path).read_text())
        self.base = profile["base_url"].rstrip("/") + "/"
        parsed = urlparse(self.base)
        if parsed.scheme != "https" or parsed.hostname != "aicup-v2.magicutapp.com":
            raise ValueError("Unexpected Retouch service host")
        self.profile = profile
        self.protocol = profile.get("protocol", "v3")
        if self.protocol not in ("v3", "v5"):
            raise ValueError("Unknown Retouch protocol")
        self.public_key = serialization.load_der_public_key(base64.b64decode(profile["public_key_der_base64"]))
        self.session = requests.Session()
        self.service_id = ""
        self.refreshed = 0.0

    def close(self):
        self.session.close()

    def public_params(self):
        return {
            "appId": str(self.profile["app_id"]),
            "uuId": "jrxc_" + self.profile["android_id"],
            "pkgName": "com.magic.retouch", "lang": "en_US", "country": "US",
            "channelName": self.profile.get("channel", "googleplay"),
            "appVersion": self.profile.get("version_name", "2.2.4.2"),
            "version": str(self.profile.get("version_code", 53)),
            "appTime": str(int(time.time() * 1000)),
            "isVip": "false", **({"uId": self.service_id} if self.protocol == "v3" else {}),
        }

    def request_json(self, path, **kwargs):
        try:
            response = self.session.post(self.base + path, timeout=(20, 90), **kwargs)
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as error:
            raise ServiceError("تعذر الاتصال بخدمة Retouch أو أن استجابتها غير صالحة") from error
        if not isinstance(result, dict):
            raise ServiceError("استجابة Retouch ليست كائن JSON")
        return result

    @staticmethod
    def check_result(result):
        # The provider's actual code and message remain visible for diagnosis.
        if result.get("success") is False:
            code = result.get("code", "unknown")
            message = str(result.get("message", "Service rejected request"))[:200]
            if code == 1407:
                message = "الخادم يرفض إصدار التطبيق القديم؛ يلزم ملف إصدار مدعوم"
            elif code == 1406:
                message = "رصيد الخدمة غير كافٍ لهذا الوضع؛ لم تُنفذ معالجة بديلة"
            raise ServiceError(f"Retouch [{code}]: {message}")

    def bootstrap(self):
        if self.service_id and time.monotonic() - self.refreshed < 14 * 60:
            return
        result = self.request_json("v4/getUuid", data=self.public_params())
        self.check_result(result)
        if not isinstance(result.get("data"), str) or not result["data"]:
            raise ServiceError("الخادم لم يصدر جلسة للتطبيق")
        self.service_id = result["data"]
        self.refreshed = time.monotonic()

    def signed_fields(self, mode):
        app_time = str(int(time.time() * 1000))
        params = {"appTime": app_time, "priority": "50", "pkgName": "com.magic.retouch"}
        if self.protocol == "v3":
            params["uId"] = self.service_id
        else:
            point_id = self.profile.get("point_id")
            if not point_id:
                raise ServiceError("ملف الجلسة يحتاج point_id صادرًا من التطبيق لحسابك")
            # Values used by the unmodified 2.3.8.0 app for these two tools.
            params.update(check=0, enableViolationBan=False, pointId=point_id,
                          consumeType="Advanced_Elimination" if mode == "ai" else "Ordinary_Elimination")
        content = json.dumps(params, separators=(",", ":")).encode()
        block = self.public_key.key_size // 8 - 11
        encrypted = b"".join(self.public_key.encrypt(content[i:i + block], padding.PKCS1v15())
                             for i in range(0, len(content), block))
        fields = self.public_params()
        fields.update(appTime=app_time, decrypt=base64.b64encode(encrypted).decode(),
                      sign=hashlib.md5(("com.magic.retouch" + (self.service_id if self.protocol == "v3" else "") + app_time).encode()).hexdigest())
        return fields

    def clean(self, image_data, mask_data, timeout=180, mode="ai"):
        if mode not in ("ai", "standard"):
            raise ValueError("Unknown removal mode")
        original = decode_image(image_data)
        mask = decode_image(mask_data, "L")
        if original.size != mask.size:
            raise ValueError("يجب أن تكون أبعاد القناع مساوية لأبعاد الصورة")
        mask = mask.point(lambda x: 255 if x >= 128 else 0)
        box = mask.getbbox()
        if not box:
            raise ValueError("القناع فارغ؛ اجعل منطقة النص بيضاء وبقية الصورة سوداء")
        left, top, right, bottom = box
        box = (max(0, left - 128), max(0, top - 128), min(original.width, right + 128), min(original.height, bottom + 128))
        crop, crop_mask = original.crop(box), mask.crop(box)
        scale = min(1, 900 / max(crop.size))
        size = tuple(max(8, (int(n * scale) + 7) // 8 * 8) for n in crop.size)
        source = encode_image(crop.resize(size, Image.Resampling.LANCZOS))
        selection = encode_image(crop_mask.resize(size, Image.Resampling.NEAREST))
        if self.protocol == "v3":
            self.bootstrap()
        fields = self.signed_fields(mode)
        fields.update(imageFileName="retouch.webp", maskImageFileName="mask.webp")
        route = "fooocusinpaint" if mode == "ai" else "inpaintUpload"
        result = self.request_json(self.protocol + "/" + route, data=fields, files={
            "base64" if mode == "ai" else "imageFile": ("retouch.webp", source, "app/octet-stream"),
            "maskBase64" if mode == "ai" else "maskImageFile": ("mask.webp", selection, "app/octet-stream"),
        })
        self.check_result(result)
        key = result.get("data")
        if not isinstance(key, str) or not key:
            raise ServiceError("الخادم لم يُعد معرف مهمة")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(1.5)
            poll_version = "v5" if self.protocol == "v5" else "v4"
            result = self.request_json(poll_version + "/downLoad/" + quote(key, safe=""))
            if result.get("code") == 409:
                continue
            self.check_result(result)
            url = result.get("data")
            if not isinstance(url, str) or urlparse(url).scheme != "https":
                raise ServiceError("الخادم لم يُعد رابط صورة HTTPS")
            # An authenticated service result, never a user-supplied download URL.
            with self.session.get(url, stream=True, timeout=(20, 60)) as response:
                response.raise_for_status()
                parts, total = [], 0
                for part in response.iter_content(65536):
                    total += len(part)
                    if total > MAX_BYTES:
                        raise ServiceError("نتيجة الخادم أكبر من الحد المسموح")
                    parts.append(part)
            restored = decode_image(b"".join(parts)).resize(crop.size, Image.Resampling.LANCZOS)
            output = original.copy()
            output.paste(restored, box[:2], crop_mask)
            # All pixels outside the requested mask remain identical to the input.
            return encode_image(output, "PNG")
        raise ServiceError("انتهت مهلة انتظار Retouch؛ لم تُستخدم معالجة محلية")
