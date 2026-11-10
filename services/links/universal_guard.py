"""Limit unsolicited Universal handler downloads in Telegram groups.

Private chats keep accepting generic URLs as before. Group comments and messages
only auto-download links from supported platforms or direct media files.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,;:!?)]}>\"'"

_SUPPORTED_HOSTS = frozenset(
    {
        "audible.com", "audible.com.br", "audible.co.uk", "audible.de",
        "audible.fr", "audible.in", "audible.it", "audible.es", "audible.co.jp",
        "deezer.com", "deezer.page.link",
        "eporner.com",
        "facebook.com", "fb.com", "fb.watch",
        "instagram.com",
        "kwai.com", "kwai-video.com", "kw.ai",
        "linkedin.com", "lnkd.in",
        "mediafire.com",
        "mega.nz",
        "pin.it",
        "pornhub.com",
        "reddit.com", "redd.it",
        "soundcloud.com", "on.soundcloud.com", "soundcloud.app.goo.gl",
        "spotify.com",
        "threads.com", "threads.net",
        "tiktok.com",
        "twitter.com", "x.com", "t.co",
        "xvideos.com",
        "youtube.com", "youtu.be", "youtube-nocookie.com",
        "drive.google.com", "docs.google.com",
    }
)
_TELEGRAM_HOSTS = ("t.me", "telegram.me", "telegram.dog", "telesco.pe")
_PINTEREST_HOST_RE = re.compile(
    r"(?:[a-z0-9-]+\.)?pinterest\.[a-z]{2,}(?:\.[a-z]{2})?$",
    re.IGNORECASE,
)
_DIRECT_MEDIA_SUFFIXES = (
    ".mp4", ".m4v", ".mov", ".webm", ".m3u8", ".avi", ".mkv",
    ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".aac",
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".svg",
)


def _is_host_or_subdomain(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def is_group_download_url(url: str) -> bool:
    """Whether Universal may *automatically* try this URL in a group.

    Domain matching is hostname-based, not a substring match (so a fake
    youtube.com.evil.example domain is not accepted). Telegram links are
    ignored even if their paths happen to have media extensions.
    """
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"http", "https"} or not host:
        return False
    if any(_is_host_or_subdomain(host, domain) for domain in _TELEGRAM_HOSTS):
        return False
    if any(_is_host_or_subdomain(host, domain) for domain in _SUPPORTED_HOSTS):
        return True
    if _PINTEREST_HOST_RE.fullmatch(host):
        return True
    return parsed.path.lower().endswith(_DIRECT_MEDIA_SUFFIXES)


def select_group_download_url(text: str | None) -> str | None:
    """Choose the first eligible link; ignore unrelated URLs in the post."""
    if not text:
        return None
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(_TRAILING_PUNCTUATION)
        if is_group_download_url(url):
            return url
    return None
