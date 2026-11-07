"""Public Instagram post fallback through parth-dl, with no account login.

Return the same media dictionaries used by the SaveInsta integration.
Do not use parth-dl for Stories or highlights.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from services.platforms.instagram_saveinsta import _probe

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}


def download_parth(url: str, output_dir: str) -> list[dict]:
    """Download a complete post/reel/carousel; discard partial results on errors."""
    parsed = urlparse(url)
    if (parsed.scheme != "https"
            or parsed.hostname not in {"instagram.com", "www.instagram.com"}
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not re.fullmatch(r"/(?:p|reel|reels)/[A-Za-z0-9_-]+/?", parsed.path)):
        return []

    # Lazy import: optional fallback must not stop Instagram's other extractors.
    from parth_dl import download

    os.makedirs(output_dir, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="instagram_parth_", dir=output_dir))
    try:
        paths = download(url, output_path=str(folder))
        if isinstance(paths, (str, os.PathLike)):
            paths = [paths]
        if not isinstance(paths, (list, tuple)) or not paths:
            raise ValueError("parth-dl returned no media files")

        media = []
        for index, raw_path in enumerate(paths):
            if not isinstance(raw_path, (str, os.PathLike)):
                raise ValueError("parth-dl returned a non-file result")
            path = Path(raw_path).resolve()
            if not path.is_relative_to(folder.resolve()) or not path.is_file():
                raise ValueError("parth-dl returned a file outside its output directory")
            if path.stat().st_size <= 0:
                raise ValueError("parth-dl returned an empty media file")

            suffix = path.suffix.lower()
            if suffix in PHOTO_EXTENSIONS:
                kind = "photo"
            elif suffix in VIDEO_EXTENSIONS:
                kind = "video"
            else:
                raise ValueError("parth-dl returned an unsupported media type")

            media.append({
                "url": str(path),
                "type": kind,
                "index": index,
                **_probe(str(path), kind),
            })

        return media
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
