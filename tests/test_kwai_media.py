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


def test_share_preview_extracts_original_caption_without_likes_comments():
    raw = (
        '13571 Like(s), 100 Comment(s). Kwai video from '
        'SPORTS(@TL.ESPORTE): "#ufc #mma #porrada #boxe #Lutas"'
    )
    caption, creator = kwai_media._parse_kwai_post_description(raw)
    assert caption == "#ufc #mma #porrada #boxe #Lutas"
    assert creator == "SPORTS (@TL.ESPORTE)"
    assert kwai_media.format_kwai_post_caption(caption, creator) == (
        "👤 SPORTS (@TL.ESPORTE)\n\n#ufc #mma #porrada #boxe #Lutas"
    )


def test_share_caption_handles_unicode_newlines_and_html_entities():
    caption, creator = kwai_media._parse_kwai_post_description(
        '1,234 Like(s), 75 Comment(s). Kwai video from '
        'Criadora(@criadora): "Minha legenda ❤️\\n#UFC &amp; #MMA"'
    )
    assert caption == "Minha legenda ❤️\\n#UFC & #MMA"
    assert creator == "Criadora (@criadora)"


def test_generic_share_heading_does_not_become_video_caption():
    text, creator = kwai_media._parse_kwai_post_description(
        "100 Like(s), 10 Comment(s). Kwai video from perfil"
    )
    assert text == ""
    assert creator is None
    assert kwai_media.format_kwai_post_caption("Vídeo do Kwai", "Kwai") == ""


@pytest.mark.asyncio
async def test_realistic_share_metadata_extracts_caption_and_creator(monkeypatch):
    final = "https://www.kwai.com/@TL.ESPORTE/video/123456"
    media = "https://aws-br-cdn.kwai.net/upic/video.mp4?signature=ok"
    description = (
        '13571 Like(s), 100 Comment(s). Kwai video from '
        'SPORTS(@TL.ESPORTE): &quot;#ufc #mma #porrada #boxe #Lutas&quot;'
    )
    page = (
        f'<meta property="og:description" content="{description}">'
        f'<meta property="og:video" content="{media}">'
    )

    class Response:
        url = final
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def text(self, **_kwargs):
            return page

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(kwai_media.aiohttp, "ClientSession", lambda **_kwargs: Session())
    result = await kwai_media.extract_kwai_video_page(
        "https://k.kwai.com/p/example"
    )
    assert result is not None
    assert result["title"] == "#ufc #mma #porrada #boxe #Lutas"
    assert result["author"] == "SPORTS (@TL.ESPORTE)"


def test_kwai_slogan_is_not_a_post_caption():
    for text in (
        "Make Everyone Shine",
        "Kwai - Make Everyone Shine",
        "Kwai is a social network for short videos and trends. Discover funny short videos.",
    ):
        caption, _ = kwai_media._parse_kwai_post_description(text)
        assert caption == ""
    assert kwai_media.format_kwai_post_caption("Make Everyone Shine", "matematicacoml") == (
        "👤 @matematicacoml"
    )


def test_embedded_post_caption_matches_requested_photo_id_only():
    requested = "5188578357266245817"
    other = "5188578357266245818"
    page = (
        '<meta property="og:title" content="Make Everyone Shine">'
        '<script id="post-state" type="application/json">'
        '{"items":['
        f'{{"photoId":"{other}","caption":"Legenda de outro vídeo #outra"}},'
        f'{{"photoId":"{requested}","caption":"Por que funciona?? #matematica #concursos"}}'
        ']}'
        '</script>'
    )
    original = kwai_media._extract_embedded_post_caption(
        page, f"https://www.kwai.com/@matematicacoml/video/{requested}"
    )
    assert original == "Por que funciona?? #matematica #concursos"
    unrelated = kwai_media._extract_embedded_post_caption(
        page, "https://www.kwai.com/@matematicacoml/video/5000000000000000000"
    )
    assert unrelated == ""


def test_embedded_post_caption_supports_escaped_json_strings():
    requested = "5188578357266245817"
    caption = "Por que funciona?? #matemática #concursos"
    import json
    page = json.dumps({
        "photoId": requested,
        "caption": caption,
        "recommendations": [{"photoId": "5188578357266245888", "caption": "Outra postagem"}],
    }, ensure_ascii=True)
    assert kwai_media._extract_embedded_post_caption(
        page, f"https://www.kwai.com/@matematicacoml/video/{requested}"
    ) == caption


@pytest.mark.asyncio
async def test_generic_og_title_uses_matching_embedded_caption(monkeypatch):
    video_id = "5188578357266245817"
    final = f"https://www.kwai.com/@matematicacoml/video/{video_id}"
    media = "https://aws-br-cdn.kwai.net/upic/math.mp4?tag=123"
    page = (
        '<meta property="og:title" content="Make Everyone Shine">'
        '<meta property="og:description" content="Kwai is a social network for short videos and trends.">'
        '<script type="application/json">'
        f'{{"photoId":"{video_id}","caption":"Por que funciona?? #matematica"}}'
        '</script>'
        f'<meta property="og:video" content="{media}">'
    )

    class Response:
        status = 200
        url = final
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return False
        async def text(self, **_):
            return page

    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return False
        def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(kwai_media.aiohttp, "ClientSession", lambda **_: Session())
    result = await kwai_media.extract_kwai_video_page(final)
    assert result is not None
    assert result["title"] == "Por que funciona?? #matematica"
    assert result["author"] == "matematicacoml"


@pytest.mark.asyncio
async def test_generic_og_title_without_post_caption_falls_back_safely(monkeypatch):
    final = "https://www.kwai.com/@matematicacoml/video/5188578357266245817"
    media = "https://aws-br-cdn.kwai.net/upic/math.mp4?tag=123"
    page = (
        '<meta property="og:title" content="Make Everyone Shine">'
        '<meta property="og:description" content="Kwai is a social network for short videos and trends.">'
        f'<meta property="og:video" content="{media}">'
    )

    class Response:
        status = 200
        url = final
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return False
        async def text(self, **_):
            return page
    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return False
        def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(kwai_media.aiohttp, "ClientSession", lambda **_: Session())
    result = await kwai_media.extract_kwai_video_page(final)
    assert result is not None
    assert result["title"] == "Vídeo do Kwai"
    assert kwai_media.format_kwai_post_caption(result["title"], result["author"]) == (
        "👤 @matematicacoml"
    )


def test_nuxt_site_title_is_not_video_caption():
    for value in (
        "kwai-nuxt-pwa-pc",
        "kwai-nuxt-pwa-pc-online",
        "Make Everyone Shine",
    ):
        assert kwai_media._is_generic_kwai_text(value)
        assert kwai_media._parse_kwai_post_description(value)[0] == ""


@pytest.mark.asyncio
async def test_nuxt_site_title_alone_does_not_become_caption(monkeypatch):
    final = "https://www.kwai.com/@matematicacoml/video/5188578357266245817"
    media = "https://aws-br-cdn.kwai.net/upic/video.mp4"
    page = (
        '<meta name="description" content="Kwai is a social network for short videos">'
        '<meta property="og:title" content="kwai-nuxt-pwa-pc">'
        f'<meta property="og:video" content="{media}">'
    )

    class Response:
        url = final
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def text(self, **_):
            return page

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(kwai_media.aiohttp, "ClientSession", lambda **_: Session())
    result = await kwai_media.extract_kwai_video_page(final)
    assert result is not None
    assert result["title"] == "Vídeo do Kwai"
    assert kwai_media.format_kwai_post_caption(
        result["title"], result["author"]
    ) == "👤 @matematicacoml"
