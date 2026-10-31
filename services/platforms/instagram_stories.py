"""Public Stories through Save Insta, without the Firefox account or cookies."""

import asyncio
import logging
import os
import re
from urllib.parse import urlparse

from services.platforms.instagram_saveinsta import download_saveinsta

logger = logging.getLogger(__name__)


async def fetch_public_instagram_story(url: str, output_dir: str):
    # Import on invocation to reuse the service models without a module cycle.
    from services.platforms.instagram_media import InstagramMedia, InstagramVideo

    match = re.fullmatch(r"/stories/([A-Za-z0-9_.]+)/(\d+)/?", urlparse(url).path)
    if not match or match.group(1).lower() == "highlights":
        return None
    if os.getenv("INSTAGRAM_SAVEINSTA_ENABLED", "1").strip().lower() in {"0", "false", "off"}:
        return None
    try:
        items = await asyncio.to_thread(download_saveinsta, url, output_dir)
        if not items:
            return None
        logger.info("Instagram public Story matched requested ID: items=%s", len(items))
        return InstagramVideo(
            id=match.group(2), description="", author=match.group(1),
            media_list=[InstagramMedia(**item) for item in items],
        )
    except Exception as exc:
        logger.warning("Instagram public Story unavailable: %s", type(exc).__name__)
        return None
