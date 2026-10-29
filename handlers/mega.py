"""Handle public MEGA links in Telegram messages."""
import asyncio
import hashlib
import logging
import re
import tempfile
from pathlib import Path

from aiogram import F, Router, types
from aiogram.types import FSInputFile
from aiogram.exceptions import TelegramBadRequest
from utils.media_cache import build_media_cache_key

import messages as bm
from app_context import bot, db
from handlers import commands as cmd_mod
from handlers.request_dedupe import claim_message_request
from handlers.utils import get_bot_url
from handlers.telegram_ui_utils import react_to_message
from services.platforms.mega import download_mega, is_mega_url

router = Router(name="mega")
URL = re.compile(r"https://[^\s<>]+", re.I)
LIMIT = asyncio.Semaphore(2)
logger = logging.getLogger(__name__)


@router.message(
    F.text.regexp(
        r"https://(?:www\.)?mega\.nz/file/",
        mode="search",
    )
)
async def handle_mega(message: types.Message):
    if not message.from_user:
        return

    if await cmd_mod.is_banned(
        message.from_user.id, message.chat.id
    ):
        return

    url = next(
        (
            u
            for raw in URL.findall(message.text or "")
            if is_mega_url(u := raw.rstrip(".,;!)]"))
        ),
        None,
    )
    if not url:
        return

    lease = await claim_message_request(
        message, service="mega", url=url
    )
    if lease is None:
        return

    await react_to_message(message, "👾")

    status = None
    etapa = "inicializacao"

    try:
        lang = await db.get_language(message.from_user.id) or "pt"

        url_digest = hashlib.sha256(
            url.encode("utf-8")
        ).hexdigest()

        cache_key = build_media_cache_key(
            f"mega:{url_digest}",
            variant="document",
        )

        etapa = "consulta_cache"
        cached_file_id = await db.get_file_id(cache_key)

        if cached_file_id:
            try:
                await message.answer_document(
                    allow_sending_without_reply=True,
                    document=cached_file_id,
                    caption=(
                        "📄 Arquivo baixado do MEGA"
                        if str(lang).lower().startswith("pt")
                        else "📄 File downloaded from MEGA"
                    ),
                    parse_mode="HTML",
                )
            except TelegramBadRequest:
                pass
            else:
                lease.mark_success()
                return

        etapa = "mensagem_status"
        status = await message.answer(
            bm.downloading_file_status(lang, platform='MEGA'),
            allow_sending_without_reply=True,
            parse_mode="HTML",
        )

        async with LIMIT:
            with tempfile.TemporaryDirectory(
                prefix="mega-"
            ) as directory:
                etapa = "download"
                file = await download_mega(
                    url, Path(directory)
                )

                etapa = "atualizacao_status"
                await status.edit_text(
                    bm.uploading_file_status(lang, platform='MEGA'),
                    parse_mode="HTML",
                )

                descricao = (
                    "Arquivo baixado do MEGA"
                    if str(lang).lower().startswith("pt")
                    else "File downloaded from MEGA"
                )

                caption = bm.captions(
                    "on",
                    descricao,
                    await get_bot_url(bot),
                )

                etapa = "envio_telegram"
                sent = await message.answer_document(
                    FSInputFile(file),
                    allow_sending_without_reply=True,
                    caption=caption,
                    parse_mode="HTML",
                )

                lease.mark_success()

                if sent and sent.document:
                    etapa = "salvar_cache"
                    await db.add_file(
                        cache_key,
                        sent.document.file_id,
                        "document",
                    )

        try:
            await status.delete()
        except Exception:
            pass

    except Exception as exc:
        logger.error(
            "Falha MEGA etapa=%s tipo=%s",
            etapa,
            type(exc).__name__,
        )
        if status is not None:
            try:
                await status.edit_text(
                    "❌ Não foi possível baixar ou enviar "
                    "esse arquivo do MEGA."
                )
            except Exception:
                pass
    finally:
        lease.finish()
