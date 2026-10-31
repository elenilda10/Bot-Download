"""Shared MP3 delivery pipeline used by the YouTube, SoundCloud and Spotify flows.

``run_audio_flow`` mirrors ``run_single_media_flow``: cached-file lookup,
optional metadata fetch, retryable download (supplied by the caller),
size check, MP3 metadata preparation, upload, cache store, and temp-file
cleanup in ``finally``.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from utils.download_manager import DownloadMetrics


def _default_extract_file_id(sent: Any) -> str | None:
    audio = getattr(sent, "audio", None)
    return audio.file_id if audio else None


@dataclass
class AudioFlowResult:
    file_id: str | None
    sent_message: Any = None
    from_cache: bool = False


async def run_audio_flow(
    *,
    cache_key: str,
    db_service: Any,
    send_cached: Callable[[str], Awaitable[Any]],
    download_audio: Callable[[], Awaitable[DownloadMetrics | None]],
    on_missing_audio: Callable[[], Awaitable[None]],
    max_file_size: int,
    on_too_large: Callable[[], Awaitable[None]],
    prepare_metadata: Callable[[str], Awaitable[Any]],
    send_downloaded: Callable[[str, Any], Awaitable[Any]],
    cleanup_path: Callable[[str], Awaitable[None]],
    fetch_metadata: Callable[[], Awaitable[bool]] | None = None,
    extract_file_id: Callable[[Any], str | None] = _default_extract_file_id,
    cache_file_type: str = "audio",
    on_cache_store_error: Callable[[Exception], Awaitable[None]] | None = None,
    on_after_send: Callable[[AudioFlowResult], Awaitable[None]] | None = None,
    cache_enabled: bool = True,
) -> AudioFlowResult | None:
    """Run the shared audio pipeline and return the delivery result.

    Returns ``None`` when the flow ended early (missing media, oversized
    file, or an aborted ``fetch_metadata``); the corresponding callback has
    already produced user feedback in that case. Backpressure and unexpected
    errors propagate to the caller unchanged. Set cache_enabled=False for private
    licensed sources that must revalidate access and must not share file IDs.
    """
    cached_file_id = await db_service.get_file_id(cache_key) if cache_enabled else None
    if cached_file_id:
        sent = await send_cached(cached_file_id)
        result = AudioFlowResult(
            file_id=cached_file_id, sent_message=sent, from_cache=True
        )
        if on_after_send:
            await on_after_send(result)
        return result

    if fetch_metadata is not None and not await fetch_metadata():
        return None

    metrics = await download_audio()
    if not metrics:
        await on_missing_audio()
        return None

    try:
        if metrics.size >= max_file_size:
            await on_too_large()
            return None

        prepared_metadata = await prepare_metadata(metrics.path)
        try:
            sent = await send_downloaded(metrics.path, prepared_metadata)
            file_id = extract_file_id(sent)
            if file_id and cache_enabled:
                try:
                    await db_service.add_file(cache_key, file_id, cache_file_type)
                except Exception as exc:
                    if on_cache_store_error is None:
                        raise
                    await on_cache_store_error(exc)
            result = AudioFlowResult(file_id=file_id, sent_message=sent)
            if on_after_send:
                await on_after_send(result)
            return result
        finally:
            if prepared_metadata is not None:
                prepared_metadata.cleanup()
    finally:
        await cleanup_path(metrics.path)
