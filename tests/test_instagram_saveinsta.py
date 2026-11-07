"""Standalone tests: python -m unittest discover -s tests -p test_instagram_saveinsta.py"""

import json
import base64
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
    def test_unsupported_urls_never_reach_saveinsta(self, curl):
        for url in (
            "https://www.instagram.com/stories/alice/",
            "https://www.instagram.com/stories/highlights/123/",
            "https://www.instagram.com/share/abc/",
            "https://www.instagram.com.evil.test/p/abc/",
        ):
            self.assertEqual(source.download_saveinsta(url, "/unused"), [])
        curl.assert_not_called()

    def story_markup(self, media_id, video=False):
        cache = base64.urlsafe_b64encode(str(media_id).encode()).decode()
        def proxy(url):
            payload = base64.urlsafe_b64encode(json.dumps({"url": url}).encode()).decode().rstrip("=")
            return "https://dl.snapcdn.app/saveinsta?token=header." + payload + ".signature"
        poster = proxy("https://scontent.cdninstagram.com/image.jpg?ig_cache_key=" + cache)
        if not video:
            return f'<li><a href="{poster}">Download Image</a></li>'
        mp4 = proxy("https://scontent.cdninstagram.com/video.mp4")
        return f'<li><i class="icon-dlvideo"></i><a href="{poster}">Download Thumbnail</a><a href="{mp4}">Download Video</a></li>'

    def test_story_video_matches_id_from_its_own_poster(self):
        markup = self.story_markup(123) + self.story_markup(456, video=True)
        links = source.parse_media_links(markup, story_id="456")
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0][0], "video")

    def test_missing_unknown_or_duplicate_story_ids_fail_closed(self):
        for markup in (self.story_markup(123), MIXED, self.story_markup(456) * 2):
            with self.subTest(markup=markup), self.assertRaises(ValueError):
                source.parse_media_links(markup, story_id="456")

    @patch.object(source, "_probe", return_value={"width": 720, "height": 900, "duration": 1})
    def test_story_download_only_sends_selected_item_and_profile_query(self, probe):
        calls = []
        base = self.fake_curl(calls)
        def curl(url, **kwargs):
            if url.endswith("/ajaxSearch"):
                calls.append((url, kwargs["data"]))
                return json.dumps({"status": "ok", "data": self.story_markup(123) + self.story_markup(456, True)})
            if kwargs.get("destination"):
                Path(kwargs["destination"]).write_bytes(b"video")
                return "video/mp4"
            return base(url, **kwargs)
        with tempfile.TemporaryDirectory() as folder, patch.object(source, "_curl", side_effect=curl):
            items = source.download_saveinsta("https://www.instagram.com/stories/alice/456/?tracking=1", folder)
            self.assertEqual([item["type"] for item in items], ["video"])
            self.assertEqual(len(list(Path(folder).iterdir())), 1)
        query = next(data for url, data in calls if url.endswith("/ajaxSearch"))
        self.assertEqual(query["q"], "https://www.instagram.com/stories/alice/")

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
        # Import the media service without a running bot, database or network.
        config = types.ModuleType("config")
        config.COBALT_API_KEY = ""
        config.COBALT_API_URL = ""

        cobalt = types.ModuleType("utils.cobalt_client")
        cobalt.fetch_cobalt_data = AsyncMock(return_value=None)

        httpx = types.ModuleType("httpx")
        class RejectingClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, *args, **kwargs):
                return types.SimpleNamespace(status_code=503)

        httpx.AsyncClient = RejectingClient

        spec = importlib.util.spec_from_file_location(
            "_instagram_media_tests", Path(__file__).resolve().parents[1]
            / "services/platforms/instagram_media.py",
        )
        cls.service = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
            spec.name: cls.service, "config": config,
            "httpx": httpx, "utils.cobalt_client": cobalt,
            "services.platforms.instagram_saveinsta": source,
        }):
            spec.loader.exec_module(cls.service)

    async def test_saveinsta_first_and_caption_from_embed(self):
        service = self.service
        items = [
            {"url": "photo.jpg", "type": "photo", "index": 0},
            {"url": "video.mp4", "type": "video", "index": 1},
        ]
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict("os.environ", {"INSTAGRAM_SAVEINSTA_ENABLED": "1"}):
                with patch.object(service, "download_saveinsta", return_value=items) as save:
                    with patch.object(service, "download_parth") as parth:
                        with patch.object(service, "_run_gallery_dl") as gallery:
                            with patch.object(service, "fetch_post_metadata", return_value={"description": "caption", "author": "alice"}):
                                result = await service.fetch_instagram_media(
                                    "https://www.instagram.com/p/abc/", folder
                                )
        self.assertEqual([item.type for item in result.media_list], ["photo", "video"])
        self.assertEqual(result.description, "caption")
        self.assertEqual(result.author, "alice")
        save.assert_called_once()
        parth.assert_not_called()
        gallery.assert_not_called()

    async def test_saveinsta_failure_parth_second_and_caption_recovered(self):
        service = self.service
        items = [
            {"url": "photo_01.jpg", "type": "photo", "index": 0},
            {"url": "photo_02.jpg", "type": "photo", "index": 1},
        ]
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict("os.environ", {"INSTAGRAM_SAVEINSTA_ENABLED": "1", "INSTAGRAM_PARTH_ENABLED": "1"}):
                with patch.object(service, "download_saveinsta", side_effect=ValueError("private")) as save:
                    with patch.object(service, "download_parth", return_value=items) as parth:
                        with patch.object(service, "_run_gallery_dl") as gallery:
                            with patch.object(service, "fetch_post_metadata", return_value={"description": "full caption", "author": "realracingclub"}):
                                result = await service.fetch_instagram_media(
                                    "https://www.instagram.com/p/DfcIKMLjNKh/", folder
                                )
        save.assert_called_once()
        parth.assert_called_once()
        gallery.assert_not_called()
        self.assertEqual(result.description, "full caption")
        self.assertEqual(result.author, "realracingclub")
        self.assertEqual([item.index for item in result.media_list], [0, 1])

    async def test_gallery_last_only_after_all_public_fallbacks(self):
        service = self.service
        calls = []
        last = service.InstagramVideo(
            "abc", "gallery caption", "alice",
            [service.InstagramMedia("file", "photo")]
        )

        def save(*args):
            calls.append("save")
            return []

        def parth(*args):
            calls.append("parth")
            return []

        async def cobalt(*args, **kwargs):
            calls.append("cobalt_local")
            return None

        async def public_post(*args, **kwargs):
            calls.append("cobalt_public")
            return types.SimpleNamespace(status_code=503)

        def ytdlp(*args):
            calls.append("ytdlp")
            return None

        def gallery(*args):
            calls.append("gallery")
            return last

        with tempfile.TemporaryDirectory() as folder:
            with patch.dict("os.environ", {"INSTAGRAM_SAVEINSTA_ENABLED": "1", "INSTAGRAM_PARTH_ENABLED": "1"}):
                with patch.object(service, "PUBLIC_COBALT_INSTANCES", ["https://example.invalid"]):
                    with patch.object(service, "download_saveinsta", side_effect=save):
                        with patch.object(service, "download_parth", side_effect=parth):
                            with patch.object(service, "fetch_cobalt_data", side_effect=cobalt):
                                with patch.object(service.httpx.AsyncClient, "post", side_effect=public_post):
                                    with patch.object(service, "_run_ytdlp", side_effect=ytdlp):
                                        with patch.object(service, "_run_gallery_dl", side_effect=gallery):
                                            result = await service.fetch_instagram_media(
                                                "https://www.instagram.com/p/abc/", folder
                                            )
        self.assertIs(result, last)
        self.assertEqual(
            calls, ["save", "parth", "cobalt_local", "cobalt_public", "ytdlp", "gallery"]
        )

    async def test_saveinsta_disabled_keeps_parth_enabled(self):
        service = self.service
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict("os.environ", {"INSTAGRAM_SAVEINSTA_ENABLED": "0", "INSTAGRAM_PARTH_ENABLED": "1"}):
                with patch.object(service, "download_saveinsta") as save:
                    with patch.object(service, "download_parth", return_value=[
                        {"url": "photo.jpg", "type": "photo"}
                    ]) as parth:
                        with patch.object(service, "fetch_post_metadata", return_value={}):
                            result = await service.fetch_instagram_media(
                                "https://www.instagram.com/p/abc/", folder
                            )
        self.assertEqual(len(result.media_list), 1)
        save.assert_not_called()
        parth.assert_called_once()

    async def test_metadata_error_does_not_discard_download(self):
        service = self.service
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(service, "download_saveinsta", return_value=[
                {"url": "video.mp4", "type": "video"}
            ]):
                with patch.object(service, "fetch_post_metadata", side_effect=TimeoutError):
                    result = await service.fetch_instagram_media(
                        "https://www.instagram.com/p/abc/", folder
                    )
        self.assertEqual(len(result.media_list), 1)
        self.assertEqual(result.description, "")

    async def test_story_bypasses_other_extractors(self):
        service = self.service
        handler = types.ModuleType("services.platforms.instagram_stories")
        handler.fetch_public_instagram_story = AsyncMock(return_value=None)
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(sys.modules, {handler.__name__: handler}):
                with patch.object(service, "download_saveinsta") as save:
                    with patch.object(service, "download_parth") as parth:
                        with patch.object(service, "_run_gallery_dl") as gallery:
                            result = await service.fetch_instagram_media(
                                "https://www.instagram.com/stories/alice/456/", folder
                            )
        self.assertIsNone(result)
        handler.fetch_public_instagram_story.assert_awaited_once()
        save.assert_not_called()
        parth.assert_not_called()
        gallery.assert_not_called()

    async def test_cookie_free_extractor_flags(self):
        service = self.service
        def command_result(cmd, **kwargs):
            return types.SimpleNamespace(returncode=1, stderr="login required", stdout="")
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(service.subprocess, "run", side_effect=command_result) as runner:
                service._run_gallery_dl("https://www.instagram.com/p/abc/", folder, "abc")
                gallery_cmd = runner.call_args.args[0]
                self.assertIn("--config-ignore", gallery_cmd)
                self.assertNotIn("--cookies", gallery_cmd)
                self.assertNotIn("--cookies-from-browser", gallery_cmd)

            with patch.object(service.subprocess, "run", side_effect=command_result) as runner:
                service._run_ytdlp("https://www.instagram.com/p/abc/", folder, "abc")
                meta_cmd = runner.call_args_list[0].args[0]
                self.assertIn("--ignore-config", meta_cmd)
                self.assertIn("--no-cookies", meta_cmd)
                self.assertIn("--no-cookies-from-browser", meta_cmd)
                self.assertNotIn("--cookies", meta_cmd)


if __name__ == "__main__":
    unittest.main()
