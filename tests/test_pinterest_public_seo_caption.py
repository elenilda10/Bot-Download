"""Public SEO fallback for Pinterest Pins with media but missing caption.

All HTTP requests and gallery-dl subprocesses are simulated. No cookies or
Pinterest accounts are used during tests.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from services.platforms import pinterest_media as pm

CANONICAL = "https://www.pinterest.com/pin/123456789012345/"
SHORT = "https://pin.it/gLqzFSUZO"


def _fake_public_client(monkeypatch, response_for_request):
    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(response_for_request)

    def factory(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return real_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(pm.httpx, "AsyncClient", factory)


def test_pinterest_html_meta_prefers_exact_public_pin_title():
    html = (
        '<html><head><title>Pinterest</title>'
        '<meta property="og:title" content="Neymar o colecionador de troféus | Pinterest">'
        '<meta property="og:description" content="Gabrielzinho publicou este Pin">'
        '</head></html>'
    )
    assert pm._pinterest_caption_from_html(html) == "Neymar o colecionador de troféus"


def test_pinterest_html_meta_recovers_description_and_html_entities():
    html = (
        '<html><head>'
        '<meta name="description" content="Fotos &amp; vídeos do álbum">'
        '</head></html>'
    )
    assert pm._pinterest_caption_from_html(html) == "Fotos & vídeos do álbum"


@pytest.mark.parametrize("html", [
    "<html><title>Pinterest</title></html>",
    '<meta property="og:title" content="Pinterest - Login">',
    '<meta name="description" content="Discover recipes, home ideas, style inspiration">',
])
def test_generic_pinterest_landing_pages_are_not_used_as_captions(html):
    assert pm._pinterest_caption_from_html(html) == ""


@pytest.mark.asyncio
async def test_anonymous_public_page_can_rescue_caption(monkeypatch):
    requests = []

    def serve(request: httpx.Request):
        requests.append(request)
        assert request.url.host == "www.pinterest.com"
        assert request.url.path == "/pin/123456789012345/"
        assert "cookie" not in request.headers
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text='<meta property="og:title" content="Neymar o colecionador de troféus">',
        )

    _fake_public_client(monkeypatch, serve)
    result = await pm._fetch_pinterest_public_caption(CANONICAL)
    assert result == "Neymar o colecionador de troféus"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_anonymous_pinresource_seo_title_is_last_resort(monkeypatch):
    requests = []

    def serve(request: httpx.Request):
        requests.append(str(request.url))
        if request.url.path == "/pin/123456789012345/":
            return httpx.Response(200, text="<title>Pinterest</title>",
                headers={"content-type": "text/html"})
        assert request.url.path == "/resource/PinResource/get/"
        assert "123456789012345" in str(request.url)
        assert "cookie" not in request.headers
        return httpx.Response(
            200, json={
                "resource_response": {
                    "data": {
                        "seo_title": "Neymar o colecionador de troféus",
                        "seo_description": "",
                    }
                }
            }
        )

    _fake_public_client(monkeypatch, serve)
    result = await pm._fetch_pinterest_public_caption(CANONICAL)
    assert result == "Neymar o colecionador de troféus"
    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    SHORT,
    "http://www.pinterest.com/pin/123456789012345/",
    "https://www.pinterest.com.evil.test/pin/123456789012345/",
    "https://www.pinterest.com/pin/123456789012345/?token=secret",
    "https://www.pinterest.com/pin/not-numeric/",
    "https://www.pinterest.com/pin/123/",
    "https://127.0.0.1/pin/123456789012345/",
])
async def test_seo_lookup_never_uses_arbitrary_or_unsanitized_urls(monkeypatch, url):
    def reject(_request):
        raise AssertionError("unexpected outbound request")

    _fake_public_client(monkeypatch, reject)
    assert await pm._fetch_pinterest_public_caption(url) == ""


@pytest.mark.asyncio
async def test_seo_lookup_fails_closed_on_redirect_or_blocked_public_page(monkeypatch):
    def serve(_request):
        return httpx.Response(302, headers={
            "location": "https://www.pinterest.com/login/",
        })

    _fake_public_client(monkeypatch, serve)
    assert await pm._fetch_pinterest_public_caption(CANONICAL) == ""


@pytest.mark.asyncio
async def test_seo_lookup_bounded_page_no_caption_is_nonfatal(monkeypatch):
    def serve(request):
        if request.url.path == "/pin/123456789012345/":
            return httpx.Response(
                200,
                content=b"A" * (pm._PINTEREST_SEO_MAX_BYTES + 100),
                headers={"content-type": "text/html"},
            )
        return httpx.Response(403)

    _fake_public_client(monkeypatch, serve)
    assert await pm._fetch_pinterest_public_caption(CANONICAL) == ""


@pytest.mark.asyncio
async def test_no_description_gallery_fallback_uses_public_seo_without_changing_media(monkeypatch):
    def fake_process(output):
        return SimpleNamespace(
            returncode=0, communicate=AsyncMock(return_value=(output, b"")),
        )

    redirect = json.dumps([
        [6, "https://www.pinterest.com/pin/123456789012345/sent/?private=ignored", {}],
    ]).encode()
    gallery = b"https://i.pinimg.com/originals/neymar.jpg\n"
    subprocess = AsyncMock(side_effect=[
        fake_process(redirect), fake_process(b""), fake_process(gallery),
    ])
    seo = AsyncMock(return_value="Neymar o colecionador de troféus")
    monkeypatch.setattr(pm.asyncio, "create_subprocess_exec", subprocess)
    monkeypatch.setattr(pm, "_fetch_pinterest_public_caption", seo)
    result = await pm._fetch_public_pin_gallery(SHORT)
    assert result is not None
    assert result.description == "Neymar o colecionador de troféus"
    assert result.media_list[0].url == "https://i.pinimg.com/originals/neymar.jpg"
    assert result.media_list[0].type == "photo"
    seo.assert_awaited_once_with(CANONICAL)
