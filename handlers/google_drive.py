"""Handle public Google Drive file links."""
import asyncio
import re
import tempfile
from pathlib import Path

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile

import messages as bm
from app_context import bot, db
from handlers import commands as cmd_mod
from handlers.request_dedupe import claim_message_request
from handlers.utils import get_bot_url
from handlers.telegram_ui_utils import react_to_message
from services.platforms.google_drive import (
    download_google_drive,
    extract_drive_id,
)
from utils.media_cache import build_media_cache_key

router = Router(name="google_drive")
URL = re.compile(r"https://[^\s<>]+", re.I)
LIMIT = asyncio.Semaphore(2)


@router.message(
    F.text.regexp(
        r"https://(?:drive\.google\.com|drive\.usercontent\.google\.com)/",
        mode="search",
    )
)
async def handle_google_drive(message: types.Message):
    if not message.from_user:
        return

    if await cmd_mod.is_banned(
        message.from_user.id, message.chat.id
    ):
        return

    file_id = None

    for raw in URL.findall(message.text or ""):
        candidate = raw.rstrip(".,;!)]")
        file_id = extract_drive_id(candidate)
        if file_id:
            break

    if not file_id:
        return

    lease = await claim_message_request(
        message,
        service="google_drive",
        url=file_id,
    )
    if lease is None:
        return

    await react_to_message(message, "👾")

    status = None

    try:
        lang = await db.get_language(
            message.from_user.id
        ) or "pt"

        descricao = (
            "Arquivo baixado do Google Drive"
            if str(lang).lower().startswith("pt")
            else "File downloaded from Google Drive"
        )

        caption = bm.captions(
            "on",
            descricao,
            await get_bot_url(bot),
        )

        cache_key = build_media_cache_key(
            f"drive:{file_id}",
            variant="document",
        )

        cached_file_id = await db.get_file_id(cache_key)

        if cached_file_id:
            try:
                await message.answer_document(
                    allow_sending_without_reply=True,
                    document=cached_file_id,
                    caption=caption,
                    parse_mode="HTML",
                )
            except TelegramBadRequest:
                pass
            else:
                lease.mark_success()
                return

        status = await message.answer(
            bm.downloading_file_status(lang, platform='Google Drive'),
            allow_sending_without_reply=True,
            parse_mode="HTML",
        )

        async with LIMIT:
            with tempfile.TemporaryDirectory(
                prefix="google-drive-"
            ) as directory:
                file = await download_google_drive(
                    file_id,
                    Path(directory),
                )

                await status.edit_text(
                    bm.uploading_file_status(lang, platform='Google Drive'),
                    parse_mode="HTML",
                )

                sent = await message.answer_document(
                    FSInputFile(file),
                    allow_sending_without_reply=True,
                    caption=caption,
                    parse_mode="HTML",
                )

                lease.mark_success()

                if sent and sent.document:
                    await db.add_file(
                        cache_key,
                        sent.document.file_id,
                        "document",
                    )

        try:
            await status.delete()
        except Exception:
            pass

    except Exception:
        if status is not None:
            try:
                await status.edit_text(
                    "❌ Não foi possível baixar ou enviar "
                    "esse arquivo do Google Drive."
                )
            except Exception:
                pass
    finally:
        lease.finish()
