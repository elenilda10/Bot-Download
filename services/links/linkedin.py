"""Resolve public LinkedIn post links without following redirects off LinkedIn."""

import asyncio
from html.parser import HTMLParser
import re
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

LINKEDIN_URL = re.compile(
    r"""(https?://(?:lnkd\.in/|(?:www\.)?linkedin\.com/(?:posts/|feed/update/))[^\s<>"']+)""",
    re.IGNORECASE,
)
_POST = re.compile(r"/posts/[^/?#]+-\d+-[\w-]{4}/?")
_ACTIVITY = re.compile(r"/feed/update/urn:li:activity:\d+/?")
_HOSTS = {"lnkd.in", "linkedin.com", "www.linkedin.com"}
_REDIRECT = {301, 302, 303, 307, 308}


class LinkedInRedirectError(ValueError):
    pass


def _checked(url):
    try:
        parts = urlsplit(url)
        if (parts.scheme not in {"http", "https"} or parts.netloc.lower() not in _HOSTS
                or "\\" in url or any(ord(c) < 32 for c in url)):
            raise LinkedInRedirectError("Unexpected LinkedIn redirect destination")
    except ValueError as exc:
        raise LinkedInRedirectError("Invalid LinkedIn URL") from exc
    return parts


class _PageLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.targets = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and (a.get("http-equiv") or "").lower() == "refresh":
            match = re.fullmatch(r"\s*\d+\s*;\s*url\s*=\s*(.+)", a.get("content") or "", re.I)
            if match:
                self.targets.append(match[1].strip(" \"'"))
        elif tag == "link" and "canonical" in (a.get("rel") or "").lower().split():
            self.targets.append(a.get("href"))
        elif tag == "meta" and a.get("property") == "og:url":
            self.targets.append(a.get("content"))


async def _request(session, url):
    import aiohttp

    try:
        async with session.get(url, allow_redirects=False) as response:
            if response.status in _REDIRECT:
                return response.headers.get("Location")
            if response.status == 200:
                parser = _PageLinks()
                body = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    body.extend(chunk)
                    if len(body) >= 65536:
                        break
                parser.feed(body.decode("utf-8", "replace"))
                return next((v for v in parser.targets if v and urljoin(url, v) != url), None)
            return None
    except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
        # Retry transport failures with the VPS curl client over IPv4/HTTP 1.1.
        # Do not use -L: the caller checks every destination before contacting it.
        proc = await asyncio.create_subprocess_exec(
            "curl", "-q", "-4", "--http1.1", "--silent", "--max-time", "6",
            "--max-filesize", "65536", "--output", "/dev/null", "--write-out",
            "%{redirect_url}", "--", url, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            out, _ = await proc.communicate()
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        return out.decode().strip() if proc.returncode == 0 else None


async def _resolve(url, request):
    seen = set()
    for _ in range(7):
        parts = _checked(url)
        path = unquote(parts.path)
        if parts.hostname != "lnkd.in":
            if _POST.fullmatch(parts.path) or _ACTIVITY.fullmatch(path):
                canonical_path = path if _ACTIVITY.fullmatch(path) else parts.path
                return urlunsplit(("https", "www.linkedin.com", canonical_path, "", ""))
            if parts.path.rstrip("/") not in {"/redir/redirect", "/safety/go"}:
                break
        url = urlunsplit(("https", parts.netloc.lower(), parts.path, parts.query, ""))
        if url in seen:
            break
        seen.add(url)
        location = await request(url)
        if not location:
            break
        url = urljoin(url, location)
    raise LinkedInRedirectError("Could not resolve a public LinkedIn post")


async def resolve_linkedin_url(url):
    import aiohttp

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=6), cookie_jar=aiohttp.DummyCookieJar(),
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"},
    ) as session:
        try:
            return await asyncio.wait_for(_resolve(url, lambda link: _request(session, link)), 20)
        except (OSError, asyncio.TimeoutError, aiohttp.ClientError) as exc:
            raise LinkedInRedirectError("LinkedIn short link unavailable") from exc
