import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from services.platforms.linkedin_photo import _PostPage, find_post_photo


POST = "https://www.linkedin.com/posts/utrecht-share-7524497580509286400-6KXD/"
IMAGE = "https://media.licdn.com/dms/image/v2/example/feedshare-shrink_1280/photo?e=1&t=abc"


def page(html):
    parsed = _PostPage()
    parsed.feed(html)
    return parsed


class LinkedInPhotoTests(unittest.TestCase):
    def resolve(self, pages, rendered=None):
        with patch("services.platforms.linkedin_photo._read_page", new_callable=AsyncMock, side_effect=pages) as read, patch(
            "services.platforms.linkedin_browser.browser_photo", new_callable=AsyncMock, return_value=rendered,
        ) as browser:
            self.browser = browser
            return asyncio.run(find_post_photo(POST, "test")), read

    def test_public_post_keeps_caption_and_signed_query(self):
        result, read = self.resolve([page(
            f'<meta property="og:url" content="{POST}"><meta property="og:image" content="{IMAGE.replace("&","&amp;")}">'
            '<meta property="og:description" content="Utrecht &amp; seus canais">'
        )])
        self.assertEqual((result.url, result.description), (IMAGE, "Utrecht & seus canais"))
        self.assertEqual(read.await_count, 1)

    def test_embed_skips_avatar_and_uses_attached_image(self):
        result, read = self.resolve([None, page(
            '<img src="https://media.licdn.com/dms/image/example/profile-displayphoto-shrink_100_100/x">'
            f'<img class="share-images__image" data-delayed-url="{IMAGE}">'
        )])
        self.assertEqual(result.url, IMAGE)
        self.assertTrue(read.await_args.args[1].endswith("urn:li:share:7524497580509286400"))

    def test_wrong_post_external_image_and_video_do_not_become_photo(self):
        for html in (
            f'<link rel="canonical" href="{POST.replace("7524497580509286400","123")}"><meta property="og:image" content="{IMAGE}">',
            f'<meta property="og:image" content="{IMAGE.replace("media.licdn.com","example.com")}">',
            f'<meta property="og:video" content="movie"><meta property="og:image" content="{IMAGE}">',
        ):
            with self.subTest(html=html):
                self.assertIsNone(self.resolve([page(html), None])[0])

    def test_missing_html_photo_renders_the_same_post(self):
        from services.platforms.linkedin_photo import PostPhoto
        rendered = PostPhoto(IMAGE, "Utrecht")
        self.assertEqual(self.resolve([None, None], rendered)[0], rendered)
        self.browser.assert_awaited_once_with(POST)

    def test_video_skips_browser(self):
        self.resolve([page('<meta property="og:type" content="video.other">')])
        self.browser.assert_not_awaited()
