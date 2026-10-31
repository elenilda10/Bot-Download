"""Real single-item handlers keep cache delivery and do not delete the parent link."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from handlers import spotify, youtube


def message():
    return SimpleNamespace(
        from_user=SimpleNamespace(id=7, username="tester"),
        chat=SimpleNamespace(id=99, type="private"),
        business_connection_id=None,
        text="https://music.youtube.com/playlist?list=PLabcdefgh",
        caption=None,
        answer=AsyncMock(),
        answer_audio=AsyncMock(
            return_value=SimpleNamespace(audio=SimpleNamespace(file_id="cached"))
        ),
    )


def common(monkeypatch, module):
    for name in [
        "react_to_message",
        "safe_delete_message",
        "safe_edit_text",
        "update_info",
        "send_chat_action_if_needed",
        "send_analytics",
    ]:
        monkeypatch.setattr(module, name, AsyncMock())
    monkeypatch.setattr(
        module,
        "load_user_settings",
        AsyncMock(
            return_value={"language": "en", "captions": "on", "delete_message": "on"}
        ),
    )
    monkeypatch.setattr(
        module, "get_bot_url", AsyncMock(return_value="https://t.me/test_bot")
    )
    monkeypatch.setattr(module, "maybe_delete_user_message", AsyncMock())
    monkeypatch.setattr(
        module,
        "claim_message_request",
        AsyncMock(side_effect=AssertionError("parent owns lease")),
    )
    monkeypatch.setattr(
        module,
        "should_skip_duplicate_business_message",
        AsyncMock(
            side_effect=AssertionError("parent handles duplicate business updates")
        ),
    )
    monkeypatch.setattr(
        module,
        "db",
        SimpleNamespace(
            get_language=AsyncMock(return_value="en"),
            get_file_id=AsyncMock(return_value="cached"),
            add_file=AsyncMock(),
        ),
    )


@pytest.mark.asyncio
async def test_spotify_collection_item_sends_cached_audio_and_preserves_source(
    monkeypatch,
):
    common(monkeypatch, spotify)
    monkeypatch.setattr(
        spotify,
        "get_spotify_track",
        AsyncMock(side_effect=AssertionError("cache hit must not fetch")),
    )
    msg = message()
    assert (
        await spotify.process_spotify(
            msg,
            direct_url="https://open.spotify.com/track/abc123",
            collection_item=True,
            collection_track={
                "spotify_id": "abc123",
                "title": "Song",
                "artists": "Artist",
            },
        )
        is True
    )
    msg.answer_audio.assert_awaited_once()
    assert msg.answer_audio.await_args.kwargs["audio"] == "cached"
    spotify.maybe_delete_user_message.assert_awaited_once_with(msg, "off")
    spotify.claim_message_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_youtube_music_collection_item_returns_success_on_cache_hit(monkeypatch):
    common(monkeypatch, youtube)
    monkeypatch.setattr(
        youtube,
        "_get_youtube_video_with_timeout",
        AsyncMock(
            return_value={
                "id": "abcdefghijk",
                "title": "Song",
                "webpage_url": "https://youtube.com/watch?v=abcdefghijk",
                "duration": 120,
            }
        ),
    )
    monkeypatch.setattr(
        youtube, "get_bot_avatar_thumbnail", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        youtube,
        "send_audio_with_thumbnail",
        AsyncMock(
            return_value=SimpleNamespace(audio=SimpleNamespace(file_id="cached"))
        ),
    )
    monkeypatch.setattr(
        youtube,
        "download_mp3_with_ytdlp_metrics",
        AsyncMock(side_effect=AssertionError("cache hit must not download")),
    )
    msg = message()
    assert (
        await youtube.download_music(
            msg,
            direct_url="https://youtube.com/watch?v=abcdefghijk",
            collection_item=True,
        )
        is True
    )
    youtube.send_audio_with_thumbnail.assert_awaited_once()
    assert youtube.send_audio_with_thumbnail.await_args.kwargs["audio"] == "cached"
    youtube.maybe_delete_user_message.assert_awaited_once_with(msg, "off")
    youtube.claim_message_request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "playlist,audio", [("OLAK5uy_abcdefgh", True), ("PLabcdefgh", False)]
)
async def test_youtube_routes_album_to_audio_but_keeps_regular_playlist_video(
    monkeypatch, playlist, audio
):
    from handlers import collections

    process = AsyncMock(return_value=True)
    monkeypatch.setattr(collections, "process_collection", process)
    assert (
        await youtube.download_video(
            message(), direct_url=f"https://youtube.com/playlist?list={playlist}"
        )
        is True
    )
    assert process.await_args.kwargs["audio"] is audio
