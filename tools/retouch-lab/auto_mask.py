"""CPU text detection only; image restoration runs on the hosted service."""
import hashlib
import os
from pathlib import Path
import threading

import cv2
import numpy as np
from PIL import Image
import requests

MODEL_URL = "https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.2.1/comictextdetector.pt.onnx"
MODEL_SHA256 = "1a86ace74961413cbd650002e7bb4dcec4980ffa21b2f19b86933372071d718f"


class TextDetector:
    def __init__(self, model_path=None, full_blocks=False):
        self.path = Path(model_path or os.environ.get("COMIC_TEXT_DETECTOR_PATH", "~/.cache/manga-cloud/comictextdetector.pt.onnx")).expanduser()
        self.net = None
        self.lock = threading.Lock()
        self.full_blocks = full_blocks

    def load(self):
        if self.net is not None:
            return
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".download")
            try:
                digest = hashlib.sha256()
                size = 0
                with requests.get(MODEL_URL, stream=True, timeout=(20, 90)) as response:
                    response.raise_for_status()
                    with temporary.open("wb") as target:
                        for chunk in response.iter_content(1024 * 1024):
                            size += len(chunk)
                            if size > 105 * 1024 * 1024:
                                raise ValueError("Unexpected text detector download size")
                            digest.update(chunk)
                            target.write(chunk)
                if digest.hexdigest() != MODEL_SHA256:
                    raise ValueError("Text detector digest mismatch")
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)
        if hashlib.sha256(self.path.read_bytes()).hexdigest() != MODEL_SHA256:
            raise ValueError("Text detector digest mismatch")
        cv2.setNumThreads(2)
        self.net = cv2.dnn.readNetFromONNX(str(self.path))

    def detect(self, image):
        with self.lock:
            self.load()
            source = np.array(image.convert("RGB"))
            height, width = source.shape[:2]
            result = np.zeros((height, width), dtype=np.uint8)
            # Overlapping tiles retain the letter size in long webtoon pages.
            tile, step = 1280, 1088
            for y in sorted(set(list(range(0, max(1, height - tile + 1), step)) + [max(0, height - tile)])):
                for x in sorted(set(list(range(0, max(1, width - tile + 1), step)) + [max(0, width - tile)])):
                    crop = source[y:y + tile, x:x + tile]
                    h, w = crop.shape[:2]
                    scale = min(1024 / w, 1024 / h)
                    nw, nh = round(w * scale), round(h * scale)
                    canvas = np.full((1024, 1024, 3), 114, dtype=np.uint8)
                    canvas[:nh, :nw] = cv2.resize(crop, (nw, nh))
                    self.net.setInput(cv2.dnn.blobFromImage(canvas, 1 / 255.0, (1024, 1024)))
                    blocks, segmentation = self.net.forward(["blk", "seg"])
                    probability = cv2.resize(segmentation[0, 0, :nh, :nw], (w, h))
                    mask = (probability >= 0.25).astype(np.uint8) * 255
                    # Restrict segmentation to confidently detected text blocks.
                    allowed = np.zeros((h, w), dtype=np.uint8)
                    for row in np.asarray(blocks).reshape(-1, 7):
                        if float(row[4] * max(row[5:])) < 0.35:
                            continue
                        cx, cy, bw, bh = row[:4] / scale
                        left, top = max(0, int(cx - bw / 2) - 8), max(0, int(cy - bh / 2) - 8)
                        right, bottom = min(w, int(cx + bw / 2) + 9), min(h, int(cy + bh / 2) + 9)
                        if right > left and bottom > top and np.count_nonzero(mask[top:bottom, left:right]) >= 8:
                            allowed[top:bottom, left:right] = 255
                    mask = allowed if self.full_blocks else cv2.bitwise_and(mask, allowed)
                    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    cleaned = np.zeros_like(mask)
                    for contour in contours:
                        if cv2.contourArea(contour) >= 4:
                            cv2.drawContours(cleaned, [contour], -1, 255, cv2.FILLED)
                    cleaned = cv2.dilate(cleaned, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
                    result[y:y + h, x:x + w] = np.maximum(result[y:y + h, x:x + w], cleaned)
            if np.count_nonzero(result) > result.size * 0.4:
                raise ValueError("تحديد النص واسع جدًا؛ أرسل لوحة أو قصاصة أصغر")
            return Image.fromarray(result)
