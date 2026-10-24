"""Regression tests for Kwai's public HTML video extraction (no network)."""

import pytest

from services.platforms import kwai_media


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
