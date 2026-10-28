"""Local Reddit download adapter; independent of Telegram's universal handler."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from services.logger import logger as logging
from services.platforms.firefox_cookies import firefox_profile
from services.platforms.reddit_json import RedditImageError, fetch_reddit_post, gallery_sources, download_reddit_gallery
from services.links.reddit import is_reddit_media_url, normalize_reddit_url
from services.platforms.universal_extractor import download_universal_media


async def download_reddit_media(url: str, *, output_dir: str, max_file_size: int):
    if not is_reddit_media_url(url):
        return None
    started = time.monotonic()
    info = await fetch_reddit_post(normalize_reddit_url(url), firefox_profile('reddit'))
    logging.info('Reddit JSON: status=%s http=%s seconds=%.2f', info['status'], info.get('http'), time.monotonic() - started)
    if info['status'] == 'ok':
        sources = gallery_sources(info['post'])
        if sources:
            try:
                result = await download_reddit_gallery(info['post'], sources, output_dir, max_file_size)
            except Exception as exc:
                logging.warning('Reddit gallery failed: %s', exc.code if isinstance(exc, RedditImageError) else type(exc).__name__)
                return None
            logging.info('Reddit gallery: items=%s seconds=%.2f', len(result.media_list), time.monotonic() - started)
        else:
            # Keep the established merged-video path for non-static galleries.
            result = await asyncio.wait_for(download_universal_media(
                info['canonical'], output_dir=output_dir, post_id='reddit_post'
            ), timeout=420)
    elif info['status'] == 'direct':
        result = await asyncio.wait_for(download_universal_media(
            normalize_reddit_url(url), output_dir=output_dir, post_id='reddit_post'
        ), timeout=420)
    else:
        # Do not turn a known JSON rejection into several 90-second retries.
        return None
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
