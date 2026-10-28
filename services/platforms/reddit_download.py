"""Local Reddit download adapter; independent of Telegram's universal handler."""
from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse
from pathlib import Path

from services.logger import logger as logging
from services.platforms.firefox_cookies import firefox_profile
from services.platforms.reddit_json import (
    RedditImageError, fetch_reddit_post, gallery_sources, single_image_sources,
    native_video_source, download_reddit_gallery,
)
from services.platforms.reddit_native import RedditVideoError, download_native_video
from services.links.reddit import is_reddit_media_url, normalize_reddit_url
from services.platforms.universal_extractor import download_universal_media


async def _notify_kind(callback, kind, count):
    if callback is not None:
        try:
            await callback(kind, count)
        except Exception as exc:
            logging.warning('Reddit status update skipped: %s', type(exc).__name__)


async def download_reddit_media(url: str, *, output_dir: str, max_file_size: int, on_kind=None):
    if not is_reddit_media_url(url):
        return None
    started = time.monotonic()
    info = await fetch_reddit_post(normalize_reddit_url(url), firefox_profile('reddit'))
    logging.info('Reddit JSON: status=%s http=%s seconds=%.2f', info['status'], info.get('http'), time.monotonic() - started)
    if info['status'] == 'ok':
        sources = gallery_sources(info['post']) or single_image_sources(info['post'])
        video = native_video_source(info['post'])
        logging.info('Reddit media kind: images=%s native_video=%s source_host=%s',
                     len(sources or []), bool(video),
                     urlparse(info['post'].get('url') or '').hostname)
        if sources:
            await _notify_kind(on_kind, 'photo', len(sources))
            try:
                result = await download_reddit_gallery(info['post'], sources, output_dir, max_file_size)
            except Exception as exc:
                logging.warning('Reddit gallery failed: %s', exc.code if isinstance(exc, RedditImageError) else type(exc).__name__)
                return None
            logging.info('Reddit gallery: items=%s seconds=%.2f', len(result.media_list), time.monotonic() - started)
        elif video:
            await _notify_kind(on_kind, 'video', 1)
            try:
                result = await download_native_video(info['post'], video, output_dir, max_file_size)
            except Exception as exc:
                logging.warning('Reddit native video failed: %s', exc.code if isinstance(exc, RedditVideoError) else type(exc).__name__)
                return None
            logging.info('Reddit native video: seconds=%.2f audio=%s',
                         time.monotonic() - started, result.media_list[0].has_audio)
        else:
            # External or unsupported post types keep the legacy fallback.
            result = await asyncio.wait_for(download_universal_media(
                info['canonical'], output_dir=output_dir, post_id='reddit_post'
            ), timeout=420)
    elif info['status'] == 'direct':
        await _notify_kind(on_kind, 'video' if urlparse(url).hostname == 'v.redd.it' else 'photo', 1)
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
