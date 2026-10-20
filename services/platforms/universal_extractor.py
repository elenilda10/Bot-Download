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
from services.platforms.kwai_media import extract_kwai_video_page, is_kwai_url
from services.platforms.facebook_media import (
    _facebook_failure_reason,
    _facebook_group_post_id,
    _is_facebook_url,
    _verified_facebook_group_photos,
    download_facebook_media,
    facebook_caption,
    facebook_gallery_arguments,
    facebook_ytdlp_arguments,
    facebook_video_caption,
)

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
    if _is_facebook_url(url):
        # Anonymous-only. An authenticated session must never be silently
        # shared with all users of the Universal handler.
        return None
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
            # Facebook's share URLs may redirect on GET but not on HEAD.
            methods = ("GET", "HEAD") if _is_facebook_url(url) else ("HEAD",)
            for method in methods:
                try:
                    async with session.request(
                        method, url, allow_redirects=True,
                        timeout=aiohttp.ClientTimeout(total=8),
                    ) as resp:
                        resolved = str(resp.url)
                        if resp.status < 400 and resolved != url:
                            return resolved
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    continue
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        pass
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
    """Use the actual video dimensions instead of arbitrary 1280x720 defaults."""
    unknown = {"width": None, "height": None, "duration": None}
    if not os.path.isfile(file_path) or os.path.getsize(file_path) == 0:
        return unknown

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration:format=duration",
            "-of", "json", file_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        data_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=25)
        if proc.returncode != 0:
            return unknown
        data = json.loads(data_bytes)
        stream = next(iter(data.get("streams") or []), {})
        data_format = data.get("format") or {}

        def positive_int(value):
            try:
                parsed = float(value)
                if not 0 < parsed < 2_147_483_647:
                    return None
                return int(round(parsed))
            except (TypeError, ValueError, OverflowError):
                return None

        return {
            "width": positive_int(stream.get("width")),
            "height": positive_int(stream.get("height")),
            "duration": positive_int(data_format.get("duration"))
            or positive_int(stream.get("duration")),
        }
    except (OSError, ValueError, TypeError, asyncio.TimeoutError):
        logging.warning("Universal ffprobe unavailable; letting Telegram infer size")
        return unknown
    finally:
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.communicate()


async def _generate_thumbnail(video_path: str, output_thumb_path: str) -> Optional[str]:
    """Generate a JPEG that meets Telegram's thumbnail limits (320px, 200KB)."""
    if not os.path.isfile(video_path) or os.path.getsize(video_path) == 0:
        return None

    # Avoid accidentally reusing the invalid wide previews made by older code.
    try:
        os.remove(output_thumb_path)
    except FileNotFoundError:
        pass
    except OSError:
        return None

    for seek in ("1", "0"):
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-nostdin", "-y", "-ss", seek, "-i", video_path,
                "-frames:v", "1",
                "-vf", "scale=320:320:force_original_aspect_ratio=decrease",
                "-q:v", "5", output_thumb_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=30)
            if (proc.returncode == 0
                    and os.path.isfile(output_thumb_path)
                    and 0 < os.path.getsize(output_thumb_path) < 200_000):
                return output_thumb_path
        except (OSError, asyncio.TimeoutError):
            logging.warning("Universal thumbnail generation failed")
        finally:
            if proc is not None and proc.returncode is None:
                proc.kill()
                await proc.communicate()
        # An ultra-short video may not contain a frame at 1s.
        try:
            os.remove(output_thumb_path)
        except FileNotFoundError:
            pass
        except OSError:
            pass
    return None


async def _download_kwai(
    url: str,
    output_dir: str,
    post_id: str,
    on_progress: Optional[
        Callable[[int, Optional[int], Optional[float]], None]
    ] = None,
) -> Optional[UniversalMediaResult]:
    info = await extract_kwai_video_page(url)
    if not info:
        return None

    media_url = info["media_url"]

    os.makedirs(output_dir, exist_ok=True)

    target_video = os.path.join(
        output_dir,
        f"{post_id}_kwai.mp4",
    )

    thumb_path = os.path.join(
        output_dir,
        f"{post_id}_kwai_thumb.jpg",
    )

    headers = {
        "User-Agent": _DEFAULT_UA,
        "Referer": "https://www.kwai.com/",
        "Accept": "*/*",
    }

    timeout = aiohttp.ClientTimeout(
        connect=15,
        sock_read=180,
        total=600,
    )

    downloaded = 0

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(
                media_url,
                allow_redirects=True,
                timeout=timeout,
            ) as response:

                if response.status != 200:
                    logging.warning(
                        "Kwai CDN returned status=%s",
                        response.status,
                    )
                    return None

                total_header = response.headers.get("Content-Length")

                try:
                    total = int(total_header) if total_header else None
                except (TypeError, ValueError):
                    total = None

                with open(target_video, "wb") as output:
                    async for chunk in response.content.iter_chunked(
                        256 * 1024
                    ):
                        if not chunk:
                            continue

                        output.write(chunk)
                        downloaded += len(chunk)

                        if on_progress:
                            on_progress(
                                downloaded,
                                total,
                                None,
                            )

    except Exception as exc:
        logging.warning(
            "Kwai download failed url=%s error=%s",
            url,
            exc,
        )

        try:
            if os.path.exists(target_video):
                os.remove(target_video)
        except OSError:
            pass

        return None

    if (
        not os.path.exists(target_video)
        or os.path.getsize(target_video) == 0
    ):
        return None

    meta = await _get_video_metadata(target_video)

    thumb = await _generate_thumbnail(
        target_video,
        thumb_path,
    )

    return UniversalMediaResult(
        id=post_id,
        description=info.get("title") or "Vídeo do Kwai",
        author=info.get("author") or "Kwai",
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


async def _download_with_gallery_dl(
    url: str, output_dir: str, post_id: str,
    *, facebook_browser_profile: Optional[str] = None,
) -> Optional[UniversalMediaResult]:
    target_dir = os.path.join(output_dir, f"gdl_{post_id}")
    os.makedirs(target_dir, exist_ok=True)

    binary = GDL_BIN if os.path.exists(GDL_BIN) else "gallery-dl"

    cmd = [
        binary,
        "-d", target_dir,
        "--write-metadata",
        "--no-mtime",
        "--user-agent", _DEFAULT_UA,
    ]

    if not _is_facebook_url(url):
        # Preserve Facebook's gallery set directory for identity checks.
        cmd.extend(["-o", "directory=", "--filename", "{post_id}_{num}.{extension}"])

    if _is_facebook_url(url):
        cmd.extend(facebook_gallery_arguments(facebook_browser_profile))
    cookie_file = _resolve_cookie(url)
    if cookie_file:
        cmd.extend(["--cookies", cookie_file])
    cmd.append(url)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=75 if _is_facebook_url(url) else None,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            logging.warning("gallery-dl timed out: facebook=%s", _is_facebook_url(url))
            return None

        downloaded = []
        caption = ""
        facebook_post_caption = ""
        author = "Reddit" if ("reddit" in url or "redd.it" in url) else "Universal"

        for root, _, files in os.walk(target_dir):
            for file in files:
                fpath = os.path.join(root, file)
                if file.endswith(".json"):
                    try:
                        with open(fpath, "r", encoding="utf-8") as jf:
                            m_data = json.load(jf)
                            # Facebook's gallery-dl emits both an album
                            # title (e.g. "Photos from ... post") and the
                            # author's actual text under "caption". Prefer
                            # that text even if another photo's JSON is read
                            # first. Keep other platforms' caption behavior.
                            if _is_facebook_url(url):
                                facebook_post_caption = (
                                    facebook_post_caption or facebook_caption(m_data)
                                )
                            caption = caption or m_data.get("title") or m_data.get("description") or m_data.get("content") or ""
                            author = m_data.get("author") or m_data.get("uploader") or m_data.get("username") or author
                    except Exception:
                        pass
                elif not file.endswith((".part", ".ytdl", ".txt")) and os.path.getsize(fpath) > 0:
                    # Only actual media: sidecars and audio-only tracks must
                    # never be sent to the Telegram video endpoint.
                    if _is_facebook_url(url):
                        ext = os.path.splitext(fpath)[1].lower()
                        if ext not in {*_IMAGE_EXTS, ".mp4", ".mov", ".webm", ".mkv"}:
                            continue
                    downloaded.append(fpath)

        if not downloaded:
            if proc.returncode != 0 and stderr:
                if _is_facebook_url(url):
                    logging.warning(
                        "Facebook gallery-dl failed: code=%s reason=%s",
                        proc.returncode,
                        _facebook_failure_reason(stderr.decode("utf-8", errors="ignore")),
                    )
                else:
                    logging.warning(
                        "gallery-dl failure stderr: %s",
                        stderr.decode("utf-8", errors="ignore")[:300],
                    )
            return None

        def _sort_key(filepath: str):
            match = re.search(r"_(\d+)\.[^.]+$", filepath)
            return int(match.group(1)) if match else 0

        downloaded.sort(key=_sort_key)
        if _is_facebook_url(url):
            # Only a single Facebook post/gallery, never an entire profile.
            downloaded = downloaded[:10]
            if _facebook_group_post_id(url):
                if not _verified_facebook_group_photos(url, downloaded):
                    logging.warning("Facebook group post photo identity not verified")
                    return None
        media_list = []
        for idx, path in enumerate(downloaded):
            ext = os.path.splitext(path)[1].lower()
            m_type = "photo" if ext in _IMAGE_EXTS else "video"
            if m_type == "video":
                # gallery-dl (including Reddit) doesn't supply Telegram
                # preview/ffprobe metadata. Derive both from the local file.
                meta = await _get_video_metadata(path)
                thumb = await _generate_thumbnail(
                    path, os.path.join(output_dir, f"{post_id}_{idx}_thumb.jpg")
                )
            else:
                meta = {"width": None, "height": None, "duration": None}
                thumb = path
            media_list.append(
                UniversalMediaItem(
                    url=path,
                    type=m_type,
                    thumb=thumb,
                    width=meta["width"],
                    height=meta["height"],
                    duration=meta["duration"],
                    index=idx,
                )
            )

        chosen_caption = facebook_post_caption or caption
        final_description = (
            chosen_caption.strip() if isinstance(chosen_caption, str) and chosen_caption.strip()
            else f"Post ({len(media_list)} items)"
        )

        return UniversalMediaResult(
            id=post_id,
            description=final_description,
            author=author,
            media_list=media_list,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logging.warning("gallery-dl extraction failed: facebook=%s error=%s",
                        _is_facebook_url(url), type(exc).__name__)
        return None


async def _download_with_ytdlp(
    url: str,
    output_dir: str = "downloads",
    on_progress: Optional[Callable[[int, Optional[int], Optional[float]], None]] = None,
    post_id: Optional[str] = None,
    facebook_browser_profile: Optional[str] = None,
    **kwargs: Any,
) -> Optional[UniversalMediaResult]:
    # A number of generic extractors need ?id=, signed URLs or other query
    # parameters. _clean_url() drops them, so preserve the query for yt-dlp.
    target_url = url.split("#", 1)[0].strip()
    if not post_id:
        post_id = hashlib.blake2s(target_url.encode("utf-8"), digest_size=8).hexdigest()
    os.makedirs(output_dir, exist_ok=True)

    binary = YTDLP_BIN if os.path.exists(YTDLP_BIN) else "yt-dlp"
    output_template = os.path.join(output_dir, f"{post_id}.%(ext)s")
    thumb_path = os.path.join(output_dir, f"{post_id}_thumb.jpg")
    cookie_file = _resolve_cookie(target_url)

    # Normal yt-dlp requests remain the default. When a site rejects them,
    # retry once with curl_cffi-powered Chrome impersonation if available.
    # No new router, proxy or VPN is enabled for the Universal handler.
    title = ""
    uploader = "Universal"
    info_args = [
        binary, "--dump-single-json", "--no-warnings",
        "--no-check-certificates",
        "--no-playlist", "--socket-timeout", "30", "--retries", "2",
    ]
    if cookie_file:
        info_args.extend(["--cookies", cookie_file])

    # Facebook's media pages often fail on metadata extraction while
    # still exposing downloadable formats. Do not preflight it twice.
    for impersonate in (() if _is_facebook_url(target_url) else (False, True)):
        cmd = [
            *info_args,
            *(["--impersonate", "chrome"] if impersonate
              else ["--user-agent", _DEFAULT_UA]),
            target_url,
        ]
        proc_info = None
        try:
            proc_info = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc_info.communicate(), timeout=90)
            if proc_info.returncode == 0 and stdout:
                data = json.loads(stdout.decode("utf-8", errors="replace"))
                title = (
                    facebook_video_caption(data) if _is_facebook_url(target_url)
                    else data.get("title") or data.get("description") or ""
                )
                uploader = data.get("uploader") or data.get("channel") or "Universal"
                break
        except (asyncio.TimeoutError, OSError, ValueError) as exc:
            logging.warning("Universal yt-dlp info attempt failed (%s): %s",
                            "chrome" if impersonate else "normal", type(exc).__name__)
        finally:
            if proc_info is not None and proc_info.returncode is None:
                proc_info.kill()
                await proc_info.communicate()

    base_download_args = [
        binary, "--newline", "--no-warnings", "--no-check-certificates",
        "--no-playlist", "--socket-timeout", "30", "--retries", "2",
        "--fragment-retries", "2",
        "-f", "bestvideo*+bestaudio/best",
        "--merge-output-format", "mp4",
        "--print", "after_move:filepath",
        "--progress-template",
        "PROG:%(progress.downloaded_bytes)s:%(progress.total_bytes_estimate)s:%(progress.speed)s",
        "-o", output_template,
    ]
    is_facebook = _is_facebook_url(target_url)
    if cookie_file:
        base_download_args.extend(["--cookies", cookie_file])
    if is_facebook:
        base_download_args.extend(facebook_ytdlp_arguments(facebook_browser_profile))

    async def _one_download(impersonate: bool | str) -> str | None:
        browser_args = (
            ["--impersonate", "chrome-99:windows-10"]
            if impersonate == "chrome99" else
            ["--impersonate", "chrome"] if impersonate else
            ["--user-agent", _DEFAULT_UA]
        )
        cmd = [*base_download_args, *browser_args, target_url]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output_path = None
        # Drain stdout throughout the download so progress and subprocess pipes
        # cannot block. Keep only a bounded error tail, not source URLs.
        recent: list[str] = []

        async def _consume() -> None:
            nonlocal output_path
            while True:
                data = await proc.stdout.readline()
                if not data:
                    return
                line = data.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if line.startswith("PROG:") and on_progress:
                    parts = line.split(":")
                    if len(parts) >= 4:
                        try:
                            downloaded = int(parts[1]) if parts[1] not in {"", "NA"} else 0
                            total = int(parts[2]) if parts[2] not in {"", "NA"} else None
                            speed = float(parts[3]) if parts[3] not in {"", "NA"} else None
                            on_progress(downloaded, total, speed)
                        except (TypeError, ValueError, OverflowError):
                            pass
                elif os.path.isfile(line):
                    output_path = line
                else:
                    recent.append(line)
                    if len(recent) > 8:
                        recent.pop(0)

        try:
            await asyncio.wait_for(_consume(), timeout=180 if is_facebook else 900)
            await asyncio.wait_for(proc.wait(), timeout=15)
        except asyncio.TimeoutError:
            if proc.returncode is None:
                proc.kill()
            await proc.communicate()
            logging.warning(
                "Universal yt-dlp timed out: mode=%s",
                "chrome99" if impersonate == "chrome99" else
                "chrome" if impersonate else "normal",
            )
            return None
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.kill()
            await proc.communicate()
            raise

        if proc.returncode != 0:
            logging.warning(
                "Universal yt-dlp download failed: mode=%s code=%s reason=%s",
                "chrome99" if impersonate == "chrome99" else
                "chrome" if impersonate else "normal", proc.returncode,
                _facebook_failure_reason(recent) if is_facebook else "failed",
            )
            return None
        if output_path and os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
            return output_path
        for path in sorted(glob.glob(os.path.join(output_dir, f"{post_id}.*"))):
            if (os.path.isfile(path)
                    and not path.endswith((".part", ".ytdl", ".jpg", ".json", ".temp"))
                    and os.path.getsize(path) > 0):
                return path
        return None

    try:
        target_video = None
        for impersonate in (
            (False, True, "chrome99") if is_facebook else (False, True)
        ):
            target_video = await _one_download(impersonate)
            if target_video:
                break

        if not target_video:
            logging.warning("Universal yt-dlp produced no file: post_id=%s", post_id)
            return None

        if is_facebook:
            info_path = os.path.join(output_dir, f"{post_id}.info.json")
            try:
                with open(info_path, "r", encoding="utf-8") as fp:
                    info = json.load(fp)
                # Prefer Facebook's post caption/description over yt-dlp's
                # often generic title ("Video"). Leave empty when Facebook
                # provides no genuine caption instead of inventing one.
                title = facebook_video_caption(info) or title
                uploader = info.get("uploader") or info.get("channel") or uploader
            except (OSError, ValueError, AttributeError):
                pass
            finally:
                try:
                    os.remove(info_path)
                except OSError:
                    pass

        ext = os.path.splitext(target_video)[1].lower()
        if ext in _IMAGE_EXTS:
            return UniversalMediaResult(
                id=post_id, description=title, author=uploader,
                media_list=[UniversalMediaItem(
                    url=target_video, type="photo", thumb=target_video, index=0,
                )],
            )

        meta = await _get_video_metadata(target_video)
        thumb = await _generate_thumbnail(target_video, thumb_path)
        return UniversalMediaResult(
            id=post_id, description=title, author=uploader,
            media_list=[UniversalMediaItem(
                url=target_video,
                type="video", thumb=thumb,
                width=meta["width"], height=meta["height"],
                duration=meta["duration"], index=0,
            )],
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logging.warning("Universal yt-dlp processing failed: %s", type(exc).__name__)
        return None


async def download_universal_media(
    url: str,
    output_dir: str = "downloads",
    on_progress: Optional[Callable[[int, Optional[int], Optional[float]], None]] = None,
    post_id: Optional[str] = None,
    facebook_browser_profile: Optional[str] = None,
    **kwargs: Any,
) -> Optional[UniversalMediaResult]:
    os.makedirs(output_dir, exist_ok=True)

    if not post_id:
        # Keep meaningful query parameters in the media identity.
        clean = url.split("#", 1)[0].strip()
        post_id = hashlib.blake2s(clean.encode("utf-8"), digest_size=8).hexdigest()

    if _is_facebook_url(url):
        return await download_facebook_media(
            url,
            output_dir=output_dir,
            on_progress=on_progress,
            post_id=post_id,
            facebook_browser_profile=facebook_browser_profile,
        )

    if is_kwai_url(url):
        res_kwai = await _download_kwai(
            url,
            output_dir,
            post_id,
            on_progress=on_progress,
        )
        if res_kwai:
            return res_kwai

    res_direct = await _download_direct_image(url, output_dir, post_id=post_id)
    if res_direct:
        return res_direct

    res_gdl = await _download_with_gallery_dl(url, output_dir, post_id=post_id)
    if res_gdl and res_gdl.media_list:
        return res_gdl

    resolved = await _resolve_redirects(url)
    return await _download_with_ytdlp(resolved, output_dir=output_dir, on_progress=on_progress, post_id=post_id, **kwargs)
