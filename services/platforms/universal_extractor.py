import asyncio
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from typing import List, Optional

from config import MAX_FILE_SIZE, OUTPUT_DIR
from services.logger import logger as logging
from services.settings import resolve_video_quality_format

logging = logging.bind(service="universal_extractor")


@dataclass
class UniversalMediaResult:
    media_type: str  # "video", "photo", "album"
    files: List[str]
    title: Optional[str] = None
    source_url: Optional[str] = None


async def extract_with_gallery_dl(url: str, dest_dir: str) -> Optional[UniversalMediaResult]:
    """Tenta extrair álbuns/fotos via gallery-dl."""
    cmd = [
        "gallery-dl",
        "--dest", dest_dir,
        "--no-mtime",
        url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode == 0:
            found_files = []
            for root, _, files in os.walk(dest_dir):
                for f in files:
                    if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4")):
                        found_files.append(os.path.join(root, f))
            if found_files:
                media_type = "album" if len(found_files) > 1 else ("video" if found_files[0].endswith(".mp4") else "photo")
                return UniversalMediaResult(media_type=media_type, files=sorted(found_files))
    except Exception as exc:
        logging.debug("gallery-dl extraction error for %s: %s", url, exc)
    return None


async def extract_with_ytdlp(url: str, dest_dir: str, video_quality: Optional[str] = None) -> Optional[UniversalMediaResult]:
    """Baixa vídeos ou mídias via yt-dlp com user-agent realista."""
    format_selector = resolve_video_quality_format(video_quality)
    out_template = os.path.join(dest_dir, "%(title).80s_%(id)s.%(ext)s")
    
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "--referer", url,
        "--format", format_selector,
        "--merge-output-format", "mp4",
        "--max-filesize", str(MAX_FILE_SIZE - 1),
        "-o", out_template,
        "--print-json",
        url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        title = None
        if stdout:
            try:
                info = json.loads(stdout.decode("utf-8").strip().splitlines()[-1])
                title = info.get("title")
            except Exception:
                pass

        found_files = []
        for root, _, files in os.walk(dest_dir):
            for f in files:
                if not f.endswith(".json"):
                    found_files.append(os.path.join(root, f))
        if found_files:
            return UniversalMediaResult(
                media_type="video" if any(f.endswith((".mp4", ".mov", ".mkv", ".webm")) for f in found_files) else "photo",
                files=found_files,
                title=title,
                source_url=url,
            )
    except Exception as exc:
        logging.debug("yt-dlp extraction error for %s: %s", url, exc)
    return None


async def download_universal_media(url: str, video_quality: Optional[str] = None) -> Optional[UniversalMediaResult]:
    task_id = str(uuid.uuid4())[:8]
    task_dir = os.path.join(OUTPUT_DIR, f"universal_{task_id}")
    os.makedirs(task_dir, exist_ok=True)

    # 1. Tenta gallery-dl primeiro para fotos/álbuns
    res = await extract_with_gallery_dl(url, task_dir)
    if res and res.files:
        return res

    # 2. Roda yt-dlp universal
    res = await extract_with_ytdlp(url, task_dir, video_quality=video_quality)
    if res and res.files:
        return res

    shutil.rmtree(task_dir, ignore_errors=True)
    return None
