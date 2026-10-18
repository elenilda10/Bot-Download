"""Offline regressions for the XVideos router's URL and player parsing."""
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "services" / "platforms" / "xvideos_media.py"


def load_module():
    # Avoid requiring production secrets just to test pure parsing helpers.
    original = sys.modules.get("config")
    dummy = types.ModuleType("config")
    dummy.MAX_FILE_SIZE = 1500 * 1024 * 1024
    sys.modules["config"] = dummy
    try:
        spec = importlib.util.spec_from_file_location("xvideos_media_test", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        if original is None:
            sys.modules.pop("config", None)
        else:
            sys.modules["config"] = original


service = load_module()


class XVideosMediaTests(unittest.IsolatedAsyncioTestCase):
    def test_video_link_extracts_canonical_https_url(self):
        url = "https://www.xvideos.com/video.oltvhef0c13/example_title?foo=bar"
        self.assertEqual(
            service.extract_xvideos_url("watch " + url),
            "https://www.xvideos.com/video.oltvhef0c13/example_title",
        )

    def test_rejects_unrelated_and_unsafe_urls(self):
        self.assertIsNone(service.extract_xvideos_url("https://www.xvideos.com.evil.com/video.123/test"))
        self.assertIsNone(service.extract_xvideos_url("http://www.xvideos.com/video.123/test"))
        self.assertIsNone(service.extract_xvideos_url("https://example.org/video.123/test"))
        self.assertIsNone(service._cdn_media_url("http://hls-cdn77.xvideos-cdn.com/playlist.m3u8"))
        self.assertIsNone(service._cdn_media_url("https://hls-cdn77.xvideos-cdn.com.evil.org/x"))
        self.assertIsNone(service._cdn_media_url("https://localhost/media.m3u8"))

    def test_accepts_only_https_media_cdn(self):
        url = "https://hls-cdn77.xvideos-cdn.com/path/master.m3u8?temporary=secret"
        self.assertEqual(service._cdn_media_url(url), url)
        self.assertIsNone(service._cdn_media_url("https://user@hls-cdn77.xvideos-cdn.com/test"))
        self.assertIsNone(service._cdn_media_url("https://hls-cdn77.xvideos-cdn.com:8080/test"))

    def test_extracts_encoded_player_id_and_cdn(self):
        html = "html5player.setEncodedIdVideo('ENCODED_ab12'); html5player.setIdCdnHLS(77);"
        self.assertEqual(service._player_arg(html, "setEncodedIdVideo"), "ENCODED_ab12")
        self.assertEqual(service._player_arg(html, "setIdCdnHLS"), "77")
        self.assertIsNone(service._player_arg(html, "setVideoHLS"))

    async def test_prefers_hls_without_calling_mp4_on_success(self):
        hls = "https://hls-cdn77.xvideos-cdn.com/index.m3u8"
        mp4 = "https://mp4-cdn77.xvideos-cdn.com/video.mp4"
        expected = service.XVideosDownload(Path("/tmp/test.mp4"), 1920, 1080, 800)
        with patch.object(service, "_discover_sources", AsyncMock(return_value=[hls, mp4])), \
                patch.object(service, "_download_source", AsyncMock(return_value=expected)) as downloader:
            result = await service.download_xvideos("https://www.xvideos.com/video.oltvhef0c13/title", Path("/tmp/test"))
            self.assertEqual(result.height, 1080)
            downloader.assert_awaited_once()
            self.assertEqual(downloader.call_args.args[0], hls)


if __name__ == "__main__":
    unittest.main()
