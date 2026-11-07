"""Isolated tests for the optional parth-dl post fallback."""

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from services.platforms import instagram_parth as source


class ParthFallbackTests(unittest.TestCase):
    def test_post_carousel_preserves_mixed_media_order(self):
        def fake_download(url, output_path):
            folder = Path(output_path)
            photo = folder / "post_01.jpg"
            video = folder / "post_02.mp4"
            photo.write_bytes(b"valid-photo")
            video.write_bytes(b"valid-video")
            return [str(photo), str(video)]

        with tempfile.TemporaryDirectory() as root:
            with patch.dict(sys.modules, {"parth_dl": types.SimpleNamespace(download=fake_download)}):
                with patch.object(source, "_probe", return_value={
                    "width": 1080, "height": 1350, "duration": 0
                }):
                    items = source.download_parth(
                        "https://www.instagram.com/p/DfcIKMLjNKh/?cplk=tracking", root
                    )
            self.assertEqual([item["type"] for item in items], ["photo", "video"])
            self.assertEqual([item["index"] for item in items], [0, 1])
            self.assertTrue(all(Path(item["url"]).is_file() for item in items))

    def test_failed_carousel_deletes_partial_files(self):
        def fake_download(url, output_path):
            file = Path(output_path) / "partial.jpg"
            file.write_bytes(b"partial")
            raise RuntimeError("Second media failed")

        with tempfile.TemporaryDirectory() as root:
            with patch.dict(sys.modules, {"parth_dl": types.SimpleNamespace(download=fake_download)}):
                with self.assertRaises(RuntimeError):
                    source.download_parth("https://www.instagram.com/p/abc/", root)
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_unsupported_url_never_invokes_parth(self):
        fake_download = unittest.mock.Mock()
        with patch.dict(sys.modules, {"parth_dl": types.SimpleNamespace(download=fake_download)}):
            for url in (
                "https://www.instagram.com/stories/alice/123/",
                "https://www.instagram.com/p/abc/../../secret",
                "https://instagram.com.evil.test/p/abc/",
                "http://www.instagram.com/p/abc/",
            ):
                self.assertEqual(source.download_parth(url, "/unused"), [])
        fake_download.assert_not_called()

    def test_rejects_unexpected_paths_and_removes_temporary_files(self):
        def fake_download(url, output_path):
            file = Path(output_path).parent / "outside.jpg"
            file.write_bytes(b"outside")
            return [str(file)]

        with tempfile.TemporaryDirectory() as root:
            with patch.dict(sys.modules, {"parth_dl": types.SimpleNamespace(download=fake_download)}):
                with self.assertRaisesRegex(ValueError, "outside"):
                    source.download_parth("https://www.instagram.com/p/abc/", root)
            self.assertEqual(sorted(p.name for p in Path(root).iterdir()), ["outside.jpg"])


if __name__ == "__main__":
    unittest.main()
