import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from handlers import telegram_ui_utils, tiktok
from services.media import delivery
from services.inline.album_links import get_inline_album_request
from services.inline.video_requests import (
    claim_inline_video_request,
    create_inline_video_request,
    get_inline_video_request,
)


@pytest.fixture(autouse=True)
def stub_user_agent(monkeypatch):
    monkeypatch.setattr(tiktok, "UserAgent", lambda: SimpleNamespace(random="Agent"))
    tiktok.tiktok_service._expanded_tiktok_url_cache.clear()


class DummyResponse:
    def __init__(self, url=None):
        self.url = url

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class DummySession:
    def __init__(self, *, head_handler=None):
        self._head_handler = head_handler

    def head(self, *args, **kwargs):
        assert self._head_handler is not None
        return self._head_handler(*args, **kwargs)


@pytest.mark.asyncio
async def test_process_tiktok_url_expands_short_links(monkeypatch):
    expected_url = "https://www.tiktok.com/@user/video/123456"

    def fake_head(url, allow_redirects, headers, timeout=None):
        assert allow_redirects is True
        assert "User-Agent" in headers
        return DummyResponse(url=expected_url)

    monkeypatch.setattr(
        tiktok,
        "get_http_session",
        AsyncMock(return_value=DummySession(head_handler=fake_head)),
    )
    result = await tiktok.process_tiktok_url_async("https://vm.tiktok.com/ABC123/")
    assert result == expected_url


@pytest.mark.asyncio
async def test_process_tiktok_url_returns_original_on_error(monkeypatch):
    original_url = "https://vm.tiktok.com/ABC123/"

    def boom(*_a, **_k):
        raise tiktok.aiohttp.ClientError("fail")

    monkeypatch.setattr(
        tiktok,
        "get_http_session",
        AsyncMock(return_value=DummySession(head_handler=boom)),
    )
    result = await tiktok.process_tiktok_url_async(original_url)
    assert result == original_url


@pytest.mark.asyncio
async def test_process_tiktok_url_coalesces_concurrent_expansions(monkeypatch):
    expected_url = "https://www.tiktok.com/@user/video/123456"
    head_calls = []
    release = asyncio.Event()

    class SlowResponse:
        def __init__(self, url):
            self.url = url

        async def __aenter__(self):
            await release.wait()
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def fake_head(url, allow_redirects, headers, timeout=None):
        head_calls.append(url)
        return SlowResponse(expected_url)

    monkeypatch.setattr(
        tiktok,
        "get_http_session",
        AsyncMock(return_value=DummySession(head_handler=fake_head)),
    )

    short_url = "https://vm.tiktok.com/ABC123/"
    first = asyncio.create_task(tiktok.process_tiktok_url_async(short_url))
    await asyncio.sleep(0)
    second = asyncio.create_task(tiktok.process_tiktok_url_async(short_url))
    await asyncio.sleep(0)
    release.set()

    assert await asyncio.gather(first, second) == [expected_url, expected_url]
    assert head_calls == [short_url]


@pytest.mark.asyncio
async def test_process_tiktok_url_strips_query(monkeypatch):
    expanded_url = "https://www.tiktok.com/@user/video/123456"

    def fake_head(url, allow_redirects, headers, timeout=None):
        return DummyResponse(url=f"{expanded_url}?is_from_webapp=1&sender_device=pc")

    monkeypatch.setattr(
        tiktok,
        "get_http_session",
        AsyncMock(return_value=DummySession(head_handler=fake_head)),
    )
    result = await tiktok.process_tiktok_url_async(
        f"{expanded_url}?is_from_webapp=1&sender_device=pc"
    )
    assert result == expanded_url


def test_get_video_id_from_url():
    url = "https://www.tiktok.com/@user/video/1234567890?lang=en"
    assert tiktok.get_video_id_from_url(url) == "1234567890"


@pytest.mark.asyncio
async def test_video_info_returns_dataclass():
    data = {
        "error": None,
        "code": 0,
        "data": {
            "id": "123",
            "title": "Funny video",
            "cover": "https://example.com/cover.jpg",
            "play_count": 1000,
            "digg_count": 100,
            "comment_count": 25,
            "share_count": 10,
            "music_info": {"play": "https://example.com/music.mp3"},
            "author": {"unique_id": "creator"},
            "duration": 37,
        },
    }
    info = await tiktok.video_info(data)
    assert info is not None
    assert info.id == "123"
    assert info.author == "creator"
    assert info.duration_seconds == 37


@pytest.mark.asyncio
async def test_video_info_none_on_error():
    assert await tiktok.video_info({"error": "quota"}) is None
    assert await tiktok.video_info({"error": None, "code": 1, "message": "bad"}) is None


@pytest.mark.asyncio
async def test_process_tiktok_skips_outgoing_business_owner_message(monkeypatch):
    telegram_ui_utils._business_owner_user_cache.clear()
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=42, username="owner"),
        chat=SimpleNamespace(id=99, type="private"),
        message_id=7,
        business_connection_id="business-1",
        sender_business_bot=None,
        text="https://www.tiktok.com/@creator/video/123",
        caption=None,
    )

    monkeypatch.setattr(
        tiktok, "get_bot_url", AsyncMock(return_value="https://t.me/maxloadbot")
    )
    monkeypatch.setattr(
        tiktok.bot,
        "get_business_connection",
        AsyncMock(return_value=SimpleNamespace(user=SimpleNamespace(id=42))),
    )
    monkeypatch.setattr(tiktok, "fetch_tiktok_data_with_retry", AsyncMock())
    monkeypatch.setattr(tiktok, "claim_message_request", AsyncMock())
    monkeypatch.setattr(
        tiktok, "should_skip_duplicate_business_message", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(tiktok, "react_to_message", AsyncMock())
    monkeypatch.setattr(tiktok, "update_info", AsyncMock())

    await tiktok.process_tiktok(message)

    tiktok.should_skip_duplicate_business_message.assert_awaited_once()
    tiktok.fetch_tiktok_data_with_retry.assert_not_awaited()
    tiktok.claim_message_request.assert_not_awaited()
    tiktok.react_to_message.assert_not_awaited()
    tiktok.update_info.assert_awaited_once_with(message)


@pytest.mark.asyncio
async def test_process_tiktok_video_reuses_inflight_download_across_business_chats(
    monkeypatch,
):
    from services.media import orchestration

    orchestration.reset_single_media_flow_tracking()
    file_ids: dict[str, str] = {}
    download_started = asyncio.Event()
    allow_download_finish = asyncio.Event()
    downloaded_sends = 0
    cached_sends = 0

    info_payload = {
        "error": None,
        "code": 0,
        "data": {
            "id": "123",
            "title": "Funny video",
            "cover": "https://example.com/cover.jpg",
            "play_count": 1000,
            "digg_count": 100,
            "comment_count": 25,
            "share_count": 10,
            "music_info": {"play": "https://example.com/music.mp3"},
            "author": {"unique_id": "creator"},
        },
    }

    async def fake_get_file_id(key: str):
        return file_ids.get(key)

    async def fake_add_file(key: str, file_id: str, _file_type: str):
        file_ids[key] = file_id

    async def fake_download_video(*_args, **_kwargs):
        download_started.set()
        await allow_download_finish.wait()
        return SimpleNamespace(path="/tmp/tiktok-demo.mp4", size=1024)

    def make_message(chat_id: int, message_id: int):
        async def reply_video(video, **_kwargs):
            nonlocal downloaded_sends, cached_sends
            if video == "telegram-file-id-1":
                cached_sends += 1
                return SimpleNamespace(
                    video=SimpleNamespace(file_id="cached-reply-file-id")
                )
            downloaded_sends += 1
            return SimpleNamespace(video=SimpleNamespace(file_id="telegram-file-id-1"))

        return SimpleNamespace(
            from_user=SimpleNamespace(id=42, username="sender"),
            chat=SimpleNamespace(id=chat_id, type="private"),
            message_id=message_id,
            reply_video=AsyncMock(side_effect=reply_video),
        )

    monkeypatch.setattr(tiktok, "send_analytics", AsyncMock())
    monkeypatch.setattr(
        tiktok.db, "get_file_id", AsyncMock(side_effect=fake_get_file_id)
    )
    monkeypatch.setattr(tiktok.db, "add_file", AsyncMock(side_effect=fake_add_file))
    monkeypatch.setattr(
        tiktok.tiktok_service,
        "download_video",
        AsyncMock(side_effect=fake_download_video),
    )
    monkeypatch.setattr(
        delivery, "build_video_send_kwargs", AsyncMock(return_value={})
    )
    monkeypatch.setattr(tiktok, "send_chat_action_if_needed", AsyncMock())
    monkeypatch.setattr(tiktok, "safe_delete_message", AsyncMock())
    monkeypatch.setattr(tiktok, "maybe_delete_user_message", AsyncMock())
    monkeypatch.setattr(tiktok, "remove_file", AsyncMock())

    settings = {
        "captions": "on",
        "delete_message": "off",
        "info_buttons": "off",
        "url_button": "off",
        "audio_button": "off",
    }
    first = make_message(1001, 11)
    second = make_message(1002, 22)

    first_task = asyncio.create_task(
        tiktok.process_tiktok_video(
            first, info_payload, "https://t.me/maxloadbot", settings, "business-1"
        )
    )
    await download_started.wait()
    second_task = asyncio.create_task(
        tiktok.process_tiktok_video(
            second, info_payload, "https://t.me/maxloadbot", settings, "business-2"
        )
    )
    await asyncio.sleep(0)
    allow_download_finish.set()

    assert await asyncio.gather(first_task, second_task) == [True, True]
    assert tiktok.tiktok_service.download_video.await_count == 1
    assert (
        tiktok.tiktok_service.download_video.await_args.kwargs["request_id"]
        == "tiktok_video:123"
    )
    tiktok.db.add_file.assert_awaited_once_with(
        "tiktok#video:123", "telegram-file-id-1", "video"
    )
    assert downloaded_sends == 1
    assert cached_sends == 1


@pytest.mark.asyncio
async def test_process_tiktok_photos_replies_only_on_first_sent_message(monkeypatch):
    status_message = SimpleNamespace(delete=AsyncMock())
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        chat=SimpleNamespace(id=99, type="private"),
        message_id=7,
        answer=AsyncMock(return_value=status_message),
        answer_media_group=AsyncMock(
            return_value=[
                SimpleNamespace(photo=[SimpleNamespace(file_id="sent-photo-id")])
            ]
        ),
        answer_photo=AsyncMock(
            return_value=SimpleNamespace(
                photo=[SimpleNamespace(file_id="sent-last-photo-id")]
            )
        ),
        reply_photo=AsyncMock(),
    )
    info = tiktok.TikTokVideo(
        id="123",
        description="caption",
        cover="https://cdn.example.com/cover.jpg",
        author="creator",
        views=1,
        likes=2,
        comments=3,
        shares=4,
        music_play_url="https://cdn.example.com/audio.mp3",
    )

    monkeypatch.setattr(tiktok, "send_analytics", AsyncMock())
    monkeypatch.setattr(tiktok, "video_info", AsyncMock(return_value=info))
    monkeypatch.setattr(tiktok, "send_chat_action_if_needed", AsyncMock())
    monkeypatch.setattr(tiktok, "safe_delete_message", AsyncMock())
    monkeypatch.setattr(tiktok, "maybe_delete_user_message", AsyncMock())
    monkeypatch.setattr(tiktok.db, "get_file_id", AsyncMock(return_value=None))
    monkeypatch.setattr(tiktok.db, "add_file", AsyncMock())

    await tiktok.process_tiktok_photos(
        message,
        {"data": {"id": "123"}},
        "https://t.me/maxloadbot",
        {
            "captions": "on",
            "delete_message": "off",
            "info_buttons": "off",
            "url_button": "off",
            "audio_button": "off",
        },
        None,
        ["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"],
    )

    assert message.answer_media_group.await_args.kwargs["reply_to_message_id"] == 7
    assert (
        message.answer_photo.await_args.kwargs["photo"]
        == "https://cdn.example.com/2.jpg"
    )
    assert message.reply_photo.await_count == 0
    assert tiktok.db.add_file.await_count == 2


@pytest.mark.asyncio
async def test_download_tiktok_doc_callback_uses_pressing_user_settings(monkeypatch):
    settings = {
        "captions": "on",
        "delete_message": "off",
        "info_buttons": "off",
        "url_button": "off",
        "audio_button": "off",
        "as_document": "off",
    }
    call = SimpleNamespace(
        data="doc:tiktok:123",
        from_user=SimpleNamespace(id=42),
        answer=AsyncMock(),
        message=SimpleNamespace(
            # call.message is authored by the bot, not the pressing user.
            from_user=SimpleNamespace(id=999999, is_bot=True),
            chat=SimpleNamespace(id=42, type="private"),
            business_connection_id=None,
        ),
    )

    monkeypatch.setattr(tiktok.db, "get_file_id", AsyncMock(return_value=None))
    monkeypatch.setattr(tiktok.db, "user_settings", AsyncMock(return_value=settings))
    monkeypatch.setattr(
        tiktok,
        "fetch_tiktok_data_with_retry",
        AsyncMock(return_value={"error": None, "code": 0, "data": {"id": "123"}}),
    )
    monkeypatch.setattr(
        tiktok, "get_bot_url", AsyncMock(return_value="https://t.me/maxloadbot")
    )
    process_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(tiktok, "process_tiktok_video", process_mock)

    await tiktok.download_tiktok_doc_callback(call)

    tiktok.db.user_settings.assert_awaited_once_with(42)
    assert process_mock.await_args.kwargs["actor_user_id"] == 42
    override_settings = process_mock.await_args.args[3]
    assert override_settings["as_document"] == "on"


@pytest.mark.asyncio
async def test_inline_tiktok_query_returns_send_button(monkeypatch):
    settings = {
        "captions": "on",
        "delete_message": "off",
        "info_buttons": "on",
        "url_button": "on",
        "audio_button": "on",
    }
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        chat_type="inline",
        query="https://www.tiktok.com/@creator/video/123",
        answer=AsyncMock(),
    )

    monkeypatch.setattr(tiktok, "CHANNEL_ID", None)
    monkeypatch.setattr(tiktok, "send_analytics", AsyncMock())
    monkeypatch.setattr(tiktok.db, "user_settings", AsyncMock(return_value=settings))
    monkeypatch.setattr(
        tiktok.db, "get_file_id", AsyncMock(return_value="cached-file-id")
    )
    monkeypatch.setattr(
        tiktok, "get_bot_url", AsyncMock(return_value="https://t.me/maxloadbot")
    )
    monkeypatch.setattr(
        tiktok,
        "fetch_tiktok_data_with_retry",
        AsyncMock(
            return_value={
                "error": None,
                "code": 0,
                "data": {
                    "id": "123",
                    "title": "Funny video",
                    "cover": "https://example.com/cover.jpg",
                    "play_count": 1000,
                    "digg_count": 100,
                    "comment_count": 25,
                    "share_count": 10,
                    "music_info": {"play": "https://example.com/music.mp3"},
                    "author": {"unique_id": "creator"},
                },
            }
        ),
    )

    await tiktok.inline_tiktok_query(query)

    results = query.answer.await_args.args[0]
    assert len(results) == 1
    result = results[0]
    assert result.title == "TikTok Video"
    assert result.reply_markup.inline_keyboard[0][0].text == "Enviar vídeo inline"
    assert (
        result.reply_markup.inline_keyboard[0][0].callback_data
        == f"inline:tiktok:{result.id.removeprefix('tiktok_inline:')}"
    )
    assert result.thumbnail_url == "https://example.com/cover.jpg"
    token = result.id.removeprefix("tiktok_inline:")
    request = get_inline_video_request(token)
    assert request is not None
    assert request.source_url == "https://www.tiktok.com/@creator/video/123"
    assert request.user_settings == settings


@pytest.mark.asyncio
async def test_inline_tiktok_query_sanitizes_invalid_preview_url(monkeypatch):
    settings = {
        "captions": "on",
        "delete_message": "off",
        "info_buttons": "on",
        "url_button": "on",
        "audio_button": "on",
    }
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        chat_type="inline",
        query="https://www.tiktok.com/@creator/video/123",
        answer=AsyncMock(),
    )

    monkeypatch.setattr(tiktok, "CHANNEL_ID", 12345)
    monkeypatch.setattr(tiktok, "send_analytics", AsyncMock())
    monkeypatch.setattr(tiktok.db, "user_settings", AsyncMock(return_value=settings))
    monkeypatch.setattr(
        tiktok.db, "get_file_id", AsyncMock(return_value="cached-file-id")
    )
    monkeypatch.setattr(
        tiktok, "get_bot_url", AsyncMock(return_value="https://t.me/maxloadbot")
    )
    monkeypatch.setattr(
        tiktok,
        "fetch_tiktok_data_with_retry",
        AsyncMock(
            return_value={
                "error": None,
                "code": 0,
                "data": {
                    "id": "123",
                    "title": "Funny video",
                    "cover": "https://example.com/cover.webp",
                    "play_count": 1000,
                    "digg_count": 100,
                    "comment_count": 25,
                    "share_count": 10,
                    "music_info": {"play": "https://example.com/music.mp3"},
                    "author": {"unique_id": "creator"},
                },
            }
        ),
    )

    await tiktok.inline_tiktok_query(query)

    results = query.answer.await_args.args[0]
    assert len(results) == 1
    assert results[0].thumbnail_url is None


@pytest.mark.asyncio
async def test_inline_tiktok_query_returns_send_button_for_single_photo(monkeypatch):
    settings = {
        "captions": "on",
        "delete_message": "off",
        "info_buttons": "on",
        "url_button": "on",
        "audio_button": "on",
    }
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        chat_type="inline",
        query="https://www.tiktok.com/@creator/video/123",
        answer=AsyncMock(),
    )

    monkeypatch.setattr(tiktok, "CHANNEL_ID", 12345)
    monkeypatch.setattr(tiktok, "send_analytics", AsyncMock())
    monkeypatch.setattr(tiktok.db, "user_settings", AsyncMock(return_value=settings))
    monkeypatch.setattr(tiktok.db, "get_file_id", AsyncMock(return_value=None))
    monkeypatch.setattr(
        tiktok, "get_bot_url", AsyncMock(return_value="https://t.me/maxloadbot")
    )
    monkeypatch.setattr(
        tiktok,
        "fetch_tiktok_data_with_retry",
        AsyncMock(
            return_value={
                "error": None,
                "code": 0,
                "data": {
                    "id": "123",
                    "title": "Funny photo",
                    "cover": "https://example.com/cover.jpg",
                    "play_count": 1000,
                    "digg_count": 100,
                    "comment_count": 25,
                    "share_count": 10,
                    "music_info": {"play": "https://example.com/music.mp3"},
                    "author": {"unique_id": "creator"},
                    "images": ["https://example.com/photo.jpg"],
                },
            }
        ),
    )

    await tiktok.inline_tiktok_query(query)

    results = query.answer.await_args.args[0]
    assert len(results) == 1
    result = results[0]
    assert result.title == "TikTok Photo"
    assert result.reply_markup.inline_keyboard[0][0].text == "Enviar foto inline"
    assert result.thumbnail_url == "https://example.com/photo.jpg"
    token = result.id.removeprefix("tiktok_inline:")
    request = get_inline_video_request(token)
    assert request is not None
    assert request.source_url == "https://www.tiktok.com/@creator/video/123"


@pytest.mark.asyncio
async def test_inline_tiktok_query_returns_album_deeplink_for_multi_photo_post(
    monkeypatch,
):
    settings = {
        "captions": "on",
        "delete_message": "off",
        "info_buttons": "on",
        "url_button": "on",
        "audio_button": "on",
    }
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        chat_type="inline",
        query="https://www.tiktok.com/@creator/video/123",
        answer=AsyncMock(),
    )

    monkeypatch.setattr(tiktok, "send_analytics", AsyncMock())
    monkeypatch.setattr(tiktok, "CHANNEL_ID", None)
    monkeypatch.setattr(tiktok.db, "user_settings", AsyncMock(return_value=settings))
    monkeypatch.setattr(
        tiktok, "get_bot_url", AsyncMock(return_value="https://t.me/maxloadbot")
    )
    monkeypatch.setattr(
        tiktok,
        "fetch_tiktok_data_with_retry",
        AsyncMock(
            return_value={
                "error": None,
                "code": 0,
                "data": {
                    "id": "123",
                    "title": "Photo set",
                    "cover": "https://example.com/cover.jpg",
                    "play_count": 1000,
                    "digg_count": 100,
                    "comment_count": 25,
                    "share_count": 10,
                    "music_info": {"play": "https://example.com/music.mp3"},
                    "author": {"unique_id": "creator"},
                    "images": [
                        "https://example.com/photo-1.webp",
                        "https://example.com/photo-2.webp",
                    ],
                },
            }
        ),
    )

    await tiktok.inline_tiktok_query(query)

    results = query.answer.await_args.args[0]
    assert len(results) == 1
    result = results[0]
    assert result.title == tiktok.bm.inline_album_title("TikTok")
    assert result.thumbnail_url == "https://example.com/cover.jpg"
    assert result.input_message_content.message_text == tiktok.bm.captions(
        settings["captions"],
        "Photo set",
        "https://t.me/maxloadbot",
    )
    deep_link = result.reply_markup.inline_keyboard[0][0].url
    token = deep_link.split("?start=album_", 1)[1]
    request = get_inline_album_request(token)
    assert request is not None
    assert request.service == "tiktok"
    assert request.url == "https://www.tiktok.com/@creator/video/123"


@pytest.mark.asyncio
async def test_inline_tiktok_query_uses_cached_preview_photo_file_id_for_album(
    monkeypatch,
):
    settings = {
        "captions": "on",
        "delete_message": "off",
        "info_buttons": "on",
        "url_button": "on",
        "audio_button": "on",
    }
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        chat_type="inline",
        query="https://www.tiktok.com/@creator/video/123",
        answer=AsyncMock(),
    )

    monkeypatch.setattr(tiktok, "send_analytics", AsyncMock())
    monkeypatch.setattr(tiktok, "CHANNEL_ID", 12345)
    monkeypatch.setattr(tiktok.db, "user_settings", AsyncMock(return_value=settings))
    monkeypatch.setattr(
        tiktok.db, "get_file_id", AsyncMock(return_value="cached-preview-file-id")
    )
    monkeypatch.setattr(
        tiktok, "get_bot_url", AsyncMock(return_value="https://t.me/maxloadbot")
    )
    monkeypatch.setattr(
        tiktok,
        "fetch_tiktok_data_with_retry",
        AsyncMock(
            return_value={
                "error": None,
                "code": 0,
                "data": {
                    "id": "123",
                    "title": "Photo set",
                    "cover": "https://example.com/cover.jpg",
                    "play_count": 1000,
                    "digg_count": 100,
                    "comment_count": 25,
                    "share_count": 10,
                    "music_info": {"play": "https://example.com/music.mp3"},
                    "author": {"unique_id": "creator"},
                    "images": [
                        "https://example.com/photo-1.webp",
                        "https://example.com/photo-2.webp",
                    ],
                },
            }
        ),
    )

    await tiktok.inline_tiktok_query(query)

    results = query.answer.await_args.args[0]
    assert len(results) == 1
    result = results[0]
    assert isinstance(result, tiktok.types.InlineQueryResultCachedPhoto)
    assert result.photo_file_id == "cached-preview-file-id"
    assert result.caption == tiktok.bm.captions(
        settings["captions"],
        "Photo set",
        "https://t.me/maxloadbot",
    )


@pytest.mark.asyncio
async def test_chosen_inline_tiktok_result_edits_inline_message(monkeypatch):
    settings = {
        "captions": "on",
        "delete_message": "off",
        "info_buttons": "on",
        "url_button": "on",
        "audio_button": "on",
    }
    token = create_inline_video_request(
        "tiktok",
        "https://www.tiktok.com/@creator/video/123",
        42,
        settings,
    )
    result = SimpleNamespace(
        result_id=f"tiktok_inline:{token}",
        inline_message_id="inline-message-1",
        from_user=SimpleNamespace(full_name="Inline User"),
    )

    monkeypatch.setattr(
        tiktok,
        "fetch_tiktok_data_with_retry",
        AsyncMock(
            return_value={
                "error": None,
                "code": 0,
                "data": {
                    "id": "123",
                    "title": "Funny video",
                    "cover": "https://example.com/cover.jpg",
                    "play_count": 1000,
                    "digg_count": 100,
                    "comment_count": 25,
                    "share_count": 10,
                    "music_info": {"play": "https://example.com/music.mp3"},
                    "author": {"unique_id": "creator"},
                },
            }
        ),
    )
    monkeypatch.setattr(
        tiktok.db, "get_file_id", AsyncMock(return_value="cached-file-id")
    )
    monkeypatch.setattr(
        tiktok, "get_bot_url", AsyncMock(return_value="https://t.me/maxloadbot")
    )
    monkeypatch.setattr(tiktok, "safe_edit_inline_text", AsyncMock(return_value=True))
    monkeypatch.setattr(tiktok, "safe_edit_inline_media", AsyncMock(return_value=True))

    await tiktok.chosen_inline_tiktok_result(result)

    assert tiktok.safe_edit_inline_text.await_count == 1
    assert (
        tiktok.safe_edit_inline_text.await_args_list[0].args[2]
        == tiktok.bm.uploading_status()
    )
    media = tiktok.safe_edit_inline_media.await_args.args[2]
    assert media.media == "cached-file-id"
    assert media.caption is not None
    request = get_inline_video_request(token)
    assert request is not None
    assert request.state == "completed"


@pytest.mark.asyncio
async def test_chosen_inline_tiktok_result_edits_inline_photo(monkeypatch):
    settings = {
        "captions": "on",
        "delete_message": "off",
        "info_buttons": "on",
        "url_button": "on",
        "audio_button": "on",
    }
    token = create_inline_video_request(
        "tiktok",
        "https://www.tiktok.com/@creator/video/123",
        42,
        settings,
    )
    result = SimpleNamespace(
        result_id=f"tiktok_inline:{token}",
        inline_message_id="inline-message-photo",
        from_user=SimpleNamespace(full_name="Inline User"),
    )

    monkeypatch.setattr(
        tiktok,
        "fetch_tiktok_data_with_retry",
        AsyncMock(
            return_value={
                "error": None,
                "code": 0,
                "data": {
                    "id": "123",
                    "title": "Funny photo",
                    "cover": "https://example.com/cover.jpg",
                    "play_count": 1000,
                    "digg_count": 100,
                    "comment_count": 25,
                    "share_count": 10,
                    "music_info": {"play": "https://example.com/music.mp3"},
                    "author": {"unique_id": "creator"},
                    "images": ["https://example.com/photo.jpg"],
                },
            }
        ),
    )
    monkeypatch.setattr(
        tiktok.db, "get_file_id", AsyncMock(return_value="cached-photo-id")
    )
    monkeypatch.setattr(
        tiktok, "get_bot_url", AsyncMock(return_value="https://t.me/maxloadbot")
    )
    monkeypatch.setattr(tiktok, "safe_edit_inline_text", AsyncMock(return_value=True))
    monkeypatch.setattr(tiktok, "safe_edit_inline_media", AsyncMock(return_value=True))

    await tiktok.chosen_inline_tiktok_result(result)

    media = tiktok.safe_edit_inline_media.await_args.args[2]
    assert media.media == "cached-photo-id"
    request = get_inline_video_request(token)
    assert request is not None
    assert request.state == "completed"


def _make_inline_send_call(data: str, inline_message_id):
    return SimpleNamespace(
        data=data,
        inline_message_id=inline_message_id,
        id="callback-1",
        from_user=SimpleNamespace(id=42, full_name="Inline User"),
        message=None,
        answer=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_send_inline_tiktok_video_callback_requires_inline_message_id():
    call = _make_inline_send_call("inline:tiktok:some-token", None)

    await tiktok.send_inline_tiktok_video_callback(call)

    call.answer.assert_awaited_once_with(
        "This button works only in inline mode.", show_alert=True
    )


@pytest.mark.asyncio
async def test_send_inline_tiktok_video_callback_reports_already_processing():
    settings = {
        "captions": "on",
        "delete_message": "off",
        "info_buttons": "on",
        "url_button": "on",
        "audio_button": "on",
    }
    token = create_inline_video_request(
        "tiktok",
        "https://www.tiktok.com/@creator/video/123",
        42,
        settings,
    )
    claim_inline_video_request(token)

    call = _make_inline_send_call(f"inline:tiktok:{token}", "inline-message-cb")

    await tiktok.send_inline_tiktok_video_callback(call)

    assert call.answer.await_count == 2
    last_answer = call.answer.await_args_list[-1]
    assert last_answer.args == (tiktok.bm.inline_video_already_processing(),)
    assert last_answer.kwargs == {"show_alert": False}


@pytest.mark.asyncio
async def test_run_inline_send_callback_alerts_on_unknown_value_error():
    from handlers.inline_utils import run_inline_send_callback

    sender = AsyncMock(side_effect=ValueError("boom"))
    call = _make_inline_send_call("inline:tiktok:some-token", "inline-message-cb")

    await run_inline_send_callback(call, "inline:tiktok:", sender)

    sender.assert_awaited_once()
    assert sender.await_args.kwargs["token"] == "some-token"
    assert sender.await_args.kwargs["duplicate_handler"] == "callback"
    last_answer = call.answer.await_args_list[-1]
    assert last_answer.args == (tiktok.bm.something_went_wrong(),)
    assert last_answer.kwargs == {"show_alert": True}


@pytest.mark.asyncio
async def test_run_inline_send_callback_alerts_on_permission_error():
    from handlers.inline_utils import run_inline_send_callback

    sender = AsyncMock(side_effect=PermissionError("token_owner_mismatch"))
    call = _make_inline_send_call("inline:tiktok:some-token", "inline-message-cb")

    await run_inline_send_callback(call, "inline:tiktok:", sender)

    last_answer = call.answer.await_args_list[-1]
    assert last_answer.args == (tiktok.bm.something_went_wrong(),)
    assert last_answer.kwargs == {"show_alert": True}
