"""Download public XVideos media using the player's own HLS/MP4 sources.

Never use browser session cookies. Network destinations are restricted to the
site and its media CDN, and TLS verification stays enabled.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import ssl
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import aiohttp

from config import MAX_FILE_SIZE
from services.settings import (
    VIDEO_QUALITY_BALANCED,
    VIDEO_QUALITY_SAVER,
    normalize_setting_value,
)

PAGE_HOSTS = frozenset({"xvideos.com", "www.xvideos.com", "m.xvideos.com"})
LINK_RE = re.compile(r"https://(?:www\.|m\.)?xvideos\.com/video\.[a-z0-9]+(?:/[^\s<>\"']*)?", re.I)
VIDEO_PATH_RE = re.compile(r"/video\.[a-z0-9]+(?:/[^?#]*)?", re.I)
CDN_HOST_RE = re.compile(r"[a-z0-9-]+\.xvideos-cdn\.com", re.I)
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
YTDLP_BIN = "/root/Bot-Download/venv/bin/yt-dlp"
MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_JSON_BYTES = 128 * 1024


@dataclass(frozen=True)
class XVideosDownload:
    path: Path
    width: int
    height: int
    duration: int


def _max_height_for_quality(video_quality: str | None) -> int | None:
    """Use the same quality tiers as /settings without YouTube-specific formats."""
    value = normalize_setting_value(video_quality)
    if value == VIDEO_QUALITY_SAVER:
        return 480
    if value == VIDEO_QUALITY_BALANCED:
        return 720
    return None  # "best", missing, or invalid preference: highest available


def _format_for_source(media_url: str, video_quality: str | None) -> str:
    limit = _max_height_for_quality(video_quality)
    host = urlsplit(media_url).hostname or ""
    # HLS has resolution metadata; for MP4 URLs we verify after ffprobe.
    if limit and host.startswith("hls-"):
        return f"best[height<={limit}]"
    return "best"


def extract_xvideos_url(message: str) -> str | None:
    """Find only an HTTPS video page on one of the expected hostnames."""
    for match in LINK_RE.finditer(message or ""):
        candidate = match.group(0).rstrip(".,;:!?)]}")
        p = urlsplit(candidate)
        if (
            p.scheme == "https"
            and p.hostname in PAGE_HOSTS
            and not p.username
            and not p.password
            and p.port in (None, 443)
            and len(p.path) <= 2048
            and VIDEO_PATH_RE.fullmatch(p.path)
        ):
            return urlunsplit(("https", p.hostname, p.path, "", ""))
    return None


def _cdn_media_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 8192:
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme == "https"
            and parsed.hostname
            and CDN_HOST_RE.fullmatch(parsed.hostname)
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
            and parsed.path.startswith("/")
        ):
            return value
    except ValueError:
        pass
    return None


def _player_arg(html: str, function: str) -> str | None:
    pattern = rf"html5player\.{re.escape(function)}\(\s*(['\"]?)([^,'\")\s]+)\1\s*\)"
    values = re.findall(pattern, html)
    return values[-1][1] if values else None


def _ssl_context() -> ssl.SSLContext:
    bundle = os.getenv("XVIDEOS_CA_BUNDLE", "").strip()
    if bundle:
        if not Path(bundle).is_file():
            raise RuntimeError("XVIDEOS_CA_BUNDLE does not point to a file")
        return ssl.create_default_context(cafile=bundle)
    return ssl.create_default_context()


def _allowed_site_url(url: str) -> bool:
    """Only HTTPS on XVideos-owned origins; reject redirects to other sites."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        return (
            parsed.scheme == "https"
            and bool(re.fullmatch(r"(?:[a-z0-9-]+\.)*xvideos\.com", host, re.I))
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
        )
    except ValueError:
        return False


async def _get_limited(
    session: aiohttp.ClientSession,
    url: str,
    limit: int,
    *,
    referer: str | None = None,
) -> tuple[bytes, str]:
    """Follow at most four safe redirects; never fetch an unchecked target."""
    headers = {"Referer": referer} if referer else {}
    current = url
    for attempt in range(5):
        if not _allowed_site_url(current):
            raise RuntimeError("XVideos redirect target is not allowed")
        async with session.get(current, headers=headers, allow_redirects=False) as response:
            if response.status in (301, 302, 303, 307, 308):
                if attempt >= 4:
                    raise RuntimeError("XVideos redirect limit reached")
                location = response.headers.get("Location")
                if not location:
                    raise RuntimeError("XVideos redirect missing Location header")
                target = urljoin(current, location)
                if not _allowed_site_url(target):
                    raise RuntimeError("XVideos redirect target is not allowed")
                current = target
                continue
            if response.status != 200:
                raise RuntimeError(f"XVideos source returned HTTP {response.status}")
            if response.content_length is not None and response.content_length > limit:
                raise RuntimeError("XVideos source response too large")
            body = await response.content.read(limit + 1)
            if len(body) > limit:
                raise RuntimeError("XVideos source response too large")
            return body, current
    raise RuntimeError("XVideos redirect limit reached")


async def _discover_sources(page: str) -> list[str]:
    connector = aiohttp.TCPConnector(ssl=_ssl_context(), limit=4)
    timeout = aiohttp.ClientTimeout(total=35, connect=15)
    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
        trust_env=False,
    ) as session:
        html_body, resolved_page = await _get_limited(session, page, MAX_HTML_BYTES)
        html = html_body.decode("utf-8", "replace")
        encoded = _player_arg(html, "setEncodedIdVideo")
        cdn = _player_arg(html, "setIdCdnHLS") or _player_arg(html, "setIdCDN")
        if (
            not encoded
            or len(encoded) > 256
            or not re.fullmatch(r"[A-Za-z0-9_-]+", encoded)
            or not cdn
            or not re.fullmatch(r"[0-9]{1,10}", cdn)
        ):
            raise RuntimeError("XVideos player parameters unavailable")
        endpoint = f"https://www.xvideos.com/html5player/getvideo/{quote(encoded, safe='')}/{cdn}"
        json_body, _ = await _get_limited(
            session, endpoint, MAX_JSON_BYTES, referer=resolved_page
        )
        data = json.loads(json_body.decode("utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError("Invalid XVideos player response")
        sources = []
        for key in ("hls", "mp4_high", "mp4_low"):
            media_url = _cdn_media_url(data.get(key))
            if media_url and media_url not in sources:
                sources.append(media_url)
        if not sources:
            raise RuntimeError("XVideos player returned no valid media source")
        return sources


async def _probe(path: Path) -> tuple[int, int, int]:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration", "-of", "json",
        str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), 20)
        data = json.loads(stdout) if proc.returncode == 0 else {}
        stream = (data.get("streams") or [{}])[0]
        return (int(stream.get("width") or 0), int(stream.get("height") or 0),
                int(float((data.get("format") or {}).get("duration") or 0)))
    except (ValueError, KeyError, asyncio.TimeoutError):
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        return 0, 0, 0


async def _download_source(
    media_url: str, page: str, dest: Path, *, video_quality: str | None = None
) -> XVideosDownload:
    dest.mkdir(parents=True, exist_ok=True)
    binary = YTDLP_BIN if Path(YTDLP_BIN).is_file() else "yt-dlp"
    cmd = [
        binary, "--no-config", "--no-playlist", "--no-warnings", "--quiet",
        "--socket-timeout", "30", "--retries", "2", "--fragment-retries", "3",
        "--concurrent-fragments", "4", "--max-filesize", str(MAX_FILE_SIZE),
        "--add-header", f"Referer:{page}",
        "-f", _format_for_source(media_url, video_quality), "--merge-output-format", "mp4",
        "--remux-video", "mp4", "-o", str(dest / "video.%(ext)s"),
        media_url,
    ]
    env = os.environ.copy()
    bundle = os.getenv("XVIDEOS_CA_BUNDLE", "").strip()
    if bundle:
        env["SSL_CERT_FILE"] = bundle
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        env=env, start_new_session=True,
    )
    try:
        _, _ = await asyncio.wait_for(proc.communicate(), timeout=900)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()
        raise
    if proc.returncode != 0:
        raise RuntimeError("XVideos media downloader failed")
    path = dest / "video.mp4"
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("XVideos media output missing")
    if path.stat().st_size > MAX_FILE_SIZE:
        raise RuntimeError("XVideos media exceeds upload size limit")
    width, height, duration = await _probe(path)
    if not width or not height:
        raise RuntimeError("XVideos media is not a playable video")
    limit = _max_height_for_quality(video_quality)
    if limit is not None and height > limit:
        raise RuntimeError("XVideos media exceeds requested quality tier")
    return XVideosDownload(path=path, width=width, height=height, duration=duration)


async def download_xvideos(
    page: str, folder: Path, *, video_quality: str | None = None
) -> XVideosDownload:
    """Prefer HLS, then MP4, respecting the user's /settings quality limit."""
    if extract_xvideos_url(page) != page:
        raise ValueError("Invalid XVideos video page")
    sources = await _discover_sources(page)
    for index, media_url in enumerate(sources):
        try:
            return await _download_source(
                media_url, page, folder / str(index), video_quality=video_quality
            )
        except (RuntimeError, OSError, asyncio.TimeoutError):
            if index == len(sources) - 1:
                raise
    raise RuntimeError("No XVideos media available")
