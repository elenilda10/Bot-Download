from __future__ import annotations

import re
import time
from typing import Any, Awaitable, Callable

from aiogram import Router, types
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

import messages as bm
from app_context import bot, db
from handlers.utils import get_bot_url
from services.links.detection import extract_supported_link
from services.logger import logger as logging

router = Router(name="guest")

_DEEZER_LINK_REGEX = re.compile(
    r"https?://(?:www\.)?deezer\.com/(?:[a-z]{2}/)?"
    r"(?:track|album|playlist|episode)/[0-9]+"
    r"|https?://deezer\.page\.link/[A-Za-z0-9]+",
    re.IGNORECASE,
)

_GUEST_SERVICE_NAMES = {
    "tiktok": "TikTok",
    "youtube": "YouTube",
    "instagram": "Instagram",
    "twitter": "X / Twitter",
    "soundcloud": "SoundCloud",
    "pinterest": "Pinterest",
    "threads": "Threads",
    "deezer": "Deezer",
    "spotify": "Spotify",
    "kwai": "Kwai",
}

_GUEST_INLINE_SERVICES = {
    "tiktok",
    "youtube",
    "instagram",
    "twitter",
    "soundcloud",
    "pinterest",
    "threads",
    "deezer",
}

_GUEST_MIN_INTERVAL_SECONDS = 1.5
_guest_last_request: dict[int, float] = {}


def _normalize_lang(lang: str | None) -> str:
    value = (lang or "").strip().lower()
    return "pt" if value.startswith("pt") else "en"


def _chat_type_value(chat_type: Any) -> str:
    return chat_type.value if hasattr(chat_type, "value") else str(chat_type or "guest")


def _collect_guest_text(message: types.Message) -> str:
    parts = [message.text or message.caption or ""]

    replied = getattr(message, "reply_to_message", None)
    if replied is not None:
        parts.append(getattr(replied, "text", None) or getattr(replied, "caption", None) or "")

    return "\n".join(part for part in parts if part).strip()


def _extract_guest_link(text: str) -> tuple[str, str] | None:
    deezer = _DEEZER_LINK_REGEX.search(text or "")
    if deezer:
        return "deezer", deezer.group(0).rstrip(".,;:!?)]}>\"'")

    return extract_supported_link(text)


async def _resolve_guest_language(message: types.Message) -> str:
    user = message.from_user
    if user is None:
        return "en"

    telegram_lang = _normalize_lang(getattr(user, "language_code", None))

    try:
        info = await db.get_user_info(user.id)
    except Exception:
        info = None

    if info is not None:
        stored_lang = getattr(info, "language", None)
        if stored_lang:
            return _normalize_lang(str(stored_lang))

    try:
        await db.upsert_chat(
            user_id=user.id,
            user_name=user.full_name,
            user_username=user.username,
            chat_type="private",
            language=telegram_lang,
            status="active",
            source="guest" if info is None else None,
        )
        if info is not None and not getattr(info, "language", None):
            await db.set_language(user.id, telegram_lang)
    except Exception as exc:
        logging.warning(
            "Guest user language initialization failed: user_id=%s error=%s",
            user.id,
            exc,
        )

    return telegram_lang


def _fallback_result(
    *,
    lang: str,
    bot_url: str,
    service_name: str | None = None,
    no_link: bool = False,
) -> InlineQueryResultArticle:
    if no_link:
        title = bm.guest_no_link_title(lang=lang)
        description = bm.guest_no_link_description(lang=lang)
        text = bm.guest_no_link_message(lang=lang)
    else:
        title = bm.guest_unavailable_title(service_name or "", lang=lang)
        description = bm.guest_unavailable_description(lang=lang)
        text = bm.guest_unavailable_message(service_name or "", lang=lang)

    return InlineQueryResultArticle(
        id="guest-fallback",
        title=title,
        description=description,
        input_message_content=InputTextMessageContent(
            message_text=text,
            parse_mode="HTML",
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=bm.guest_open_private_button(lang=lang),
                        url=bot_url,
                    )
                ]
            ]
        ),
    )


class GuestInlineQueryAdapter:
    """
    Makes existing inline handlers reusable for Guest Mode.

    answerGuestQuery accepts exactly one InlineQueryResult, while regular
    inline mode returns a list. We reuse the first prepared result so Guest
    Mode inherits the same media metadata, buttons and PT/EN translations.
    """

    def __init__(
        self,
        *,
        message: types.Message,
        query: str,
        lang: str,
        bot_url: str,
        service_name: str,
    ) -> None:
        self.message = message
        self.query = query
        self.from_user = message.from_user
        self.chat_type = message.chat.type
        self.id = None
        self.lang = lang
        self.bot_url = bot_url
        self.service_name = service_name
        self.answered = False

    async def answer(self, results: list[Any], **_kwargs: Any) -> Any:
        if self.answered:
            return None

        result = (
            results[0]
            if results
            else _fallback_result(
                lang=self.lang,
                bot_url=self.bot_url,
                service_name=self.service_name,
            )
        )

        sent = await self.message.answer_guest_query(result=result)
        self.answered = True
        return sent


async def _dispatch_existing_inline_handler(
    service: str,
    query: GuestInlineQueryAdapter,
) -> bool:
    if service == "tiktok":
        from handlers.tiktok import inline_tiktok_query

        await inline_tiktok_query(query)
        return True

    if service == "youtube":
        from handlers.youtube import inline_youtube_music_query, inline_youtube_query

        if "music.youtube." in query.query.lower():
            await inline_youtube_music_query(query)
        else:
            await inline_youtube_query(query)
        return True

    if service == "instagram":
        from handlers.instagram import _instagram_inline_query

        await _instagram_inline_query(query)
        return True

    if service == "twitter":
        from handlers.twitter import twitter_inline_query_handler

        await twitter_inline_query_handler(query)
        return True

    if service == "soundcloud":
        from handlers.soundcloud import inline_soundcloud_query

        await inline_soundcloud_query(query)
        return True

    if service == "pinterest":
        from handlers.pinterest import inline_pinterest_query

        await inline_pinterest_query(query)
        return True

    if service == "threads":
        from handlers.threads import inline_threads_query

        await inline_threads_query(query)
        return True

    if service == "deezer":
        from handlers.deezer import deezer_inline_query_handler

        await deezer_inline_query_handler(query)
        return True

    return False


async def _answer_guest_fallback(
    message: types.Message,
    *,
    lang: str,
    bot_url: str,
    service_name: str | None = None,
    no_link: bool = False,
) -> None:
    await message.answer_guest_query(
        result=_fallback_result(
            lang=lang,
            bot_url=bot_url,
            service_name=service_name,
            no_link=no_link,
        )
    )


@router.guest_message()
async def guest_download(message: types.Message) -> None:
    if not message.guest_query_id or message.from_user is None:
        return

    lang = await _resolve_guest_language(message)
    user_id = int(message.from_user.id)

    try:
        status = await db.status(user_id)
    except Exception:
        status = None

    bot_url = await get_bot_url(bot)

    if status in {"ban", "restricted"}:
        await _answer_guest_fallback(
            message,
            lang=lang,
            bot_url=bot_url,
            service_name=bm.guest_restricted_service_name(lang=lang),
        )
        return

    now = time.monotonic()
    last = _guest_last_request.get(user_id, 0.0)
    if now - last < _GUEST_MIN_INTERVAL_SECONDS:
        await message.answer_guest_query(
            result=InlineQueryResultArticle(
                id="guest-rate-limit",
                title=bm.guest_slow_down_title(lang=lang),
                description=bm.guest_slow_down_description(lang=lang),
                input_message_content=InputTextMessageContent(
                    message_text=bm.guest_slow_down_message(lang=lang),
                    parse_mode="HTML",
                ),
            )
        )
        return
    _guest_last_request[user_id] = now

    text = _collect_guest_text(message)
    detected = _extract_guest_link(text)

    if detected is None:
        await _answer_guest_fallback(
            message,
            lang=lang,
            bot_url=bot_url,
            no_link=True,
        )
        return

    service, source_url = detected
    service_name = _GUEST_SERVICE_NAMES.get(service, service.title())

    if service not in _GUEST_INLINE_SERVICES:
        await _answer_guest_fallback(
            message,
            lang=lang,
            bot_url=bot_url,
            service_name=service_name,
        )
        return

    adapter = GuestInlineQueryAdapter(
        message=message,
        query=source_url,
        lang=lang,
        bot_url=bot_url,
        service_name=service_name,
    )

    try:
        dispatched = await _dispatch_existing_inline_handler(service, adapter)
        if not dispatched or not adapter.answered:
            await _answer_guest_fallback(
                message,
                lang=lang,
                bot_url=bot_url,
                service_name=service_name,
            )
    except Exception as exc:
        logging.exception(
            "Guest Mode failed: user_id=%s service=%s error=%s",
            user_id,
            service,
            exc,
        )
        if not adapter.answered:
            await _answer_guest_fallback(
                message,
                lang=lang,
                bot_url=bot_url,
                service_name=service_name,
            )
