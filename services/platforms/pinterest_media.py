import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from services.logger import logger as logging
from services.platforms import CobaltMediaService, strip_url_query
from services.download.queue import get_download_queue, QueueRateLimitError, QueueBackpressureError
from services.runtime.stats import record_download
from config import MAX_FILE_SIZE
from utils.cobalt_media import parse_cobalt_media_response
from utils.download_manager import (
    DownloadError as DownloadError,  # noqa: F401  (re-exported for handlers)
    DownloadMetrics,
    DownloadRateLimitError,
    DownloadQueueBusyError,
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
_YTDLP_METADATA_TIMEOUT_SECONDS = 45
_YTDLP_VIDEO_TIMEOUT_SECONDS = 420
_YTDLP_MAX_METADATA_BYTES = 2 * 1024 * 1024


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
    """Read gallery-dl URL records: [3, media_url, metadata].

    For compatibility with previously stored/test fixtures, accept the old
    shape [2, media_url, metadata] *only* when field 2 is an URL string.
    Real Directory records [2, metadata_dict] cannot be mistaken for media.
    """
    if (
        not isinstance(record, list) or len(record) < 3
        or record[0] not in (2, 3)
        or not isinstance(record[1], str)
        or not isinstance(record[2], dict)
    ):
        return None
    return record[1], record[2]


def _pinterest_caption_from_metadata(metadata: object) -> str:
    """Use public Pin text when present; never invent a missing description."""
    if not isinstance(metadata, dict):
        return ""
    for key in (
        "description", "closeup_unified_description",
        "closeup_description", "title", "seo_title", "seo_description",
        "alt_text",
    ):
        value = metadata.get(key)
        if isinstance(value, dict):
            value = value.get("text")
        if isinstance(value, str) and value.strip():
            return value.strip()[:1000]

    # Story/album Pins may contain the author's text as paragraph blocks
    # rather than in the main description field.
    story = metadata.get("story_pin_data")
    pages = story.get("pages") if isinstance(story, dict) else None
    if isinstance(pages, list):
        paragraphs: list[str] = []
        for page in pages[:_GALLERY_MAX_ITEMS]:
            blocks = page.get("blocks") if isinstance(page, dict) else None
            if not isinstance(blocks, list):
                continue
            for block in blocks[:20]:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "story_pin_paragraph_block"
                ):
                    value = block.get("text")
                    if isinstance(value, str) and value.strip():
                        paragraphs.append(value.strip())
            if len(paragraphs) >= 8:
                break
        if paragraphs:
            return "\n".join(paragraphs)[:1000]
    return ""


def _canonical_pin_from_gallery_output(raw_output: str) -> Optional[str]:
    """Turn a gallery-dl pin.it redirect into a trusted, tracking-free Pin URL.

    Short-link -J often yields only [6, pinterest_pin_url, {}]. The redirect
    is NOT media metadata; querying its canonical Pin extracts the caption.
    """
    try:
        value = json.loads(raw_output)
    except (ValueError, TypeError):
        # In -G mode, output consists of one URL per line.
        records = [line.strip() for line in raw_output.splitlines()]
    else:
        if isinstance(value, list) and value and value[0] == 6:
            records = [value]
        elif isinstance(value, list):
            records = value
        else:
            records = []

    for record in records[:_GALLERY_MAX_ITEMS + 5]:
        target = (
            record[1]
            if isinstance(record, list) and len(record) > 1 and record[0] == 6
            else record if isinstance(record, str) else None
        )
        if not isinstance(target, str):
            continue
        try:
            parsed = urlsplit(target)
            host = (parsed.hostname or "").lower()
            if (
                parsed.scheme != "https"
                or parsed.username or parsed.password
                or parsed.port not in (None, 443)
                or not any(
                    host == domain or host.endswith("." + domain)
                    for domain in _PUBLIC_PIN_HOSTS
                )
            ):
                continue
        except ValueError:
            continue
        match = re.match(r"^/pin/(\d{5,30})(?:/|$)", parsed.path)
        if match:
            return f"https://www.pinterest.com/pin/{match.group(1)}/"
    return None


def _gallery_directory_metadata(record: object) -> dict | None:
    """Read gallery-dl Directory records: [2, metadata_dict].

    Older fixtures used a legacy [1, metadata] or [1, "", metadata] layout;
    these are recognized separately without conflating directory and URL.
    """
    if not isinstance(record, list) or not record:
        return None
    if record[0] not in (1, 2):
        return None
    if len(record) >= 2 and isinstance(record[1], dict):
        return record[1]
    if record[0] == 1 and len(record) >= 3 and isinstance(record[2], dict):
        return record[2]
    return None


def _gallery_dump_records(raw_output: str) -> list[object]:
    try:
        decoded = json.loads(raw_output)
    except (ValueError, TypeError):
        records = []
        for line in raw_output.splitlines():
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    else:
        if (
            isinstance(decoded, list) and decoded
            and isinstance(decoded[0], int) and decoded[0] in (1, 2, 3, 6)
        ):
            records = [decoded]
        elif isinstance(decoded, list):
            records = decoded
        else:
            records = [decoded]

    if (
        len(records) == 1 and isinstance(records[0], list)
        and records[0] and isinstance(records[0][0], list)
    ):
        records = records[0]
    return records


def _gallery_caption_hint(raw_output: str) -> str:
    """Find real Pin text even if JSON contains no downloadable file entry."""
    for record in _gallery_dump_records(raw_output):
        directory = _gallery_directory_metadata(record)
        if directory is not None:
            caption = _pinterest_caption_from_metadata(directory)
            if caption:
                return caption
        entry = _parse_gallery_record(record)
        if entry:
            url, metadata = entry
            if url.startswith("text:") and metadata.get("extension") == "txt":
                return url[5:].strip()[:1000]
            caption = _pinterest_caption_from_metadata(metadata)
            if caption:
                return caption
    return ""


def parse_pinterest_gallery_dump(raw_output: str, source_url: str) -> Optional["PinterestPost"]:
    """Parse anonymous gallery-dl metadata, never returning external-site URLs."""
    records = _gallery_dump_records(raw_output)

    media: list[PinterestMedia] = []
    seen: set[str] = set()
    description = ""
    pin_id = re.search(r"/pin/(\d+)", source_url)
    metadata_pin_id = ""

    for record in records:
        directory = _gallery_directory_metadata(record)
        if directory is not None:
            # Actual gallery-dl -J uses [2, metadata], including title.
            description = description or _pinterest_caption_from_metadata(directory)
            if not metadata_pin_id:
                candidate_id = str(directory.get("id") or "")
                if re.fullmatch(r"\d{5,30}", candidate_id):
                    metadata_pin_id = candidate_id
            continue

        entry = _parse_gallery_record(record)
        if entry is None:
            continue
        media_url, metadata = entry
        # gallery-dl represents some Story Pin paragraphs as text: URLs.
        # They are not downloadable media, but they can contain the album
        # caption when Pinterest's main description is empty.
        if media_url.startswith("text:"):
            if not description and metadata.get("extension") == "txt":
                description = media_url[5:].strip()[:1000]
            continue
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
            description = _pinterest_caption_from_metadata(metadata)
        if len(media) >= _GALLERY_MAX_ITEMS:
            break

    if not media:
        return None
    identifier = (
        pin_id.group(1) if pin_id else
        metadata_pin_id or
        hashlib.blake2s(source_url.encode("utf-8"), digest_size=8).hexdigest()
    )
    return PinterestPost(id=identifier, description=description, media_list=media)


def parse_pinterest_gallery_urls(raw_output: str, source_url: str) -> Optional["PinterestPost"]:
    """Parse gallery-dl -G output when -J only returned short-link redirects."""
    records: list[list] = []
    for line in raw_output.splitlines():
        media_url = line.strip()
        if _pinterest_cdn_url(media_url):
            records.append([3, media_url, {}])
        if len(records) >= _GALLERY_MAX_ITEMS:
            break
    return parse_pinterest_gallery_dump(json.dumps(records), source_url)


async def _run_public_pin_gallery(
    executable: str, url: str, output_mode: str
) -> tuple[int, bytes] | None:
    """Bounded anonymous gallery-dl run, with support for older CLI options."""
    async def _invoke(config_switch: str) -> tuple[int, bytes, bytes] | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                executable,
                config_switch, "--no-input", "--no-colors",
                "--http-timeout", "10", "--retries", "1",
                "--range", f"1-{_GALLERY_MAX_ITEMS}",
                output_mode, url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, ValueError):
            logging.warning("Pinterest gallery-dl executable could not be started")
            return None
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_GALLERY_TIMEOUT_SECONDS
            )
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.kill()
                await proc.communicate()
            raise
        except asyncio.TimeoutError:
            if proc.returncode is None:
                proc.kill()
                await proc.communicate()
            logging.warning("Pinterest gallery-dl %s timed out", output_mode)
            return None
        if len(stdout) > _GALLERY_MAX_OUTPUT_BYTES:
            logging.warning("Pinterest gallery-dl %s output exceeded limit", output_mode)
            return None
        return proc.returncode, stdout, stderr

    result = await _invoke("--config-ignore")
    if result and result[0] != 0:
        stderr = result[2].lower()
        # gallery-dl renamed --ignore-config to --config-ignore.
        if b"unrecognized arguments" in stderr or b"no such option" in stderr:
            result = await _invoke("--ignore-config")

    if result is None:
        return None
    status, stdout, _stderr = result
    if status != 0 and not stdout:
        logging.info("Pinterest gallery-dl %s returned status=%s", output_mode, status)
    return status, stdout


async def _fetch_public_pin_gallery(url: str) -> Optional["PinterestPost"]:
    if not _public_pin_url(url):
        return None

    executable = os.getenv("PINTEREST_GALLERY_DL_BIN", "").strip()
    if not executable:
        # systemd can have a PATH different from the bot's virtualenv.
        sibling = Path(sys.executable).with_name("gallery-dl")
        executable = str(sibling) if sibling.is_file() else "gallery-dl"

    dumped = await _run_public_pin_gallery(executable, url, "-J")
    result = None
    canonical_attempted = None
    caption_hint = ""
    if dumped and dumped[1]:
        try:
            raw = dumped[1].decode("utf-8")
            caption_hint = _gallery_caption_hint(raw)
            result = parse_pinterest_gallery_dump(raw, url)
            if result is None:
                # A short-link -J response can be just a redirect record.
                # Follow it once using a vetted canonical Pin URL so the
                # image/album description is available in -J metadata.
                canonical = _canonical_pin_from_gallery_output(raw)
                if canonical and canonical != url:
                    canonical_attempted = canonical
                    detailed = await _run_public_pin_gallery(
                        executable, canonical, "-J"
                    )
                    if detailed and detailed[1]:
                        detailed_raw = detailed[1].decode("utf-8")
                        caption_hint = _gallery_caption_hint(detailed_raw) or caption_hint
                        result = parse_pinterest_gallery_dump(
                            detailed_raw, canonical
                        )
        except (UnicodeDecodeError, ValueError):
            pass

    if result is None:
        # Preserve the proven -G fallback even when Pinterest refuses detailed
        # metadata: missing captions must never block the media download.
        logging.info("Pinterest JSON metadata had no downloadable media; resolving URLs")
        resolved = await _run_public_pin_gallery(executable, url, "-G")
        if resolved and resolved[1]:
            try:
                resolved_text = resolved[1].decode("utf-8")
                result = parse_pinterest_gallery_urls(resolved_text, url)
                if result and caption_hint:
                    result.description = caption_hint
                # -G has media URLs but no captions. If it exposes a trusted
                # canonical URL we have not queried, try -J once for metadata.
                canonical = _canonical_pin_from_gallery_output(resolved_text)
                if result and canonical and canonical != canonical_attempted:
                    detailed = await _run_public_pin_gallery(
                        executable, canonical, "-J"
                    )
                    if detailed and detailed[1]:
                        enriched = parse_pinterest_gallery_dump(
                            detailed[1].decode("utf-8"), canonical
                        )
                        if enriched and enriched.description:
                            result.description = enriched.description
                        elif result:
                            result.description = (
                                _gallery_caption_hint(detailed[1].decode("utf-8"))
                                or result.description
                            )
            except (UnicodeDecodeError, ValueError):
                pass

    if result:
        if not result.description and caption_hint:
            result.description = caption_hint
        logging.info(
            "Pinterest gallery-dl resolved %d item(s) as direct media (caption=%s)",
            len(result.media_list),
            "available" if result.description else "unavailable",
        )
    else:
        logging.warning("Pinterest gallery-dl did not yield supported direct media")
    return result



def _yt_dlp_executable() -> str:
    selected = os.getenv("PINTEREST_YTDLP_BIN", "").strip()
    if selected:
        return selected
    sibling = Path(sys.executable).with_name("yt-dlp")
    return str(sibling) if sibling.is_file() else "yt-dlp"


async def _run_yt_dlp(args: list[str], *, timeout: int) -> tuple[int, bytes] | None:
    """Run an anonymous, bounded yt-dlp subprocess, cleaning up on cancellation."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        logging.info("Pinterest yt-dlp executable is unavailable")
        return None
    try:
        stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.CancelledError:
        if proc.returncode is None:
            proc.kill()
            await proc.communicate()
        raise
    except asyncio.TimeoutError:
        if proc.returncode is None:
            proc.kill()
            await proc.communicate()
        logging.warning("Pinterest yt-dlp reached its time limit")
        return None
    return proc.returncode, stdout


def _parse_pinterest_video_metadata(raw: bytes, source_url: str) -> Optional["PinterestPost"]:
    if len(raw) > _YTDLP_MAX_METADATA_BYTES:
        return None
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("extractor_key") not in {"Pinterest", "PinterestIE"}:
        return None
    if payload.get("_type") in {"playlist", "multi_video"}:
        return None
    formats = payload.get("formats")
    if not isinstance(formats, list) or not any(
        isinstance(item, dict)
        and item.get("vcodec") not in (None, "none")
        and isinstance(item.get("url"), str)
        for item in formats
    ):
        return None

    pin_id = str(payload.get("id") or "").strip()
    if not re.fullmatch(r"\d{5,30}", pin_id):
        pin_match = re.search(r"/pin/(\d+)", source_url)
        pin_id = pin_match.group(1) if pin_match else hashlib.blake2s(
            source_url.encode("utf-8"), digest_size=8
        ).hexdigest()

    title = payload.get("description") or payload.get("title") or ""
    description = title.strip()[:1000] if isinstance(title, str) else ""
    return PinterestPost(
        id=pin_id, description=description,
        media_list=[PinterestMedia(url=source_url, type="video")],
    )


async def _fetch_public_pin_yt_dlp(url: str) -> Optional["PinterestPost"]:
    """Probe for an actual Pinterest video before falling back to an image."""
    if not _public_pin_url(url):
        return None
    cmd = [
        _yt_dlp_executable(),
        "--ignore-config", "--no-playlist",
        "--dump-single-json", "--skip-download", "--no-warnings", "--no-progress",
        "--socket-timeout", "10", "--retries", "1", "--extractor-retries", "1",
        url,
    ]
    result = await _run_yt_dlp(cmd, timeout=_YTDLP_METADATA_TIMEOUT_SECONDS)
    if result is None:
        return None
    status, raw = result
    if status:
        return None
    video = _parse_pinterest_video_metadata(raw, url)
    if video:
        logging.info("Pinterest yt-dlp located downloadable video: pin=%s", video.id)
    return video


async def _download_pinterest_yt_dlp(
    source_url: str, filename: str, output_dir: str
) -> Optional[DownloadMetrics]:
    """Download HLS segments and merge audio/video to MP4 in an isolated folder."""
    if not _public_pin_url(source_url) or not re.fullmatch(r"[A-Za-z0-9_.-]+\.mp4", filename):
        return None
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="pinterest-ytdlp-", dir=root) as td:
        candidate = Path(td) / "media.mp4"
        cmd = [
            _yt_dlp_executable(),
            "--ignore-config", "--no-playlist",
            "--no-warnings", "--no-progress",
            "--socket-timeout", "15", "--retries", "2",
            "--fragment-retries", "2",
            "--max-filesize", str(MAX_FILE_SIZE),
            "--merge-output-format", "mp4",
            "--remux-video", "mp4",
            "--format", "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
            "-o", str(Path(td) / "media.%(ext)s"),
            source_url,
        ]
        result = await _run_yt_dlp(cmd, timeout=_YTDLP_VIDEO_TIMEOUT_SECONDS)
        if result is None or result[0] != 0:
            logging.warning("Pinterest yt-dlp could not download/merge video")
            return None
        if not candidate.is_file() or candidate.is_symlink():
            logging.warning("Pinterest yt-dlp finished without a merged MP4 file")
            return None
        size = candidate.stat().st_size
        if size <= 0 or size >= MAX_FILE_SIZE:
            logging.warning("Pinterest yt-dlp video exceeds Telegram size allowance")
            return None
        target = root / filename
        os.replace(candidate, target)
        return DownloadMetrics(
            url=source_url,
            path=str(target),
            size=size,
            elapsed=time.perf_counter() - started,
            used_multipart=False,
            resumed=False,
        )


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
        # Public Pinterest photos and HLS videos already work through local
        # extractors on the VPS. Prefer those over the Cobalt instance, which
        # may reject Pinterest payloads with error.api.invalid_body.
        if _public_pin_url(url):
            video = await _fetch_public_pin_yt_dlp(url)
            if video and video.media_list:
                return video

            photos = await _fetch_public_pin_gallery(url)
            if photos and photos.media_list:
                return photos

        # Keep Cobalt as a last-resort fallback for unsupported link forms
        # and when public extractors return no usable direct media.
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
            logging.warning(
                "Pinterest Cobalt fallback failed: %s", type(exc).__name__
            )
            return None
        if not post or not post.media_list:
            return None
        # A manifest is not an MP4 and must not be uploaded as a normal video.
        if any(
            item.type == "video"
            and urlsplit(item.url).path.lower().endswith((".m3u8", ".mpd"))
            for item in post.media_list
        ):
            return None
        return post

    async def download_media(
        self,
        url: str,
        filename: str,
        *,
        user_id: Optional[int] = None,
        chat_id: Optional[int] = None,
        request_id: Optional[str] = None,
        size_hint: Optional[int] = None,
        on_queued=None,
        on_progress=None,
        on_retry=None,
    ) -> Optional[DownloadMetrics]:
        if not _public_pin_url(url):
            return await super().download_media(
                url, filename,
                user_id=user_id, chat_id=chat_id, request_id=request_id,
                size_hint=size_hint, on_queued=on_queued,
                on_progress=on_progress, on_retry=on_retry,
            )
        # Use the same global download queue/rate limits as direct downloads.
        # Avoid spawning unrestricted ffmpeg processes from simultaneous users.
        try:
            metrics = await get_download_queue().submit(
                lambda: _download_pinterest_yt_dlp(
                    url, filename, self._downloader.output_dir
                ),
                priority=2,
                source="pinterest_ytdlp",
                user_id=user_id,
                chat_id=chat_id,
                request_id=request_id,
                on_queued=on_queued,
            )
        except QueueRateLimitError as exc:
            raise DownloadRateLimitError(exc.retry_after) from exc
        except QueueBackpressureError as exc:
            raise DownloadQueueBusyError(exc.position) from exc
        if metrics is not None:
            record_download("pinterest", metrics)
        return metrics
