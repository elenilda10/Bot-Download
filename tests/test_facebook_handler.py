"""Regression tests for dedicated Facebook routing and link resolution."""
from unittest.mock import AsyncMock

import pytest

from handlers import facebook


def test_extract_facebook_link_does_not_capture_unrelated_urls():
    assert facebook._extract_facebook_link(
        "Veja https://example.com e https://www.facebook.com/share/r/19gWxMNRKj/!"
    ) == "https://www.facebook.com/share/r/19gWxMNRKj/"
    assert facebook._extract_facebook_link(
        "https://facebook.com.evil.example/reel/2480096172822069/"
    ) is None
    assert facebook._extract_facebook_link(
        "https://www.instagram.com/reel/123"
    ) is None


def test_canonical_video_and_photo_link():
    assert facebook._canonical_fb_media_url(
        "https://www.facebook.com/reel/2480096172822069/?share_url=tracking"
    ) == "https://www.facebook.com/reel/2480096172822069/"
    assert facebook._canonical_fb_media_url(
        "https://www.facebook.com/photo.php?fbid=1743744750448069&rdid=tracking"
    ) == "https://www.facebook.com/photo.php?fbid=1743744750448069&rdid=tracking"
    assert facebook._canonical_fb_media_url("https://example.com/reel/123") is None


@pytest.mark.asyncio
async def test_resolve_reel_share_with_curl(monkeypatch):
    proc = type("Process", (), {
        "returncode": 0,
        "communicate": AsyncMock(return_value=(
            b"https://www.facebook.com/reel/2480096172822069/?rdid=track",
            b"",
        )),
    })()
    fake = AsyncMock(return_value=proc)
    monkeypatch.setattr(facebook.asyncio, "create_subprocess_exec", fake)
    result = await facebook._resolve_facebook_short_link(
        "https://www.facebook.com/share/r/19gWxMNRKj/"
    )
    assert result == "https://www.facebook.com/reel/2480096172822069/"
    command = fake.await_args.args
    assert "curl" in command
    assert "--proto-redir" in command
    assert "--cookies-from-browser" not in command


@pytest.mark.asyncio
async def test_resolve_rejects_external_redirect(monkeypatch):
    proc = type("Process", (), {
        "returncode": 0,
        "communicate": AsyncMock(return_value=(b"https://example.com/private", b"")),
    })()
    monkeypatch.setattr(
        facebook.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
    assert await facebook._resolve_facebook_short_link(
        "https://www.facebook.com/share/r/19gWxMNRKj/"
    ) is None


@pytest.mark.asyncio
async def test_direct_reel_skips_curl(monkeypatch):
    resolver = AsyncMock()
    monkeypatch.setattr(facebook.asyncio, "create_subprocess_exec", resolver)
    assert await facebook._resolve_facebook_short_link(
        "https://www.facebook.com/reel/2480096172822069/"
    ) == "https://www.facebook.com/reel/2480096172822069/"
    resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_delegates_canonical_url(monkeypatch):
    facebook.process_universal_link = AsyncMock()
    monkeypatch.setattr(
        facebook, "_resolve_facebook_short_link",
        AsyncMock(return_value="https://www.facebook.com/reel/2480096172822069/"),
    )
    monkeypatch.setattr(
        facebook, "get_message_text",
        lambda message: "https://www.facebook.com/share/r/19gWxMNRKj/",
    )
    await facebook.process_facebook_link(object())
    assert facebook.process_universal_link.await_args.kwargs["direct_url"] == (
        "https://www.facebook.com/reel/2480096172822069/"
    )
