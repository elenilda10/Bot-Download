"""Handle public MediaFire links in Telegram messages."""
import asyncio
import re
import tempfile
from pathlib import Path

from aiogram import F, Router, types
from aiogram.types import FSInputFile
from handlers import commands as cmd_mod
from handlers.request_dedupe import claim_message_request
from services.platforms.mediafire import download_mediafire, is_mediafire_url

router = Router(name="mediafire")
URL = re.compile(r"https://[^\s<>]+", re.I)
LIMIT = asyncio.Semaphore(2)

@router.message(F.text.regexp(r"https://(?:www\.|m\.)?mediafire\.com/", mode="search"))
async def handle_mediafire(message: types.Message):
    if not message.from_user or await cmd_mod.is_banned(message.from_user.id, message.chat.id):
        return
    url = next((u for raw in URL.findall(message.text or "")
                if is_mediafire_url(u := raw.rstrip(".,;!)]"))), None)
    if not url:
        return
    lease = await claim_message_request(message, service="mediafire", url=url)
    if lease is None:
        return
    status = await message.reply("⏳ Baixando arquivo do MediaFire...")
    try:
        async with LIMIT:
            with tempfile.TemporaryDirectory(prefix="mediafire-") as directory:
                file = await download_mediafire(url, Path(directory))
                await status.edit_text("📤 Enviando arquivo ao Telegram...")
                await message.reply_document(FSInputFile(file))
        await status.delete()
    except Exception:
        await status.edit_text("❌ Não foi possível baixar ou enviar esse arquivo do MediaFire.")
