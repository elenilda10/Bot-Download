"""Handle public MediaFire links in Telegram messages."""
import asyncio
import hashlib
import re
import tempfile
from pathlib import Path

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile

import messages as bm
from app_context import bot, db
from handlers.utils import get_bot_url
from handlers.telegram_ui_utils import react_to_message
from handlers import commands as cmd_mod
from handlers.request_dedupe import claim_message_request
from services.platforms.mediafire import download_mediafire, is_mediafire_url
from utils.media_cache import build_media_cache_key

router = Router(name="mediafire")
URL = re.compile(r"https://[^\s<>]+", re.I)
LIMIT = asyncio.Semaphore(2)


@router.message(
    F.text.regexp(
        r"https://(?:www\.|m\.)?mediafire\.com/",
        mode="search",
    )
)
async def handle_mediafire(message: types.Message):
    if not message.from_user:
        return

    if await cmd_mod.is_banned(message.from_user.id, message.chat.id):
        return

    url = next(
        (
            u
            for raw in URL.findall(message.text or "")
            if is_mediafire_url(u := raw.rstrip(".,;!)]"))
        ),
        None,
    )
    if not url:
        return

    lease = await claim_message_request(
        message, service="mediafire", url=url
    )
    if lease is None:
        return

    await react_to_message(message, "👾")

    status = None

    try:
        lang = await db.get_language(message.from_user.id) or "pt"

        descricao = (
            "Arquivo baixado do MediaFire"
            if str(lang).lower().startswith("pt")
            else "File downloaded from MediaFire"
        )

        caption = bm.captions(
            "on",
            descricao,
            await get_bot_url(bot),
        )
        caption = caption.replace(
            descricao,
            bm.mediafire_document_caption(lang),
            1,
        )

        url_digest = hashlib.sha256(
            url.encode("utf-8")
        ).hexdigest()
        cache_key = build_media_cache_key(
            f"mediafire:{url_digest}",
            variant="document",
        )

        cached_file_id = await db.get_file_id(cache_key)

        if cached_file_id:
            try:
                await message.reply_document(
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

        status = await message.reply(
            bm.downloading_file_status(lang),
            allow_sending_without_reply=True,
            parse_mode="HTML",
        )

        async with LIMIT:
            with tempfile.TemporaryDirectory(
                prefix="mediafire-"
            ) as directory:
                file = await download_mediafire(
                    url, Path(directory)
                )

                await status.edit_text(
                    bm.uploading_file_status(lang),
                    parse_mode="HTML",
                )

                sent = await message.reply_document(
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
                    "esse arquivo do MediaFire."
                )
            except Exception:
                pass
    finally:
        lease.finish()
