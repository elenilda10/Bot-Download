"""Regression tests for Kwai's public HTML video extraction (no network)."""

import importlib.util
from pathlib import Path

import pytest

# Isolate public-page extraction from the bot's mandatory runtime secrets
# and database services, so this test runs directly on a developer machine.
_SPEC = importlib.util.spec_from_file_location(
    "kwai_media",
    Path(__file__).resolve().parents[1] / "services/platforms/kwai_media.py",
)
assert _SPEC and _SPEC.loader
kwai_media = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(kwai_media)


@pytest.mark.parametrize("url", [
    "https://k.kwai.com/p/CMtEC74X",
    "https://s.kw.ai/p/CMtEC74X",
    "https://www.kwai.com/@gabyvalence/video/5200118832330956026",
    "https://m.kwai.com/@gabyvalence/photo/5200118832330956026",
])
def test_kwai_links_are_detected(url):
    assert kwai_media.is_kwai_url(url)


@pytest.mark.parametrize("url", [
    "https://fakekwai.com/video/123",
    "https://kwai.com.evil.example/video/123",
    "https://example.org/video/123",
])
def test_unrelated_links_are_not_detected(url):
    assert not kwai_media.is_kwai_url(url)


def test_prefers_og_video_over_an_unrelated_recommended_video():
    wanted = "https://aws-br-cdn.kwai.net/upic/wanted.mp4?tag=abc&v=1"
    unrelated = "https://aws-br-cdn.kwai.net/upic/other.mp4"
    page = (
        f'<meta property="og:video" content="{wanted.replace("&", "&amp;")}">'
        f'<script>{{"videoUrl":"{unrelated}"}}</script>'
    )
    assert kwai_media._extract_media_url(page) == wanted


def test_reads_escaped_video_url_with_signed_query():
    wanted = "https://aws-br-cdn.kwai.net/upic/clip.mp4?tag=abc&exp=123"
    escaped = wanted.replace("/", r"\u002F").replace("&", r"\u0026")
    page = '{"videoUrl":"' + escaped + '"}'
    assert kwai_media._extract_media_url(page) == wanted


def test_reads_unlabelled_mp4_as_fallback():
    wanted = "https://aws-br-cdn.kwai.net/upic/clip.mp4?tag=abc"
    assert kwai_media._extract_media_url(f"<script>const video = '{wanted}';</script>") == wanted


def test_rejects_external_hosts_and_images():
    assert kwai_media._extract_media_url(
        '<meta property="og:video" content="https://127.0.0.1/private.mp4">'
    ) is None
    assert kwai_media._extract_media_url(
        '{"videoUrl":"https://evil.example/content.mp4"}'
    ) is None
    assert kwai_media._extract_media_url(
        '{"videoUrl":"https://aws-br-cdn.kwai.net/upic/cover.webp"}'
    ) is None


@pytest.mark.asyncio
async def test_short_share_redirect_uses_target_post_metadata(monkeypatch):
    final = "https://www.kwai.com/@gabyvalence/video/5200118832330956026"
    wanted = "https://aws-br-cdn.kwai.net/upic/wanted.mp4?tag=signed"
    page = (
        '<meta property="og:description" content="Video de teste">'
        f'<script>{{"videoUrl":"{wanted}"}}</script>'
    )

    class FakeResponse:
        status = 200
        url = final

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def text(self, **_kwargs):
            return page

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def get(self, url, **kwargs):
            assert url == "https://k.kwai.com/p/CMtEC74X"
            assert kwargs["allow_redirects"]
            return FakeResponse()

    monkeypatch.setattr(kwai_media.aiohttp, "ClientSession", lambda **_kwargs: FakeSession())

    result = await kwai_media.extract_kwai_video_page("https://k.kwai.com/p/CMtEC74X")
    assert result == {
        "page_url": final,
        "media_url": wanted,
        "title": "Video de teste",
        "author": "gabyvalence",
    }

@pytest.mark.asyncio
async def test_retries_canonical_post_when_short_share_is_empty(monkeypatch):
    short = "https://k.kwai.com/p/CMtEC74X"
    canonical = "https://www.kwai.com/@gabyvalence/video/5200118832330956026"
    with_tracking = canonical + "?sharePage=photo&share_uid=1234"
    media = "https://aws-br-cdn.kwai.net/upic/clip.mp4?tag=signed"
    requested_urls = []

    class FakeResponse:
        status = 200

        def __init__(self, url, body):
            self.url = url
            self.body = body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def text(self, **_kwargs):
            return self.body

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def get(self, url, **_kwargs):
            requested_urls.append(url)
            if url == short:
                return FakeResponse(with_tracking, "<html>Loading...</html>")
            assert url == canonical
            return FakeResponse(canonical, f'<meta property="og:video" content="{media}">')

    monkeypatch.setattr(
        kwai_media.aiohttp, "ClientSession", lambda **_kwargs: FakeSession()
    )

    result = await kwai_media.extract_kwai_video_page(short)
    assert result
    assert result["media_url"] == media
    assert requested_urls == [short, canonical]


@pytest.mark.asyncio
async def test_chrome_fallback_recovers_media_from_video_free_html(monkeypatch):
    short = "https://k.kwai.com/p/CMtEC74X"
    canonical = "https://www.kwai.com/@gabyvalence/video/5200118832330956026"
    tracked_url = canonical + "?share_uid=123"
    wanted = "https://aws-br-cdn.kwai.net/upic/chrome-video.mp4?sign=abc&exp=123"
    requested = []
    chrome_urls = []

    class FakeResponse:
        status = 200
        url = tracked_url

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def text(self, **_kwargs):
            return "<html>app shell without MP4</html>"

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def get(self, url, **_kwargs):
            requested.append(url)
            return FakeResponse()

    def chrome_fetch(url):
        chrome_urls.append(url)
        return canonical, f'<meta property="og:video" content="{wanted.replace("&", "&amp;")}">'

    monkeypatch.setattr(kwai_media.aiohttp, "ClientSession", lambda **_kwargs: FakeSession())
    monkeypatch.setattr(kwai_media, "_fetch_kwai_chrome_page", chrome_fetch)

    result = await kwai_media.extract_kwai_video_page(short)
    assert result is not None
    assert result["media_url"] == wanted
    assert result["page_url"] == canonical
    assert result["author"] == "gabyvalence"
    assert requested == [short, canonical]
    assert chrome_urls == [canonical]


@pytest.mark.asyncio
async def test_chrome_fallback_rejects_untrusted_media_and_fails_closed(monkeypatch):
    short = "https://k.kwai.com/p/CMtEC74X"
    canonical = "https://www.kwai.com/@gabyvalence/video/5200118832330956026"

    class FakeResponse:
        status = 200
        url = canonical

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def text(self, **_kwargs):
            return "Kwai app shell"

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def get(self, url, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(kwai_media.aiohttp, "ClientSession", lambda **_kwargs: FakeSession())
    monkeypatch.setattr(
        kwai_media, "_fetch_kwai_chrome_page",
        lambda *_args: (
            canonical,
            '<meta property="og:video" content="http://127.0.0.1/internal.mp4">',
        ),
    )
    assert await kwai_media.extract_kwai_video_page(short) is None
