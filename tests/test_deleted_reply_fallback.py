"""Regression tests for group anti-spam deletion during Telegram uploads."""

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    SendAudio, SendDocument, SendMediaGroup, SendMessage, SendPhoto, SendVideo
)
from aiogram.types import InputMediaPhoto, ReplyParameters

from services.runtime.telegram_deleted_reply import DeletedReplyFallbackMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method",
    [
        SendMessage(chat_id=-100123, text="done", reply_parameters=ReplyParameters(message_id=77)),
        SendVideo(chat_id=-100123, video="cached-video-id", reply_parameters=ReplyParameters(message_id=77)),
        SendPhoto(chat_id=-100123, photo="cached-photo-id", reply_parameters=ReplyParameters(message_id=77)),
        SendDocument(chat_id=-100123, document="cached-document-id", reply_parameters=ReplyParameters(message_id=77)),
        SendAudio(chat_id=-100123, audio="cached-audio-id", reply_parameters=ReplyParameters(message_id=77)),
        SendMediaGroup(
            chat_id=-100123,
            media=[
                InputMediaPhoto(media="cached-photo-id-1"),
                InputMediaPhoto(media="cached-photo-id-2"),
            ],
            reply_parameters=ReplyParameters(message_id=77),
        ),
    ],
    ids=["text", "video", "photo", "document", "audio", "album"],
)
async def test_deleted_original_resends_same_payload_without_reply(method):
    requests = []
    result = object()

    async def send(_bot, request):
        requests.append(request)
        if len(requests) == 1:
            raise TelegramBadRequest(
                method=request, message="Bad Request: message to be replied not found"
            )
        return result

    actual = await DeletedReplyFallbackMiddleware()(send, None, method)
    assert actual is result
    assert len(requests) == 2
    assert requests[0] is method
    assert requests[1] is not method
    assert requests[0].reply_parameters.message_id == 77
    assert requests[1].reply_parameters is None
    assert requests[1].chat_id == method.chat_id
    assert requests[1].model_dump(exclude={"reply_parameters", "reply_to_message_id"}) == method.model_dump(
        exclude={"reply_parameters", "reply_to_message_id"}
    )


@pytest.mark.asyncio
async def test_legacy_reply_to_message_id_also_falls_back():
    method = SendMessage(chat_id=-100123, text="ready", reply_to_message_id=77)
    attempts = []

    async def send(_bot, request):
        attempts.append(request)
        if len(attempts) == 1:
            raise TelegramBadRequest(
                method=request, message="Bad Request: reply message not found"
            )
        return "success"

    assert await DeletedReplyFallbackMiddleware()(send, None, method) == "success"
    assert len(attempts) == 2
    assert attempts[1].reply_to_message_id is None


@pytest.mark.asyncio
async def test_unrelated_bad_request_does_not_retry():
    method = SendVideo(chat_id=-100123, video="cached-file", reply_parameters=ReplyParameters(message_id=77))
    attempts = []

    async def send(_bot, request):
        attempts.append(request)
        raise TelegramBadRequest(method=request, message="Bad Request: wrong file identifier")

    with pytest.raises(TelegramBadRequest, match="wrong file identifier"):
        await DeletedReplyFallbackMiddleware()(send, None, method)
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_no_reply_reference_is_never_retried():
    method = SendPhoto(chat_id=-100123, photo="cached-photo")
    attempts = []

    async def send(_bot, request):
        attempts.append(request)
        raise TelegramBadRequest(method=request, message="Bad Request: message to be replied not found")

    with pytest.raises(TelegramBadRequest):
        await DeletedReplyFallbackMiddleware()(send, None, method)
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_fallback_is_attempted_only_once():
    method = SendDocument(chat_id=-100123, document="cached-document", reply_parameters=ReplyParameters(message_id=77))
    attempts = []

    async def send(_bot, request):
        attempts.append(request)
        raise TelegramBadRequest(method=request, message="Bad Request: message to be replied not found")

    with pytest.raises(TelegramBadRequest):
        await DeletedReplyFallbackMiddleware()(send, None, method)
    assert len(attempts) == 2
