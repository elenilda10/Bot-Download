import asyncio
import hashlib
import json
import os
import re
from urllib.parse import urlsplit
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from services.logger import logger as logging
from services.platforms import CobaltMediaService, strip_url_query
from utils.cobalt_media import parse_cobalt_media_response
from utils.download_manager import (
    DownloadError as DownloadError,  # noqa: F401  (re-exported for handlers)
    DownloadMetrics,
)

logging = logging.bind(service="pinterest_media")

strip_pinterest_url = strip_url_query


def _derive_description(data: dict) -> str:
    output = data.get("output")
    if isinstance(output, dict):
        metadata = output.get("metadata")
        if isinstance(metadata, dict):
            title = metadata.get("title")
            if isinstance(title, str) and title.strip():
                return title.strip()
    description = data.get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()
    return ""



_GALLERY_TIMEOUT_SECONDS = 25
_GALLERY_MAX_ITEMS = 20
_GALLERY_MAX_OUTPUT_BYTES = 2 * 1024 * 1024
_PUBLIC_PIN_HOSTS = {
    "pinterest.com", "pinterest.com.br", "pinterest.co.uk", "pinterest.ca",
    "pinterest.de", "pinterest.fr", "pinterest.es", "pinterest.it",
    "pinterest.jp",
}
_PHOTO_EXTENSIONS = {"jpg", "jpeg", "png"}
_VIDEO_EXTENSIONS = {"mp4", "m4v", "mov"}


def _public_pin_url(url: str) -> bool:
    """Limit anonymous gallery fallback to individual Pinterest Pins."""
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or parsed.username or parsed.password:
            return False
        if parsed.port not in (None, 443):
            return False
    except ValueError:
        return False
    if host in {"pin.it", "www.pin.it"}:
        return bool(re.fullmatch(r"/[A-Za-z0-9_-]{3,80}/?", parsed.path))
    if not any(host == domain or host.endswith("." + domain) for domain in _PUBLIC_PIN_HOSTS):
        return False
    return bool(re.fullmatch(r"/pin/(?:[A-Za-z0-9_-]+--)?[0-9]+/?", parsed.path))


def _pinterest_cdn_url(url: str) -> bool:
    """Do not pass arbitrary extractor-supplied URLs to the file downloader."""
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        return (
            parsed.scheme == "https"
            and (host == "pinimg.com" or host.endswith(".pinimg.com"))
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
        )
    except ValueError:
        return False


def _parse_gallery_record(record: object) -> tuple[str, dict] | None:
    """gallery-dl -J emits [2, media_url, metadata] file records."""
    if (
        not isinstance(record, list) or len(record) < 3
        or record[0] != 2 or not isinstance(record[1], str)
        or not isinstance(record[2], dict)
    ):
        return None
    return record[1], record[2]


def parse_pinterest_gallery_dump(raw_output: str, source_url: str) -> Optional["PinterestPost"]:
    """Parse anonymous gallery-dl metadata, never returning external-site URLs."""
    records: list[object] = []
    try:
        value = json.loads(raw_output)
        # A single media record is itself a JSON list: [2, url, metadata].
        # Treat it as one entry, not as three independent records.
        records = (
            [value] if _parse_gallery_record(value) is not None
            else value if isinstance(value, list)
            else [value]
        )
    except (ValueError, TypeError):
        for line in raw_output.splitlines():
            try:
                records.append(json.loads(line))
            except ValueError:
                continue

    if len(records) == 1 and isinstance(records[0], list) and records[0] and isinstance(records[0][0], list):
        records = records[0]

    media: list[PinterestMedia] = []
    seen: set[str] = set()
    description = ""
    pin_id = re.search(r"/pin/(\d+)", source_url)

    for record in records:
        entry = _parse_gallery_record(record)
        if entry is None:
            continue
        media_url, metadata = entry
        if not _pinterest_cdn_url(media_url) or media_url in seen:
            continue
        ext = str(metadata.get("extension") or "").lower().lstrip(".")
        if ext not in _PHOTO_EXTENSIONS | _VIDEO_EXTENSIONS:
            ext = urlsplit(media_url).path.rsplit(".", 1)[-1].lower()
        if ext in _PHOTO_EXTENSIONS:
            kind = "photo"
        elif ext in _VIDEO_EXTENSIONS:
            kind = "video"
        else:
            # HLS manifests, animated GIFs and unsupported types require a
            # different Telegram delivery path and must not be sent as photos.
            continue
        seen.add(media_url)
        media.append(PinterestMedia(url=media_url, type=kind))
        if not description:
            for key in ("title", "description", "seo_description"):
                value = metadata.get(key)
                if isinstance(value, str) and value.strip():
                    description = value.strip()[:1000]
                    break
        if len(media) >= _GALLERY_MAX_ITEMS:
            break

    if not media:
        return None
    identifier = (
        pin_id.group(1) if pin_id else
        hashlib.blake2s(source_url.encode("utf-8"), digest_size=8).hexdigest()
    )
    return PinterestPost(id=identifier, description=description, media_list=media)


async def _fetch_public_pin_gallery(url: str) -> Optional["PinterestPost"]:
    if not _public_pin_url(url):
        return None
    executable = os.getenv("PINTEREST_GALLERY_DL_BIN", "").strip() or "gallery-dl"
    try:
        proc = await asyncio.create_subprocess_exec(
            executable,
            "--config-ignore", "--no-input", "--no-colors",
            "--http-timeout", "10", "--retries", "1",
            "--range", f"1-{_GALLERY_MAX_ITEMS}",
            "-J", url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, ValueError):
        logging.info("Pinterest anonymous gallery-dl fallback not available")
        return None
    try:
        try:
            stdout, _stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_GALLERY_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            logging.warning("Pinterest gallery-dl timed out")
            return None
    except asyncio.CancelledError:
        if proc.returncode is None:
            proc.kill()
            await proc.communicate()
        raise

    if len(stdout) > _GALLERY_MAX_OUTPUT_BYTES:
        logging.warning("Pinterest gallery-dl metadata exceeds size limit")
        return None
    if proc.returncode != 0 and not stdout:
        logging.info("Pinterest gallery-dl did not resolve public pin, status=%s", proc.returncode)
        return None
    try:
        result = parse_pinterest_gallery_dump(stdout.decode("utf-8"), url)
    except (UnicodeDecodeError, ValueError):
        return None
    if result:
        logging.info("Pinterest gallery-dl fallback resolved %s media item(s)", len(result.media_list))
    return result


@dataclass
class PinterestMedia:
    url: str
    type: str
    thumb: Optional[str] = None


@dataclass
class PinterestPost:
    id: str
    description: str
    media_list: list[PinterestMedia]


def get_pinterest_preview_url(media: Optional[PinterestMedia]) -> Optional[str]:
    if not media:
        return None
    if media.type == "photo":
        return media.thumb or media.url
    return media.thumb or None


def parse_pinterest_post(data: dict) -> Optional[PinterestPost]:
    parsed = parse_cobalt_media_response(data, allow_multi_tunnel=True, source="pinterest")
    if not parsed:
        return None

    media_list = [
        PinterestMedia(url=media_url, type=media_type, thumb=thumb)
        for media_url, media_type, thumb in parsed.items
    ]

    return PinterestPost(
        id=hashlib.blake2s(
            json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"),
            digest_size=8,
        ).hexdigest(),
        description=_derive_description(data),
        media_list=media_list,
    )


class PinterestMediaService(CobaltMediaService):
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
            source="pinterest",
            cobalt_api_url=cobalt_api_url,
            cobalt_api_key=cobalt_api_key,
            fetch_cobalt_data_func=fetch_cobalt_data_func,
            retry_async_operation_func=retry_async_operation_func,
            logger=logging,
            download_error_message="Error downloading Pinterest media: url=%s error=%s",
        )

    async def fetch_post(self, url: str) -> Optional[PinterestPost]:
        payload = {
            "url": url,
            "downloadMode": "auto",
            "videoQuality": "1080",
            "alwaysProxy": True,
            "localProcessing": "disabled",
        }
        try:
            data = await self._fetch_cobalt_json(payload)
            post = parse_pinterest_post(data) if data else None
        except Exception as exc:
            logging.warning("Pinterest Cobalt extraction failed, trying anonymous fallback: %s", type(exc).__name__)
            post = None
        if post and post.media_list:
            return post
        return await _fetch_public_pin_gallery(url)
