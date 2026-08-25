import time
from typing import Optional

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, InlineQuery, Message

from app_context import db

DEFAULT_LANG = "en"


def _normalize_lang(language_code: Optional[str]) -> str:
    if not language_code:
        return DEFAULT_LANG
    lang = language_code.lower().strip()
    return "pt" if lang.startswith("pt") else "en"


def _ban_message(lang: str = DEFAULT_LANG) -> str:
    if _normalize_lang(lang) == "pt":
        return "Você está banido. Entre em contato com o suporte para mais informações."
    return "You are banned. Please contact support for more information!"


def _service_unavailable_message(lang: str = DEFAULT_LANG) -> str:
    if _normalize_lang(lang) == "pt":
        return "O serviço está temporariamente indisponível. Tente novamente mais tarde."
    return "Service is temporarily unavailable. Please try again later."


class UserBannedMiddleware(BaseMiddleware):
    def __init__(self, ttl_seconds: float = 12.0):
        super().__init__()
        self._ttl_seconds = ttl_seconds
        self._status_cache: dict[int, tuple[float, str]] = {}

    async def _get_status(self, user_id: int) -> str:
        now = time.monotonic()
        cached = self._status_cache.get(user_id)
        if cached and now - cached[0] <= self._ttl_seconds:
            return cached[1]

        try:
            user_status = await db.status(user_id)
        except Exception:
            user_status = cached[1] if cached else "active"

        status_value = user_status or "active"
        self._status_cache[user_id] = (now, status_value)
        return status_value

    async def on_pre_process_message(self, message: Message, data: dict):
        if not message.from_user:
            return

        user_status = await self._get_status(message.from_user.id)
        user_lang = data.get("user_lang") or _normalize_lang(
            message.from_user.language_code
        )

        if user_status == "ban":
            if message.chat.type == "private":
                await message.answer(
                    _ban_message(user_lang),
                    parse_mode="HTML",
                )
            data["_skip_handler"] = True
            return

        if user_status == "restricted":
            if message.chat.type == "private":
                await message.answer(_service_unavailable_message(user_lang))
            data["_skip_handler"] = True
            return

    async def on_pre_process_callback_query(
        self, callback_query: CallbackQuery, data: dict
    ):
        if not callback_query.from_user:
            return

        user_status = await self._get_status(callback_query.from_user.id)
        user_lang = data.get("user_lang") or _normalize_lang(
            callback_query.from_user.language_code
        )

        if user_status == "ban":
            await callback_query.answer(_ban_message(user_lang), show_alert=True)
            data["_skip_handler"] = True
            return

        if user_status == "restricted":
            await callback_query.answer(
                _service_unavailable_message(user_lang), show_alert=True
            )
            data["_skip_handler"] = True
            return

    async def on_pre_process_inline_query(
        self, inline_query: InlineQuery, data: dict
    ):
        if not inline_query.from_user:
            return

        user_status = await self._get_status(inline_query.from_user.id)
        if user_status in {"ban", "restricted"}:
            data["_skip_handler"] = True
            return

    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            await self.on_pre_process_message(event, data)
        elif isinstance(event, CallbackQuery):
            await self.on_pre_process_callback_query(event, data)
        elif isinstance(event, InlineQuery):
            await self.on_pre_process_inline_query(event, data)

        if data.get("_skip_handler"):
            return None

        return await handler(event, data)
