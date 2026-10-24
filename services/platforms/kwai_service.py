"""Standalone public Kwai media download service.

Does not depend on the universal extractor, Facebook handlers or bot runtime.
Only public page extraction, bounded CDN streaming and ffmpeg/ffprobe metadata.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import aiohttp

from services.platforms.kwai_media import (
    _DEFAULT_UA,
    _is_allowed_media_url,
    extract_kwai_video_page,
    is_kwai_url,
)

logger = logging.getLogger(__name__)


@dataclass
class KwaiDownloadItem:
    url: str
    type: str = "video"
    thumb: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[int] = None
    index: int = 0


@dataclass
class KwaiDownloadResult:
    id: str
    description: str
    author: str
    media_list: list[KwaiDownloadItem]


async def _run_probe(video_file: str) -> dict | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration:format=duration",
            "-of", "json", video_file,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=25)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return None
        if proc.returncode:
            return None
        data = json.loads(stdout)
        streams = data.get("streams") or []
        if not streams:
            return None
        stream = streams[0]
        fmt = data.get("format") or {}

        def positive_int(value):
            try:
                value = float(value)
                return int(round(value)) if 0 < value < 2_147_483_647 else None
            except (TypeError, ValueError, OverflowError):
                return None

        return {
            "width": positive_int(stream.get("width")),
            "height": positive_int(stream.get("height")),
            "duration": positive_int(fmt.get("duration"))
            or positive_int(stream.get("duration")),
        }
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        logger.warning("Kwai ffprobe failed to read video stream")
        return None


async def _make_thumbnail(video_file: str, thumb_file: str) -> str | None:
    for seek in ("1", "0"):
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-nostdin", "-y", "-ss", seek, "-i", video_file,
                "-frames:v", "1",
                "-vf", "scale=320:320:force_original_aspect_ratio=decrease",
                "-q:v", "5", thumb_file,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                continue
            if (
                proc.returncode == 0
                and os.path.isfile(thumb_file)
                and 0 < os.path.getsize(thumb_file) < 200_000
            ):
                return thumb_file
        except OSError:
            pass
        Path(thumb_file).unlink(missing_ok=True)
    return None


async def download_kwai_media(
    url: str,
    *,
    output_dir: str = "downloads",
    post_id: str,
    max_file_size: int = 1_000_000_000,
) -> KwaiDownloadResult | None:
    """Download a public Kwai video with a firm byte limit.

    The caller must pass a unique post_id per request to prevent concurrency
    collisions and is responsible for removing returned local files.
    """
    if not is_kwai_url(url):
        return None
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", post_id):
        raise ValueError("Invalid Kwai temporary media ID")

    info = await extract_kwai_video_page(url)
    if not info:
        return None
    media_url = info.get("media_url")
    if not media_url or not _is_allowed_media_url(media_url):
        return None

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    video_path = directory / f"{post_id}.mp4"
    thumb_path = directory / f"{post_id}_thumb.jpg"
    max_bytes = max(1, int(max_file_size))
    size = 0
    completed = False

    try:
        timeout = aiohttp.ClientTimeout(connect=15, sock_read=180, total=600)
        async with aiohttp.ClientSession(
            headers={
                "User-Agent": _DEFAULT_UA,
                "Referer": "https://www.kwai.com/",
                "Accept": "*/*",
            },
            timeout=timeout,
        ) as session:
            async with session.get(media_url, allow_redirects=False) as response:
                if response.status != 200:
                    logger.warning("Kwai CDN returned status=%s", response.status)
                    return None
                # A CDN redirect must not turn a public video fetch into an
                # arbitrary host or private-network request.
                if not _is_allowed_media_url(str(response.url)):
                    logger.warning("Kwai CDN redirected outside allowed domains")
                    return None
                header = response.headers.get("Content-Length")
                if header and header.isdigit() and int(header) > max_bytes:
                    logger.warning("Kwai video exceeds configured size limit")
                    return None
                with video_path.open("wb") as output:
                    async for chunk in response.content.iter_chunked(256 * 1024):
                        size += len(chunk)
                        if size > max_bytes:
                            logger.warning("Kwai video stream exceeded size limit")
                            return None
                        output.write(chunk)

        if size == 0:
            return None
        metadata = await _run_probe(str(video_path))
        if metadata is None:
            logger.warning("Kwai download did not produce a valid video")
            return None
        thumb = await _make_thumbnail(str(video_path), str(thumb_path))
        completed = True
        return KwaiDownloadResult(
            id=post_id,
            description=info.get("title") or "Vídeo do Kwai",
            author=info.get("author") or "Kwai",
            media_list=[
                KwaiDownloadItem(
                    url=str(video_path),
                    thumb=thumb,
                    width=metadata["width"],
                    height=metadata["height"],
                    duration=metadata["duration"],
                )
            ],
        )
    except (aiohttp.ClientError, TimeoutError, OSError, ValueError) as exc:
        logger.warning("Kwai media download failed: %s", type(exc).__name__)
        return None
    finally:
        if not completed:
            video_path.unlink(missing_ok=True)
            thumb_path.unlink(missing_ok=True)
