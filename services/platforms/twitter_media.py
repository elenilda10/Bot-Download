import asyncio
import glob
import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Optional

import aiohttp
from services.logger import logger as logging

logging = logging.bind(service="twitter_media")


@dataclass
class TwitterMediaItem:
    url: str
    type: str  # "video" ou "photo"
    thumb: Optional[str] = None
    width: Optional[int] = 1280
    height: Optional[int] = 720
    duration: Optional[int] = 0
    index: int = 0


@dataclass
class TwitterMediaResult:
    id: str
    description: str
    author: str
    likes: int
    replies: int
    retweets: int
    media_list: list[TwitterMediaItem]


def _clean_url(url: str) -> str:
    return url.split("?")[0].split("#")[0].strip()


def _extract_tweet_id(url: str) -> Optional[str]:
    match = re.search(r"/status/(\d+)", url)
    return match.group(1) if match else None


def _get_cookie_file() -> Optional[str]:
    for cand in [
        "/root/Bot-Download/cookies/twitter_cookies.txt",
        "/root/Bot-Download/cookies/twitter.txt",
        "/root/Bot-Download/cookies/x.txt",
    ]:
        if os.path.exists(cand):
            return cand
    return None


async def _get_video_metadata(file_path: str) -> dict:
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


async def _fetch_fxtwitter_data(url: str, output_dir: str, post_id: str) -> Optional[TwitterMediaResult]:
    """Resolve tweets, vídeos em quotes e carrosséis via API direta FxTwitter/FixupX."""
    match = re.search(r"(?:twitter|x)\.com/([^/]+)/status/(\d+)", url)
    if not match:
        return None

    screen_name, tweet_id = match.groups()
    api_url = f"https://api.fxtwitter.com/{screen_name}/status/{tweet_id}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=8)) as response:
                if response.status != 200:
                    return None
                payload = await response.json()

        tweet = payload.get("tweet")
        if not tweet:
            return None

        # Resolve dados do tweet citado se o principal não tiver mídia direta
        media_source = tweet.get("media")
        if not media_source and tweet.get("quote"):
            quote = tweet.get("quote")
            media_source = quote.get("media")

        if not media_source:
            return None

        description = tweet.get("text") or ""
        author = tweet.get("author", {}).get("name") or screen_name
        likes = tweet.get("likes") or 0
        replies = tweet.get("replies") or 0
        retweets = tweet.get("retweets") or 0

        media_items = []
        all_media = media_source.get("all") or []

        # Se não vier a lista "all", monta a partir de videos e photos
        if not all_media:
            for v in media_source.get("videos") or []:
                all_media.append(v)
            for p in media_source.get("photos") or []:
                all_media.append(p)

        for idx, item in enumerate(all_media):
            m_type = item.get("type", "photo")
            m_url = item.get("url")
            if not m_url:
                continue

            is_video = m_type == "video" or m_type == "gif" or m_url.lower().endswith((".mp4", ".mov", ".mkv", ".webm"))
            ext = "mp4" if is_video else "jpg"
            target_file = os.path.join(output_dir, f"{post_id}_fx_{idx}.{ext}")

            # Download da stream
            cmd = ["curl", "-sL", m_url, "-o", target_file]
            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.communicate()

            if os.path.exists(target_file) and os.path.getsize(target_file) > 0:
                thumb = None
                width = item.get("width") or 1280
                height = item.get("height") or 720
                duration = int(item.get("duration") or 0)

                if is_video:
                    meta = await _get_video_metadata(target_file)
                    width = meta["width"]
                    height = meta["height"]
                    duration = meta["duration"] or duration
                    thumb_path = os.path.join(output_dir, f"{post_id}_fx_thumb_{idx}.jpg")
                    thumb = await _generate_thumbnail(target_file, thumb_path)

                media_items.append(
                    TwitterMediaItem(
                        url=target_file,
                        type="video" if is_video else "photo",
                        thumb=thumb,
                        width=width,
                        height=height,
                        duration=duration,
                        index=idx,
                    )
                )

        if media_items:
            logging.info("FxTwitter resolved %d item(s) for tweet_id=%s", len(media_items), tweet_id)
            return TwitterMediaResult(
                id=post_id,
                description=description,
                author=author,
                likes=likes,
                replies=replies,
                retweets=retweets,
                media_list=media_items,
            )
    except Exception as exc:
        logging.warning("FxTwitter error: %s", exc)

    return None


async def download_twitter_media(url: str, output_dir: str = "/root/Bot-Download/downloads") -> Optional[TwitterMediaResult]:
    clean_url = _clean_url(url)
    post_id = _extract_tweet_id(clean_url) or hashlib.blake2s(clean_url.encode("utf-8"), digest_size=8).hexdigest()
    os.makedirs(output_dir, exist_ok=True)

    # 1. Prioridade: FxTwitter API (Instantâneo, resolve quotes, vídeos de 1080p e carrosséis)
    fx_res = await _fetch_fxtwitter_data(clean_url, output_dir, post_id)
    if fx_res and fx_res.media_list:
        return fx_res

    # 2. Fallback: yt-dlp local com cookies
    cookie_file = _get_cookie_file()
    raw_template = os.path.join(output_dir, f"{post_id}_raw.%(ext)s")
    final_mp4 = os.path.join(output_dir, f"{post_id}_fast.mp4")
    thumb_path = os.path.join(output_dir, f"{post_id}_thumb.jpg")

    cmd_dl = [
        "yt-dlp",
        "--no-warnings",
        "--no-check-certificates",
        "--print-json",
        "--concurrent-fragments", "5",
        "-f", "bestvideo+bestaudio/best",
        "--merge-output-format", "mp4",
        "-o", raw_template,
    ]
    if cookie_file:
        cmd_dl.extend(["--cookies", cookie_file])
    cmd_dl.append(clean_url)

    title = ""
    uploader = "Twitter"
    likes = 0
    replies = 0
    retweets = 0
    has_video = False

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_dl,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()

        if stdout:
            for line in stdout.decode("utf-8", errors="ignore").splitlines():
                if line.strip().startswith("{") and line.strip().endswith("}"):
                    try:
                        info = json.loads(line)
                        title = info.get("description") or info.get("title") or ""
                        uploader = info.get("uploader") or info.get("channel") or info.get("uploader_id") or "Twitter"
                        likes = info.get("like_count") or 0
                        replies = info.get("comment_count") or 0
                        retweets = info.get("repost_count") or 0
                        formats = info.get("formats", [])
                        has_video = any(f.get("vcodec") != "none" and f.get("vcodec") is not None for f in formats)
                        break
                    except Exception:
                        continue

        raw_files = glob.glob(os.path.join(output_dir, f"{post_id}_raw*"))
        if raw_files and (has_video or any(f.endswith(".mp4") for f in raw_files)):
            downloaded_raw = raw_files[0]
            cmd_fast_remux = [
                "ffmpeg", "-y",
                "-i", downloaded_raw,
                "-c", "copy",
                "-movflags", "+faststart",
                final_mp4,
            ]
            proc_remux = await asyncio.create_subprocess_exec(
                *cmd_fast_remux,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc_remux.communicate()

            video_to_use = final_mp4 if (os.path.exists(final_mp4) and os.path.getsize(final_mp4) > 0) else downloaded_raw
            if os.path.exists(downloaded_raw) and video_to_use == final_mp4:
                try:
                    os.remove(downloaded_raw)
                except Exception:
                    pass

            meta = await _get_video_metadata(video_to_use)
            thumb = await _generate_thumbnail(video_to_use, thumb_path)

            return TwitterMediaResult(
                id=post_id,
                description=title,
                author=uploader,
                likes=likes,
                replies=replies,
                retweets=retweets,
                media_list=[
                    TwitterMediaItem(
                        url=video_to_use,
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
        logging.error("yt-dlp error for %s: %s", clean_url, exc)

    return None
