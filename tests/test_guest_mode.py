from types import SimpleNamespace

import pytest
from aiogram import Router, types

import messages as bm
import handlers.guest as guest_module
from handlers.guest import (
    GuestInlineQueryAdapter,
    _collect_guest_text,
    _extract_guest_link,
    _fallback_result,
    _guest_request_from_result_id,
)


class FakeGuestMessage:
    def __init__(self, *, lang="en"):
        self.guest_query_id = "guest-query-1"
        self.from_user = SimpleNamespace(
            id=123,
            full_name="Guest User",
            username="guest",
            language_code=lang,
        )
        self.chat = SimpleNamespace(type="group")
        self.text = ""
        self.caption = None
        self.reply_to_message = None
        self.results = []

    async def answer_guest_query(self, *, result):
        self.results.append(result)
        return SimpleNamespace(inline_message_id="inline-guest-1")


def test_aiogram_exposes_guest_mode_api():
    assert hasattr(Router(), "guest_message")
    assert hasattr(types.Message, "answer_guest_query")
    assert "guest_message" in types.Update.model_fields


def test_guest_collects_link_from_replied_message():
    message = FakeGuestMessage(lang="pt")
    message.text = "@SaveVideoBot baixa isso"
    message.reply_to_message = SimpleNamespace(
        text="https://www.instagram.com/reel/abc123/",
        caption=None,
    )

    combined = _collect_guest_text(message)

    assert "@SaveVideoBot" in combined
    assert "instagram.com/reel/abc123" in combined


@pytest.mark.parametrize(
    ("url", "service"),
    [
        ("https://www.instagram.com/reel/abc123/", "instagram"),
        ("https://vm.tiktok.com/abc123/", "tiktok"),
        ("https://youtu.be/abc123", "youtube"),
        ("https://www.threads.net/@user/post/abc", "threads"),
        ("https://www.deezer.com/track/123456", "deezer"),
    ],
)
def test_guest_extracts_supported_links(url, service):
    detected = _extract_guest_link(url)

    assert detected is not None
    assert detected[0] == service


@pytest.mark.asyncio
async def test_guest_adapter_reuses_first_inline_result():
    message = FakeGuestMessage(lang="en")
    adapter = GuestInlineQueryAdapter(
        message=message,
        query="https://youtu.be/abc123",
        lang="en",
        bot_url="https://t.me/TestBot",
        service_name="YouTube",
    )
    first = types.InlineQueryResultArticle(
        id="first",
        title="First",
        input_message_content=types.InputTextMessageContent(
            message_text="First result"
        ),
    )
    second = types.InlineQueryResultArticle(
        id="second",
        title="Second",
        input_message_content=types.InputTextMessageContent(
            message_text="Second result"
        ),
    )

    sent = await adapter.answer([first, second])

    assert sent.inline_message_id == "inline-guest-1"
    assert adapter.answered is True
    assert len(message.results) == 1
    assert message.results[0].id == "first"


@pytest.mark.asyncio
async def test_guest_adapter_uses_localized_fallback_for_empty_results():
    message = FakeGuestMessage(lang="pt")
    adapter = GuestInlineQueryAdapter(
        message=message,
        query="https://open.spotify.com/track/abc",
        lang="pt",
        bot_url="https://t.me/TestBot",
        service_name="Spotify",
    )

    await adapter.answer([])

    result = message.results[0]
    assert "Spotify" in result.title
    assert "privado" in result.description.lower()
    assert "Abrir no privado" == result.reply_markup.inline_keyboard[0][0].text


def test_guest_fallback_is_bilingual():
    pt = _fallback_result(
        lang="pt",
        bot_url="https://t.me/TestBot",
        no_link=True,
    )
    en = _fallback_result(
        lang="en",
        bot_url="https://t.me/TestBot",
        no_link=True,
    )

    assert "Link não encontrado" == pt.title
    assert "No link found" == en.title
    assert bm.guest_open_private_button(lang="pt") == "Abrir no privado"
    assert bm.guest_open_private_button(lang="en") == "Open private chat"



@pytest.mark.parametrize(
    ("result_id", "expected"),
    [
        ("tiktok_inline:tok1", ("tiktok", "tok1")),
        ("youtube_inline:tok2", ("youtube", "tok2")),
        ("ytmusic_inline:tok3", ("ytmusic", "tok3")),
        ("instagram_inline:tok4", ("instagram", "tok4")),
        ("twitter_inline:tok5", ("twitter", "tok5")),
        ("soundcloud_inline:tok6", ("soundcloud", "tok6")),
        ("pinterest_inline:tok7", ("pinterest", "tok7")),
        ("threads_inline:tok8", ("threads", "tok8")),
        ("deezer_inline:tok9", ("deezer", "tok9")),
        ("instagram_album:album1", None),
        ("guest-fallback", None),
    ],
)
def test_guest_result_id_maps_to_auto_download(result_id, expected):
    assert _guest_request_from_result_id(result_id) == expected


@pytest.mark.asyncio
async def test_guest_adapter_schedules_automatic_download(monkeypatch):
    message = FakeGuestMessage(lang="pt")
    scheduled = {}

    def fake_schedule(*, result_id, inline_message_id, message):
        scheduled.update(
            result_id=result_id,
            inline_message_id=inline_message_id,
            message=message,
        )
        return True

    monkeypatch.setattr(
        guest_module,
        "_schedule_guest_auto_download",
        fake_schedule,
    )

    adapter = GuestInlineQueryAdapter(
        message=message,
        query="https://vm.tiktok.com/abc123/",
        lang="pt",
        bot_url="https://t.me/TestBot",
        service_name="TikTok",
    )
    result = types.InlineQueryResultArticle(
        id="tiktok_inline:guest-token",
        title="TikTok",
        input_message_content=types.InputTextMessageContent(
            message_text="Preparando"
        ),
    )

    await adapter.answer([result])

    assert scheduled["result_id"] == "tiktok_inline:guest-token"
    assert scheduled["inline_message_id"] == "inline-guest-1"
    assert scheduled["message"] is message


@pytest.mark.asyncio
async def test_guest_adapter_does_not_auto_download_album(monkeypatch):
    message = FakeGuestMessage(lang="en")
    called = False

    def fake_schedule(*, result_id, inline_message_id, message):
        nonlocal called
        called = True
        return False

    monkeypatch.setattr(
        guest_module,
        "_schedule_guest_auto_download",
        fake_schedule,
    )

    adapter = GuestInlineQueryAdapter(
        message=message,
        query="https://www.instagram.com/p/album/",
        lang="en",
        bot_url="https://t.me/TestBot",
        service_name="Instagram",
    )
    result = types.InlineQueryResultArticle(
        id="instagram_album:album-token",
        title="Instagram Album",
        input_message_content=types.InputTextMessageContent(
            message_text="Album"
        ),
    )

    await adapter.answer([result])

    assert called is True
