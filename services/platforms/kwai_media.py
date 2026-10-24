from __future__ import annotations

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

    # Some shared links return a transient PWA shell without video data.
    # Once the short URL has resolved, retry its canonical post URL without
    # tracking/query parameters before declaring the public video unavailable.
    candidates = [url]
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            for attempt in range(3):
                current_url = candidates[-1]
                async with session.get(
                    current_url,
                    allow_redirects=True,
                    timeout=timeout,
                ) as response:
                    final_url = str(response.url)
                    last_status = response.status
                    if not is_kwai_url(final_url):
                        logger.warning("Kwai share redirected to an unexpected domain")
                        return None
                    if response.status != 200:
                        if attempt == 2:
                            return None
                    else:
                        page = await response.text(errors="ignore")
                        media_url = _extract_media_url(page)
                        if media_url:
                            break

                    clean_url = final_url.split("?", 1)[0].split("#", 1)[0]
                    if is_kwai_url(clean_url) and clean_url != current_url:
                        candidates.append(clean_url)
                    elif attempt < 2:
                        candidates.append(current_url)

    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        logger.warning("Kwai page request failed: %s", type(exc).__name__)
        return None

    if not media_url:
        logger.warning(
            "Kwai page contains no playable MP4 after retries: status=%s html_size=%s",
            last_status,
            len(page),
        )
        return None

    title = (
        _extract_meta(page, "og:description")
        or _extract_meta(page, "og:title")
        or "Vídeo do Kwai"
    )

    author = "Kwai"

    match = re.search(r"/@([^/]+)/video/", final_url)
    if match:
        author = match.group(1)

    return {
        "page_url": final_url,
        "media_url": media_url,
        "title": title,
        "author": author,
    }
