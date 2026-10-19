"""Admin-only PornHub download trial with manually selectable VPN route.

This experimental handler never starts or stops WireGuard. Switching /phvpn on
binds yt-dlp requests to a preconfigured Proton WireGuard source address.
The default is off. Access stays restricted to the configured bot admin.
"""
import asyncio
import logging
import os
import re
import tempfile
from contextlib import suppress
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from aiogram import F, Router, types
from aiogram.types import FSInputFile

from config import ADMIN_ID, MAX_FILE_SIZE
from handlers import commands as cmd_mod
from handlers.request_dedupe import claim_message_request

router = Router(name="pornhub_test")
logger = logging.getLogger(__name__)
_DOWNLOAD_LIMIT = asyncio.Semaphore(1)
_URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
_LINK_PATTERN = r"(?i)https?://(?:(?:www|pt|m)\.)?pornhub\.com/view_video\.php\?viewkey="
_VPN_ENABLED = os.getenv("PORNHUB_TEST_USE_VPN", "0").strip() == "1"
_VPN_ADDRESS = os.getenv("PORNHUB_TEST_VPN_SOURCE", "10.2.0.2")
_MAX_DOWNLOAD_BYTES = min(MAX_FILE_SIZE, 450 * 1024 * 1024)
_YTDLP_BIN = "/root/Bot-Download/venv/bin/yt-dlp"


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
async def set_pornhub_test_vpn(message: types.Message):
    global _VPN_ENABLED
    if not message.from_user or message.from_user.id != ADMIN_ID:
        return
    parts = (message.text or "").strip().split()
    option = parts[1].lower() if len(parts) > 1 else "status"
    if option in ("on", "ligar"):
        _VPN_ENABLED = True
    elif option in ("off", "desligar"):
        _VPN_ENABLED = False
    elif option != "status":
        await message.reply("Use /phvpn on, /phvpn off ou /phvpn status.")
        return
    mode = "VPN via protonbot" if _VPN_ENABLED else "Contabo (sem VPN)"
    await message.reply(
        f"Teste Pornhub: {mode}.\n"
        "Este comando altera apenas o modo do downloader; não liga/desliga o WireGuard."
    )


async def _download_video(url: str, directory: Path, vpn: bool) -> Path:
    cmd = [
        _YTDLP_BIN,
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
    if vpn:
        cmd.extend(["--source-address", _VPN_ADDRESS])
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
        raise RuntimeError("Arquivo acima do limite de teste")
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
    try:
        vpn = _VPN_ENABLED  # snapshot: switching modes won't move an ongoing download
        mode = "VPN" if vpn else "Contabo"
        status = await message.reply(f"⏳ Baixando vídeo de teste via {mode}…")
        async with _DOWNLOAD_LIMIT:
            with tempfile.TemporaryDirectory(prefix="pornhub-test-") as tmp:
                file = await _download_video(url, Path(tmp), vpn=vpn)
                with suppress(Exception):
                    await status.edit_text("🚀 Enviando vídeo ao Telegram…")

                if file.suffix.lower() == ".mp4":
                    sent = await message.reply_video(
                        FSInputFile(file),
                        supports_streaming=True,
                        caption=f"✅ Vídeo enviado · conexão: {mode}",
                        allow_sending_without_reply=True,
                    )
                else:
                    sent = await message.reply_document(
                        FSInputFile(file),
                        caption=f"✅ Vídeo enviado · conexão: {mode}",
                        allow_sending_without_reply=True,
                    )
                if sent:
                    lease.mark_success()
                if status is not None:
                    with suppress(Exception):
                        await status.delete()
    except Exception as exc:
        # Avoid logging credentials, URLs, or possibly sensitive page titles.
        logger.warning("Pornhub test failed: %s", type(exc).__name__)
        if status is not None:
            with suppress(Exception):
                await status.edit_text(
                    "❌ Não foi possível baixar/enviar esse vídeo. "
                    "Confira o modo com /phvpn status e os logs da VPS."
                )
    finally:
        lease.finish()
