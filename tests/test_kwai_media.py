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


def test_refuses_unlabelled_mp4_from_page_recommendations():
    unrelated = "https://aws-br-cdn.kwai.net/upic/clip.mp4?tag=abc"
    assert kwai_media._extract_media_url(
        f"<script>const video = '{unrelated}';</script>"
    ) is None


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
        f'<meta property="og:url" content="{final}">'
        f'<meta property="og:video" content="{wanted}">'
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
            return FakeResponse(
                canonical,
                f'<meta property="og:url" content="{canonical}">'
                f'<meta property="og:video" content="{media}">',
            )

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
        return (
            canonical,
            f'<meta property="og:url" content="{canonical}">'
            f'<meta property="og:video" content="{wanted.replace("&", "&amp;")}">'
        )

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
        "#ufc #mma #porrada #boxe #Lutas"
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
    final = "https://www.kwai.com/@TL.ESPORTE/video/5200118832330956026"
    media = "https://aws-br-cdn.kwai.net/upic/video.mp4?signature=ok"
    description = (
        '13571 Like(s), 100 Comment(s). Kwai video from '
        'SPORTS(@TL.ESPORTE): &quot;#ufc #mma #porrada #boxe #Lutas&quot;'
    )
    page = (
        f'<meta property="og:description" content="{description}">'
        f'<meta property="og:url" content="{final}">'
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
    assert kwai_media.format_kwai_post_caption("Make Everyone Shine", "matematicacoml") == ""


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
        f'<meta property="og:url" content="{final}">'
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
        f'<meta property="og:url" content="{final}">'
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
    assert kwai_media.format_kwai_post_caption(result["title"], result["author"]) == ""


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
        f'<meta property="og:url" content="{final}">'
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
    ) == ""


def test_kwai_nuxt_hydration_caption_matches_visible_span():
    expected = (
        "Por que funciona?? . .#matematica#concurso#concursopublico"
        "#enem#concurseiro#foco#estudar#concurseira#professor"
    )
    page = (
        '<html><span>' + expected + '</span>'
        '<script>function load(C,D,E){'
        'E[0]={time:"2026-10-23 16:00:44",timestamp:1792742444535,'
        'caption:"' + expected + '",other:1};'
        'E[1]={caption:"Outro vídeo #diferente"}'
        '}</script></html>'
    )
    assert kwai_media._extract_visible_hydrated_caption(page) == expected


def test_kwai_does_not_take_unrendered_or_recommended_video_caption():
    page = (
        '<span>Legenda do vídeo principal</span>'
        '<span>Outra legenda recomendada</span>'
        '<script>E[0]={caption:"Legenda do vídeo principal"};'
        'E[1]={caption:"Outra legenda recomendada"};</script>'
    )
    assert kwai_media._extract_visible_hydrated_caption(page) == (
        "Legenda do vídeo principal"
    )
    assert kwai_media._extract_visible_hydrated_caption(
        '<span>Legenda do vídeo principal</span>'
        '<script>E[1]={caption:"Legenda do vídeo principal"};</script>'
    ) == ""
    assert kwai_media._extract_visible_hydrated_caption(
        '<script>E[0]={caption:"Legenda não renderizada"};</script>'
    ) == ""


def test_kwai_hydrated_caption_decodes_unicode_and_nested_span():
    expected = "Por que funciona?? ❤️ #matemática"
    page = (
        '<span>Por que <b>funciona??</b> ❤️ #matemática</span>'
        '<script>E[0]={caption:"Por que funciona?? \\u2764\\ufe0f #matem\\u00e1tica"}'
        '</script>'
    )
    assert kwai_media._extract_visible_hydrated_caption(page) == expected


def test_kwai_hydration_rejects_ambiguous_unindexed_captions():
    page = (
        '<span>Legenda original</span><span>Legenda recomendada</span>'
        '<script>let a={caption:"Legenda original"};'
        'let b={caption:"Legenda recomendada"};</script>'
    )
    assert kwai_media._extract_visible_hydrated_caption(page) == ""


@pytest.mark.asyncio
async def test_kwai_extractor_returns_visible_nuxt_caption(monkeypatch):
    url = "https://www.kwai.com/@matematicacoml/video/5188578357266245817"
    wanted = "Por que funciona?? . .#matematica#concurso#concursopublico"
    page = (
        '<meta property="og:title" content="kwai-nuxt-pwa-pc">'
        '<span>' + wanted + '</span>'
        '<script>E[0]={time:"2026-10-23 16:00:44",timestamp:1792742444535,'
        'caption:"' + wanted + '"};</script>'
        f'<meta property="og:url" content="{url}">'
        '<meta property="og:video" content="https://aws-br-cdn.kwai.net/upic/test.mp4">'
    )

    class Response:
        status = 200
        url = "https://www.kwai.com/@matematicacoml/video/5188578357266245817"

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

    monkeypatch.setattr(kwai_media.aiohttp, "ClientSession", lambda **_kw: Session())
    result = await kwai_media.extract_kwai_video_page(url)
    assert result is not None
    assert result["title"] == wanted
    assert result["author"] == "matematicacoml"


def test_kwai_caption_never_includes_profile_handle():
    caption = "Por que funciona?? #matematica #enem"
    assert kwai_media.format_kwai_post_caption(caption, "matematicacoml") == caption
    assert kwai_media.format_kwai_post_caption(caption, "@matematicacoml") == caption
    assert kwai_media.format_kwai_post_caption("", "matematicacoml") == ""


def test_verified_kwai_refuses_recommended_mp4_even_when_first_on_page():
    expected_page = "https://www.kwai.com/@gabyvalence/video/5200118832330956026"
    unrelated_mp4s = "".join(
        f'<script>const recommended_{i}="https://aws-br-cdn.kwai.net/upic/recommended_{i}.mp4";</script>'
        for i in range(14)
    )
    page = (
        '<span>Capa de urso para colchão 🐻</span>'
        '<script>E[0]={caption:"Capa de urso para colchão 🐻"};</script>'
        + unrelated_mp4s
    )
    assert kwai_media._extract_verified_media_url(page, expected_page) is None
    assert kwai_media._extract_media_url(page) is None


def test_verified_kwai_uses_mp4_scoped_to_main_post_not_recommended():
    expected_page = "https://www.kwai.com/@gabyvalence/video/5200118832330956026"
    wanted = "https://aws-br-cdn.kwai.net/upic/correct-bedding.mp4?signature=a"
    unrelated = "https://aws-br-cdn.kwai.net/upic/chandler-47sec.mp4"
    page = (
        f'<script>const ad="{unrelated}";</script>'
        '<span>Roupa de cama 🐻</span>'
        '<script>E[0]={photoId:"5200118832330956026",caption:"Roupa de cama 🐻",'
        f'video:{{url:"{wanted}"}}'
        '};'
        f'E[1]={{caption:"Vídeo recomendado",url:"{unrelated}"}};'
        '</script>'
    )
    assert kwai_media._extract_verified_media_url(page, expected_page) == wanted


def test_verified_kwai_rejects_ambiguous_mp4_inside_main_record():
    url = "https://www.kwai.com/@gabyvalence/video/5200118832330956026"
    first = "https://aws-br-cdn.kwai.net/upic/video_one.mp4"
    second = "https://aws-br-cdn.kwai.net/upic/video_two.mp4"
    page = (
        '<span>Post principal 🐻</span>'
        '<script>E[0]={caption:"Post principal 🐻",'
        f'variants:["{first}","{second}"]'
        '};</script>'
    )
    assert kwai_media._extract_verified_media_url(page, url) is None


def test_verified_kwai_og_video_must_match_requested_post():
    link = "https://www.kwai.com/@gabyvalence/video/5200118832330956026"
    other_post = "https://www.kwai.com/@other/video/5200118832330956027"
    unrelated = "https://aws-br-cdn.kwai.net/upic/chandler.mp4"
    page = (
        f'<meta property="og:url" content="{other_post}">'
        f'<meta property="og:video" content="{unrelated}">'
    )
    assert kwai_media._extract_verified_media_url(page, link) is None
    assert kwai_media._extract_verified_media_url(
        f'<meta property="og:video" content="{unrelated}">', link
    ) is None


def test_verified_kwai_accepts_post_scoped_og_video():
    link = "https://www.kwai.com/@gabyvalence/video/5200118832330956026"
    wanted = "https://aws-br-cdn.kwai.net/upic/bedding.mp4?sign=abc"
    page = (
        f'<meta property="og:url" content="{link}">'
        f'<meta property="og:video" content="{wanted.replace("&", "&amp;")}">'
    )
    assert kwai_media._extract_verified_media_url(page, link) == wanted


@pytest.mark.asyncio
async def test_kwai_extractor_refuses_foreign_video_from_recommendations(monkeypatch):
    requested = "https://www.kwai.com/@gabyvalence/video/5200118832330956026"
    unrelated = "https://aws-br-cdn.kwai.net/upic/chandler-47-seconds.mp4"
    page = (
        '<span>Vídeo de roupa de cama da Gaby</span>'
        '<script>E[0]={caption:"Vídeo de roupa de cama da Gaby"};'
        f'E[1]={{caption:"Outro vídeo",file:"{unrelated}"}};'
        '</script>'
    )

    class Response:
        status = 200
        url = requested

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

    monkeypatch.setattr(kwai_media.aiohttp, "ClientSession", lambda **_: Session())
    monkeypatch.setattr(kwai_media, "_fetch_kwai_chrome_page", lambda *_: None)

    assert await kwai_media.extract_kwai_video_page(requested) is None


def test_kwai_rejects_e0_with_wrong_post_id_even_single_mp4():
    requested = "https://www.kwai.com/@gabyvalence/video/5200118832330956026"
    unrelated = "https://aws-br-cdn.kwai.net/upic/adriana-video.mp4?signature=abc"
    body = (
        '<span>Look elegante da Adriana</span>'
        '<script>E[0]={photoId:"5199999999999999999",'
        'caption:"Look elegante da Adriana",'
        f'video:{{url:"{unrelated}"}}'
        '};</script>'
    )
    assert kwai_media._extract_verified_media_url(body, requested) is None


def test_kwai_rejects_e0_without_post_id_even_single_mp4():
    requested = "https://www.kwai.com/@gabyvalence/video/5200118832330956026"
    unrelated = "https://aws-br-cdn.kwai.net/upic/chandler-video.mp4"
    body = (
        '<span>Vídeo engraçado recomendado</span>'
        '<script>E[0]={caption:"Vídeo engraçado recomendado",'
        f'video:{{url:"{unrelated}"}}'
        '};</script>'
    )
    assert kwai_media._extract_verified_media_url(body, requested) is None


def test_kwai_photo_alias_has_same_identity_as_video_url():
    url_video = "https://www.kwai.com/@gabyvalence/video/5200118832330956026"
    url_photo = "https://www.kwai.com/@gabyvalence/photo/5200118832330956026"
    assert kwai_media._video_id_from_url(url_video) == kwai_media._video_id_from_url(url_photo)


POST_URL = "https://www.kwai.com/@gabyvalence/video/5200118832330956026"


def _realistic_nuxt_post(*, identity="5200118832330956026", caption='""', index=0):
    url = "https://aws-br-cdn.kwai.net/upic/gaby.mp4?tag=signed&keep=yes"
    escaped = url.replace("/", r"\u002F")
    return (
        '<meta property="og:description" content="Unrelated recommendation caption">'
        '<script>window.__NUXT__=(function(a,u,v){'
        f'u[{index}]={{photo_id:5200118832330956000,photo_id_str:"{identity}",'
        f'caption:{caption},user_name:"Gaby Valence",kwai_id:"gabyvalence",'
        f'main_mv_urls:[{{cdn:"aws",url:"{escaped}"}},'
        f'{{cdn:"ak",url:"{escaped.replace("aws-br", "ak-br")}"}}],'
        'recommendations:[{photo_id_str:"5256132352324514268",'
        'main_mv_urls:[{url:"https://aws-br-cdn.kwai.net/upic/foreign.mp4"}]}]};'
        'return {data:u}}(true,[],"Legenda original ❤️"));</script>'
    ), url


@pytest.mark.parametrize("index", [0, 1, 3])
def test_exact_photo_id_str_wins_over_rounded_number_and_array_position(index):
    page, url = _realistic_nuxt_post(index=index, caption="v")
    post = kwai_media._extract_verified_hydrated_post(page, POST_URL)
    assert post == {"media_url": url, "title": "Legenda original ❤️", "author": "Gaby Valence"}
    assert kwai_media._extract_verified_media_url(page, POST_URL) == url


def test_wrong_string_id_never_uses_rounded_number_or_nested_recommendation():
    page, _ = _realistic_nuxt_post(identity="5191956055422505042")
    assert kwai_media._extract_verified_media_url(page, POST_URL) is None


def test_wrong_string_id_also_blocks_legacy_numeric_id_fallback():
    page, _ = _realistic_nuxt_post(identity="5191956055422505042", caption='"Gaby"')
    page = '<span>Gaby</span>' + page.replace(
        "photo_id:5200118832330956000", "photo_id:5200118832330956026"
    )
    assert kwai_media._extract_verified_media_url(page, POST_URL) is None


def test_same_record_can_have_empty_or_alias_caption_without_blocking_video():
    for caption in ('""', "unknownAlias", '"Minha legenda"'):
        page, url = _realistic_nuxt_post(caption=caption)
        assert kwai_media._extract_verified_media_url(page, POST_URL) == url


def test_nuxt_alias_does_not_execute_expressions_or_leak_recommended_text():
    page, _ = _realistic_nuxt_post(caption="v")
    page = page.replace('[],"Legenda original ❤️"', '[],dangerousCall()')
    post = kwai_media._extract_verified_hydrated_post(page, POST_URL)
    assert post["title"] == ""


def test_nested_id_is_not_an_owning_post_identity():
    page, _ = _realistic_nuxt_post(identity="5191956055422505042")
    page = page.replace('photo_id_str:"5256132352324514268"', 'photo_id_str:"5200118832330956026"')
    assert kwai_media._extract_verified_hydrated_post(page, POST_URL) is None


@pytest.mark.asyncio
async def test_firefox_fallback_is_explicit_and_accepts_only_owning_record(monkeypatch):
    page, media = _realistic_nuxt_post(caption="v")
    calls = []

    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return False
        def get(self, *_args, **_kwargs):
            raise kwai_media.aiohttp.ClientError("network unavailable")

    monkeypatch.setattr(kwai_media.aiohttp, "ClientSession", lambda **_: Session())
    monkeypatch.setattr(kwai_media, "_fetch_kwai_chrome_page", lambda *_: None)

    def firefox(url, profile):
        calls.append((url, profile))
        return url, page

    monkeypatch.setattr(kwai_media, "_fetch_kwai_firefox_page", firefox)
    assert await kwai_media.extract_kwai_video_page(POST_URL) is None
    assert calls == []
    result = await kwai_media.extract_kwai_video_page(POST_URL, firefox_profile="/test/profile")
    assert result["media_url"] == media
    assert result["title"] == "Legenda original ❤️"
    assert calls == [(POST_URL, "/test/profile")]

    page, _ = _realistic_nuxt_post(identity="5191956055422505042")
    assert await kwai_media.extract_kwai_video_page(POST_URL, firefox_profile="/test/profile") is None


@pytest.mark.asyncio
async def test_empty_verified_caption_never_uses_foreign_page_description(monkeypatch):
    page, _ = _realistic_nuxt_post()
    class Response:
        status = 200
        url = POST_URL
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        async def text(self, **_): return page
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        def get(self, *_a, **_k): return Response()
    monkeypatch.setattr(kwai_media.aiohttp, "ClientSession", lambda **_: Session())
    result = await kwai_media.extract_kwai_video_page(POST_URL)
    assert result["title"] == "Vídeo do Kwai"


SHOP_URL = (
    'https://m-shop.kwai.com/krn-web/detail?itemId=4311573118'
    '&photoId=150111566247027&photoId=5232206978093612857'
    '&share_item_type=goods_video_distribute'
    '&share_item_info=5232206978093612857&authorKwaiId=samyashop'
)
SHOP_VIDEO = 'https://www.kwai.com/@samyashop/video/5232206978093612857'


def test_shop_uses_outer_shared_video_identity_instead_of_product_photo():
    assert kwai_media._shop_shared_video_url(SHOP_URL) == SHOP_VIDEO
    assert kwai_media.is_kwai_url('https://k.kwai.com/w/0C00dDjZ')


@pytest.mark.parametrize('url', [
    SHOP_URL.replace('m-shop.kwai.com', 'evil.example'),
    SHOP_URL.replace('goods_video_distribute', 'product'),
    SHOP_URL.replace('share_item_info=5232206978093612857', 'share_item_info=99999999'),
    SHOP_URL + '&share_item_info=150111566247027',
    SHOP_URL + '&authorKwaiId=other',
    SHOP_URL.replace('samyashop', 'bad%2Fprofile'),
    'https://m-shop.kwai.com/krn-web/detail?itemId=4311573118',
])
def test_shop_refuses_ambiguous_or_product_only_links(url):
    assert kwai_media._shop_shared_video_url(url) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('use_profile', [False, True])
async def test_shop_short_redirect_downloads_only_exact_shared_post(monkeypatch, use_profile):
    page, media = _realistic_nuxt_post(identity='5232206978093612857')
    calls = []
    profile_calls = []

    class Response:
        status = 200
        def __init__(self, url, body):
            self.url, self.body = url, body
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        async def text(self, **_): return self.body

    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        def get(self, url, **kwargs):
            calls.append(url)
            assert 'Cookie' not in kwargs.get('headers', {})
            if len(calls) == 1:
                return Response(SHOP_URL, '<html>Kwai Shop</html>')
            assert url == SHOP_VIDEO
            return Response(url, '<html>empty</html>' if use_profile else page)

    def firefox(url, profile):
        profile_calls.append(url)
        assert url == SHOP_VIDEO
        return url, page

    monkeypatch.setattr(kwai_media.aiohttp, 'ClientSession', lambda **_: Session())
    monkeypatch.setattr(kwai_media, '_fetch_kwai_chrome_page', lambda *_: None)
    monkeypatch.setattr(kwai_media, '_fetch_kwai_firefox_page', firefox)
    result = await kwai_media.extract_kwai_video_page(
        'https://k.kwai.com/w/0C00dDjZ',
        firefox_profile='/test/profile' if use_profile else None,
    )
    assert result['page_url'] == SHOP_VIDEO
    assert result['media_url'] == media
    assert profile_calls == ([SHOP_VIDEO] if use_profile else [])


def _photo_post(identity='5200118832330956026', video=False):
    own = 'https://aws-br-pic.kwai.net/upic/photo.jpg?tag=1'
    mirror = own.replace('aws-br', 'ak-br')
    other = 'https://aws-br-pic.kwai.net/upic/foreign.jpg'
    clip = 'https://aws-br-cdn.kwai.net/upic/own.mp4'
    return ('<script>u[0]={photo_id_str:"' + identity + '",'
            'caption:"Fotos originais",user_name:"Autora",'
            'images:[{url:"' + own + '"},{url:"' + mirror + '"}],'
            'headurls:[{url:"' + other + '"}],'
            'recommendations:[{images:[{url:"' + other + '"}]}]'
            + (',main_mv_urls:[{url:"' + clip + '"}]' if video else '')
            + '};</script>')


def test_photo_collection_excludes_avatars_recommendations_and_cdn_mirrors():
    result = kwai_media._extract_verified_image_post(_photo_post(), POST_URL)
    assert result['image_urls'] == ['https://aws-br-pic.kwai.net/upic/photo.jpg?tag=1']
    assert result['title'] == 'Fotos originais'


def test_foreign_record_never_supplies_photos():
    assert kwai_media._extract_verified_image_post(_photo_post('9999999999999999999'), POST_URL) is None


def test_same_post_can_have_photos_and_video():
    page = _photo_post(video=True)
    assert kwai_media._extract_verified_image_post(page, POST_URL)
    assert kwai_media._extract_verified_hydrated_post(page, POST_URL)['media_url'].endswith('/own.mp4')


def test_cover_is_only_photo_fallback_when_video_missing():
    page = '<script>u[0]={photo_id_str:"5200118832330956026",cover_first_frame_urls:[{url:"https://aws-br-pic.kwai.net/cover.webp"}]};</script>'
    assert kwai_media._extract_verified_image_post(page, POST_URL)['image_urls']
    page = page.replace('cover_first_frame_urls:', 'main_mv_urls:[{url:"https://aws-br-cdn.kwai.net/v.mp4"}],cover_first_frame_urls:')
    assert kwai_media._extract_verified_image_post(page, POST_URL) is None


@pytest.mark.asyncio
async def test_photo_only_page_returns_images_without_requiring_mp4(monkeypatch):
    class Response:
        status = 200
        url = POST_URL
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        async def text(self, **_): return _photo_post()
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        def get(self, *_args, **_kwargs): return Response()
    monkeypatch.setattr(kwai_media.aiohttp, 'ClientSession', lambda **_: Session())
    result = await kwai_media.extract_kwai_video_page(POST_URL)
    assert result['media_url'] is None
    assert result['image_urls']
    assert result['title'] == 'Fotos originais'
