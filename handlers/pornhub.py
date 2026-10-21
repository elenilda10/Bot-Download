"""Admin-only PornHub downloader in isolated Proton WireGuard network namespace.

All yt-dlp and helper processes run within ph-proton, which has only a VPN
interface, DNS 10.2.0.1 and no eth0 route. Tunnel failure never uses Contabo.
The bot's other handlers continue to run in the original host namespace.
"""
import asyncio
import json
import logging
import math
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from aiogram import F, Router, types
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile

import keyboards as kb
import messages as bm
from app_context import bot, db
from config import ADMIN_ID, MAX_FILE_SIZE
from handlers import commands as cmd_mod
from handlers.request_dedupe import claim_message_request
from handlers.utils import (
    get_bot_url,
    handle_download_error,
    handle_video_too_large,
    load_user_settings,
    maybe_delete_user_message,
    react_to_message,
    safe_delete_message,
    safe_edit_text,
    send_chat_action_if_needed,
)
from utils.media_cache import build_media_cache_key

router = Router(name="pornhub_test")
logger = logging.getLogger(__name__)
_DOWNLOAD_LIMIT = asyncio.Semaphore(1)
_URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
_LINK_PATTERN = r"(?i)https?://(?:(?:www|pt|m)\.)?pornhub\.com/view_video\.php\?viewkey="
_VPN_INTERFACE = "protonbot"
_VPN_NAMESPACE = "ph-proton"
_MAX_DOWNLOAD_BYTES = min(MAX_FILE_SIZE, 450 * 1024 * 1024)
_YTDLP_BIN = "/root/Bot-Download/venv/bin/yt-dlp"
# Change the version if download format/quality rules change.
_CACHE_VARIANT = "auto480-fallback720-v1"


class VideoTooLargeError(Exception):
    """The downloaded video exceeds the current bot's media size limit."""


@dataclass
class DownloadedVideo:
    path: Path
    description: str | None
    duration: int | None = None
    width: int | None = None
    height: int | None = None
    thumbnail: Path | None = None
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None


def _positive_int(value: object) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return int(round(number)) if math.isfinite(number) and number >= 1 else None


def _nonnegative_int(value: object) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return int(number) if math.isfinite(number) and number >= 0 else None


def _site_description(info: dict) -> str | None:
    """Prefer the video's real title/description; SaveVid formats and escapes it."""
    title = str(info.get("title") or "").strip()
    description = str(info.get("description") or "").strip()
    if description and description.casefold().startswith(title.casefold()) and title:
        caption = description
    else:
        caption = "\n\n".join(part for part in (title, description) if part)
    return caption[:800].rstrip() or None


async def _probe_and_make_thumbnail(video: DownloadedVideo) -> None:
    """Read MP4 metadata and generate a JPEG preview inside the VPN namespace."""
    if video.path.suffix.lower() != ".mp4":
        return

    # Metadata may be missing from the MP4 produced by HLS extraction. Passing
    # duration/width/height makes Telegram render video size more consistently.
    probe_cmd = [
        "ip", "netns", "exec", _VPN_NAMESPACE,
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration",
        "-of", "json", str(video.path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *probe_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        output, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        if proc.returncode == 0:
            info = json.loads(output)
            stream = next(iter(info.get("streams") or []), {})
            video.duration = _positive_int(info.get("format", {}).get("duration")) or video.duration
            video.width = _positive_int(stream.get("width")) or video.width
            video.height = _positive_int(stream.get("height")) or video.height
    except (OSError, ValueError, asyncio.TimeoutError):
        logger.warning("Pornhub ffprobe unavailable; using extractor metadata")
        if "proc" in locals() and proc.returncode is None:
            proc.kill()
            await proc.communicate()

    thumbnail_path = video.path.parent / "preview.jpg"
    ffmpeg_cmd = [
        "ip", "netns", "exec", _VPN_NAMESPACE,
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-y", "-ss", "1", "-i", str(video.path),
        "-frames:v", "1",
        "-vf", "scale=320:320:force_original_aspect_ratio=decrease",
        "-q:v", "5", str(thumbnail_path),
    ]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=30)
        # Telegram thumbnail restrictions: JPEG, <= 320 px, < 200 KB.
        if (proc.returncode == 0 and thumbnail_path.is_file()
                and 0 < thumbnail_path.stat().st_size < 200_000):
            video.thumbnail = thumbnail_path
    except (OSError, asyncio.TimeoutError):
        logger.warning("Pornhub thumbnail unavailable; sending video without preview")
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.communicate()


def _extract_video_url(text: str) -> str | None:
    for match in _URL_RE.finditer(text or ""):
        raw = match.group(0).rstrip(".,;!)]}")
        try:
            parsed = urlsplit(raw)
            if parsed.scheme not in {"http", "https"}:
                continue
            if parsed.hostname not in {
                "pornhub.com", "www.pornhub.com", "pt.pornhub.com", "m.pornhub.com"
            } or parsed.path != "/view_video.php":
                continue
            viewkey = parse_qs(parsed.query).get("viewkey", [""])[0]
            if not re.fullmatch(r"[A-Za-z0-9]{6,40}", viewkey):
                continue
            return f"https://{parsed.hostname}/view_video.php?{urlencode({'viewkey': viewkey})}"
        except ValueError:
            continue
    return None


@router.message(F.text.regexp(r"^/phvpn(?:@\w+)?(?:\s|$)"))
async def show_pornhub_test_vpn(message: types.Message):
    if not message.from_user or message.from_user.id != ADMIN_ID:
        return
    await message.answer(
        "🔒 Router Pornhub: VPN fixa no namespace ph-proton.\n"
        "DNS Proton 10.2.0.1. Este comando é apenas informativo; "
        "/phvpn on e /phvpn off não alteram a conexão."
    )


async def _ensure_vpn_route() -> None:
    """Fail closed: require VPN route inside the isolated network namespace."""
    proc = await asyncio.create_subprocess_exec(
        "ip", "-4", "-n", _VPN_NAMESPACE, "route", "get", "1.1.1.1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError("Não foi possível validar a rota VPN")
    route = stdout.decode("utf-8", errors="replace")
    if proc.returncode != 0 or f"dev {_VPN_INTERFACE}" not in route:
        raise RuntimeError("VPN indisponível: rota isolada não encontrada")


async def _download_video(
    url: str, directory: Path, *, prepare_preview: bool = True
) -> DownloadedVideo:
    await _ensure_vpn_route()
    cmd = [
        "ip", "netns", "exec", _VPN_NAMESPACE, _YTDLP_BIN,
        "-4",
        "--no-playlist",
        "--impersonate", "chrome",
        "--downloader", "m3u8:native",
        "--socket-timeout", "25",
        "--retries", "2",
        "--fragment-retries", "2",
        "--concurrent-fragments", "2",
        "--no-progress",
        "--write-info-json",
        "--max-filesize", str(_MAX_DOWNLOAD_BYTES),
        "-f", "best[height<=480]/best[height<=720]",
        "--merge-output-format", "mp4",
        "-o", str(directory / "%(id)s.%(ext)s"),
    ]
    cmd.append(url)

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=900)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise

    if process.returncode != 0:
        detail = output.decode("utf-8", errors="replace")[-600:]
        raise RuntimeError(f"yt-dlp exit code {process.returncode}: {detail}")

    files = [
        path for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in {".mp4", ".mkv", ".webm"}
    ]
    if not files:
        raise RuntimeError("yt-dlp não produziu arquivo de vídeo")
    video = max(files, key=lambda f: f.stat().st_size)
    if video.stat().st_size > _MAX_DOWNLOAD_BYTES:
        raise VideoTooLargeError("Arquivo acima do limite de teste")

    # Info JSON is downloaded by yt-dlp inside ph-proton and removed with the
    # temporary directory. Never fetch source metadata from the host network.
    info_path = next(iter(directory.glob("*.info.json")), None)
    info = {}
    if info_path is not None:
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("Pornhub metadata unavailable; using default caption")

    result = DownloadedVideo(
        path=video,
        description=_site_description(info),
        duration=_positive_int(info.get("duration")),
        width=_positive_int(info.get("width")),
        height=_positive_int(info.get("height")),
        views=_nonnegative_int(info.get("view_count")),
        likes=_nonnegative_int(info.get("like_count")),
        comments=_nonnegative_int(info.get("comment_count")),
        shares=_nonnegative_int(info.get("repost_count")),
    )
    if prepare_preview:
        await _probe_and_make_thumbnail(result)
    return result


async def _convert_video_to_mp3(source: Path, directory: Path) -> Path:
    """Offline ffmpeg conversion inside VPN-only namespace; never use host network."""
    target = directory / "audio.mp3"
    proc = await asyncio.create_subprocess_exec(
        "ip", "netns", "exec", _VPN_NAMESPACE,
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-y", "-i", str(source), "-map", "0:a:0", "-vn",
        "-c:a", "libmp3lame", "-q:a", "4",
        str(target),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, errors = await asyncio.wait_for(proc.communicate(), timeout=300)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        if proc.returncode is None:
            proc.kill()
        await proc.communicate()
        raise
    if proc.returncode != 0 or not target.is_file():
        # Log only the process status, not potentially sensitive media details.
        raise RuntimeError(f"MP3 conversion failed ({proc.returncode})")
    if target.stat().st_size > _MAX_DOWNLOAD_BYTES:
        raise VideoTooLargeError("MP3 acima do limite de teste")
    return target


def _video_identity(url: str) -> str:
    """The source viewkey is unique; generic request dedupe drops URL queries."""
    return parse_qs(urlsplit(url).query)["viewkey"][0]


def _cache_keys(url: str) -> tuple[str, str]:
    """Use viewkey rather than www/pt/m hostname as cache identity."""
    viewkey = _video_identity(url)
    base = f"pornhub:{viewkey}"
    return (
        build_media_cache_key(base, variant=f"video:{_CACHE_VARIANT}"),
        build_media_cache_key(base, variant=f"document:{_CACHE_VARIANT}"),
    )


def _audio_cache_key(url: str) -> str:
    return build_media_cache_key(
        f"pornhub:{_video_identity(url)}",
        variant=f"audio:mp3:{_CACHE_VARIANT}",
    )


def _build_video_buttons(
    url: str,
    settings: dict,
    *,
    lang: str = "pt",
    metadata: DownloadedVideo | None = None,
    as_document: bool = False,
):
    """Use the same keyboard/settings builder as the rest of SaveVid."""
    viewkey = _video_identity(url)
    return kb.return_video_info_keyboard(
        views=metadata.views if metadata else None,
        likes=metadata.likes if metadata else None,
        comments=metadata.comments if metadata else None,
        shares=metadata.shares if metadata else None,
        music_play_url=None,
        video_url=url,
        user_settings=settings,
        audio_callback_data=f"ph:mp3:{viewkey}",
        file_callback_data=None if as_document else f"ph:doc:{viewkey}",
        lang=lang,
        has_video=True,
        platform="pornhub",  # Music recognition is IG/TikTok-only by design.
    )


async def _reply_with_cached_file(
    message: types.Message,
    *,
    file_id: str,
    as_document: bool,
    caption: str,
    reply_markup=None,
) -> types.Message | None:
    """Reuse Telegram's file_id; invalid IDs fall back to the VPN download."""
    try:
        if as_document:
            return await message.answer_document(
                document=file_id,
                reply_markup=reply_markup,
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                allow_sending_without_reply=True,
            )
        return await message.answer_video(
            video=file_id,
            reply_markup=reply_markup,
            caption=caption or None,
            parse_mode=ParseMode.HTML,
            supports_streaming=True,
            allow_sending_without_reply=True,
        )
    except TelegramBadRequest:
        logger.warning("Pornhub cached Telegram file_id rejected; fetching via VPN")
        return None



@router.callback_query(
    F.data.startswith("ph:doc:") | F.data.startswith("ph:mp3:")
)
async def handle_pornhub_media_button(call: types.CallbackQuery):
    """Respect per-chat switches; all source downloads stay in ph-proton."""
    if not call.from_user or call.from_user.id != ADMIN_ID:
        await call.answer("Indisponível.", show_alert=True)
        return
    if not isinstance(call.message, types.Message) or not call.data:
        await call.answer()
        return

    parts = call.data.split(":", 2)
    if len(parts) != 3 or parts[1] not in {"doc", "mp3"}:
        await call.answer()
        return
    kind, viewkey = parts[1], parts[2]
    if not re.fullmatch(r"[A-Za-z0-9]{6,40}", viewkey):
        await call.answer()
        return

    message = call.message
    user_settings = await load_user_settings(db, message)
    lang = user_settings.get("language") or await db.get_language(
        call.from_user.id
    ) or "pt"
    if kind == "doc":
        permitted = (
            user_settings.get("file_button") == "on"
            and user_settings.get("as_document") != "on"
        )
    else:
        permitted = user_settings.get("audio_button") == "on"
    if not permitted:
        await call.answer(
            "Ative o botão nas configurações do bot."
            if str(lang).lower().startswith("pt") else
            "Enable this button in bot settings.",
            show_alert=True,
        )
        return
    await call.answer()
    url = f"https://www.pornhub.com/view_video.php?viewkey={viewkey}"
    _, doc_key = _cache_keys(url)
    cache_key = doc_key if kind == "doc" else _audio_cache_key(url)
    status = None
    try:
        status = await message.answer(
            bm.downloading_video_status(lang=lang),
            parse_mode=ParseMode.HTML,
        )
        bot_url = await get_bot_url(bot)
        footer = bm.captions(
            user_settings.get("captions", "on"), None, bot_url
        )
        async with _DOWNLOAD_LIMIT:
            cached_id = await db.get_file_id(cache_key)
            sent = None
            if cached_id:
                await safe_edit_text(
                    status, bm.uploading_status(lang=lang),
                    parse_mode=ParseMode.HTML,
                )
                try:
                    if kind == "doc":
                        sent = await message.answer_document(
                            document=cached_id, caption=footer,
                            parse_mode=ParseMode.HTML,
                            reply_markup=_build_video_buttons(
                                url, user_settings, lang=lang, as_document=True
                            ),
                            allow_sending_without_reply=True,
                        )
                    else:
                        sent = await message.answer_audio(
                            audio=cached_id, caption=footer,
                            parse_mode=ParseMode.HTML,
                            allow_sending_without_reply=True,
                        )
                    logger.info("Pornhub button media reused from shared cache: %s", kind)
                except TelegramBadRequest:
                    logger.warning("Pornhub button cached file_id rejected")

            if sent is None:
                with tempfile.TemporaryDirectory(prefix="pornhub-button-") as tmp:
                    directory = Path(tmp)
                    media = await _download_video(
                        url, directory, prepare_preview=False
                    )
                    await safe_edit_text(
                        status, bm.uploading_status(lang=lang),
                        parse_mode=ParseMode.HTML,
                    )
                    caption = bm.captions(
                        user_settings.get("captions", "on"),
                        media.description, bot_url,
                    )
                    if kind == "doc":
                        sent = await message.answer_document(
                            FSInputFile(media.path),
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                            reply_markup=_build_video_buttons(
                                url, user_settings, lang=lang,
                                metadata=media, as_document=True,
                            ),
                            allow_sending_without_reply=True,
                        )
                        if sent and sent.document:
                            await db.add_file(
                                cache_key, sent.document.file_id, "document"
                            )
                    else:
                        mp3 = await _convert_video_to_mp3(media.path, directory)
                        title = (media.description or "Audio").splitlines()[0][:100]
                        sent = await message.answer_audio(
                            audio=FSInputFile(mp3, filename=f"{viewkey}.mp3"),
                            title=title,
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                            allow_sending_without_reply=True,
                        )
                        if sent and sent.audio:
                            await db.add_file(
                                cache_key, sent.audio.file_id, "audio"
                            )
                    if sent:
                        logger.info("Pornhub button media cached in database: %s", kind)
    except VideoTooLargeError:
        await handle_video_too_large(message, lang=lang)
    except Exception as exc:
        logger.warning("Pornhub button failed: %s", type(exc).__name__)
        await handle_download_error(message, lang=lang)
    finally:
        await safe_delete_message(status)


@router.message(
    F.text.regexp(_LINK_PATTERN, mode="search")
    | F.caption.regexp(_LINK_PATTERN, mode="search")
)
async def download_pornhub_test(message: types.Message):
    # Admin-only until content access and age-verification requirements are reviewed.
    if not message.from_user or message.from_user.id != ADMIN_ID:
        return
    if await cmd_mod.is_banned(message.from_user.id, message.chat.id):
        return

    url = _extract_video_url(message.text or message.caption or "")
    if not url:
        return

    # Encode the viewkey into the path: generic dedupe otherwise drops the
    # query string and treats different Pornhub videos as the same request.
    dedupe_url = f"https://pornhub.com/video/{_video_identity(url)}"
    lease = await claim_message_request(
        message, service="pornhub_test", url=dedupe_url
    )
    if lease is None:
        return

    status = None
    user_lang = "pt"
    user_settings = {}
    try:
        # Reuse the same language, captions and status messages as the other handlers.
        user_settings = await load_user_settings(db, message)
        user_lang = user_settings.get("language") or await db.get_language(
            message.from_user.id
        ) or "pt"
        await react_to_message(message, "👾")
        status = await message.answer(
            bm.downloading_video_status(lang=user_lang),
            parse_mode=ParseMode.HTML,
        )

        video_cache_key, doc_cache_key = _cache_keys(url)
        as_document = user_settings.get("as_document") == "on"
        selected_cache_key = doc_cache_key if as_document else video_cache_key
        bot_url = await get_bot_url(bot)
        # The shared cache stores file IDs, not source-page metadata. Cached
        # sends keep the SaveVid footer and user's current caption settings.
        cached_caption = bm.captions(
            user_settings.get("captions", "on"), None, bot_url
        )

        # Check inside the semaphore: two requests for the same video will not
        # download it twice, even when they arrive from different chats.
        async with _DOWNLOAD_LIMIT:
            sent = None
            cached_id = await db.get_file_id(selected_cache_key)
            if cached_id:
                await safe_edit_text(
                    status, bm.uploading_status(lang=user_lang),
                    parse_mode=ParseMode.HTML,
                )
                await send_chat_action_if_needed(
                    bot, message.chat.id, ChatAction.UPLOAD_VIDEO, None
                )
                sent = await _reply_with_cached_file(
                    message,
                    file_id=cached_id,
                    as_document=as_document,
                    caption=cached_caption,
                    reply_markup=_build_video_buttons(
                        url, user_settings, lang=user_lang,
                        as_document=as_document,
                    ),
                )
                if sent:
                    logger.info(
                        "Pornhub Telegram media reused from shared cache: %s",
                        "document" if as_document else "video",
                    )

            if sent is None:
                with tempfile.TemporaryDirectory(prefix="pornhub-test-") as tmp:
                    media = await _download_video(url, Path(tmp))
                    file = media.path
                    await safe_edit_text(
                        status, bm.uploading_status(lang=user_lang),
                        parse_mode=ParseMode.HTML,
                    )
                    await send_chat_action_if_needed(
                        bot, message.chat.id, ChatAction.UPLOAD_VIDEO, None
                    )
                    caption = bm.captions(
                        user_settings.get("captions", "on"),
                        media.description, bot_url,
                    )

                    reply_markup = _build_video_buttons(
                        url, user_settings, lang=user_lang,
                        metadata=media, as_document=as_document,
                    )
                    if not as_document and file.suffix.lower() == ".mp4":
                        sent = await message.answer_video(
                            FSInputFile(file),
                            supports_streaming=True,
                            reply_markup=reply_markup,
                            duration=media.duration,
                            width=media.width,
                            height=media.height,
                            thumbnail=(
                                FSInputFile(media.thumbnail, filename="thumb.jpg")
                                if media.thumbnail else None
                            ),
                            cover=(
                                FSInputFile(media.thumbnail, filename="cover.jpg")
                                if media.thumbnail else None
                            ),
                            caption=caption or None,
                            parse_mode=ParseMode.HTML,
                            allow_sending_without_reply=True,
                        )
                    else:
                        sent = await message.answer_document(
                            FSInputFile(file),
                            reply_markup=reply_markup,
                            caption=caption or None,
                            parse_mode=ParseMode.HTML,
                            allow_sending_without_reply=True,
                        )

                    if sent and sent.video:
                        await db.add_file(
                            video_cache_key, sent.video.file_id, "video"
                        )
                        logger.info("Pornhub Telegram video cached in database")
                    elif sent and sent.document:
                        await db.add_file(
                            doc_cache_key, sent.document.file_id, "document"
                        )
                        logger.info("Pornhub Telegram document cached in database")

            if sent:
                lease.mark_success()
                await safe_delete_message(status)
                status = None
                await maybe_delete_user_message(
                    message, user_settings.get("delete_message")
                )
    except VideoTooLargeError:
        logger.warning("Pornhub test failed: VideoTooLargeError")
        await safe_delete_message(status)
        await handle_video_too_large(message, lang=user_lang)
    except Exception as exc:
        # Avoid logging credentials, URLs or possibly sensitive page titles.
        logger.warning("Pornhub test failed: %s", type(exc).__name__)
        await safe_delete_message(status)
        await handle_download_error(message, lang=user_lang)
    finally:
        lease.finish()
