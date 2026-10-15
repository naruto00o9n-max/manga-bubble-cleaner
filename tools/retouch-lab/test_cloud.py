import unittest
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw

from cloud import RetouchClient, ServiceError, decode_image, encode_image


class DownloadResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def raise_for_status(self):
        pass

    def iter_content(self, _):
        yield encode_image(Image.new("RGB", (128, 128), "red"), "PNG")


class Session:
    def get(self, *_args, **_kwargs):
        return DownloadResponse()


class FixtureClient(RetouchClient):
    def __init__(self, rejection=None):
        self.protocol = "v5"
        self.profile = {}
        self.session = Session()
        self.rejection = rejection
        self.calls = []
        self.polls = 0

    def signed_fields(self, mode):
        return {}

    def request_json(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if "downLoad" not in path:
            return self.rejection or {"success": True, "code": 0, "data": "own-job"}
        self.polls += 1
        if self.polls == 1:
            return {"success": False, "code": 409, "data": None}
        return {"success": True, "code": 0, "data": "https://service-results.example/result.webp"}


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.image = Image.new("RGB", (240, 180), "blue")
        self.mask = Image.new("L", self.image.size, 0)
        ImageDraw.Draw(self.mask).rectangle((30, 40, 79, 89), fill=255)
        self.source = encode_image(self.image, "PNG")
        self.selection = encode_image(self.mask, "PNG")

    @patch("cloud.time.sleep")
    def test_cloud_crop_resize_preserves_every_unselected_pixel(self, _):
        client = FixtureClient()
        result = decode_image(client.clean(self.source, self.selection))
        difference = ImageChops.difference(self.image, result)
        self.assertEqual(result.size, self.image.size)
        self.assertIsNone(ImageChops.multiply(difference, ImageChops.invert(self.mask).convert("RGB")).getbbox())
        self.assertEqual(result.getpixel((40, 50)), (255, 0, 0))
        self.assertEqual(client.polls, 2)
        fields = client.calls[0][1]["files"]
        self.assertEqual(set(fields), {"base64", "maskBase64"})

    def test_no_result_or_fallback_when_provider_rejects_balance(self):
        client = FixtureClient({"success": False, "code": 1406, "message": "Insufficient balance"})
        with self.assertRaisesRegex(ServiceError, "1406"):
            client.clean(self.source, self.selection)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.polls, 0)

    def test_wrong_mask_dimensions_fail_before_network(self):
        client = FixtureClient()
        with self.assertRaises(ValueError):
            client.clean(self.source, encode_image(Image.new("L", (10, 10), 255), "PNG"))
        self.assertFalse(client.calls)

    def test_empty_mask_fails_before_network(self):
        client = FixtureClient()
        with self.assertRaises(ValueError):
            client.clean(self.source, encode_image(Image.new("L", self.image.size, 0), "PNG"))
        self.assertFalse(client.calls)


if __name__ == "__main__":
    unittest.main()
