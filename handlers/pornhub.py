"""Admin-only PornHub downloader in isolated Proton WireGuard network namespace.

All yt-dlp and helper processes run within ph-proton, which has only a VPN
interface, DNS 10.2.0.1 and no eth0 route. Tunnel failure never uses Contabo.
The bot's other handlers continue to run in the original host namespace.
"""
import asyncio
import logging
import re
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from aiogram import F, Router, types
from aiogram.enums import ChatAction, ParseMode
from aiogram.types import FSInputFile

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

router = Router(name="pornhub_test")
logger = logging.getLogger(__name__)
_DOWNLOAD_LIMIT = asyncio.Semaphore(1)
_URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
_LINK_PATTERN = r"(?i)https?://(?:(?:www|pt|m)\.)?pornhub\.com/view_video\.php\?viewkey="
_VPN_INTERFACE = "protonbot"
_VPN_NAMESPACE = "ph-proton"
_MAX_DOWNLOAD_BYTES = min(MAX_FILE_SIZE, 450 * 1024 * 1024)
_YTDLP_BIN = "/root/Bot-Download/venv/bin/yt-dlp"


class VideoTooLargeError(Exception):
    """The downloaded video exceeds the current bot's media size limit."""


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
    await message.reply(
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


async def _download_video(url: str, directory: Path) -> Path:
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
    return video


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

    lease = await claim_message_request(message, service="pornhub_test", url=url)
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

        async with _DOWNLOAD_LIMIT:
            with tempfile.TemporaryDirectory(prefix="pornhub-test-") as tmp:
                file = await _download_video(url, Path(tmp))
                await safe_edit_text(
                    status, bm.uploading_status(lang=user_lang), parse_mode=ParseMode.HTML
                )
                await send_chat_action_if_needed(
                    bot, message.chat.id, ChatAction.UPLOAD_VIDEO, None
                )
                bot_url = await get_bot_url(bot)
                caption = bm.captions(
                    user_settings.get("captions", "on"), None, bot_url
                )

                if file.suffix.lower() == ".mp4":
                    sent = await message.reply_video(
                        FSInputFile(file),
                        supports_streaming=True,
                        caption=caption or None,
                        parse_mode=ParseMode.HTML,
                        allow_sending_without_reply=True,
                    )
                else:
                    sent = await message.reply_document(
                        FSInputFile(file),
                        caption=caption or None,
                        parse_mode=ParseMode.HTML,
                        allow_sending_without_reply=True,
                    )

                if sent:
                    lease.mark_success()
                await safe_delete_message(status)
                status = None
                if sent:
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
