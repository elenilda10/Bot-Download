"""Dedicated Facebook Telegram flow: resolve, download, caption and send.

Facebook-specific downloads never enter the Universal message handler. Shared
download binaries are used only by the services/platforms layer. Firefox
cookies are opt-in for the administrator's ordinary private conversation.
No Facebook content is stored in the shared Telegram file_id cache.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import time
from typing import Optional

from aiogram import F, Router, types
from aiogram.enums import ChatAction, ParseMode
from aiogram.types import (
    FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto, InputMediaVideo,
)

import messages as bm
from app_context import bot, db, send_analytics
from config import OUTPUT_DIR, MAX_FILE_SIZE, ADMIN_ID
from handlers import commands as cmd_mod
from handlers.request_dedupe import claim_message_request
from handlers.utils import (
    get_bot_url, get_message_text, handle_download_error,
    load_user_settings, maybe_delete_user_message, react_to_message,
    safe_delete_message, safe_edit_text, should_skip_duplicate_business_message,
)
from services.logger import logger as logging
from services.platforms.facebook_media import (
    FACEBOOK_LINK_REGEX, _is_facebook_url,
    _resolve_facebook_short_link, download_facebook_media,
)

router = Router(name="facebook_handler")
logging = logging.bind(service="facebook")

_LINK_PATTERN = re.compile(FACEBOOK_LINK_REGEX, re.IGNORECASE)
_PROGRESS: dict[str, dict] = {}


def _extract_facebook_link(message_text: str) -> str | None:
    """Only extract genuine Facebook hosts."""
    for match in _LINK_PATTERN.finditer(message_text or ""):
        candidate = match.group(0).rstrip(".,;!?)>]}")
        if _is_facebook_url(candidate):
            return candidate
    return None


def _facebook_keyboard(url: str, lang: str | None) -> InlineKeyboardMarkup:
    portuguese = not lang or lang.lower().startswith("pt")
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🔗 Abrir no Facebook" if portuguese else "🔗 Open Facebook",
            url=url,
        )
    ]])


def _progress_update(key: str, downloaded: int, total: int | None, speed: float | None):
    info = _PROGRESS.get(key)
    if not info:
        return
    now = time.monotonic()
    elapsed = max(now - info["last_time"], 0.001)
    calculated = max(downloaded - info["last_bytes"], 0) / elapsed
    speed = speed if speed and speed > 0 else calculated
    info.update({
        "percent": f"{downloaded / total * 100:.1f}%" if total else "Baixando...",
        "downloaded": f"{downloaded / 1048576:.1f} MB",
        "total": f"{total / 1048576:.1f} MB" if total else "?",
        "speed": f"{speed / 1048576:.1f} MB/s",
        "last_time": now,
        "last_bytes": downloaded,
    })


@router.callback_query(F.data.startswith("fbprog:"))
async def facebook_progress_callback(call: types.CallbackQuery) -> None:
    key = (call.data or "").partition(":")[2]
    state = _PROGRESS.get(key)
    if not state or call.from_user.id != state["user_id"]:
        await call.answer("⏳ Download finalizado ou indisponível.", show_alert=True)
        return
    if state.get("status") == "uploading":
        await call.answer("🚀 Enviando ao Telegram...", show_alert=True)
        return
    message = (
        f"📊 Progresso do Download:\n\n"
        f"▸ Status: {state['percent']}\n"
        f"▸ Tamanho: {state['downloaded']} / {state['total']}\n"
        f"▸ Velocidade: {state['speed']}"
    )
    await call.answer(message, show_alert=True)


@router.message(
    (F.text.regexp(FACEBOOK_LINK_REGEX, mode="search")
     | F.caption.regexp(FACEBOOK_LINK_REGEX, mode="search"))
    & ~F.text.startswith("/")
)
@router.business_message(
    (F.text.regexp(FACEBOOK_LINK_REGEX, mode="search")
     | F.caption.regexp(FACEBOOK_LINK_REGEX, mode="search"))
    & ~F.text.startswith("/")
)
async def process_facebook_link(message: types.Message) -> None:
    """Handle Facebook end-to-end without calling process_universal_link."""
    user_id = message.from_user.id if message.from_user else None
    chat_id = message.chat.id if message.chat else None
    business_id = getattr(message, "business_connection_id", None)

    if await cmd_mod.is_banned(user_id, chat_id):
        return
    if await should_skip_duplicate_business_message(
        message, bot, service_name="Facebook", logger=logging,
    ):
        return

    source = _extract_facebook_link(get_message_text(message))
    if not source:
        return
    lease = await claim_message_request(message, service="facebook", url=source)
    if lease is None:
        return

    status = None
    progress_key = None
    work_dir = None
    successful = False
    settings = {}
    try:
        target = await _resolve_facebook_short_link(source)
        if not target:
            await message.reply(
                "Não consegui abrir o link do Facebook. "
                "Tente copiar o endereço direto do Reel, vídeo ou foto.",
                allow_sending_without_reply=True,
            )
            return

        settings = await load_user_settings(db, message)
        lang = settings.get("language")
        if not lang and user_id is not None:
            lang = await db.get_language(user_id)
        bot_url = await get_bot_url(bot)
        await react_to_message(message, "👾", business_id=business_id)
        await bot.send_chat_action(
            chat_id=message.chat.id,
            action=ChatAction.UPLOAD_VIDEO,
            business_connection_id=business_id,
        )

        profile = (
            os.environ.get("FACEBOOK_FIREFOX_PROFILE", "").strip() or None
        ) if (
            user_id == ADMIN_ID
            and message.chat.type == "private"
            and business_id is None
        ) else None

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        work_dir = tempfile.mkdtemp(prefix="facebook-", dir=OUTPUT_DIR)
        progress_key = hashlib.blake2s(
            f"{chat_id}:{user_id}:{message.message_id}:{target}".encode(),
            digest_size=8,
        ).hexdigest()
        _PROGRESS[progress_key] = {
            "user_id": user_id,
            "percent": "0%", "downloaded": "0 MB", "total": "?",
            "speed": "--", "last_time": time.monotonic(), "last_bytes": 0,
        }
        progress_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="📊 Ver Progresso", callback_data=f"fbprog:{progress_key}",
            )
        ]])
        if business_id is None:
            status = await message.answer(
                bm.downloading_video_status(lang=lang),
                reply_markup=progress_keyboard,
            )

        result = await download_facebook_media(
            target,
            output_dir=work_dir,
            facebook_browser_profile=profile,
            on_progress=lambda downloaded, total=None, speed=None: _progress_update(
                progress_key, downloaded, total, speed,
            ),
        )
        if not result or not result.media_list:
            await handle_download_error(message, business_id=business_id)
            await react_to_message(message, "👎", business_id=business_id)
            return

        if len(result.media_list) > 10:
            logging.warning("Facebook extraction returned too many media files")
            await handle_download_error(message, business_id=business_id)
            return

        for item in result.media_list:
            if not os.path.isfile(item.url) or os.path.getsize(item.url) <= 0:
                logging.warning("Facebook media path invalid")
                await handle_download_error(message, business_id=business_id)
                return
            if os.path.getsize(item.url) >= MAX_FILE_SIZE:
                logging.warning("Facebook media exceeds max upload size")
                await handle_download_error(message, business_id=business_id)
                return

        _PROGRESS[progress_key]["status"] = "uploading"
        if status:
            await safe_edit_text(status, bm.uploading_status(lang=lang))

        caption = bm.captions(
            settings.get("captions", "on"), result.description, bot_url,
        )
        keyboard = _facebook_keyboard(target, lang)
        if len(result.media_list) == 1:
            item = result.media_list[0]
            file = FSInputFile(item.url, filename=os.path.basename(item.url))
            if settings.get("as_document") == "on":
                await message.reply_document(
                    document=file, caption=caption or None,
                    parse_mode=ParseMode.HTML,
                    disable_content_type_detection=True,
                    reply_markup=keyboard, allow_sending_without_reply=True,
                )
            elif item.type == "video":
                preview_path = item.thumb if item.thumb and os.path.isfile(item.thumb) else None
                await message.reply_video(
                    video=file,
                    thumbnail=FSInputFile(preview_path, filename="thumbnail.jpg") if preview_path else None,
                    cover=FSInputFile(preview_path, filename="cover.jpg") if preview_path else None,
                    caption=caption or None, parse_mode=ParseMode.HTML,
                    width=item.width or None,
                    height=item.height or None,
                    duration=item.duration or None,
                    supports_streaming=True,
                    reply_markup=keyboard, allow_sending_without_reply=True,
                )
            else:
                await message.reply_photo(
                    photo=file, caption=caption or None,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard, allow_sending_without_reply=True,
                )
        else:
            album = []
            for index, item in enumerate(result.media_list):
                file = FSInputFile(item.url, filename=os.path.basename(item.url))
                item_caption = caption if index == 0 else None
                if item.type == "video":
                    preview = item.thumb if item.thumb and os.path.isfile(item.thumb) else None
                    album.append(InputMediaVideo(
                        media=file, caption=item_caption, parse_mode=ParseMode.HTML,
                        thumbnail=FSInputFile(preview, filename="thumbnail.jpg") if preview else None,
                        width=item.width or None, height=item.height or None,
                        duration=item.duration or None, supports_streaming=True,
                    ))
                else:
                    album.append(InputMediaPhoto(
                        media=file, caption=item_caption, parse_mode=ParseMode.HTML,
                    ))
            await message.reply_media_group(
                media=album, allow_sending_without_reply=True,
            )
            # Albums cannot have inline keyboards; send the source link separately.
            await message.answer(
                "🔗 Publicação original:", reply_markup=keyboard,
            )

        if user_id is not None:
            await send_analytics(
                user_id=user_id, chat_type=message.chat.type,
                action_name="universal",
            )
        successful = True
        lease.mark_success()
        await maybe_delete_user_message(message, settings.get("delete_message"))
    except Exception as exc:
        logging.exception("Facebook delivery failed: %s", type(exc).__name__)
        await handle_download_error(message, business_id=business_id)
        await react_to_message(message, "👎", business_id=business_id)
    finally:
        if status:
            await safe_delete_message(status)
        if progress_key:
            _PROGRESS.pop(progress_key, None)
        if lease is not None:
            lease.finish()
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
