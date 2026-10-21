"""Botões XVideos com links persistentes e cache."""
from __future__ import annotations

import asyncio
import hashlib
import re
import tempfile
from pathlib import Path

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile

import keyboards as kb
import messages as bm
from app_context import bot, db
from config import MAX_FILE_SIZE
from handlers import commands as cmd_mod
from handlers.utils import get_bot_url, safe_delete_message, safe_edit_text
from services.logger import logger as logging
from services.platforms.xvideos_media import (
    download_xvideos, extract_xvideos_url, get_xvideos_title,
)
from services.settings import normalize_setting_value

router = Router(name="xvideos_buttons")
LIMIT = asyncio.Semaphore(2)


def link_id(page: str) -> str:
    return hashlib.sha256(page.encode()).hexdigest()[:24]


def media_key(page: str, quality: str, kind: str) -> str:
    digest = hashlib.sha256(page.encode()).hexdigest()
    return f"xvideos:{digest}:{quality}:{kind}"


async def build_xvideos_buttons(page, settings, language):
    actions = (
        settings.get("audio_button") == "on"
        or (
            settings.get("file_button") == "on"
            and settings.get("as_document") != "on"
        )
    )

    ready = False
    if actions:
        key = f"xvideos:source:{link_id(page)}"
        await db.add_file(key, page, "source_url")
        ready = await db.get_file_id(key) == page

    return kb.return_video_info_keyboard(
        views=None,
        likes=None,
        comments=None,
        shares=None,
        music_play_url=None,
        video_url=page,
        user_settings=settings,
        audio_callback_data=(
            f"xv:a:{link_id(page)}" if ready else None
        ),
        file_callback_data=(
            f"xv:d:{link_id(page)}" if ready else None
        ),
        lang=language or "pt",
        platform="xvideos",
    )


async def convert_mp3(video: Path, folder: Path) -> Path:
    output = folder / "audio.mp3"

    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostdin", "-hide_banner",
        "-loglevel", "error", "-y",
        "-i", str(video),
        "-map", "0:a:0", "-vn",
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        str(output),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    try:
        await asyncio.wait_for(process.communicate(), timeout=360)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise

    if (
        process.returncode != 0
        or not output.is_file()
        or not 0 < output.stat().st_size <= MAX_FILE_SIZE
    ):
        raise RuntimeError("XVideos MP3 conversion failed")

    return output


@router.callback_query(F.data.startswith("xv:"))
async def handle_xvideos_button(call: types.CallbackQuery):
    parts = (call.data or "").split(":")
    message = call.message

    if (
        len(parts) != 3
        or parts[1] not in ("a", "d")
        or not re.fullmatch(r"[0-9a-f]{24}", parts[2])
    ):
        await call.answer("Botão inválido.", show_alert=True)
        return

    if not message or not callable(
        getattr(message, "reply_document", None)
    ):
        await call.answer("Mensagem indisponível.", show_alert=True)
        return

    if await cmd_mod.is_banned(
        call.from_user.id, message.chat.id
    ):
        await call.answer()
        return

    page = await db.get_file_id(
        f"xvideos:source:{parts[2]}"
    )

    if (
        not isinstance(page, str)
        or extract_xvideos_url(page) != page
        or link_id(page) != parts[2]
    ):
        await call.answer(
            "Link expirado. Envie o link original novamente.",
            show_alert=True,
        )
        return

    await call.answer()

    target = (
        message.chat.id
        if message.chat.type != "private"
        else call.from_user.id
    )
    settings = await db.user_settings(target)
    language = (
        settings.get("language")
        or await db.get_language(call.from_user.id)
    )
    quality = (
        normalize_setting_value(settings.get("video_quality"))
        or "best"
    )

    kind = "audio:mp3" if parts[1] == "a" else "document"
    key = media_key(page, quality, kind)

    caption = None
    if settings.get("captions") == "on":
        try:
            title = await asyncio.wait_for(
                get_xvideos_title(page), timeout=35
            )
        except Exception:
            title = None

        caption = bm.captions(
            "on",
            title or "XVideos",
            await get_bot_url(bot),
        )

    status = None

    try:
        async with LIMIT:
            status = await message.answer(
                bm.downloading_video_status(lang=language)
            )

            cached = await db.get_file_id(key)
            sent = None

            if cached:
                try:
                    if parts[1] == "a":
                        sent = await message.answer_audio(
                            audio=cached,
                            caption=caption,
                            parse_mode="HTML",
                            allow_sending_without_reply=True,
                        )
                    else:
                        sent = await message.answer_document(
                            document=cached,
                            caption=caption,
                            parse_mode="HTML",
                            disable_content_type_detection=True,
                            allow_sending_without_reply=True,
                        )
                except TelegramBadRequest:
                    sent = None

            if sent is None:
                with tempfile.TemporaryDirectory(
                    prefix="xvideos-action-"
                ) as tmp:
                    folder = Path(tmp)
                    media = await download_xvideos(
                        page, folder, video_quality=quality
                    )

                    await safe_edit_text(
                        status,
                        bm.uploading_status(lang=language),
                    )

                    if parts[1] == "a":
                        audio = await convert_mp3(
                            media.path, folder
                        )
                        sent = await message.answer_audio(
                            audio=FSInputFile(audio),
                            caption=caption,
                            parse_mode="HTML",
                            allow_sending_without_reply=True,
                        )
                        attachment = sent.audio
                    else:
                        sent = await message.answer_document(
                            document=FSInputFile(media.path),
                            caption=caption,
                            parse_mode="HTML",
                            disable_content_type_detection=True,
                            allow_sending_without_reply=True,
                        )
                        attachment = sent.document

                    if attachment and attachment.file_id:
                        await db.add_file(
                            key, attachment.file_id, kind
                        )

    except Exception as exc:
        logging.warning(
            "XVideos callback failed: %s",
            type(exc).__name__,
        )
        await message.answer(
            "Não foi possível processar este arquivo agora."
        )
    finally:
        if status:
            await safe_delete_message(status)
