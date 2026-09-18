import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional
import aiohttp

from services.logger import logger as logging
from services.platforms import CobaltMediaService, strip_url_query
from utils.cobalt_media import parse_cobalt_media_response
from utils.download_manager import (
    DownloadError as DownloadError,
    DownloadMetrics,
)

logging = logging.bind(service="reddit_media")

strip_reddit_url = strip_url_query


@dataclass
class RedditMedia:
    url: str
    type: str
    thumb: Optional[str] = None
    index: int = 0


@dataclass
class RedditVideo:
    id: str
    description: str
    author: str
    media_list: list[RedditMedia]


async def _resolve_reddit_url(url: str) -> str:
    """Resolve links encurtados (/s/ ou redd.it) para a URL canônica do post."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.head(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                final_url = str(resp.url)
                if "/comments/" in final_url:
                    return final_url
    except Exception as exc:
        logging.warning("Error resolving Reddit redirect url=%s: %s", url, exc)
    return url


async def _fetch_reddit_with_gallery_dl(url: str) -> Optional[RedditVideo]:
    resolved_url = await _resolve_reddit_url(url)
    clean_url = strip_reddit_url(resolved_url)

    post_match = re.search(r"comments/([a-zA-Z0-9]+)", clean_url)
    post_id = post_match.group(1) if post_match else hashlib.blake2s(clean_url.encode("utf-8"), digest_size=8).hexdigest()

    cookie_file = "/root/bot_teste/cookies/reddit_cookies.txt"
    cmd = ["gallery-dl", "-j"]
    if os.path.exists(cookie_file):
        cmd.extend(["--cookies", cookie_file])
    cmd.append(clean_url)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0 or not stdout:
            logging.warning(
                "gallery-dl returned code %s for Reddit: %s",
                proc.returncode,
                stderr.decode("utf-8", errors="ignore")[:200],
            )
            return None

        raw_output = stdout.decode("utf-8", errors="ignore").strip()
        parsed_entries = []

        try:
            full_json = json.loads(raw_output)
            if isinstance(full_json, list):
                parsed_entries = full_json
        except Exception:
            for line in raw_output.splitlines():
                line = line.strip()
                if line:
                    try:
                        parsed_entries.append(json.loads(line))
                    except Exception:
                        continue

        media_list = []
        author = "reddit_user"
        description = ""

        for item in parsed_entries:
            file_url = None
            metadata = {}

            if isinstance(item, list):
                if len(item) >= 3 and isinstance(item[1], str) and item[1].startswith("http"):
                    file_url = item[1]
                    if isinstance(item[2], dict):
                        metadata = item[2]
                elif len(item) >= 2 and isinstance(item[1], dict):
                    metadata = item[1]
                    file_url = metadata.get("url") or metadata.get("display_url") or metadata.get("video_url")
            elif isinstance(item, dict):
                metadata = item
                file_url = metadata.get("url") or metadata.get("display_url") or metadata.get("video_url")

            # Metadados de título e autor
            if not description and metadata:
                description = metadata.get("title") or metadata.get("description") or metadata.get("content") or ""

            if author == "reddit_user" and metadata:
                author = metadata.get("author") or metadata.get("user") or metadata.get("uploader") or "reddit_user"

            if file_url:
                extension = metadata.get("extension") or ""
                is_video = (
                    extension.lower() in ("mp4", "m4v", "webm", "mov")
                    or ".mp4" in file_url.lower()
                    or metadata.get("type") == "video"
                )
                media_type = "video" if is_video else "photo"

                media_list.append(
                    RedditMedia(
                        url=file_url,
                        type=media_type,
                        index=len(media_list),
                    )
                )

        if media_list:
            logging.info(
                "gallery-dl resolved %d Reddit media item(s). Author=%s",
                len(media_list),
                author,
            )
            return RedditVideo(
                id=post_id,
                description=description.strip(),
                author=author,
                media_list=media_list,
            )
    except Exception as exc:
        logging.error("gallery-dl reddit error: %s", exc)
    return None


class RedditMediaService(CobaltMediaService):
    def __init__(
        self,
        output_dir: str,
        *,
        cobalt_api_url: str,
        cobalt_api_key: str,
        fetch_cobalt_data_func: Callable[..., Awaitable[dict | None]],
        retry_async_operation_func: Callable[..., Awaitable[DownloadMetrics | None]],
    ) -> None:
        super().__init__(
            output_dir,
            source="reddit",
            cobalt_api_url=cobalt_api_url,
            cobalt_api_key=cobalt_api_key,
            fetch_cobalt_data_func=fetch_cobalt_data_func,
            retry_async_operation_func=retry_async_operation_func,
            logger=logging,
            download_error_message="Error downloading Reddit media: url=%s error=%s",
        )

    async def fetch_data(self, url: str, audio_only: bool = False) -> Optional[RedditVideo]:
        # 1. Prioridade para gallery-dl com cookies e resolução de redirecionamento
        gdl_res = await _fetch_reddit_with_gallery_dl(url)
        if gdl_res and gdl_res.media_list:
            return gdl_res

        # 2. Fallback para Cobalt API
        logging.info("gallery-dl returned empty for Reddit %s, trying Cobalt fallback", url)
        resolved_url = await _resolve_reddit_url(url)
        payload = {
            "url": resolved_url,
            "videoQuality": "720",
            "downloadMode": "audio" if audio_only else "auto",
        }
        try:
            data = await self._fetch_cobalt_json(payload)
            if data:
                parsed = parse_cobalt_media_response(data, audio_only=audio_only, source="reddit")
                if parsed and parsed.items:
                    media_list = [
                        RedditMedia(url=media_url, type=media_type, thumb=thumb, index=idx)
                        for idx, (media_url, media_type, thumb) in enumerate(parsed.items)
                    ]
                    post_match = re.search(r"comments/([a-zA-Z0-9]+)", resolved_url)
                    post_id = post_match.group(1) if post_match else hashlib.blake2s(resolved_url.encode("utf-8"), digest_size=8).hexdigest()

                    return RedditVideo(
                        id=post_id,
                        description=data.get("filename", ""),
                        author="reddit_user",
                        media_list=media_list,
                    )
        except Exception as exc:
            logging.warning("Cobalt fallback failed for Reddit: %s", exc)

        return None
