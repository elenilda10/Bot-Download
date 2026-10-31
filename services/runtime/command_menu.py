"""Keep Telegram's native command descriptions aligned with the chat locale."""

import logging
from collections import OrderedDict

from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommandScopeChat

from i18n import bot_commands, normalize_locale, reset_locale, set_locale

_synced = OrderedDict()
_CACHE_LIMIT = 4096


async def sync_command_menu(bot, chat_id: int, locale: str) -> bool:
    """Cache successful registrations only; failures must not block bot requests."""
    locale = normalize_locale(locale)
    key = (bot.id, chat_id)
    if _synced.get(key) == locale:
        _synced.move_to_end(key)
        return True
    token = set_locale(locale)
    try:
        # A chat-specific default outranks global language-specific menus, so
        # /lang can differ from the language of the user's Telegram app.
        result = await bot.set_my_commands(
            commands=bot_commands(locale),
            scope=BotCommandScopeChat(chat_id=chat_id),
            language_code="",
            request_timeout=5,
        )
        if not result:
            return False
    except (TelegramAPIError, TimeoutError, OSError):
        logging.getLogger(__name__).warning(
            "Failed to synchronize command menu: chat_id=%s locale=%s",
            chat_id, locale,
        )
        return False
    finally:
        reset_locale(token)
    _synced[key] = locale
    _synced.move_to_end(key)
    if len(_synced) > _CACHE_LIMIT:
        _synced.popitem(last=False)
    return True
