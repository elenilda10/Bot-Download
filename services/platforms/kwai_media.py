from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from html.parser import HTMLParser
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



_GENERIC_KWAI_TITLES = frozenset({
    "make everyone shine",
    "kwai-nuxt-pwa-pc",
    "kwai-nuxt-pwa-pc-online",
    "kwai - make everyone shine",
    "kwai",
    "kwai video",
    "kwai - video & drama",
    "get kwai app",
    "vídeo do kwai",
    "video do kwai",
})
_GENERIC_KWAI_DESCRIPTIONS = (
    "kwai is a social network for short videos",
    "discover funny short videos",
    "contribute to the virtual community",
    "download the app to share your life",
)


def _is_generic_kwai_text(value: str | None) -> bool:
    """Detect site-wide marketing text, never the creator's post caption."""
    cleaned = " ".join(html.unescape(value or "").strip().casefold().split())
    return (
        not cleaned
        or cleaned.rstrip(".!") in _GENERIC_KWAI_TITLES
        or any(cleaned.startswith(prefix) for prefix in _GENERIC_KWAI_DESCRIPTIONS)
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

    if _is_generic_kwai_text(text):
        text = ""

    return text, creator


def format_kwai_post_caption(description: str | None, author: str | None) -> str:
    """Keep only the author's actual post text; never append a profile handle.

    The signature retains author for compatibility with existing callers.
    bm.captions() handles HTML escaping and the bot's footer.
    """
    description = (description or "").strip()
    return "" if _is_generic_kwai_text(description) else description



_POST_ID_FIELD = re.compile(r'"(?:photoId|photo_id)"\s*:\s*"?(\d{8,})"?')
_EMBEDDED_CAPTION_FIELD = re.compile(
    r'"(?:caption|photoCaption|videoCaption)"\s*:\s*(?P<value>"(?:[^"\\]|\\.)*")'
)


def _extract_embedded_post_caption(page: str, post_url: str) -> str:
    """Only take a caption associated with the requested Kwai photoId.

    Public Kwai pages often ship post-specific JSON/JS state while the
    metadata tags still contain Kwai's generic slogan. Never use the caption
    of a recommended video found elsewhere on the same page.
    """
    match = re.search(r"/video/(\d{8,})(?:[/?#]|$)", post_url)
    if not match:
        return ""
    requested_id = match.group(1)

    for id_match in _POST_ID_FIELD.finditer(page):
        if id_match.group(1) != requested_id:
            continue

        # Prefer decoding the exact post object. Try surrounding JSON object
        # boundaries (the nearest may be a nested dictionary).
        start = id_match.start()
        prefix = page[max(0, start - 15000):start]
        braces = [i for i, c in enumerate(prefix) if c == "{"][-60:]
        decoder = json.JSONDecoder()

        for pos in reversed(braces):
            try:
                parsed, _ = decoder.raw_decode(prefix[pos:] + page[start:start + 15000])
            except (json.JSONDecodeError, ValueError):
                continue
            if (
                isinstance(parsed, dict)
                and str(parsed.get("photoId") or parsed.get("photo_id") or "") == requested_id
            ):
                caption = parsed.get("caption") or parsed.get("photoCaption") or parsed.get("videoCaption")
                if isinstance(caption, str):
                    result, _ = _parse_kwai_post_description(caption)
                    if result and not _is_generic_kwai_text(result):
                        return result

        # Some Kwai inline state is JavaScript rather than strict JSON.
        # Restrict this fallback to captions next to the target's photoId;
        # reject a different photoId if it lies between them.
        context_start = max(0, start - 1800)
        context_end = min(len(page), id_match.end() + 1800)
        nearby = page[context_start:context_end]
        candidates = list(_EMBEDDED_CAPTION_FIELD.finditer(nearby))
        for candidate in sorted(
            candidates, key=lambda item: abs(context_start + item.start() - start)
        ):
            between = page[min(start, context_start + candidate.start()):
                           max(start, context_start + candidate.start())]
            if _POST_ID_FIELD.search(between):
                continue
            try:
                caption = json.loads(candidate.group("value"))
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(caption, str):
                continue
            parsed_text, _ = _parse_kwai_post_description(caption)
            if parsed_text and not _is_generic_kwai_text(parsed_text):
                return parsed_text

    return ""



# Kwai's Nuxt hydration can expose the main post as E[0]={time:...,caption:"..."}
# without either photoId or a JSON-quoted key. Validate the caption against
# a rendered <span> in the same response so an unrelated recommended video
# cannot silently become the downloaded post's description.
_HYDRATED_CAPTION_FIELD = re.compile(
    r'(?<![\w$])(?:caption|photoCaption|videoCaption)\s*:\s*'
    r'(?P<value>"(?:[^"\\]|\\.)*")'
)
_HYDRATION_ENTRY = re.compile(r"\b[A-Za-z_$][\w$]*\[(\d+)\]\s*=\s*\{")


class _KwaiPageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.spans: list[str] = []
        self.scripts: list[str] = []
        self._open_spans: list[list[str]] = []
        self._script_parts: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self._script_parts = []
        elif tag == "span" and self._script_parts is None:
            self._open_spans.append([])

    def handle_endtag(self, tag):
        if tag == "script" and self._script_parts is not None:
            self.scripts.append("".join(self._script_parts))
            self._script_parts = None
        elif tag == "span" and self._open_spans and self._script_parts is None:
            self.spans.append("".join(self._open_spans.pop()))

    def handle_data(self, data):
        if self._script_parts is not None:
            self._script_parts.append(data)
        else:
            for span in self._open_spans:
                span.append(data)


def _comparable_kwai_caption(text: str) -> str:
    # Collapse whitespace only for matching; preserve the author's wording.
    return " ".join(html.unescape(text).split()).casefold()


def _extract_visible_hydrated_caption(page: str) -> str:
    """Use public JS hydration only when it agrees with rendered post text.

    Prefer the E[0] entry when present: later entries can be recommended
    videos. Reject unverified JS captions and marketing boilerplate.
    """
    parser = _KwaiPageText()
    parser.feed(page)
    visible = {
        _comparable_kwai_caption(span)
        for span in parser.spans
        if 4 <= len(span.strip()) <= 4096
    }

    primary: list[str] = []
    unindexed: list[str] = []

    for script in parser.scripts:
        for found in _HYDRATED_CAPTION_FIELD.finditer(script):
            try:
                original = json.loads(found.group("value"))
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(original, str):
                continue
            caption, _ = _parse_kwai_post_description(original)
            if not caption or _is_generic_kwai_text(caption):
                continue
            normalized = _comparable_kwai_caption(caption)
            if normalized not in visible:
                continue

            # Inspect the nearest state-array assignment, if provided.
            # E[0] is the top-level post shown on this Kwai video page.
            prefix = script[max(0, found.start() - 1500):found.start()]
            entries = list(_HYDRATION_ENTRY.finditer(prefix))
            if entries:
                if entries[-1].group(1) == "0":
                    primary.append(caption)
            else:
                unindexed.append(caption)

    if primary:
        return primary[0]
    # Without an index, only accept an unambiguous caption corroborated by
    # a visible <span>; do not select among multiple post/recommendation texts.
    distinct = list(dict.fromkeys(unindexed))
    return distinct[0] if len(distinct) == 1 else ""



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

    # Never select page-wide unlabeled MP4 links: these can be recommendations.

    for value in candidates:
        value = value.rstrip(";,)}")
        if _is_allowed_media_url(value) and urlparse(value).path.lower().endswith(".mp4"):
            return value
    return None



# For downloads, a Kwai page can contain many recommended CDN videos.
# Resolve media only when its owning post can be verified.
def _video_id_from_url(url: str) -> str | None:
    if not is_kwai_url(url):
        return None
    match = re.search(r"/video/(\d{8,})(?:[/?#]|$)", urlparse(url).path)
    return match.group(1) if match else None


def _allowed_mp4(value: str) -> str | None:
    candidate = _normalize_page(value).rstrip(";,)}")
    if _is_allowed_media_url(candidate) and urlparse(candidate).path.lower().endswith(".mp4"):
        return candidate
    return None


def _javascript_object_at(script: str, brace: int) -> str | None:
    """Read a JS object without treating quoted braces as structure."""
    if brace < 0 or brace >= len(script) or script[brace] != "{":
        return None
    depth = 0
    quote = None
    escaped = False
    for i in range(brace, min(len(script), brace + 250_000)):
        char = script[i]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in ('"', "'", chr(96)):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return script[brace:i + 1]
    return None


def _scoped_mp4_urls(block: str) -> list[str]:
    normalized = _normalize_page(block)
    urls = re.findall(
        r"""https?://[^"'<>\\\s]+?\.mp4(?:\?[^"'<>\\\s]*)?""",
        normalized, flags=re.IGNORECASE,
    )
    found = []
    for url in urls:
        candidate = _allowed_mp4(url)
        if candidate and candidate not in found:
            found.append(candidate)
    return found


def _extract_verified_media_url(page: str, post_url: str) -> str | None:
    """Never choose an unassociated MP4 from recommended Kwai content.

    Accept a post-scoped og:video URL when og:url identifies the same post,
    or media inside the primary Nuxt E[0] object whose caption is visibly
    rendered on this page. Ambiguity causes a safe failure.
    """
    requested_id = _video_id_from_url(post_url)
    if not requested_id:
        return None

    og_url = _extract_meta(page, "og:url")
    if og_url and _video_id_from_url(og_url) == requested_id:
        for field in ("og:video", "og:video:url", "twitter:player:stream"):
            url = _allowed_mp4(_extract_meta(page, field))
            if url:
                return url

    parser = _KwaiPageText()
    parser.feed(page)
    visible = {
        _comparable_kwai_caption(span)
        for span in parser.spans
        if 4 <= len(span.strip()) <= 4096
    }
    verified: list[str] = []
    for script in parser.scripts:
        for entry in _HYDRATION_ENTRY.finditer(script):
            if entry.group(1) != "0":
                continue
            block = _javascript_object_at(script, entry.end() - 1)
            if not block:
                continue
            # The top slot is not guaranteed to be the requested post:
            # inaccessible videos can be replaced by a recommendation.
            # Only accept media when the main object explicitly declares
            # the same photoId as the requested canonical URL.
            scoped_id = re.search(
                r'(?:["\']?photoId["\']?|["\']?photo_id["\']?)\s*:\s*["\']?(\d{8,})',
                block,
            )
            if not scoped_id or scoped_id.group(1) != requested_id:
                continue
            found = _HYDRATED_CAPTION_FIELD.search(block)
            if not found:
                continue
            try:
                caption = json.loads(found.group("value"))
            except (json.JSONDecodeError, ValueError):
                continue
            if (
                not isinstance(caption, str)
                or _comparable_kwai_caption(caption) not in visible
            ):
                continue
            candidates = _scoped_mp4_urls(block)
            if len({urlparse(item).path for item in candidates}) == 1 and candidates:
                verified.append(candidates[0])

    if len({urlparse(item).path for item in verified}) == 1 and verified:
        return verified[0]
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
                    media_url = _extract_verified_media_url(page, final_url)

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
                        media_url = _extract_verified_media_url(page, final_url)

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
            candidate = _extract_verified_media_url(chrome_html, chrome_final_url)
            if candidate:
                final_url, page, media_url = chrome_final_url, chrome_html, candidate
                logger.info("Kwai verified post-scoped video extracted via Chrome impersonation")

    if not media_url:
        logger.warning(
            "Kwai page contains no verified post-scoped MP4: status=%s html_size=%s",
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
    title = (
        _extract_embedded_post_caption(page, final_url)
        or _extract_visible_hydrated_caption(page)
    )
    creator = None
    for key in ("og:description", "twitter:description", "description"):
        raw = _extract_meta(page, key)
        if not raw:
            continue
        text, candidate_author = _parse_kwai_post_description(raw)
        if candidate_author:
            creator = candidate_author
        if text and not _is_generic_kwai_text(text):
            if not title:
                title = text
            break
    if creator:
        author = creator
    # Do not use generic site/app title as a video caption. An absent
    # description is safer than attributing marketing text to the creator.
    title = title or "Vídeo do Kwai"

    return {
        "page_url": final_url,
        "media_url": media_url,
        "title": title,
        "author": author,
    }
