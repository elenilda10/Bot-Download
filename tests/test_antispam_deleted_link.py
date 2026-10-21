"""Regression tests: antispam removed the link before the media was uploaded.

The Message object remains available in the handler, but Telegram rejects any
new send that still references the deleted message_id. Only answer_* is safe.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest

from handlers import telegram_ui_utils
from services.media import delivery


def _deleted_link_method(*args, **kwargs):
    raise AssertionError("A mensagem original foi apagada: nao use reply_*")


@pytest.mark.asyncio
async def test_cached_video_still_sends_after_original_message_deleted():
    message = SimpleNamespace(
        message_id=78,
        reply_video=AsyncMock(side_effect=_deleted_link_method),
        answer_video=AsyncMock(
            return_value=SimpleNamespace(video=SimpleNamespace(file_id="delivered-video-id"))
        ),
    )
    send_cached, _, extract = delivery.make_video_or_document_senders(
        message,
        caption="Video",
        reply_markup_fn=lambda: None,
        as_document=False,
        cached_log_label="regression",
    )

    sent = await send_cached("cached-file-id")
    assert extract(sent) == "delivered-video-id"
    message.reply_video.assert_not_awaited()
    assert message.answer_video.await_args.kwargs["video"] == "cached-file-id"
    assert "reply_to_message_id" not in message.answer_video.await_args.kwargs
    assert "reply_parameters" not in message.answer_video.await_args.kwargs


@pytest.mark.asyncio
async def test_album_sends_without_deleted_message_id():
    sent_media = [
        SimpleNamespace(photo=[SimpleNamespace(file_id="sent-1")]),
        SimpleNamespace(photo=[SimpleNamespace(file_id="sent-2")]),
    ]
    message = SimpleNamespace(
        message_id=78,
        answer_media_group=AsyncMock(return_value=sent_media),
        reply_media_group=AsyncMock(side_effect=_deleted_link_method),
    )
    db = SimpleNamespace(add_file=AsyncMock())
    entries = [
        {"kind": "photo", "file_id": "cached-1", "cached": True},
        {"kind": "photo", "file_id": "cached-2", "cached": True},
    ]

    result = await delivery.send_cached_media_entries(message, entries, db_service=db)
    assert result == sent_media[-1]
    message.reply_media_group.assert_not_awaited()
    kwargs = message.answer_media_group.await_args.kwargs
    assert len(kwargs["media"]) == 2
    assert "reply_to_message_id" not in kwargs
    assert "reply_parameters" not in kwargs


@pytest.mark.asyncio
async def test_single_photo_sends_without_reply_id():
    message = SimpleNamespace(
        message_id=78,
        answer_photo=AsyncMock(
            return_value=SimpleNamespace(photo=[SimpleNamespace(file_id="sent-photo")])
        ),
        reply_photo=AsyncMock(side_effect=_deleted_link_method),
    )
    db = SimpleNamespace(add_file=AsyncMock())
    entries = [{"kind": "photo", "file_id": "cached-photo", "cached": True}]
    sent = await delivery.send_cached_media_entries(message, entries, db_service=db)
    assert sent.photo[-1].file_id == "sent-photo"
    message.reply_photo.assert_not_awaited()
    assert "reply_to_message_id" not in message.answer_photo.await_args.kwargs


@pytest.mark.asyncio
async def test_error_notification_can_be_sent_even_after_antispam_deletes_link():
    message = SimpleNamespace(
        message_id=78,
        chat=SimpleNamespace(id=-1001, type="private"),
        business_connection_id=None,
        react=AsyncMock(side_effect=RuntimeError("source message missing")),
        answer=AsyncMock(),
        reply=AsyncMock(side_effect=_deleted_link_method),
    )
    await telegram_ui_utils.handle_download_error(message, text="Falha", lang="pt")
    message.answer.assert_awaited_once_with("Falha")
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_already_removed_source_is_silent():
    message = SimpleNamespace(
        delete=AsyncMock(
            side_effect=TelegramBadRequest(
                method=SimpleNamespace(__api_method__="deleteMessage"),
                message="Bad Request: message to delete not found",
            )
        ),
        answer=AsyncMock(),
    )
    deleted = await telegram_ui_utils.maybe_delete_user_message(message, "on")
    assert deleted is False
    message.answer.assert_not_awaited()
