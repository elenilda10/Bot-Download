from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse, urlunparse

from utils.download_manager import (
    DownloadConfig,
    DownloadError,
    DownloadMetrics,
    DownloadQueueBusyError,
    DownloadRateLimitError,
    ResilientDownloader,
)


def strip_url_query(url: str) -> str:
    """Drop the query string and fragment from a URL, keeping the path."""
    try:
        parsed = urlparse(url)
    except Exception:
        return url
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, "", ""))


class BasePlatformMediaService(ABC):
    """Abstract base for platform-specific media services (Instagram, TikTok, YouTube, etc.)."""

    @abstractmethod
    async def fetch_data(self, url: str) -> Optional[Any]:
        """Fetch metadata / media info for the given URL."""

    @abstractmethod
    async def download_media(
        self,
        url: str,
        filename: str,
        *,
        user_id: Optional[int] = None,
        chat_id: Optional[int] = None,
        request_id: Optional[str] = None,
        size_hint: Optional[int] = None,
        on_queued: Any = None,
        on_progress: Any = None,
        on_retry: Any = None,
    ) -> Optional[DownloadMetrics]:
        """Download media from the resolved URL to OUTPUT_DIR/filename."""


class CobaltMediaService:
    """Concrete shared base for Cobalt-backed platform media services.

    Owns the common downloader configuration, the Cobalt request helper, and
    the retryable download_media wrapper shared by the Instagram, Pinterest,
    SoundCloud and Threads services.
    """

    _COBALT_TIMEOUT_SECONDS = 15
    _COBALT_ATTEMPTS = 3

    def __init__(
        self,
        output_dir: str,
        *,
        source: str,
        retry_async_operation_func: Callable[..., Awaitable[DownloadMetrics | None]],
        logger: Any,
        download_error_message: str,
        cobalt_api_url: Optional[str] = None,
        cobalt_api_key: Optional[str] = None,
        fetch_cobalt_data_func: Optional[Callable[..., Awaitable[dict | None]]] = None,
        download_headers: Optional[dict[str, str]] = None,
    ) -> None:
        self._source = source
        self._downloader = ResilientDownloader(
            output_dir,
            config=DownloadConfig(
                chunk_size=1024 * 1024,
                multipart_threshold=16 * 1024 * 1024,
                max_workers=6,
                retry_backoff=0.8,
            ),
            source=source,
        )
        self._cobalt_api_url = cobalt_api_url
        self._cobalt_api_key = cobalt_api_key
        self._fetch_cobalt_data = fetch_cobalt_data_func
        self._retry_async_operation = retry_async_operation_func
        self._download_headers = download_headers
        self._logger = logger
        self._download_error_message = download_error_message

    async def _fetch_cobalt_json(self, payload: dict) -> Optional[dict]:
        return await self._fetch_cobalt_data(
            self._cobalt_api_url,
            self._cobalt_api_key,
            payload,
            source=self._source,
            timeout=self._COBALT_TIMEOUT_SECONDS,
            attempts=self._COBALT_ATTEMPTS,
        )

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
        async def _download_once() -> DownloadMetrics:
            return await self._downloader.download(
                url,
                filename,
                headers=self._download_headers,
                user_id=user_id,
                chat_id=chat_id,
                request_id=request_id,
                size_hint=size_hint,
                on_queued=on_queued,
                on_progress=on_progress,
            )

        try:
            return await self._retry_async_operation(
                _download_once,
                attempts=3,
                retry_on_exception=lambda exc: not isinstance(exc, (DownloadRateLimitError, DownloadQueueBusyError)),
                on_retry=on_retry,
            )
        except (DownloadRateLimitError, DownloadQueueBusyError):
            raise
        except DownloadError as exc:
            self._logger.error(self._download_error_message, url, exc)
            return None
