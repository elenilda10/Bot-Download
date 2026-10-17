from __future__ import annotations

import time
from datetime import datetime, timezone

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNotFound,
)
from aiogram.types import Chat, Message

from services.runtime.pending_requests import (
    get_pending,
    pop_pending,
)

# Compartilhados por todas as instâncias do middleware
membership_cache = {}
resuming_users = set()


def mark_subscribed(user_id: int) -> None:
    membership_cache[user_id] = (time.monotonic() + 90, True)


async def resume_pending_request(bot, user, pending) -> str:
    """Reproduz o link original sem passar novamente pelos middlewares."""

    source_chat_id = pending.source_chat_id or user.id
    source_message_id = pending.source_message_id

    if (
        (source_chat_id > 0 and source_chat_id != user.id)
        or not source_message_id
        or source_message_id <= 0
    ):
        return "invalid"

    if source_chat_id < 0:
        try:
            await bot.send_chat_action(
                chat_id=user.id,
                action="typing",
            )
        except (
            TelegramForbiddenError,
            TelegramNotFound,
            TelegramBadRequest,
        ):
            return "dm_required"

    chat_type = (
        "private"
        if source_chat_id > 0
        else "supergroup"
        if str(source_chat_id).startswith("-100")
        else "group"
    )

    original_message = Message.model_construct(
        message_id=source_message_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=source_chat_id, type=chat_type),
        from_user=user,
        text=pending.url,
        caption=None,
    ).as_(bot)

    from handlers.media_download import _process_pending_message

    await _process_pending_message(original_message)

    # Não apaga uma solicitação mais recente do mesmo usuário.
    current = get_pending(user.id)
    if (
        current is not None
        and current.url == pending.url
        and current.created_at_epoch == pending.created_at_epoch
    ):
        pop_pending(user.id)

    return "resumed"
