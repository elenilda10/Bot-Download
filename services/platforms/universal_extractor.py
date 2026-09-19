import asyncio
import glob
import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Optional, Any, Callable
import aiohttp

from services.logger import logger as logging

logging = logging.bind(service="universal_extractor")

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".svg")
_DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

GDL_BIN = "/root/Bot-Download/venv/bin/gallery-dl"
YTDLP_BIN = "/root/Bot-Download/venv/bin/yt-dlp"
REDDIT_COOKIES = "/root/Bot-Download/cookies/reddit_cookies.txt"


@dataclass
class UniversalMediaItem:
    url: str
    type: str
    thumb: Optional[str] = None
    width: Optional[int] = 1280
    height: Optional[int] = 720
    duration: Optional[int] = 0
    index: int = 0


@dataclass
class UniversalMediaResult:
    id: str
    description: str
    author: str
    media_list: list[UniversalMediaItem]


def _clean_url(url: str) -> str:
    return url.split("?")[0].split("#")[0].strip()


def _resolve_cookie(url: str) -> Optional[str]:
    if "reddit.com" in url or "redd.it" in url:
        if os.path.exists(REDDIT_COOKIES) and os.path.getsize(REDDIT_COOKIES) > 0:
            return REDDIT_COOKIES
        gen_cookie = "/root/Bot-Download/cookies.txt"
        if os.path.exists(gen_cookie) and os.path.getsize(gen_cookie) > 0:
            return gen_cookie
    return None


async def _resolve_redirects(url: str) -> str:
    try:
        headers = {"User-Agent": _DEFAULT_UA}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.head(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                return str(resp.url)
    except Exception as exc:
        logging.warning("Error resolving redirect for url=%s: %s", url, exc)
        return url


async def _download_direct_image(url: str, output_dir: str, post_id: str) -> Optional[UniversalMediaResult]:
    clean = _clean_url(url)
    ext = os.path.splitext(clean)[1].lower()
    if ext not in _IMAGE_EXTS:
        return None

    out_ext = ".png" if ext == ".svg" else ext
    local_path = os.path.join(output_dir, f"{post_id}{out_ext}")

    headers = {"User-Agent": _DEFAULT_UA}
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.read()

        with open(local_path, "wb") as f:
            f.write(data)

        filename = os.path.basename(clean)
        return UniversalMediaResult(
            id=post_id,
            description=filename,
            author="Web",
            media_list=[
                UniversalMediaItem(
                    url=local_path,
                    type="photo",
                    thumb=local_path,
                    width=1280,
                    height=720,
                    duration=0,
                    index=0,
                )
            ],
        )
    except Exception as exc:
        logging.warning("Direct image download error for %s: %s", url, exc)
        return None


async def _get_video_metadata(file_path: str) -> dict:
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        return {"width": 1280, "height": 720, "duration": 0}

    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,duration:format=duration",
        "-of", "json",
        file_path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0 and stdout:
            data = json.loads(stdout.decode("utf-8", errors="ignore"))
            streams = data.get("streams", [])
            v_stream = streams[0] if streams else {}
            width = int(v_stream.get("width") or 0)
            height = int(v_stream.get("height") or 0)
            dur_val = v_stream.get("duration") or data.get("format", {}).get("duration")
            duration = int(float(dur_val)) if dur_val else 0
            return {"width": width or 1280, "height": height or 720, "duration": duration}
    except Exception as exc:
        logging.warning("ffprobe error: %s", exc)
    return {"width": 1280, "height": 720, "duration": 0}


async def _generate_thumbnail(video_path: str, output_thumb_path: str) -> Optional[str]:
    if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
        return None

    cmd = [
        "ffmpeg", "-y",
        "-ss", "00:00:01",
        "-i", video_path,
        "-vframes", "1",
        "-vf", "scale=640:-1",
        "-q:v", "3",
        output_thumb_path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if os.path.exists(output_thumb_path) and os.path.getsize(output_thumb_path) > 0:
            return output_thumb_path
    except Exception as exc:
        logging.warning("Thumb error: %s", exc)
    return None


async def _download_with_gallery_dl(url: str, output_dir: str, post_id: str) -> Optional[UniversalMediaResult]:
    target_dir = os.path.join(output_dir, f"gdl_{post_id}")
    os.makedirs(target_dir, exist_ok=True)

    binary = GDL_BIN if os.path.exists(GDL_BIN) else "gallery-dl"

    cmd = [
        binary,
        "-d", target_dir,
        "-o", "directory=",
        "--filename", "{post_id}_{num}.{extension}",
        "--write-metadata",
        "--no-mtime",
        "--user-agent", _DEFAULT_UA,
    ]

    cookie_file = _resolve_cookie(url)
    if cookie_file:
        cmd.extend(["--cookies", cookie_file])

    cmd.append(url)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        downloaded = []
        caption = ""
        author = "Reddit" if ("reddit" in url or "redd.it" in url) else "Universal"

        for root, _, files in os.walk(target_dir):
            for file in files:
                fpath = os.path.join(root, file)
                if file.endswith(".json"):
                    try:
                        with open(fpath, "r", encoding="utf-8") as jf:
                            m_data = json.load(jf)
                            caption = caption or m_data.get("title") or m_data.get("description") or m_data.get("content") or ""
                            author = m_data.get("author") or m_data.get("uploader") or m_data.get("username") or author
                    except Exception:
                        pass
                elif not file.endswith((".part", ".ytdl", ".txt")) and os.path.getsize(fpath) > 0:
                    downloaded.append(fpath)

        if not downloaded:
            if proc.returncode != 0 and stderr:
                logging.warning("gallery-dl failure stderr: %s", stderr.decode("utf-8", errors="ignore")[:300])
            return None

        def _sort_key(filepath: str):
            match = re.search(r"_(\d+)\.[^.]+$", filepath)
            return int(match.group(1)) if match else 0

        downloaded.sort(key=_sort_key)
        media_list = []
        for idx, path in enumerate(downloaded):
            ext = os.path.splitext(path)[1].lower()
            m_type = "photo" if ext in _IMAGE_EXTS else "video"
            thumb = path if m_type == "photo" else None
            media_list.append(
                UniversalMediaItem(
                    url=path,
                    type=m_type,
                    thumb=thumb,
                    index=idx,
                )
            )

        final_description = caption.strip() if caption.strip() else f"Post ({len(media_list)} items)"

        return UniversalMediaResult(
            id=post_id,
            description=final_description,
            author=author,
            media_list=media_list,
        )
    except Exception as exc:
        logging.warning("gallery-dl failed for %s: %s", url, exc)
        return None


async def _download_with_ytdlp(
    url: str,
    output_dir: str = "/root/Bot-Download/downloads",
    on_progress: Optional[Callable[[int, Optional[int], Optional[float]], None]] = None,
    post_id: Optional[str] = None,
    **kwargs: Any,
) -> Optional[UniversalMediaResult]:
    clean_url = _clean_url(url)
    if not post_id:
        post_id = hashlib.blake2s(clean_url.encode("utf-8"), digest_size=8).hexdigest()

    os.makedirs(output_dir, exist_ok=True)

    binary = YTDLP_BIN if os.path.exists(YTDLP_BIN) else "yt-dlp"
    final_output_template = os.path.join(output_dir, f"{post_id}.%(ext)s")
    thumb_path = os.path.join(output_dir, f"{post_id}_thumb.jpg")

    cookie_file = _resolve_cookie(clean_url)

    title = ""
    uploader = "Universal"
    cmd_info = [
        binary,
        "--dump-single-json",
        "--no-warnings",
        "--no-check-certificates",
        "--user-agent", _DEFAULT_UA,
    ]
    if cookie_file:
        cmd_info.extend(["--cookies", cookie_file])
    cmd_info.append(clean_url)

    try:
        proc_info = await asyncio.create_subprocess_exec(
            *cmd_info,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_info, _ = await proc_info.communicate()
        if proc_info.returncode == 0 and stdout_info:
            info_data = json.loads(stdout_info.decode("utf-8", errors="ignore"))
            title = info_data.get("title") or info_data.get("description") or ""
            uploader = info_data.get("uploader") or info_data.get("channel") or "Universal"
    except Exception as exc:
        logging.warning("JSON info error: %s", exc)

    cmd_dl = [
        binary,
        "--newline",
        "--no-warnings",
        "--no-check-certificates",
        "-f", "bestvideo*+bestaudio/best",
        "--merge-output-format", "mp4",
        "--user-agent", _DEFAULT_UA,
        "--print", "after_move:filepath",
        "--progress-template", "PROG:%(progress.downloaded_bytes)s:%(progress.total_bytes_estimate)s:%(progress.speed)s",
        "-o", final_output_template,
    ]
    if cookie_file:
        cmd_dl.extend(["--cookies", cookie_file])
    cmd_dl.append(clean_url)

    try:
        proc_dl = await asyncio.create_subprocess_exec(
            *cmd_dl,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        final_path_from_stdout = None
        while True:
            line_bytes = await proc_dl.stdout.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            if line.startswith("PROG:") and on_progress:
                parts = line.split(":")
                if len(parts) >= 4:
                    try:
                        dl = int(parts[1]) if parts[1] and parts[1] != "NA" else 0
                        tot = int(parts[2]) if parts[2] and parts[2] != "NA" else None
                        spd = float(parts[3]) if parts[3] and parts[3] != "NA" else None
                        on_progress(dl, tot, spd)
                    except Exception:
                        pass
            elif not line.startswith("[") and os.path.exists(line):
                final_path_from_stdout = line

        _, stderr_dl = await proc_dl.communicate()

        target_video = final_path_from_stdout
        if not target_video or not os.path.exists(target_video):
            candidates = glob.glob(os.path.join(output_dir, f"{post_id}*"))
            valid = [
                c for c in candidates
                if not c.endswith((".part", ".ytdl", ".jpg", ".json", ".temp")) and os.path.getsize(c) > 0
            ]
            target_video = valid[0] if valid else None

        if not target_video:
            err_msg = stderr_dl.decode("utf-8", errors="ignore") if stderr_dl else "Sem stderr"
            logging.error("Nenhum arquivo final encontrado para post_id=%s. Erro yt-dlp: %s", post_id, err_msg[:300])
            return None

        ext = os.path.splitext(target_video)[1].lower()
        if ext in _IMAGE_EXTS:
            return UniversalMediaResult(
                id=post_id,
                description=title,
                author=uploader,
                media_list=[
                    UniversalMediaItem(
                        url=target_video,
                        type="photo",
                        thumb=target_video,
                        index=0,
                    )
                ],
            )

        meta = await _get_video_metadata(target_video)
        thumb = await _generate_thumbnail(target_video, thumb_path)

        return UniversalMediaResult(
            id=post_id,
            description=title,
            author=uploader,
            media_list=[
                UniversalMediaItem(
                    url=target_video,
                    type="video",
                    thumb=thumb,
                    width=meta["width"],
                    height=meta["height"],
                    duration=meta["duration"],
                    index=0,
                )
            ],
        )
    except Exception as exc:
        logging.error("Universal download failed: %s", exc)

    return None


async def download_universal_media(
    url: str,
    output_dir: str = "/root/Bot-Download/downloads",
    on_progress: Optional[Callable[[int, Optional[int], Optional[float]], None]] = None,
    post_id: Optional[str] = None,
    **kwargs: Any,
) -> Optional[UniversalMediaResult]:
    os.makedirs(output_dir, exist_ok=True)

    if not post_id:
        clean = _clean_url(url)
        post_id = hashlib.blake2s(clean.encode("utf-8"), digest_size=8).hexdigest()

    res_direct = await _download_direct_image(url, output_dir, post_id=post_id)
    if res_direct:
        return res_direct

    res_gdl = await _download_with_gallery_dl(url, output_dir, post_id=post_id)
    if res_gdl and res_gdl.media_list:
        return res_gdl

    resolved = await _resolve_redirects(url)
    return await _download_with_ytdlp(resolved, output_dir=output_dir, on_progress=on_progress, post_id=post_id, **kwargs)
