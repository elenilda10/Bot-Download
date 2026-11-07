import asyncio
import unittest
from unittest.mock import AsyncMock

from services.links.detection import extract_supported_links
from services.links.linkedin import LinkedInRedirectError, _PageLinks, _resolve


SHORT = "https://lnkd.in/p/eyWCpHiH"
POST = "https://www.linkedin.com/posts/test-ugcPost-7524581986955386880-O7jh/"
FEED = "https://www.linkedin.com/feed/update/urn:li:activity:7524581986955386880"


class LinkedInLinksTest(unittest.TestCase):
    def test_short_link_chain(self):
        fetch = AsyncMock(side_effect=["/s/example", POST + "?utm_source=share#fragment"])
        self.assertEqual(asyncio.run(_resolve(SHORT, fetch)), POST)
        self.assertEqual(fetch.await_count, 2)

    def test_direct_posts_need_no_http_request(self):
        for url in (POST, FEED):
            fetch = AsyncMock()
            self.assertEqual(asyncio.run(_resolve(url + "?utm_source=test", fetch)), url)
            fetch.assert_not_awaited()

    def test_rejects_unrelated_targets_and_loops(self):
        for target in (SHORT, None, "https://example.com/", "https://www.linkedin.com/login",
                       "https://lnkd.in@localhost/p/test", "https://lnkd.in:8443/p/a"):
            fetch = AsyncMock(return_value=target)
            with self.subTest(target=target), self.assertRaises(LinkedInRedirectError):
                asyncio.run(_resolve(SHORT, fetch))
            fetch.assert_awaited_once_with(SHORT)

    def test_html_destination_and_batch_detection(self):
        parser = _PageLinks()
        parser.feed(f'<meta http-equiv="refresh" content="0; url={POST}?a=1&amp;b=2">')
        self.assertEqual(parser.targets, [POST + "?a=1&b=2"])
        self.assertEqual(extract_supported_links(f"{SHORT} {POST}?utm_source=test {SHORT}"),
                         [("linkedin", SHORT), ("linkedin", POST)])

    def test_reset_retries_without_automatically_following_redirects(self):
        import aiohttp
        from unittest.mock import Mock, patch
        from services.links.linkedin import _request

        session = Mock()
        session.get.side_effect = aiohttp.ServerDisconnectedError()
        proc = Mock(returncode=0)
        proc.communicate = AsyncMock(return_value=(POST.encode(), None))
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc) as spawn:
            self.assertEqual(asyncio.run(_request(session, SHORT)), POST)
        session.get.assert_called_once_with(SHORT, allow_redirects=False)
        self.assertIn("-4", spawn.await_args.args)
        self.assertNotIn("-L", spawn.await_args.args)
