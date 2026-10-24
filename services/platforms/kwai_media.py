from __future__ import annotations

import asyncio
import html
import logging
import re
from urllib.parse import urlparse

import aiohttp


logger = logging.getLogger(__name__)

_DEFAULT_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Mobile Safari/537.36"
)


def is_kwai_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False

    return (
        host == "kwai.com"
        or host.endswith(".kwai.com")
        or host == "kwai-video.com"
        or host.endswith(".kwai-video.com")
        or host == "kw.ai"
        or host.endswith(".kw.ai")
    )


def _is_allowed_media_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except Exception:
        return False

    if parsed.scheme not in {"http", "https"}:
        return False

    return (
        host == "kwai.net"
        or host.endswith(".kwai.net")
        or host == "yximgs.com"
        or host.endswith(".yximgs.com")
    )


def _normalize_page(text: str) -> str:
    text = html.unescape(text)

    replacements = (
        ("\\u002F", "/"),
        ("\\u002f", "/"),
        ("\\u0026", "&"),
        ("\\u003D", "="),
        ("\\u003A", ":"),
        ("\\u003a", ":"),
        ("\\u003d", "="),
        ("\\/", "/"),
    )

    for old, new in replacements:
        text = text.replace(old, new)

    return text


def _extract_meta(text: str, key: str) -> str:
    escaped = re.escape(key)

    patterns = (
        rf'<meta[^>]+(?:property|name)=["\']{escaped}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{escaped}["\']',
    )

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return html.unescape(match.group(1)).strip()

    return ""



_ENGAGEMENT_PREFIX = re.compile(
    r"^\s*[\d.,\s]+Like(?:\(s\)|s)?\s*,\s*"
    r"[\d.,\s]+Comment(?:\(s\)|s)?\.\s*",
    flags=re.IGNORECASE,
)
_KWAI_SHARE_DESCRIPTION = re.compile(
    r"^Kwai\s+video\s+from\s+(?P<creator>.+?)\s*:\s*(?P<caption>.*)$",
    flags=re.IGNORECASE | re.DOTALL,
)


def _parse_kwai_post_description(raw: str) -> tuple[str, str | None]:
    """Recover the creator's actual caption from Kwai's share-preview text.

    Example: '13571 Like(s), 100 Comment(s). Kwai video from
    SPORTS(@TL.ESPORTE): "#ufc #mma"' -> ('#ufc #mma', 'SPORTS (@TL.ESPORTE)').
    Likes and comments are ephemeral and do not belong in the Telegram caption.
    """
    text = html.unescape(raw or "").strip()
    text = _ENGAGEMENT_PREFIX.sub("", text)
    creator = None

    match = _KWAI_SHARE_DESCRIPTION.match(text)
    if match:
        creator = re.sub(
            r"\s*\(\s*(@[^)]+)\s*\)", r" (\1)", match.group("creator").strip()
        )
        text = match.group("caption").strip()
    elif re.match(r"^Kwai\s+video\s+from\b", text, flags=re.IGNORECASE):
        # A generic Kwai share heading without the actual caption.
        return "", None

    if len(text) >= 2 and (text[0], text[-1]) in {
        ('"', '"'), ("'", "'"), ("“", "”"), ("«", "»")
    }:
        text = text[1:-1].strip()

    if text.casefold() in {"kwai", "kwai video", "vídeo do kwai", "video do kwai"}:
        text = ""

    return text, creator


def format_kwai_post_caption(description: str | None, author: str | None) -> str:
    """Plain-text Telegram body; bm.captions() escapes HTML and adds footer."""
    description = (description or "").strip()
    if description.casefold() in {"vídeo do kwai", "video do kwai"}:
        description = ""

    creator = (author or "").strip()
    if creator.casefold() == "kwai":
        creator = ""
    elif re.fullmatch(r"[\w.-]+", creator, flags=re.UNICODE):
        creator = "@" + creator.lstrip("@")

    parts = []
    if creator:
        parts.append("👤 " + creator)
    if description:
        parts.append(description)
    return "\n\n".join(parts)



def _extract_media_url(page: str) -> str | None:
    """Find a playable MP4 from Kwai's public page, preferring post metadata.

    Signed CDN URLs must retain their entire query string. Do not accept
    arbitrary hosts: public pages may contain links unrelated to the media.
    """
    normalized = _normalize_page(page)
    candidates: list[str] = []

    # Page metadata is post-specific; a generic MP4 in the HTML may belong
    # to a recommendation rather than the video requested by the user.
    for key in ("og:video", "og:video:url", "twitter:player:stream"):
        value = _extract_meta(page, key)
        if value:
            candidates.append(_normalize_page(value))

    # Kwai pages also embed video properties inside JSON/JS state. These
    # properties are commonly escaped as https:\/\/ or https:\u002F\u002F.
    keyed_pattern = (
        r"""["'](?:playUrl|videoUrl|video_url|photoUrl|contentUrl|content_url)["']"""
        r"""\s*:\s*["'](?P<url>https?://[^"'<>\\\s]+)"""
    )
    candidates.extend(
        match.group("url")
        for match in re.finditer(keyed_pattern, normalized, re.IGNORECASE)
    )

    # Backward-compatible fallback for HTML that exposes an MP4 without
    # naming the property containing it.
    candidates.extend(re.findall(
        r"""https?://[^"'<>\\\s]+?\.mp4(?:\?[^"'<>\\\s]*)?""",
        normalized,
        re.IGNORECASE,
    ))

    for value in candidates:
        value = value.rstrip(";,)}")
        if _is_allowed_media_url(value) and urlparse(value).path.lower().endswith(".mp4"):
            return value
    return None


def _fetch_kwai_chrome_page(url: str) -> tuple[str, str] | None:
    """Fetch public page with Chrome TLS impersonation; no browser cookies.

    Kwai may serve a video-free app shell to aiohttp while returning the
    public MP4 metadata when accessed with curl_cffi's Chrome impersonation.
    Run this blocking request with asyncio.to_thread from the async caller.
    """
    try:
        from curl_cffi import requests as curl_requests

        response = curl_requests.get(
            url,
            impersonate="chrome",
            timeout=25,
            allow_redirects=True,
            headers={
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
                "Referer": "https://www.kwai.com/",
            },
        )
        final_url = str(response.url)
        if not is_kwai_url(final_url):
            logger.warning("Kwai Chrome request redirected off Kwai")
            return None
        if response.status_code != 200:
            logger.warning("Kwai Chrome page returned status=%s", response.status_code)
            return None

        return final_url, response.text
    except Exception as exc:
        # Do not log full signed URLs, request headers or response bodies.
        logger.warning("Kwai Chrome request failed: %s", type(exc).__name__)
        return None


async def extract_kwai_video_page(url: str) -> dict | None:
    if not is_kwai_url(url):
        return None

    headers = {
        "User-Agent": _DEFAULT_UA,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Referer": "https://www.kwai.com/",
    }

    timeout = aiohttp.ClientTimeout(total=30)
    final_url = url
    page = ""
    media_url = None
    last_status = None

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            # The short link redirects to a canonical URL. Try both, since
            # the HTML can differ even when both return HTTP 200.
            async with session.get(
                url, allow_redirects=True, timeout=timeout
            ) as response:
                final_url = str(response.url)
                last_status = response.status
                if not is_kwai_url(final_url):
                    logger.warning("Kwai share redirected off Kwai")
                    return None
                if response.status == 200:
                    page = await response.text(errors="ignore")
                    media_url = _extract_media_url(page)

            canonical = final_url.split("?", 1)[0].split("#", 1)[0]
            if not media_url and canonical != url and is_kwai_url(canonical):
                async with session.get(
                    canonical, allow_redirects=True, timeout=timeout
                ) as response:
                    final_url = str(response.url)
                    last_status = response.status
                    if not is_kwai_url(final_url):
                        logger.warning("Kwai canonical URL redirected off Kwai")
                        return None
                    if response.status == 200:
                        page = await response.text(errors="ignore")
                        media_url = _extract_media_url(page)

    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        logger.warning("Kwai aiohttp page request failed: %s", type(exc).__name__)

    if not media_url:
        # Confirmed on a real public Kwai URL: curl_cffi Chrome returns
        # MP4s while aiohttp returns an HTML shell. Do not use user cookies.
        chrome_url = final_url.split("?", 1)[0].split("#", 1)[0]
        if not is_kwai_url(chrome_url):
            return None

        chrome_page = await asyncio.to_thread(_fetch_kwai_chrome_page, chrome_url)
        if chrome_page:
            chrome_final_url, chrome_html = chrome_page
            candidate = _extract_media_url(chrome_html)
            if candidate:
                final_url, page, media_url = chrome_final_url, chrome_html, candidate
                logger.info("Kwai video extracted via Chrome impersonation")

    if not media_url:
        logger.warning(
            "Kwai page contains no playable MP4: status=%s html_size=%s",
            last_status,
            len(page),
        )
        return None

    author = "Kwai"
    match = re.search(r"/@([^/]+)/video/", final_url)
    if match:
        author = match.group(1)

    # OG descriptions may wrap the user's real text with dynamic counters
    # and "Kwai video from ..." boilerplate. Keep only the original caption.
    title = ""
    creator = None
    for key in ("og:description", "twitter:description", "description", "og:title"):
        raw = _extract_meta(page, key)
        if not raw:
            continue
        text, candidate_author = _parse_kwai_post_description(raw)
        if candidate_author:
            creator = candidate_author
        if text:
            title = text
            break
    if creator:
        author = creator
    title = title or "Vídeo do Kwai"

    return {
        "page_url": final_url,
        "media_url": media_url,
        "title": title,
        "author": author,
    }
