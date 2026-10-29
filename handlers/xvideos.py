"""Route XVideos video links to the site's public HLS/MP4 downloader."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from aiogram import F, Router, types
from aiogram.enums import ChatAction
from aiogram.types import FSInputFile
from aiogram.exceptions import TelegramBadRequest
import hashlib
import keyboards as kb
from services.settings import normalize_setting_value

import messages as bm
from handlers.xvideos_actions import build_xvideos_buttons
from app_context import bot, db, send_analytics
from handlers import commands as cmd_mod
from handlers.request_dedupe import claim_message_request
from handlers.utils import (
    get_bot_url,
    get_message_text,
    handle_download_error,
    load_user_settings,
    maybe_delete_user_message,
    safe_delete_message,
    safe_edit_text,
    should_skip_duplicate_business_message,
)
from services.logger import logger as logging
from services.platforms.xvideos_media import LINK_RE, download_xvideos, extract_xvideos_url, get_xvideos_title


async def _make_xvideos_thumbnail(video: Path) -> Path | None:
    """Cria uma thumbnail JPEG compativel com o Telegram."""
    capa = video.with_name(video.stem + "_thumb.jpg")
    processo = None

    try:
        processo = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-ss", "00:00:04",
            "-i", str(video),
            "-frames:v", "1",
            "-vf", "scale=320:320:force_original_aspect_ratio=decrease",
            "-q:v", "4",
            str(capa),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        await asyncio.wait_for(processo.communicate(), timeout=25)

        if (
            processo.returncode == 0
            and capa.is_file()
            and 0 < capa.stat().st_size <= 200 * 1024
        ):
            return capa

    except Exception:
        if processo is not None and processo.returncode is None:
            processo.kill()
            await processo.wait()

    capa.unlink(missing_ok=True)
    return None



def _xv_cache_key(
    page: str,
    quality: str,
    kind: str,
) -> str:
    digest = hashlib.sha256(
        page.encode("utf-8")
    ).hexdigest()

    return f"xvideos:{digest}:{quality}:{kind}"


router = Router(name="xvideos")
LIMIT = asyncio.Semaphore(2)


@router.message(F.text.regexp(LINK_RE.pattern, mode="search") | F.caption.regexp(LINK_RE.pattern, mode="search"))
@router.business_message(F.text.regexp(LINK_RE.pattern, mode="search") | F.caption.regexp(LINK_RE.pattern, mode="search"))
async def handle_xvideos(message: types.Message) -> None:
    if not message.from_user or not message.chat:
        return
    if await cmd_mod.is_banned(message.from_user.id, message.chat.id):
        return
    if await should_skip_duplicate_business_message(
        message, bot, service_name="XVideos", logger=logging
    ):
        return

    page = extract_xvideos_url(get_message_text(message))
    if not page:
        return
    lease = await claim_message_request(message, service="xvideos", url=page)
    if lease is None:
        return

    status = None
    business_id = getattr(message, "business_connection_id", None)
    try:
        settings = await load_user_settings(db, message)
        language = settings.get("language") or await db.get_language(message.from_user.id)
        if not business_id:
            status = await message.answer(bm.downloading_video_status(lang=language, platform='Xvideos'))
        await bot.send_chat_action(
            chat_id=message.chat.id,
            action=ChatAction.UPLOAD_VIDEO,
            business_connection_id=business_id,
        )
        await send_analytics(
            user_id=message.from_user.id,
            chat_type=message.chat.type,
            action_name="xvideos",
        )

        qualidade = (
            normalize_setting_value(settings.get("video_quality"))
            or "best"
        )
        como_arquivo = settings.get("as_document") == "on"
        tipo = "document" if como_arquivo else "video"

        cache_key = _xv_cache_key(page, qualidade, tipo)

        keyboard = await build_xvideos_buttons(
            page, settings, language
        )

        caption = None
        if settings.get("captions", "on") == "on":
            try:
                titulo = await asyncio.wait_for(
                    get_xvideos_title(page), timeout=35
                )
            except Exception:
                titulo = None

            caption = bm.captions(
                "on",
                titulo or "XVideos",
                await get_bot_url(bot),
            )

        async with LIMIT:
            cached_id = await db.get_file_id(cache_key)
            enviado = None

            if cached_id:
                try:
                    if status:
                        await safe_edit_text(
                            status,
                            bm.uploading_status(lang=language, platform='Xvideos'),
                        )

                    if como_arquivo:
                        enviado = await message.answer_document(
                            document=cached_id,
                            caption=caption,
                            parse_mode="HTML",
                            reply_markup=keyboard,
                            disable_content_type_detection=True,
                            allow_sending_without_reply=True,
                        )
                    else:
                        enviado = await message.answer_video(
                            video=cached_id,
                            caption=caption,
                            parse_mode="HTML",
                            reply_markup=keyboard,
                            supports_streaming=True,
                            allow_sending_without_reply=True,
                        )

                except TelegramBadRequest:
                    enviado = None

            if enviado is None:
                with tempfile.TemporaryDirectory(
                    prefix="xvideos-"
                ) as directory:

                    media = await download_xvideos(
                        page,
                        Path(directory),
                        video_quality=qualidade,
                    )

                    if status:
                        await safe_edit_text(
                            status,
                            bm.uploading_status(lang=language, platform='Xvideos'),
                        )

                    if como_arquivo:
                        enviado = await message.answer_document(
                            document=FSInputFile(media.path),
                            caption=caption,
                            parse_mode="HTML",
                            reply_markup=keyboard,
                            disable_content_type_detection=True,
                            allow_sending_without_reply=True,
                        )
                    else:
                        capa = await _make_xvideos_thumbnail(
                            media.path
                        )

                        enviado = await message.answer_video(
                            video=FSInputFile(media.path),
                            thumbnail=(
                                FSInputFile(capa)
                                if capa else None
                            ),
                            caption=caption,
                            parse_mode="HTML",
                            reply_markup=keyboard,
                            width=media.width,
                            height=media.height,
                            duration=media.duration or None,
                            supports_streaming=True,
                            allow_sending_without_reply=True,
                        )

                    arquivo_enviado = (
                        enviado.document
                        if como_arquivo
                        else enviado.video
                    )

                    if (
                        arquivo_enviado
                        and arquivo_enviado.file_id
                    ):
                        await db.add_file(
                            cache_key,
                            arquivo_enviado.file_id,
                            tipo,
                        )
        lease.mark_success()
        await maybe_delete_user_message(message, settings.get("delete_message"))
    except Exception as exc:
        # Source URLs and short-lived CDN tokens must never be written to logs.
        logging.warning("XVideos request failed: %s", type(exc).__name__)
        await handle_download_error(message, business_id=business_id)
    finally:
        if status:
            await safe_delete_message(status)
        lease.finish()
