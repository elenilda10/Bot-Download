"""Dedicated Facebook link routing and short-link normalization.

Delivery remains in Universal so captions, progress and Telegram uploads stay
consistent. Authenticated Facebook access is explicitly restricted by Universal
to ADMIN_ID in the admin's private chat and is never placed in shared cache.
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin, urlsplit

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
    r"https?://(?:(?:[a-z0-9-]+\.)*facebook\.com|"
    r"(?:www\.)?fb\.com|(?:www\.)?fb\.watch|"
    r"(?:www\.)?fbs\.watch)"
    r"(?::443)?(?=[/?#\s]|$)[^\s<>]*"
)
_LINK_PATTERN = re.compile(FACEBOOK_LINK_REGEX, re.IGNORECASE)
_REEL_PATH = re.compile(r"/(?:reel|reels)/(\d+)/?", re.IGNORECASE)
_VIDEO_PATH = re.compile(r"/videos/(\d+)/?", re.IGNORECASE)
_GROUP_POST_PATH = re.compile(r"/groups/(\d+)/(permalink|posts)/(\d+)/?", re.IGNORECASE)
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
    match = _GROUP_POST_PATH.fullmatch(path)
    if match:
        # Gallery extraction must use the post URL itself, not a URL with
        # share_url/rdid tracking parameters.
        return (
            f"https://www.facebook.com/groups/{match.group(1)}/"
            f"{match.group(2).lower()}/{match.group(3)}/"
        )
    # Do not drop 'fbid' on photo.php or 'v' on watch pages.
    return url.split("#", 1)[0]


async def _resolve_facebook_short_link(url: str) -> str | None:
    """Resolve Facebook short links using curl GETs without unsafe redirects.

    curl's TLS/HTTP behavior worked on the VPS for /share/r/ where aiohttp
    did not reliably resolve. Every hop is checked BEFORE requesting it,
    preventing Facebook link redirects to unrelated or internal hosts.
    No cookies or authenticated headers are passed to curl.
    """
    if not _is_facebook_url(url) or not _is_facebook_media_link(url):
        return None
    current = url
    for _ in range(5):
        if not _needs_redirect(current):
            return _canonical_fb_media_url(current)

        command = [
            "curl", "--silent", "--show-error",
            "--max-time", "12", "--connect-timeout", "5",
            "--proto", "=https",
            "--user-agent", "Mozilla/5.0",
            "--output", "/dev/null", "--dump-header", "-", current,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=14)
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

        # A single GET response is enough: read the Location response header
        # without following the redirect or downloading the target page.
        header_lines = stdout.decode("utf-8", errors="replace").splitlines()
        locations = [
            line.split(":", 1)[1].strip()
            for line in header_lines
            if line.lower().startswith("location:")
        ]
        if not locations:
            logging.warning("Facebook short link did not return a redirect")
            return None

        next_url = urljoin(current, locations[-1])
        if urlsplit(next_url).scheme != "https" or not _is_facebook_url(next_url):
            logging.warning("Facebook short-link redirect rejected (non-Facebook)")
            return None
        current = next_url

    logging.warning("Facebook short-link redirect limit reached")
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
