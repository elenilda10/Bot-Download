import asyncio
import glob
import hashlib
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, urlunparse

import httpx
from config import COBALT_API_KEY, COBALT_API_URL
from utils.cobalt_client import fetch_cobalt_data

logger = logging.getLogger(__name__)

EXTENSION_COOKIES_PATH = "/root/Bot-Download/cookies.txt"
GDL_BIN = "/root/Bot-Download/venv/bin/gallery-dl"
YTDLP_BIN = "/root/Bot-Download/venv/bin/yt-dlp"

PUBLIC_COBALT_INSTANCES = [
    "https://cobalt-api.kwiatekm.tokyo",
    "https://cobalt.xy2.dev",
    "https://dl.khann.me",
    "https://api.cobalt.tools",
    "https://cobalt.ducks.party",
    "https://cobalt.canine.tools",
]

BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def strip_instagram_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _extract_story_target_id(url: str) -> Optional[str]:
    match = re.search(r"/stories/[^/?#&]+/(\d+)", url)
    return match.group(1) if match else None


def _extract_instagram_post_id(url: str) -> Optional[str]:
    story_id = _extract_story_target_id(url)
    if story_id:
        return story_id

    match = re.search(r"/(?:p|reel|reels|tv|stories/[^/?#&]+)/([A-Za-z0-9_-]+)", url)
    if match:
        return match.group(1)
    match_fallback = re.search(r"/(?:p|reel|reels|tv|stories)/([A-Za-z0-9_-]+)", url)
    return match_fallback.group(1) if match_fallback else None


@dataclass
class InstagramMedia:
    url: str
    type: str  # "photo" ou "video"
    thumb: Optional[str] = None
    width: Optional[int] = 1280
    height: Optional[int] = 720
    duration: Optional[int] = 0
    index: int = 0


@dataclass
class InstagramVideo:
    id: str
    description: str
    author: str
    media_list: list[InstagramMedia]


def get_instagram_preview_url(media: Optional[InstagramMedia]) -> Optional[str]:
    if not media:
        return None
    return media.thumb or media.url


def _run_gallery_dl(url: str, output_dir: str, post_id: str, story_id: Optional[str] = None) -> Optional[InstagramVideo]:
    try:
        target_dir = os.path.join(output_dir, f"gdl_{post_id}")
        os.makedirs(target_dir, exist_ok=True)

        binary = GDL_BIN if os.path.exists(GDL_BIN) else "gallery-dl"

        # Aqui entra a flag corrigida com {shortcode}_{num}.{extension}
        cmd = [
            binary,
            "--no-part",
            "--no-mtime",
            "--retries", "3",
            "-d", target_dir,
            "-o", "directory=",
            "--filename", "{shortcode}_{num}.{extension}",
            "--write-metadata",
            "--user-agent", BROWSER_UA,
        ]

        if story_id:
            cmd.extend(["--filter", f"str(media_id) == '{story_id}' or str(id) == '{story_id}' or str(post_id) == '{story_id}'"])

        if os.path.exists(EXTENSION_COOKIES_PATH) and os.path.getsize(EXTENSION_COOKIES_PATH) > 0:
            cmd.extend(["--cookies", EXTENSION_COOKIES_PATH])

        cmd.append(url)

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            logger.warning("gallery-dl output/warn: %s", proc.stderr)

        saved_files = []
        caption = ""
        author = "instagram_user"

        for root, _, files in os.walk(target_dir):
            for file in files:
                fpath = os.path.join(root, file)
                if file.endswith(".json"):
                    try:
                        with open(fpath, "r", encoding="utf-8") as jf:
                            m_data = json.load(jf)
                            caption = caption or m_data.get("description") or m_data.get("caption") or ""
                            author = m_data.get("username") or m_data.get("author") or author
                    except Exception:
                        pass
                elif not file.endswith((".part", ".ytdl", ".txt")):
                    if os.path.getsize(fpath) > 0:
                        saved_files.append(fpath)

        if not saved_files:
            return None

        # Ordena pelo sufixo numérico (_1, _2, _3...) para manter a ordem do carrossel
        def _sort_key(filepath: str):
            match = re.search(r"_(\d+)\.[^.]+$", filepath)
            return int(match.group(1)) if match else 0

        saved_files.sort(key=_sort_key)

        if story_id:
            matching_files = [f for f in saved_files if story_id in os.path.basename(f)]
            if matching_files:
                for f in saved_files:
                    if f not in matching_files:
                        try:
                            os.remove(f)
                        except Exception:
                            pass
                saved_files = matching_files
            else:
                saved_files = [saved_files[0]]

        media_items = []
        for idx, fpath in enumerate(saved_files):
            ext = os.path.splitext(fpath)[1].lower()
            is_vid = ext in [".mp4", ".mov", ".mkv", ".webm"]
            media_items.append(
                InstagramMedia(
                    url=fpath,
                    type="video" if is_vid else "photo",
                    thumb=None,
                    width=1080,
                    height=1080,
                    duration=0,
                    index=idx,
                )
            )

        if media_items:
            return InstagramVideo(id=post_id, description=caption.strip(), author=author, media_list=media_items)

    except Exception as exc:
        logger.warning("gallery-dl falhou: %s", exc)
    return None


def _run_ytdlp(url: str, output_dir: str, post_id: str) -> Optional[InstagramVideo]:
    try:
        out_template = os.path.join(output_dir, f"{post_id}_ytdlp_%(autonumber)s.%(ext)s")
        binary = YTDLP_BIN if os.path.exists(YTDLP_BIN) else "yt-dlp"

        cmd = [
            binary,
            "--yes-playlist",
            "--no-warnings",
            "-f", "bestvideo*+bestaudio/best",
            "--merge-output-format", "mp4",
            "--write-info-json",
            "--user-agent", BROWSER_UA,
            "-o", out_template,
        ]

        if os.path.exists(EXTENSION_COOKIES_PATH) and os.path.getsize(EXTENSION_COOKIES_PATH) > 0:
            cmd.extend(["--cookies", EXTENSION_COOKIES_PATH])

        cmd.append(url)

        subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        info_json_pattern = os.path.join(output_dir, f"{post_id}_ytdlp_*.info.json")
        info_files = sorted(glob.glob(info_json_pattern))

        media_items = []
        caption = ""
        author = "instagram_user"

        if info_files:
            for idx, info_path in enumerate(info_files):
                try:
                    with open(info_path, "r", encoding="utf-8") as f:
                        info = json.load(f)

                    caption = caption or info.get("description") or ""
                    author = info.get("uploader") or info.get("channel") or author

                    base_name = info_path.replace(".info.json", "")
                    target_file = None
                    for ext in ["mp4", "jpg", "jpeg", "webp", "png"]:
                        candidate = f"{base_name}.{ext}"
                        if os.path.exists(candidate):
                            target_file = candidate
                            break

                    if not target_file:
                        continue

                    is_vid = target_file.endswith(".mp4")
                    thumb_candidate = f"{base_name}.jpg"
                    thumb = thumb_candidate if (is_vid and os.path.exists(thumb_candidate)) else None

                    media_items.append(
                        InstagramMedia(
                            url=target_file,
                            type="video" if is_vid else "photo",
                            thumb=thumb,
                            width=info.get("width") or 1080,
                            height=info.get("height") or 1080,
                            duration=int(info.get("duration") or 0),
                            index=idx,
                        )
                    )
                except Exception as file_err:
                    logger.warning("Erro processando info do yt-dlp: %s", file_err)
                finally:
                    if os.path.exists(info_path):
                        try:
                            os.remove(info_path)
                        except Exception:
                            pass

        if media_items:
            return InstagramVideo(id=post_id, description=caption.strip(), author=author, media_list=media_items)

    except Exception as exc:
        logger.warning("yt-dlp falhou: %s", exc)
    return None


async def _download_cobalt_payload(data: dict, post_id: str, output_dir: str, is_reel: bool = False) -> Optional[InstagramVideo]:
    status = data.get("status")
    items_to_download = []

    if status == "picker" or "picker" in data:
        picker = data.get("picker") or []
        for item in picker:
            if isinstance(item, dict):
                m_url = item.get("url")
                m_type = item.get("type", "video" if is_reel else "photo")
                if m_url:
                    items_to_download.append((m_url, "video" if m_type == "video" else "photo"))
    elif data.get("url"):
        default_type = "video" if (is_reel or status in ["redirect", "tunnel", "stream"]) else "photo"
        items_to_download.append((data.get("url"), default_type))

    if not items_to_download:
        return None

    media_items = []
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        for idx, (m_url, m_type) in enumerate(items_to_download):
            try:
                resp = await client.get(m_url)
                if resp.status_code != 200:
                    continue

                content_type = resp.headers.get("Content-Type", "").lower()
                if "video" in content_type or m_url.endswith((".mp4", ".mov")) or is_reel:
                    m_type = "video"

                ext = "mp4" if m_type == "video" else "jpg"
                out_path = os.path.join(output_dir, f"{post_id}_cobalt_{idx}.{ext}")
                with open(out_path, "wb") as f:
                    f.write(resp.content)

                media_items.append(
                    InstagramMedia(
                        url=out_path,
                        type=m_type,
                        thumb=None,
                        width=1280,
                        height=720,
                        duration=0,
                        index=idx,
                    )
                )
            except Exception as exc:
                logger.warning("Falha ao baixar item %s do Cobalt: %s", m_url, exc)

    if media_items:
        return InstagramVideo(id=post_id, description="", author="instagram_user", media_list=media_items)
    return None


async def fetch_instagram_media(url: str, output_dir: str = "/root/Bot-Download/downloads") -> Optional[InstagramVideo]:
    story_id = _extract_story_target_id(url)
    clean_url = strip_instagram_url(url)
    post_id = _extract_instagram_post_id(url) or hashlib.blake2s(clean_url.encode("utf-8"), digest_size=8).hexdigest()
    is_reel = "/reel/" in url or "/reels/" in url
    is_album_or_story = "/p/" in url or "/stories/" in url
    os.makedirs(output_dir, exist_ok=True)
    loop = asyncio.get_running_loop()

    # Prioridade para o gallery-dl: com os cookies da extensão, ele extrai o carrossel completo
    res_gdl = await loop.run_in_executor(None, _run_gallery_dl, clean_url, output_dir, post_id, story_id)
    if res_gdl and res_gdl.media_list:
        return res_gdl

    # Fallback 1: Cobalt local
    payload = {"url": clean_url, "videoQuality": "1080", "downloadMode": "auto"}
    try:
        data = await fetch_cobalt_data(COBALT_API_URL, COBALT_API_KEY, payload, source="instagram")
        if data and isinstance(data, dict) and data.get("status") != "error":
            res = await _download_cobalt_payload(data, post_id, output_dir, is_reel=is_reel)
            if res and res.media_list:
                return res
    except Exception as exc:
        logger.warning("Cobalt local falhou: %s", exc)

    # Fallback 2: yt-dlp
    res_ytdlp = await loop.run_in_executor(None, _run_ytdlp, clean_url, output_dir, post_id)
    if res_ytdlp and res_ytdlp.media_list:
        return res_ytdlp

    # Fallback 3: Instâncias públicas do Cobalt
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for base_url in PUBLIC_COBALT_INSTANCES:
            try:
                resp = await client.post(f"{base_url.rstrip('/')}/", json=payload, headers=headers)
                if resp.status_code == 200:
                    pub_data = resp.json()
                    if pub_data.get("status") != "error":
                        res = await _download_cobalt_payload(pub_data, post_id, output_dir, is_reel=is_reel)
                        if res and res.media_list:
                            return res
            except Exception:
                continue

    return None
