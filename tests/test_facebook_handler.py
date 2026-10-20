"""Regression tests for dedicated Facebook routing and link resolution."""
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace
from pathlib import Path

import pytest

from handlers import facebook
from services.platforms import facebook_media


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
    assert facebook_media._canonical_fb_media_url(
        "https://www.facebook.com/reel/2480096172822069/?share_url=tracking"
    ) == "https://www.facebook.com/reel/2480096172822069/"
    assert facebook_media._canonical_fb_media_url(
        "https://www.facebook.com/photo.php?fbid=1743744750448069&rdid=tracking"
    ) == "https://www.facebook.com/photo.php?fbid=1743744750448069&rdid=tracking"
    assert facebook_media._canonical_fb_media_url(
        "https://www.facebook.com/groups/256772258373648/permalink/2061916421192547/?rdid=123&share_url=tracking"
    ) == "https://www.facebook.com/groups/256772258373648/permalink/2061916421192547/"
    assert facebook_media._canonical_fb_media_url("https://example.com/reel/123") is None


@pytest.mark.asyncio
async def test_resolve_reel_share_with_curl(monkeypatch):
    proc = type("Process", (), {
        "returncode": 0,
        "communicate": AsyncMock(return_value=(
            b"HTTP/2 302\r\nlocation: https://www.facebook.com/reel/2480096172822069/?rdid=track\r\n\r\n",
            b"",
        )),
    })()
    fake = AsyncMock(return_value=proc)
    monkeypatch.setattr(facebook_media.asyncio, "create_subprocess_exec", fake)
    result = await facebook._resolve_facebook_short_link(
        "https://www.facebook.com/share/r/19gWxMNRKj/"
    )
    assert result == "https://www.facebook.com/reel/2480096172822069/"
    command = fake.await_args.args
    assert "curl" in command
    assert "--dump-header" in command
    assert "--location" not in command
    assert "--cookies-from-browser" not in command


@pytest.mark.asyncio
async def test_resolve_rejects_external_redirect(monkeypatch):
    proc = type("Process", (), {
        "returncode": 0,
        "communicate": AsyncMock(return_value=(
            b"HTTP/2 302\r\nlocation: https://example.com/private\r\n\r\n",
            b"",
        )),
    })()
    monkeypatch.setattr(
        facebook_media.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
    assert await facebook._resolve_facebook_short_link(
        "https://www.facebook.com/share/r/19gWxMNRKj/"
    ) is None


@pytest.mark.asyncio
async def test_direct_reel_skips_curl(monkeypatch):
    resolver = AsyncMock()
    monkeypatch.setattr(facebook_media.asyncio, "create_subprocess_exec", resolver)
    assert await facebook._resolve_facebook_short_link(
        "https://www.facebook.com/reel/2480096172822069/"
    ) == "https://www.facebook.com/reel/2480096172822069/"
    resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_downloads_reel_directly_and_sends_original_caption(monkeypatch, tmp_path):
    monkeypatch.setattr(facebook, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(
        facebook, "_resolve_facebook_short_link",
        AsyncMock(return_value="https://www.facebook.com/reel/2480096172822069/"),
    )
    monkeypatch.setattr(
        facebook, "get_message_text",
        lambda _: "https://www.facebook.com/share/r/19gWxMNRKj/",
    )
    monkeypatch.setattr(facebook.cmd_mod, "is_banned", AsyncMock(return_value=False))
    monkeypatch.setattr(
        facebook, "should_skip_duplicate_business_message",
        AsyncMock(return_value=False),
    )
    lease = SimpleNamespace(mark_success=Mock(), finish=Mock())
    monkeypatch.setattr(
        facebook, "claim_message_request", AsyncMock(return_value=lease),
    )
    monkeypatch.setattr(
        facebook, "load_user_settings",
        AsyncMock(return_value={"language": "pt", "captions": "on"}),
    )
    monkeypatch.setattr(facebook, "get_bot_url", AsyncMock(return_value="https://t.me/test_bot"))
    monkeypatch.setattr(facebook, "react_to_message", AsyncMock())
    monkeypatch.setattr(facebook, "send_analytics", AsyncMock())
    monkeypatch.setattr(facebook, "safe_delete_message", AsyncMock())
    monkeypatch.setattr(facebook, "safe_edit_text", AsyncMock())
    monkeypatch.setattr(facebook, "maybe_delete_user_message", AsyncMock())
    monkeypatch.setattr(
        facebook, "bot",
        SimpleNamespace(send_chat_action=AsyncMock()),
    )
    monkeypatch.setenv("FACEBOOK_FIREFOX_PROFILE", "/home/firefoxbot/profile")

    async def download(url, *, output_dir, **kwargs):
        path = Path(output_dir) / "fb-test.mp4"
        path.write_bytes(b"fake mp4")
        assert url == "https://www.facebook.com/reel/2480096172822069/"
        assert kwargs["facebook_browser_profile"] == "/home/firefoxbot/profile"
        return SimpleNamespace(
            description="Legenda real do Reel",
            media_list=[SimpleNamespace(
                url=str(path), type="video", thumb=None,
                width=720, height=1280, duration=10,
            )],
        )

    specialized = AsyncMock(side_effect=download)
    monkeypatch.setattr(facebook, "download_facebook_media", specialized)

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=facebook.ADMIN_ID),
        chat=SimpleNamespace(id=42, type="private"),
        message_id=12,
        answer=AsyncMock(return_value=SimpleNamespace()),
        reply_video=AsyncMock(),
    )
    await facebook.process_facebook_link(message)
    specialized.assert_awaited_once()
    message.reply_video.assert_awaited_once()
    html_caption = message.reply_video.await_args.kwargs["caption"]
    assert "Legenda real do Reel" in html_caption
    assert "Powered by" in html_caption
    lease.mark_success.assert_called_once()
    lease.finish.assert_called_once()
    assert not list(tmp_path.glob("facebook-*"))


@pytest.mark.asyncio
async def test_resolve_group_share_link(monkeypatch):
    proc = type("Process", (), {
        "returncode": 0,
        "communicate": AsyncMock(return_value=(
            b"HTTP/2 302\r\nlocation: https://www.facebook.com/groups/256772258373648/permalink/2061916421192547/?rdid=tracking\r\n\r\n",
            b"",
        )),
    })()
    monkeypatch.setattr(
        facebook_media.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)
    )
    result = await facebook._resolve_facebook_short_link(
        "https://www.facebook.com/share/p/1E77eC6n4S/"
    )
    assert result == (
        "https://www.facebook.com/groups/256772258373648/"
        "permalink/2061916421192547/"
    )
