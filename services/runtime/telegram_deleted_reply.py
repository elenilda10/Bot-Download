"""Recover media sends when an anti-spam bot deletes the user's link.

Operates at the Telegram API session layer, covering all download handlers,
albums, cached file_id sends and error notifications without altering their
normal reply behavior.
"""

from __future__ import annotations

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest
from services.logger import logger as logging

logger = logging.bind(service="telegram_deleted_reply")

# Restrict to send operations which can carry a reply reference. Do not retry
# edits, deletes, callback answers or other state-changing operations.
_REPLYABLE_SEND_METHODS = frozenset({
    "SendMessage",
    "SendVideo",
    "SendPhoto",
    "SendDocument",
    "SendAudio",
    "SendMediaGroup",
    "SendAnimation",
    "SendVoice",
    "SendVideoNote",
    "SendSticker",
    "SendContact",
    "SendLocation",
    "SendVenue",
    "SendPoll",
    "SendDice",
    "SendPaidMedia",
    "CopyMessage",
})

_MISSING_REPLY_ERRORS = (
    "message to be replied not found",
    "replied message not found",
    "reply message not found",
    "reply to message not found",
    "message to reply not found",
    "message to be replied to not found",
)


def _is_missing_reply(error: TelegramBadRequest) -> bool:
    return any(token in str(error).lower() for token in _MISSING_REPLY_ERRORS)


class DeletedReplyFallbackMiddleware(BaseRequestMiddleware):
    """Attempt the original reply, then ONE unthreaded send if its target is gone.

    The retry preserves chat_id, media, caption, keyboard, business connection,
    forum topic and all other method parameters. There is no extra download
    and no retry for authorization, rate limit, media or unrelated errors.
    """

    async def __call__(self, make_request, bot, method):
        method_name = type(method).__name__
        fields = ("reply_parameters", "reply_to_message_id")
        has_reply_reference = any(getattr(method, field, None) is not None for field in fields)

        if method_name not in _REPLYABLE_SEND_METHODS or not has_reply_reference:
            return await make_request(bot, method)

        try:
            return await make_request(bot, method)
        except TelegramBadRequest as error:
            if not _is_missing_reply(error):
                raise

            updates = {
                field: None
                for field in fields
                if getattr(method, field, None) is not None
            }
            retry = method.model_copy(update=updates)
            logger.warning(
                "Original Telegram reply disappeared; sending without reply. method=%s",
                method_name,
            )
            # Intentionally one retry only. If the bot cannot post in the chat,
            # let that different failure reach the normal handler.
            return await make_request(bot, retry)
