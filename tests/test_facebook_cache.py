"""Facebook cache safety, persisted callbacks, and configurable media buttons."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from handlers import facebook
from services.platforms import facebook_cache as fc


URL = "https://www.facebook.com/reel/2480096172822069/"


def test_authenticated_cache_never_overlaps_public_cache():
    assert fc.scope_for(None, 123) == "public"
    assert fc.scope_for("/home/firefoxbot/profile", 123) == "owner:123"
    assert fc.key_for(URL, "public", "media") != fc.key_for(
        URL, "owner:123", "media"
    )
    assert fc.key_for(URL, "owner:123", "media") != fc.key_for(
        URL, "owner:456", "media"
    )


def test_owner_callbacks_reject_other_user_and_forwarded_chats():
    record = fc.callback_record(URL, "owner:123", 123, 123)
    assert fc.callback_allowed(record, 123, 123, "private")
    assert not fc.callback_allowed(record, 456, 456, "private")
    assert not fc.callback_allowed(record, 123, -10001, "supergroup")
    assert not fc.callback_allowed(record, 123, 456, "private")
    assert not fc.callback_allowed(record, 123, 123, "group")


def test_expired_and_invalid_callback_tokens_rejected():
    record = fc.callback_record(URL, "owner:123", 123, 123)
    assert not fc.callback_allowed(record, 123, 123, "private",
                                   now=record["created_at"] + 8 * 24 * 3600)
    assert not fc.callback_allowed(record, 123, 123, "private",
                                   now=record["created_at"] - 120)
    with pytest.raises(ValueError):
        fc.callback_key("../private")
    assert fc.decode("not JSON") is None
    assert fc.decode('{"version":22}') is None


def test_public_cache_may_be_reused_only_under_public_scope():
    record = fc.callback_record(URL, "public", 123, 123)
    assert fc.callback_allowed(record, 456, 456, "private")
    assert fc.callback_allowed(record, 456, -10011, "supergroup")
    assert fc.key_for(URL, "public", "media").startswith("facebook:v1:public:")


def test_facebook_buttons_respect_user_settings_and_video_only():
    defaults = facebook._facebook_keyboard(URL, "pt", {})
    labels = [b.text for row in defaults.inline_keyboard for b in row]
    assert labels == ["🔗 Abrir no Facebook"]

    on = facebook._facebook_keyboard(
        URL, "pt", {"audio_button": "on", "file_button": "on"},
        "0" * 24, has_video=True,
    )
    callbacks = [b.callback_data for row in on.inline_keyboard for b in row
                 if b.callback_data]
    assert callbacks == ["fba:" + "0" * 24, "fbd:" + "0" * 24]
    photos = facebook._facebook_keyboard(
        URL, "pt", {"audio_button": "on", "file_button": "on"},
        "0" * 24, has_video=False,
    )
    assert all(
        not b.callback_data or not b.callback_data.startswith("fba:")
        for row in photos.inline_keyboard for b in row
    )


def test_media_cache_decode_rejects_forged_ids():
    valid = {
        "version": fc.CACHE_VERSION,
        "url": URL,
        "scope": "owner:123",
        "description": "Vídeo",
        "items": [{"kind": "video", "file_id": "AAABBB"}],
    }
    assert fc.media_entries(fc.decode(fc.encode(valid)))
    valid["items"] = [{"kind": "video", "file_id": ""}]
    assert fc.media_entries(valid) is None


@pytest.mark.asyncio
async def test_callback_rejects_forwarded_authenticated_video_before_downloading(monkeypatch):
    record = fc.callback_record(URL, "owner:123", 123, 123)
    monkeypatch.setattr(
        facebook, "db",
        SimpleNamespace(get_file_id=AsyncMock(return_value=fc.encode(record))),
    )
    monkeypatch.setattr(
        facebook.cmd_mod, "is_banned", AsyncMock(return_value=False),
    )
    downloader = AsyncMock()
    monkeypatch.setattr(facebook, "download_facebook_media", downloader)
    call = SimpleNamespace(
        data="fba:" + "0" * 24,
        from_user=SimpleNamespace(id=456),
        message=SimpleNamespace(chat=SimpleNamespace(id=456, type="private")),
        answer=AsyncMock(),
    )
    await facebook.facebook_format_callback(call)
    call.answer.assert_awaited_once()
    assert call.answer.await_args.kwargs["show_alert"] is True
    downloader.assert_not_awaited()



def test_albums_hide_unavailable_conversion_callbacks():
    kb = facebook._facebook_keyboard(
        URL, "pt",
        {"audio_button": "on", "file_button": "on"},
        "0" * 24, has_video=True, single=False,
    )
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row
                 if b.callback_data]
    assert callbacks == []


def test_url_button_can_be_hidden_using_settings():
    kb = facebook._facebook_keyboard(
        URL, "pt",
        {"url_button": "off", "audio_button": "on", "file_button": "on"},
        "0" * 24, has_video=True,
    )
    assert all(b.url is None for row in kb.inline_keyboard for b in row)
    no_buttons = facebook._facebook_keyboard(
        URL, "pt", {"url_button": "off"},
    )
    assert no_buttons is None



@pytest.mark.asyncio
async def test_private_cached_mp3_callback_reuses_file_without_download(monkeypatch):
    token = "a" * 24
    actor = int(facebook.ADMIN_ID)
    url = URL
    record = fc.callback_record(url, f"owner:{actor}", actor, actor)
    cached = {
        "version": fc.CACHE_VERSION,
        "url": url, "scope": f"owner:{actor}",
        "description": "Legenda preservada",
        "items": [{"kind": "audio", "file_id": "telegram-audio-id"}],
    }
    fake_db = SimpleNamespace(
        get_file_id=AsyncMock(side_effect=lambda key: (
            fc.encode(record) if key == fc.callback_key(token)
            else fc.encode(cached) if key == fc.key_for(url, f"owner:{actor}", "audio")
            else None
        )),
        user_settings=AsyncMock(return_value={
            "audio_button": "on", "captions": "on",
        }),
    )
    monkeypatch.setattr(facebook, "db", fake_db)
    monkeypatch.setattr(
        facebook.cmd_mod, "is_banned", AsyncMock(return_value=False),
    )
    monkeypatch.setenv("FACEBOOK_FIREFOX_PROFILE", "/home/firefoxbot/profile")
    monkeypatch.setattr(
        facebook, "get_bot_url", AsyncMock(return_value="https://t.me/test"),
    )
    send = AsyncMock(return_value=SimpleNamespace(
        audio=SimpleNamespace(file_id="telegram-audio-id"),
    ))
    monkeypatch.setattr(facebook, "send_audio_with_thumbnail", send)
    download = AsyncMock()
    monkeypatch.setattr(facebook, "download_facebook_media", download)

    message = SimpleNamespace(
        chat=SimpleNamespace(id=actor, type="private"),
        business_connection_id=None,
        reply_audio=AsyncMock(),
    )
    call = SimpleNamespace(
        data=f"fba:{token}",
        from_user=SimpleNamespace(id=actor),
        message=message, answer=AsyncMock(),
    )
    await facebook.facebook_format_callback(call)
    send.assert_awaited_once()
    assert send.await_args.kwargs["audio"] == "telegram-audio-id"
    assert "Legenda preservada" in send.await_args.kwargs["caption"]
    download.assert_not_awaited()
