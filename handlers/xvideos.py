"""Route XVideos video links to the site's public HLS/MP4 downloader."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from aiogram import F, Router, types
from aiogram.enums import ChatAction
from aiogram.types import FSInputFile

import messages as bm
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
from services.platforms.xvideos_media import LINK_RE, download_xvideos, extract_xvideos_url

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
            status = await message.answer(bm.downloading_video_status(lang=language))
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
        async with LIMIT:
            with tempfile.TemporaryDirectory(prefix="xvideos-") as directory:
                media = await download_xvideos(page, Path(directory))
                if status:
                    await safe_edit_text(status, bm.uploading_status(lang=language))
                label = f"XVideos • {media.height}p"
                caption = bm.captions(
                    settings.get("captions", "on"), label, await get_bot_url(bot)
                )
                await message.reply_video(
                    video=FSInputFile(media.path),
                    caption=caption or None,
                    parse_mode="HTML",
                    width=media.width,
                    height=media.height,
                    duration=media.duration or None,
                    supports_streaming=True,
                    allow_sending_without_reply=True,
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
