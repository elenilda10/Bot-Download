"""Standalone tests: python -m unittest discover -s tests -p test_instagram_saveinsta.py"""

import json
import importlib.util
import sys
import types
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

# Load this standard-library-only client without booting the bot/database.
_spec = importlib.util.spec_from_file_location(
    "_instagram_saveinsta_tests",
    Path(__file__).resolve().parents[1] / "services/platforms/instagram_saveinsta.py",
)
source = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = source
_spec.loader.exec_module(source)

MIXED = '''<ul><li><i class="icon icon-dlphoto"></i>
<a href="https://dl.snapcdn.app/saveinsta?token=photo&amp;x=1">Download Image</a></li>
<li><i class="icon icon-dlvideo"></i>
<a href="https://dl.snapcdn.app/saveinsta?token=thumb">Download Thumbnail</a>
<a href="https://dl.snapcdn.app/saveinsta?token=video"><span>Download Video</span></a>
</li></ul>'''


class SaveInstaTests(unittest.TestCase):
    def test_mixed_album_keeps_order_and_excludes_thumbnail(self):
        self.assertEqual(source.parse_media_links(MIXED), [
            ("photo", "https://dl.snapcdn.app/saveinsta?token=photo&x=1"),
            ("video", "https://dl.snapcdn.app/saveinsta?token=video"),
        ])

    def test_incomplete_album_is_not_returned_as_success(self):
        with self.assertRaises(ValueError):
            source.parse_media_links(MIXED.replace("Download Video", "Unavailable"))

    def test_untrusted_download_host_is_rejected(self):
        with self.assertRaises(ValueError):
            source.parse_media_links(MIXED.replace("dl.snapcdn.app", "dl.snapcdn.app.evil.test"))

    def embed(self, shortcode="abc", caption="Texto 👀\\n\\n#futebol"):
        context = {
            "context": {"shortcode": shortcode},
            "gql_data": {"shortcode_media": {
                "shortcode": shortcode,
                "edge_media_to_caption": {"edges": [{"node": {"text": caption}}]},
                "owner": {"username": "alice"},
            }},
        }
        return json.dumps({"contextJSON": json.dumps(context)})

    def test_embed_caption_preserves_text_emoji_and_linebreaks(self):
        metadata = source.parse_embed_metadata(self.embed(), "abc")
        self.assertEqual(metadata, {"description": "Texto 👀\n\n#futebol", "author": "alice"})

    def test_embed_must_match_requested_post(self):
        self.assertEqual(source.parse_embed_metadata(self.embed("other"), "abc"), {})
        self.assertEqual(source.parse_embed_metadata('<div class="Caption">Comment or generic text</div>', "abc"), {})

    def test_empty_embed_caption_is_valid(self):
        self.assertEqual(source.parse_embed_metadata(self.embed(caption=""), "abc"),
                         {"description": "", "author": "alice"})

    def test_html_caption_without_json_excludes_author_and_comments(self):
        markup = '''<div class="Caption"><a class="CaptionUsername"
            href="https://www.instagram.com/alice/">alice</a><br /><br />
            Texto 👀<br /><br /><a href="/explore/tags/test/">#test</a>
            <div class="CaptionComments"><a data-log-event="captionCommentsClick"
            href="https://www.instagram.com/p/abc/">View all comments</a></div></div>'''
        result = source.parse_embed_metadata(markup, "abc")
        self.assertEqual(result["author"], "alice")
        self.assertIn("Texto 👀\n\n", result["description"])
        self.assertIn("#test", result["description"])
        self.assertNotIn("alice", result["description"])
        self.assertNotIn("comments", result["description"])
        self.assertEqual(source.parse_embed_metadata(markup, "other"), {})

    def test_html_caption_without_post_identity_is_rejected(self):
        self.assertEqual(source.parse_embed_metadata('<div class="Caption">Text</div>', "abc"), {})

    @patch.object(source, "_curl")
    def test_stories_and_other_hosts_never_reach_saveinsta(self, curl):
        for url in (
            "https://www.instagram.com/stories/alice/123/",
            "https://www.instagram.com/share/abc/",
            "https://www.instagram.com.evil.test/p/abc/",
        ):
            self.assertEqual(source.download_saveinsta(url, "/unused"), [])
        curl.assert_not_called()

    def fake_curl(self, calls, *, fail_video=False):
        def curl(url, *, data=None, destination=None):
            calls.append((url, data))
            if url.endswith("/en1"):
                return 'var k_exp="expiry", k_token="page-token";'
            if url.endswith("/userverify"):
                return json.dumps({"success": True, "token": "verify-token"})
            if url.endswith("/ajaxSearch"):
                return json.dumps({"status": "ok", "data": MIXED})
            Path(destination).write_bytes(b"media")
            if "token=video" in url:
                if fail_video:
                    raise RuntimeError("upstream failure")
                return "video/mp4"
            return "image/jpeg"
        return curl

    @patch.object(source, "_probe", return_value={"width": 720, "height": 900, "duration": 0})
    def test_download_canonicalizes_and_returns_local_album(self, probe):
        calls = []
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(source, "_curl", side_effect=self.fake_curl(calls)):
                items = source.download_saveinsta("https://www.instagram.com/p/abc/?tracking=1", folder)
            self.assertEqual([item["type"] for item in items], ["photo", "video"])
            self.assertEqual([item["index"] for item in items], [0, 1])
            self.assertTrue(all(Path(item["url"]).is_file() for item in items))
        search = next(data for url, data in calls if url.endswith("/ajaxSearch"))
        self.assertEqual(search["q"], "https://www.instagram.com/p/abc/")
        self.assertEqual(search["cftoken"], "verify-token")

    @patch.object(source, "_probe", return_value={"width": 720, "height": 900, "duration": 0})
    def test_second_item_failure_removes_entire_partial_album(self, probe):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(source, "_curl", side_effect=self.fake_curl([], fail_video=True)):
                with self.assertRaises(RuntimeError):
                    source.download_saveinsta("https://www.instagram.com/p/abc/", folder)
            self.assertEqual(list(Path(folder).iterdir()), [])


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # Extraction routing does not require Telegram, a database or HTTP clients.
        config = types.ModuleType("config")
        config.COBALT_API_KEY = ""
        config.COBALT_API_URL = ""
        cobalt = types.ModuleType("utils.cobalt_client")
        cobalt.fetch_cobalt_data = AsyncMock()
        spec = importlib.util.spec_from_file_location(
            "_instagram_media_tests", Path(__file__).resolve().parents[1]
            / "services/platforms/instagram_media.py",
        )
        cls.service = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
            spec.name: cls.service, "config": config,
            "httpx": types.ModuleType("httpx"), "utils.cobalt_client": cobalt,
            "services.platforms.instagram_saveinsta": source,
        }):
            spec.loader.exec_module(cls.service)

    async def test_gallery_success_preserves_caption_without_saveinsta(self):
        service = self.service
        original = service.InstagramVideo("abc", "original caption", "alice", [service.InstagramMedia("file", "photo")])
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(service, "_run_gallery_dl", return_value=original), patch.object(service, "download_saveinsta") as save:
                result = await service.fetch_instagram_media("https://www.instagram.com/p/abc/", folder)
        self.assertIs(result, original)
        save.assert_not_called()

    async def test_saveinsta_album_uses_existing_media_contract(self):
        service = self.service
        items = [{"url": "photo.jpg", "type": "photo", "index": 0}, {"url": "video.mp4", "type": "video", "index": 1}]
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict("os.environ", {"INSTAGRAM_SAVEINSTA_ENABLED": "1"}), patch.object(service, "_run_gallery_dl", return_value=None), patch.object(service, "download_saveinsta", return_value=items), patch.object(service, "fetch_post_metadata", return_value={"description": "caption", "author": "alice"}), patch.object(service, "_run_ytdlp") as ytdlp:
                result = await service.fetch_instagram_media("https://www.instagram.com/p/abc/", folder)
        self.assertEqual([item.type for item in result.media_list], ["photo", "video"])
        self.assertEqual(result.description, "caption")
        self.assertEqual(result.author, "alice")
        ytdlp.assert_not_called()

    async def test_metadata_failure_keeps_successful_media(self):
        service = self.service
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict("os.environ", {"INSTAGRAM_SAVEINSTA_ENABLED": "1"}), patch.object(service, "_run_gallery_dl", return_value=None), patch.object(service, "download_saveinsta", return_value=[{"url": "video.mp4", "type": "video"}]), patch.object(service, "fetch_post_metadata", side_effect=TimeoutError):
                result = await service.fetch_instagram_media("https://www.instagram.com/p/abc/", folder)
        self.assertEqual(len(result.media_list), 1)
        self.assertEqual(result.description, "")

    async def test_gallery_without_caption_also_recovers_metadata(self):
        service = self.service
        original = service.InstagramVideo("abc", "", "instagram_user", [service.InstagramMedia("file", "photo")])
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(service, "_run_gallery_dl", return_value=original), patch.object(service, "fetch_post_metadata", return_value={"description": "recovered", "author": "alice"}):
                result = await service.fetch_instagram_media("https://www.instagram.com/p/abc/", folder)
        self.assertEqual(result.description, "recovered")
        self.assertEqual(result.author, "alice")

    async def test_failure_disabled_and_story_continue_to_existing_fallback(self):
        service = self.service
        original = service.InstagramVideo("abc", "caption", "alice", [service.InstagramMedia("file", "video")])
        cases = [
            ("https://www.instagram.com/p/abc/", "1", 1),
            ("https://www.instagram.com/p/abc/", "0", 0),
            ("https://www.instagram.com/stories/alice/123/", "1", 0),
        ]
        for url, enabled, calls in cases:
            with self.subTest(url=url, enabled=enabled), tempfile.TemporaryDirectory() as folder:
                with patch.dict("os.environ", {"INSTAGRAM_SAVEINSTA_ENABLED": enabled}), patch.object(service, "_run_gallery_dl", return_value=None), patch.object(service, "download_saveinsta", side_effect=RuntimeError("upstream")) as save, patch.object(service, "_run_ytdlp", return_value=original):
                    result = await service.fetch_instagram_media(url, folder)
                self.assertIs(result, original)
                self.assertEqual(save.call_count, calls)


if __name__ == "__main__":
    unittest.main()
