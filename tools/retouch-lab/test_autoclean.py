import io
import unittest
from unittest.mock import MagicMock

import numpy as np
from PIL import Image

from free_cloud import AutomaticCleaner, FreeCloudError, HostedLama, png, context_boxes, work_regions


class Detector:
    def __init__(self, mask):
        self.mask = mask
        self.calls = 0

    def detect(self, image):
        self.calls += 1
        return self.mask if self.calls == 1 else Image.new("L", image.size)


class AutomaticCleanupTests(unittest.TestCase):
    def setUp(self):
        self.mask = Image.new("L", (120, 100))
        self.mask.paste(255, (35, 30, 65, 60))

    def test_plain_white_bubble_never_contacts_cloud(self):
        source = Image.new("RGB", self.mask.size, "white")
        source.paste("black", (40, 35, 60, 55))
        def unexpected():
            self.fail("A plain bubble must not consume cloud quota")
        result, _, info = AutomaticCleaner(Detector(self.mask), unexpected).clean(png(source))
        self.assertEqual(np.array(Image.open(io.BytesIO(result))).min(), 255)
        self.assertEqual(info["cloud_jobs"], 0)

    def test_cloud_edits_preserve_every_unmasked_pixel(self):
        source = Image.new("RGB", self.mask.size, (20, 130, 80))
        class Client:
            def restore(self, image, mask, timeout):
                return Image.new("RGB", image.size, "blue")
            def close(self):
                pass
        result, _, info = AutomaticCleaner(Detector(self.mask), Client).clean(png(source))
        restored = np.array(Image.open(io.BytesIO(result)))
        outside = np.array(self.mask) == 0
        np.testing.assert_array_equal(restored[outside], np.array(source)[outside])
        self.assertEqual(tuple(restored[40, 45]), (0, 0, 255))
        self.assertEqual(info["cloud_jobs"], 1)

    def test_transport_decodes_streamed_image_and_restores_expected_dimensions(self):
        client = HostedLama()
        client.session.close()
        client.session = MagicMock()
        response = client.session.post.return_value.__enter__.return_value
        response.iter_content.return_value = [png(Image.new("RGB", (64, 64), "blue"))]
        result = client.restore(Image.new("RGB", self.mask.size), self.mask, 30)
        self.assertEqual(result.size, self.mask.size)
        self.assertEqual(result.getpixel((1, 1)), (0, 0, 255))

    def test_detected_replacement_lettering_is_not_accepted(self):
        class RepeatedDetector:
            def detect(_, image):
                return self.mask
        class Client:
            def restore(_, image, mask, timeout):
                return Image.new("RGB", image.size, "green")
            def close(_):
                pass
        with self.assertRaisesRegex(FreeCloudError, "حروفًا"):
            AutomaticCleaner(RepeatedDetector(), Client).clean(png(Image.new("RGB", self.mask.size, "green")))

    def test_cloud_refusal_does_not_return_a_fake_cleaned_image(self):
        class Refused:
            def restore(self, image, mask, timeout):
                raise RuntimeError("Free GPU quota exhausted")
            def close(self):
                pass
        with self.assertRaises(FreeCloudError):
            AutomaticCleaner(Detector(self.mask), Refused).clean(png(Image.new("RGB", self.mask.size, "green")))

    def test_no_detection_is_reported_instead_of_claiming_success(self):
        cleaner = AutomaticCleaner(Detector(Image.new("L", self.mask.size)))
        with self.assertRaises(FreeCloudError):
            cleaner.clean(png(Image.new("RGB", self.mask.size)))

    def test_long_page_tiles_cover_mask_once_with_bounded_context(self):
        mask = Image.new("L", (1000, 2800))
        mask.paste(255, (100, 100, 900, 2700))
        regions = work_regions(mask, context_boxes(mask, mask.size), mask.size)
        coverage = np.zeros((2800, 1000), dtype=np.uint8)
        for (x0, y0, x1, y1), selected in regions:
            self.assertLessEqual(max(x1-x0, y1-y0), 896)
            coverage[y0:y1, x0:x1] += (np.array(selected) > 0).astype(np.uint8)
        np.testing.assert_array_equal(coverage, (np.array(mask) > 0).astype(np.uint8))


if __name__ == "__main__":
    unittest.main()
