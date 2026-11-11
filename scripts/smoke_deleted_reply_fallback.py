"""Offline smoke test for antispam deleted-link Telegram fallback.

Run with the Bot-Download virtualenv. No Telegram HTTP traffic is generated.
"""
import asyncio

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendDocument, SendMediaGroup, SendVideo
from aiogram.types import InputMediaPhoto, ReplyParameters

from services.runtime.telegram_deleted_reply import DeletedReplyFallbackMiddleware


async def check(method, *, expected_retries=2, error="Bad Request: message to be replied not found"):
    requests = []

    async def fake_request(bot, request):
        requests.append(request)
        if len(requests) == 1:
            raise TelegramBadRequest(method=request, message=error)
        return "sent"

    middleware = DeletedReplyFallbackMiddleware()
    try:
        result = await middleware(fake_request, None, method)
    except TelegramBadRequest:
        if expected_retries == 1:
            assert len(requests) == 1
            return
        raise
    assert expected_retries == 2
    assert result == "sent"
    assert len(requests) == 2
    assert requests[1].chat_id == method.chat_id
    assert requests[1].reply_parameters is None
    assert requests[1].reply_to_message_id is None


async def main():
    await check(SendVideo(chat_id=-10042, video="cached-video", reply_parameters=ReplyParameters(message_id=123)))
    await check(SendDocument(chat_id=-10042, document="cached-document", reply_parameters=ReplyParameters(message_id=123)))
    await check(SendMediaGroup(chat_id=-10042, media=[
        InputMediaPhoto(media="file1"), InputMediaPhoto(media="file2")
    ], reply_parameters=ReplyParameters(message_id=123)))
    await check(SendVideo(chat_id=-10042, video="cached-video", reply_parameters=ReplyParameters(message_id=123)),
                expected_retries=1, error="Bad Request: wrong file identifier")
    print("OK: 4 verificações de reenvio concluídas sem acessar o Telegram.")


if __name__ == "__main__":
    asyncio.run(main())
