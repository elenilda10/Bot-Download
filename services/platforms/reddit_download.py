"""Local Reddit download adapter; independent of Telegram's universal handler."""
from __future__ import annotations

import asyncio
from pathlib import Path

from services.links.reddit import is_reddit_media_url, normalize_reddit_url
from services.platforms.universal_extractor import download_universal_media


async def download_reddit_media(url: str, *, output_dir: str, max_file_size: int):
    if not is_reddit_media_url(url):
        return None
    # Reuse the established gallery-dl / yt-dlp extraction and Firefox cookie
    # selection. The dedicated Telegram handler owns delivery and caching.
    result = await asyncio.wait_for(download_universal_media(
        normalize_reddit_url(url), output_dir=output_dir, post_id='reddit_post'
    ), timeout=420)
    if not result or not result.media_list:
        return None
    root = Path(output_dir).resolve()
    for item in result.media_list:
        path = Path(item.url).resolve()
        if (not path.is_relative_to(root) or not path.is_file()
                or not 0 < path.stat().st_size < max_file_size
                or item.type not in {'photo', 'video'}):
            raise ValueError('Invalid, external or oversized Reddit download')
    return result
