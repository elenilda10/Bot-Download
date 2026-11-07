"""Read the public post's own photo, preserving LinkedIn's signed CDN query."""

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit
import asyncio
import re

import aiohttp


def _identity(url):
    try:
        parts = urlsplit(url)
        if parts.hostname not in {"www.linkedin.com", "linkedin.com"}:
            return None
        if parts.path.startswith("/posts/"):
            match = re.search(r"-(\d+)-[\w-]{4}/?$", parts.path)
        else:
            match = re.fullmatch(r"/(?:embed/)?feed/update/urn:li:(?:activity|share|ugcPost):(\d+)/?", parts.path)
        return match[1] if match else None
    except ValueError:
        return None


def _photo_url(value):
    try:
        parts = urlsplit(value)
        if (parts.scheme == "https" and parts.netloc.lower() in {"media.licdn.com", "media-exp1.licdn.com"}
                and "/feedshare-" in parts.path and "/dms/image/" in parts.path):
            return value
    except ValueError:
        pass
    return None


class _PostPage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.canonical = []
        self.embed_photos = []
        self.video = False
        self.video_element = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            value = a.get("content") or ""
            if value:
                self.meta.setdefault(key, value)
            if key.startswith("og:video") or (key == "og:type" and value.startswith("video")):
                self.video = True
        elif tag == "link" and "canonical" in (a.get("rel") or "").lower().split():
            self.canonical.append(a.get("href") or "")
        elif tag == "video":
            self.video_element = True
        elif tag == "img":
            classes = (a.get("class") or "").split()
            if any(c in {"share-images__image", "feed-shared-image__image"} for c in classes):
                for key in ("data-delayed-url", "data-src", "src"):
                    url = _photo_url(a.get(key) or "")
                    if url:
                        self.embed_photos.append(url)
                        break

    def matches(self, post_id):
        urls = list(self.canonical)
        if "og:url" in self.meta:
            urls.append(self.meta["og:url"])
        return all(_identity(url) == post_id for url in urls)

    def photo(self, *, embed):
        for key in ("og:image:secure_url", "og:image", "twitter:image"):
            photo = _photo_url(self.meta.get(key, ""))
            if photo:
                return photo
        if embed and len(set(self.embed_photos)) == 1:
            return self.embed_photos[0]
        return None


@dataclass
class PostPhoto:
    url: str
    description: str


async def _read_page(session, url):
    async with session.get(url, allow_redirects=False) as response:
        if response.status != 200 or response.content_type != "text/html":
            return None
        body = bytearray()
        async for chunk in response.content.iter_chunked(32768):
            body.extend(chunk)
            if len(body) >= 1024 * 1024:
                break
        parser = _PostPage()
        parser.feed(body.decode("utf-8", "replace"))
        return parser


async def find_post_photo(post_url, user_agent):
    post_id = _identity(post_url)
    if not post_id:
        return None
    kind = "ugcPost" if "-ugcPost-" in post_url else "share" if "-share-" in post_url else "activity"
    embed_url = f"https://www.linkedin.com/embed/feed/update/urn:li:{kind}:{post_id}"
    try:
        async with asyncio.timeout(12), aiohttp.ClientSession(
            headers={"User-Agent": user_agent}, cookie_jar=aiohttp.DummyCookieJar(),
        ) as session:
            description = ""
            for url in (post_url, embed_url):
                page = await _read_page(session, url)
                if not page:
                    continue
                if not page.matches(post_id):
                    continue
                if page.video or (url == embed_url and page.video_element):
                    return None
                description = description or page.meta.get("og:description", "")
                image = page.photo(embed=url == embed_url)
                if image:
                    return PostPhoto(image, description)
    except (aiohttp.ClientError, TimeoutError, ValueError):
        pass
    return None
