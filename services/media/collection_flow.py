"""Ordered, bounded collection execution independent of Telegram and providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CollectionEntry:
    url: str | None
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CollectionListing:
    title: str
    entries: list[CollectionEntry]
    total: int | None = None
    partial: bool = False
    limited: bool = False


@dataclass
class CollectionResult:
    sent: int = 0
    failed: int = 0
    unavailable: int = 0
    cancelled: bool = False
    stopped: bool = False


class CollectionItemLease:
    """The parent owns deduplication; repeated tracks remain valid collection items."""

    def mark_success(self):
        pass

    def finish(self):
        pass


async def run_collection(
    listing, deliver, progress, cancelled, *, stop_on=(), on_error=None
):
    result = CollectionResult()
    for index, entry in enumerate(listing.entries, 1):
        if cancelled():
            result.cancelled = True
            break
        await progress(index, entry)
        if not entry.url:
            result.unavailable += 1
            continue
        try:
            delivered = await deliver(entry)
        except stop_on:
            result.failed += 1
            result.stopped = True
            break
        except Exception as exc:
            result.failed += 1
            if on_error:
                await on_error(index, exc)
            continue
        if delivered:
            result.sent += 1
        else:
            result.failed += 1
    if cancelled():
        result.cancelled = True
    return result


async def cleanup_collection_audio(output_dir, prefix, remove):
    """Remove only this item's unique outputs, including partial yt-dlp files."""
    import re
    from pathlib import Path

    if not prefix or not re.fullmatch(r"[A-Za-z0-9_-]+", prefix):
        return
    for path in Path(output_dir).glob(prefix + ".*"):
        if path.is_file():
            await remove(str(path))
