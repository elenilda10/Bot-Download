"""Dedicated Facebook link routing and short-link normalization.

Delivery remains in Universal so captions, progress and Telegram uploads stay
consistent. Authenticated Facebook access is explicitly restricted by Universal
to ADMIN_ID in the admin's private chat and is never placed in shared cache.
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urlsplit

from aiogram import F, Router, types

from handlers.universal import process_universal_link
from handlers.utils import get_message_text
from services.logger import logger as logging
from services.platforms.universal_extractor import (
    _is_facebook_media_link,
    _is_facebook_url,
)

router = Router(name="facebook_handler")
logging = logging.bind(service="facebook")

# Domain-anchored pattern: not facebook.com.evil.example. No generic catch-all,
# because a matching aiogram handler stops propagation to other platform routers.
FACEBOOK_LINK_REGEX = (
    r"https?://(?:(?:[a-z0-9-]+\\.)*facebook\\.com|"
    r"(?:www\\.)?fb\\.com|(?:www\\.)?fb\\.watch|"
    r"(?:www\\.)?fbs\\.watch)"
    r"(?::443)?(?=[/?#\\s]|$)[^\\s<>]*"
)
_LINK_PATTERN = re.compile(FACEBOOK_LINK_REGEX, re.IGNORECASE)
_REEL_PATH = re.compile(r"/(?:reel|reels)/(\\d+)/?", re.IGNORECASE)
_VIDEO_PATH = re.compile(r"/videos/(\\d+)/?", re.IGNORECASE)
_SHORT_FB_HOSTS = {"fb.watch", "www.fb.watch", "fbs.watch", "www.fbs.watch"}


def _extract_facebook_link(message_text: str) -> str | None:
    """Find a real Facebook URL, safely stripping common message punctuation."""
    for match in _LINK_PATTERN.finditer(message_text or ""):
        candidate = match.group(0).rstrip(".,;!?)>]}")
        if _is_facebook_url(candidate):
            return candidate
    return None


def _needs_redirect(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        (parsed.hostname or "").lower() in _SHORT_FB_HOSTS
        or parsed.path.lower().startswith("/share/")
    )


def _canonical_fb_media_url(url: str) -> str | None:
    """Preserve photo query IDs; strip tracking only for numeric video IDs."""
    if not _is_facebook_url(url) or not _is_facebook_media_link(url):
        return None

    parts = urlsplit(url)
    path = parts.path
    match = _REEL_PATH.fullmatch(path)
    if match:
        return f"https://www.facebook.com/reel/{match.group(1)}/"
    match = _VIDEO_PATH.fullmatch(path)
    if match:
        return f"https://www.facebook.com/videos/{match.group(1)}/"
    # Do not drop 'fbid' on photo.php or 'v' on watch pages.
    return url.split("#", 1)[0]


async def _resolve_facebook_short_link(url: str) -> str | None:
    """Resolve Facebook /share/ and fb.watch links with the system curl.

    Direct yt-dlp works on /reel/<id> but not the short URL. curl's ordinary
    GET redirect flow was verified on the VPS, unlike aiohttp's redirect path.
    Only Facebook media destinations are accepted; no browser cookies here.
    """
    if not _needs_redirect(url):
        return _canonical_fb_media_url(url)
    if not _is_facebook_url(url) or not _is_facebook_media_link(url):
        return None

    command = [
        "curl", "--silent", "--show-error", "--location",
        "--max-redirs", "5", "--max-time", "16", "--connect-timeout", "5",
        "--proto", "=https", "--proto-redir", "=https",
        "--user-agent", "Mozilla/5.0", "--output", "/dev/null",
        "--write-out", "%{url_effective}", url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=19)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            logging.warning("Facebook short-link resolution timed out")
            return None
    except (OSError, ValueError):
        logging.warning("Facebook short-link resolver unavailable")
        return None

    if proc.returncode != 0:
        logging.warning("Facebook short-link resolver failed: code=%s", proc.returncode)
        return None

    final_url = stdout.decode("utf-8", errors="replace").strip()
    if _needs_redirect(final_url):
        logging.warning("Facebook short link remained unresolved")
        return None
    target = _canonical_fb_media_url(final_url)
    if not target:
        logging.warning("Facebook short link resolved to unsupported destination")
    return target


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
