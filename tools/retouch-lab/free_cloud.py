"""Automatic manga cleanup using the public CPU-hosted IOPaint/LaMa demo."""
import argparse
import base64
import io
import json
from pathlib import Path
import time

import cv2
import numpy as np
from PIL import Image
import requests

from auto_mask import TextDetector

SPACE = "Sanster/iopaint-lama"
BASE_URL = "https://sanster-iopaint-lama.hf.space"


class FreeCloudError(RuntimeError):
    pass


def png(image):
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class HostedLama:
    def __init__(self):
        self.session = requests.Session()

    def restore(self, image, mask, timeout):
        payload = {"image": base64.b64encode(png(image)).decode(),
                   "mask": base64.b64encode(png(mask)).decode(),
                   "hd_strategy": "Original", "sd_keep_unmasked_area": True}
        with self.session.post(BASE_URL + "/api/v1/inpaint", json=payload, stream=True, timeout=(20, min(180, timeout))) as response:
            response.raise_for_status()
            chunks, total = [], 0
            for chunk in response.iter_content(65536):
                total += len(chunk)
                if total > 20 * 1024 * 1024:
                    raise FreeCloudError("نتيجة الخدمة أكبر من الحد المسموح")
                chunks.append(chunk)
        with Image.open(io.BytesIO(b"".join(chunks))) as result:
            if result.width * result.height > 25_000_000:
                raise FreeCloudError("أبعاد استجابة الخدمة غير متوقعة")
            return result.convert("RGB").resize(image.size, Image.Resampling.LANCZOS)

    def close(self):
        self.session.close()


def context_boxes(mask, size):
    binary = np.array(mask)
    # Nearby text shares one request; distant bubbles keep their local context.
    grouped = cv2.dilate(binary, cv2.getStructuringElement(cv2.MORPH_RECT, (97, 97)))
    contours, _ = cv2.findContours(grouped, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    width, height = size
    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        boxes.append((max(0, x - 80), max(0, y - 80), min(width, x + w + 80), min(height, y + h + 80)))
    # Merge intersecting context windows so a region is never edited twice.
    changed = True
    while changed:
        changed = False
        for i, a in enumerate(boxes):
            for j in range(i + 1, len(boxes)):
                b = boxes[j]
                if max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3]):
                    boxes[i] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                    boxes.pop(j)
                    changed = True
                    break
            if changed:
                break
    return sorted(boxes, key=lambda box: (box[1], box[0]))


def work_regions(mask, boxes, size):
    width, height = size
    regions = []
    for box in boxes:
        if max(box[2] - box[0], box[3] - box[1]) <= 896:
            regions.append((box, mask.crop(box)))
            continue
        for y in range(box[1], box[3], 640):
            for x in range(box[0], box[2], 640):
                core = (x, y, min(x + 640, box[2]), min(y + 640, box[3]))
                if not mask.crop(core).getbbox():
                    continue
                context = (max(0, x - 128), max(0, y - 128), min(width, core[2] + 128), min(height, core[3] + 128))
                selected = Image.new("L", (context[2] - context[0], context[3] - context[1]))
                selected.paste(mask.crop(core), (x - context[0], y - context[1]))
                regions.append((context, selected))
    return regions


class AutomaticCleaner:
    def __init__(self, detector=None, client_factory=None):
        self.detector = detector or TextDetector()
        self.client_factory = client_factory or HostedLama

    def clean(self, data, progress=None):
        if len(data) > 20 * 1024 * 1024:
            raise ValueError("أرسل صورة أصغر من 20 MB")
        with Image.open(io.BytesIO(data)) as opened:
            if opened.format not in ("JPEG", "PNG", "WEBP") or opened.width * opened.height > 25_000_000:
                raise ValueError("استخدم PNG أو JPEG أو WebP حتى 25 مليون بكسل")
            source = opened.convert("RGB")
        started = time.monotonic()
        mask = self.detector.detect(source)
        if not mask.getbbox():
            raise FreeCloudError("لم يكتشف الكاشف نصًا؛ قد لا يدعم شكل الحروف في هذه الصورة")
        boxes = context_boxes(mask, source.size)
        regions = work_regions(mask, boxes, source.size)
        output = source.copy()
        jobs = 0
        client = None
        try:
            for index, (box, selection) in enumerate(regions):
                remaining = started + 600 - time.monotonic()
                if remaining <= 0:
                    raise FreeCloudError("انتهت مهلة الصورة؛ أرسل لوحة أصغر")
                if progress:
                    progress(index + 1, len(regions))
                crop = source.crop(box)
                pixels = np.array(crop)
                ring = cv2.dilate(np.array(selection), np.ones((7, 7), np.uint8)) > np.array(selection)
                background = pixels[ring]
                if len(background) and np.mean(np.min(background, axis=1) >= 248) > 0.995:
                    output.paste(Image.new("RGB", crop.size, "white"), box[:2], selection)
                    continue
                if jobs >= 8:
                    raise FreeCloudError("الصورة تحتاج طلبات سحابية كثيرة؛ أرسلها على لوحات أصغر")
                if client is None:
                    client = self.client_factory()
                try:
                    restored = client.restore(crop, selection, min(180, remaining))
                except Exception as error:
                    raise FreeCloudError("تعذر الترميم المجاني؛ قد تكون الخدمة مشغولة أو متوقفة. لم تُرجع صورة ناقصة.") from error
                output.paste(restored, box[:2], selection)
                jobs += 1
        finally:
            if client is not None:
                client.close()
        if jobs:
            remaining_text = np.array(self.detector.detect(output)) > 0
            original_mask = np.array(mask) > 0
            if np.count_nonzero(remaining_text & original_mask) > max(64, np.count_nonzero(original_mask) * 0.02):
                raise FreeCloudError("رصد فحص النتيجة حروفًا داخل منطقة الترميم؛ لم تُقبل الصورة كتنظيف ناجح")
        return png(output), png(mask), {"space": SPACE, "cloud_jobs": jobs, "detected_groups": len(boxes), "seconds": round(time.monotonic() - started, 1), "dimensions": list(source.size)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--mask-output", type=Path)
    args = parser.parse_args()
    result, mask, info = AutomaticCleaner().clean(args.image.read_bytes(), lambda i, n: print(f"Region {i}/{n}", flush=True))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(result)
    if args.mask_output:
        args.mask_output.write_bytes(mask)
    print(json.dumps(info))
