import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from services.logger import logger as logging
from services.platforms import CobaltMediaService, strip_url_query
from utils.cobalt_media import parse_cobalt_media_response
from utils.download_manager import (
    DownloadError as DownloadError,
    DownloadMetrics,
)

logging = logging.bind(service="instagram_media")

strip_instagram_url = strip_url_query


@dataclass
class InstagramMedia:
    url: str
    type: str
    thumb: Optional[str] = None
    index: int = 0


@dataclass
class InstagramVideo:
    id: str
    description: str
    author: str
    media_list: list[InstagramMedia]


def get_instagram_preview_url(media: Optional[InstagramMedia]) -> Optional[str]:
    if not media:
        return None
    if media.type == "photo":
        return media.url
    return media.thumb or None


def _extract_cobalt_description(data: dict, fallback_url: str) -> str:
    metadata = data.get("metadata") or data.get("output", {}).get("metadata") or {}
    if isinstance(metadata, dict):
        fields = ("description", "caption", "title", "text")
        for field_name in fields:
            val = metadata.get(field_name)
            if isinstance(val, str) and val.strip():
                return val.strip()
    if isinstance(data.get("filename"), str) and data["filename"].strip():
        return data["filename"].strip()
    return ""


def _extract_cobalt_author(data: dict, filename: str) -> str:
    metadata = data.get("metadata") or data.get("output", {}).get("metadata") or {}
    if isinstance(metadata, dict):
        fields = ("author", "uploader", "username", "creator", "channel")
        for field_name in fields:
            val = metadata.get(field_name)
            if isinstance(val, str) and val.strip():
                return val.strip()
    if filename:
        parts = filename.replace("\\", "/").split("/")
        for part in parts:
            part = part.strip()
            if part and not part.startswith(".") and "_" in part:
                maybe = part.split("_")[0]
                if maybe and len(maybe) >= 2:
                    return maybe
    return ""


def _extract_instagram_post_id(url: str) -> str:
    match = re.search(r"instagram\.com/(?:p|reels|reel)/([^/?#&]+)", url)
    if match:
        return match.group(1)
    return ""


async def _fetch_instagram_with_gallery_dl(url: str) -> Optional[InstagramVideo]:
    clean_url = strip_instagram_url(url)
    post_id = _extract_instagram_post_id(clean_url) or hashlib.blake2s(
        clean_url.encode("utf-8"), digest_size=8
    ).hexdigest()

    cookie_file = "/root/bot_teste/cookies/instagram_cookies.txt"
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
                "gallery-dl returned code %s: %s",
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
                if not line:
                    continue
                try:
                    parsed_entries.append(json.loads(line))
                except Exception:
                    continue

        media_list = []
        author = "instagram_user"
        description = ""

        for item in parsed_entries:
            file_url = None
            metadata = {}

            if isinstance(item, list):
                # Se for [3, "https://url...", {...}]
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

            # Extração da legenda global do post
            if not description and metadata:
                for cap_key in ("description", "caption", "text", "title"):
                    val = metadata.get(cap_key)
                    if isinstance(val, str) and val.strip():
                        description = val.strip()
                        break

            # Extração do autor
            if author == "instagram_user" and metadata:
                extracted_author = (
                    metadata.get("fullname")
                    or metadata.get("username")
                    or metadata.get("owner_username")
                )
                if extracted_author:
                    author = str(extracted_author)

            # Adiciona item de mídia
            if file_url:
                extension = metadata.get("extension") or ""
                post_type = metadata.get("type") or metadata.get("post_type") or ""
                is_video = (
                    extension.lower() in ("mp4", "m4v", "mov")
                    or ".mp4" in file_url.lower()
                    or post_type == "video"
                )
                media_type = "video" if is_video else "photo"

                media_list.append(
                    InstagramMedia(
                        url=file_url,
                        type=media_type,
                        index=len(media_list),
                    )
                )

        if media_list:
            logging.info(
                "gallery-dl resolved %d item(s). Author=%s, Description length=%d",
                len(media_list),
                author,
                len(description),
            )
            return InstagramVideo(
                id=post_id,
                description=description,
                author=author,
                media_list=media_list,
            )
    except Exception as exc:
        logging.error("gallery-dl extraction error: %s", exc)
    return None


class InstagramMediaService(CobaltMediaService):
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
            source="instagram",
            cobalt_api_url=cobalt_api_url,
            cobalt_api_key=cobalt_api_key,
            fetch_cobalt_data_func=fetch_cobalt_data_func,
            retry_async_operation_func=retry_async_operation_func,
            logger=logging,
            download_error_message="Error downloading Instagram media: url=%s error=%s",
        )

    async def fetch_data(self, url: str, audio_only: bool = False) -> Optional[InstagramVideo]:
        # 1. Prioridade absoluta para gallery-dl com cookies
        gdl_res = await _fetch_instagram_with_gallery_dl(url)
        if gdl_res and gdl_res.media_list:
            return gdl_res

        # 2. Fallback para Cobalt
        logging.info("gallery-dl returned empty for %s, trying Cobalt fallback", url)
        payload = {
            "url": url,
            "videoQuality": "720",
            "downloadMode": "audio" if audio_only else "auto",
        }
        try:
            data = await self._fetch_cobalt_json(payload)
            if data:
                parsed = parse_cobalt_media_response(data, audio_only=audio_only, source="instagram")
                if parsed and parsed.items:
                    media_list = [
                        InstagramMedia(url=media_url, type=media_type, thumb=thumb, index=idx)
                        for idx, (media_url, media_type, thumb) in enumerate(parsed.items)
                    ]

                    description = _extract_cobalt_description(data, fallback_url=url)
                    author = _extract_cobalt_author(data, data.get("filename", ""))

                    post_id = _extract_instagram_post_id(url) or hashlib.blake2s(
                        url.encode("utf-8"), digest_size=8
                    ).hexdigest()
                    return InstagramVideo(
                        id=post_id,
                        description=description,
                        author=author or "instagram_user",
                        media_list=media_list,
                    )
        except Exception as exc:
            logging.warning("Cobalt fallback failed: %s", exc)

        return None
