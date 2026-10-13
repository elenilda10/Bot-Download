"""Telegram handler for public MEGA, MediaFire and Google Drive files."""
from __future__ import annotations
import asyncio
import re
import tempfile
from pathlib import Path
from aiogram import F, Router, types
from aiogram.types import FSInputFile
from handlers import commands as cmd_mod
from handlers.request_dedupe import claim_message_request
from services.platforms.cloud_files import download_cloud_file, provider_for

router = Router(name="cloud_files")
URL = re.compile(r"https://[^\s<>]+", re.I)
LIMIT = asyncio.Semaphore(2)

@router.message(F.text.regexp(r"https://(?:www\.|m\.)?(?:mega\.nz|mega\.co\.nz|mediafire\.com|drive\.google\.com|docs\.google\.com)/", mode="search"))
async def handle_cloud_file(message: types.Message):
    if not message.from_user or await cmd_mod.is_banned(message.from_user.id, message.chat.id):
        return
    matches = URL.findall(message.text or "")
    selected = next(((provider_for(u.rstrip('.,;!)]')) , u.rstrip('.,;!)]')) for u in matches if provider_for(u.rstrip('.,;!)]'))), None)
    if not selected:
        return
    provider, url = selected
    lease = await claim_message_request(message, service="cloud_files", url=url)
    if lease is None:
        return
    status = await message.reply(f"⏳ Baixando arquivo do {provider.upper()}...")
    try:
        async with LIMIT:
            with tempfile.TemporaryDirectory(prefix="cloud-file-") as directory:
                file = await download_cloud_file(provider, url, Path(directory))
                await status.edit_text("📤 Enviando arquivo ao Telegram...")
                await message.reply_document(FSInputFile(file))
        await status.delete()
    except Exception as exc:
        await status.edit_text(f"❌ Não foi possível baixar o arquivo: {str(exc)[:180]}")
