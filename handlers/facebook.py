"""Dedicated Facebook link routing and short-link normalization.

Delivery remains in Universal so captions, progress and Telegram uploads stay
consistent. Authenticated Facebook access is explicitly restricted by Universal
to ADMIN_ID in the admin's private chat and is never placed in shared cache.
"""
from __future__ import annotations

import re

from aiogram import F, Router, types

from handlers.universal import process_universal_link
from handlers.utils import get_message_text
from services.logger import logger as logging
from services.platforms.facebook_media import (
    FACEBOOK_LINK_REGEX,
    _is_facebook_url,
    _resolve_facebook_short_link,
)

router = Router(name="facebook_handler")
logging = logging.bind(service="facebook")

_LINK_PATTERN = re.compile(FACEBOOK_LINK_REGEX, re.IGNORECASE)


def _extract_facebook_link(message_text: str) -> str | None:
    """Find a real Facebook URL, safely stripping common message punctuation."""
    for match in _LINK_PATTERN.finditer(message_text or ""):
        candidate = match.group(0).rstrip(".,;!?)>]}")
        if _is_facebook_url(candidate):
            return candidate
    return None


@router.message(
    (F.text.regexp(FACEBOOK_LINK_REGEX, mode="search")
     | F.caption.regexp(FACEBOOK_LINK_REGEX, mode="search"))
    & ~F.text.startswith("/")
)
@router.business_message(
    (F.text.regexp(FACEBOOK_LINK_REGEX, mode="search")
     | F.caption.regexp(FACEBOOK_LINK_REGEX, mode="search"))
    & ~F.text.startswith("/")
)
async def process_facebook_link(message: types.Message) -> None:
    """Handle Facebook before Universal; reuse its existing upload flow."""
    source = _extract_facebook_link(get_message_text(message))
    if not source:
        return
    target = await _resolve_facebook_short_link(source)
    if not target:
        await message.reply(
            "Não consegui abrir o link de compartilhamento do Facebook. "
            "Tente copiar o endereço direto do Reel ou da foto no Facebook.",
            allow_sending_without_reply=True,
        )
        return
    # Delegating means the only place permitted to access the Firefox account
    # remains Universal, which checks ADMIN_ID and private chat type.
    await process_universal_link(message, direct_url=target)
