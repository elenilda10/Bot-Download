import asyncio
import hashlib
import os
import re
import time
from typing import Optional, Any

from aiogram import F, Router, types
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo, URLInputFile, InlineKeyboardMarkup, InlineKeyboardButton

import keyboards as kb
import messages as bm
from app_context import bot, db, send_analytics
from config import OUTPUT_DIR, MAX_FILE_SIZE
from handlers import commands as cmd_mod
from handlers.request_dedupe import claim_message_request
from handlers.utils import (
    get_bot_avatar_thumbnail,
    get_bot_url,
    get_message_text,
    handle_download_error,
    load_user_settings,
    maybe_delete_user_message,
    react_to_message,
    remove_file,
    safe_delete_message,
    safe_edit_text,
    send_chat_action_if_needed,
    should_skip_duplicate_business_message,
    with_message_logging,
)
from services.logger import logger as logging, summarize_text_for_log
from services.media.delivery import build_audio_cache_key, send_audio_with_thumbnail
from services.platforms.universal_extractor import download_universal_media
from utils.media_cache import build_media_cache_key

logging = logging.bind(service="universal")
_UNIVERSAL_LINK_REGEX = r"https?://\S+"
_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")

router = Router(name="universal_handler")

_URL_CACHE: dict[str, str] = {}
UNIVERSAL_PROGRESS: dict[str, dict[str, Any]] = {}


def _is_pt(lang: Optional[str]) -> bool:
    if not lang:
        return True
    code = lang.strip().lower()
    return code == "pt" or code.startswith("pt_") or code.startswith("pt-")


def _make_progress_keyboard(post_id: str, lang: Optional[str] = "pt") -> InlineKeyboardMarkup:
    text = "📊 Ver Progresso" if _is_pt(lang) else "📊 View Progress"
    norm_lang = "pt" if _is_pt(lang) else "en"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=f"univprog:{post_id}:{norm_lang}")]]
    )


def _store_url_hash(url: str) -> str:
    short_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    _URL_CACHE[short_hash] = url
    return short_hash


def _get_url_from_hash(short_hash: str) -> Optional[str]:
    return _URL_CACHE.get(short_hash)


def _is_image_item(item) -> bool:
    item_type = getattr(item, "type", "") or ""
    if item_type in {"photo", "image"}:
        return True

    url_lower = str(getattr(item, "url", "")).lower().split("?")[0]
    if any(url_lower.endswith(ext) for ext in _IMAGE_EXTENSIONS):
        return True

    duration = getattr(item, "duration", None)
    if duration == 0 and item_type != "video":
        return True

    return False


@router.callback_query(F.data.startswith("univprog:"))
async def handle_universal_progress_callback(call: types.CallbackQuery):
    parts = call.data.split(":")
    post_id = parts[1] if len(parts) > 1 else ""
    lang_hint = parts[2] if len(parts) > 2 else None

    user_lang = lang_hint or await db.get_language(call.from_user.id)
    is_portuguese = _is_pt(user_lang)

    info = UNIVERSAL_PROGRESS.get(post_id)

    if not info:
        text = (
            "⏳ Inicializando download ou preparando envio..."
            if is_portuguese
            else "⏳ Initializing download or preparing upload..."
        )
        await call.answer(text, show_alert=True)
        return

    status = info.get("status")
    if status == "uploading":
        text = (
            "🚀 Download finalizado! Enviando ao Telegram..."
            if is_portuguese
            else "🚀 Download complete! Sending to Telegram..."
        )
        await call.answer(text, show_alert=True)
        return

    percent = info.get("percent", "0%")
    downloaded = info.get("downloaded", "0 MB")
    total = info.get("total", "?")
    speed = info.get("speed", "--")

    if is_portuguese:
        msg = (
            f"📊 Progresso do Download:\n\n"
            f"▸ Status: {percent}\n"
            f"▸ Tamanho: {downloaded} / {total}\n"
            f"▸ Velocidade: {speed}"
        )
    else:
        msg = (
            f"📊 Download Progress:\n\n"
            f"▸ Status: {percent}\n"
            f"▸ Size: {downloaded} / {total}\n"
            f"▸ Speed: {speed}"
        )

    await call.answer(msg, show_alert=True)


@router.message(
    (
        F.text.regexp(_UNIVERSAL_LINK_REGEX, mode="search")
        | F.caption.regexp(_UNIVERSAL_LINK_REGEX, mode="search")
    )
    & ~F.text.startswith("/")
)
@router.business_message(
    (
        F.text.regexp(_UNIVERSAL_LINK_REGEX, mode="search")
        | F.caption.regexp(_UNIVERSAL_LINK_REGEX, mode="search")
    )
    & ~F.text.startswith("/")
)
@with_message_logging("universal", "message")
async def process_universal_link(
    message: types.Message, direct_url: Optional[str] = None
) -> None:
    user_id = message.from_user.id if message.from_user else None
    chat_id = message.chat.id if message.chat else None
    if await cmd_mod.is_banned(user_id, chat_id):
        return

    request_lease = None
    business_id = getattr(message, "business_connection_id", None)
    text = direct_url or get_message_text(message)
    logging.info(
        "Universal request: user_id=%s url=%s",
        user_id or "unknown",
        summarize_text_for_log(text),
    )
    if await should_skip_duplicate_business_message(
        message, bot, service_name="Universal", logger=logging
    ):
        return

    request_url_match = re.search(_UNIVERSAL_LINK_REGEX, text or "")
    if not request_url_match:
        return

    url = request_url_match.group(0).strip()
    request_lease = await claim_message_request(message, service="universal", url=url)
    if request_lease is None:
        return

    await react_to_message(message, "👾", business_id=business_id)
    await bot.send_chat_action(
        chat_id=message.chat.id,
        action=ChatAction.UPLOAD_VIDEO,
        business_connection_id=business_id,
    )

    user_settings = await load_user_settings(db, message)
    user_lang = user_settings.get("language") or await db.get_language(message.from_user.id)
    clean_url = url.split("?")[0].split("#")[0].strip()
    post_id = hashlib.blake2s(clean_url.encode("utf-8"), digest_size=8).hexdigest()

    status_message: Optional[types.Message] = None
    progress_kb = _make_progress_keyboard(post_id, lang=user_lang)

    if business_id is None:
        status_message = await message.answer(
            bm.downloading_video_status(lang=user_lang),
            reply_markup=progress_kb,
        )

    initial_status_text = "Baixando..." if _is_pt(user_lang) else "Downloading..."
    initial_speed_text = "Calculando..." if _is_pt(user_lang) else "Calculating..."

    UNIVERSAL_PROGRESS[post_id] = {
        "percent": "0%",
        "downloaded": "0 MB",
        "total": "?",
        "speed": initial_speed_text,
        "last_time": time.monotonic(),
        "last_bytes": 0,
    }

    def on_progress(downloaded_bytes: int, total_bytes: Optional[int] = None, speed: Optional[float] = None) -> None:
        curr_time = time.monotonic()
        info = UNIVERSAL_PROGRESS.get(post_id)
        if not info:
            return

        if speed is not None and speed > 0:
            speed_mb = speed / (1024 * 1024)
        else:
            dt = max(curr_time - info.get("last_time", curr_time), 0.001)
            db_delta = max(downloaded_bytes - info.get("last_bytes", 0), 0)
            speed_mb = (db_delta / dt) / (1024 * 1024)

        pct_str = f"{(downloaded_bytes / total_bytes * 100):.1f}%" if total_bytes else initial_status_text

        info.update({
            "percent": pct_str,
            "downloaded": f"{downloaded_bytes / (1024 * 1024):.1f} MB",
            "total": f"{total_bytes / (1024 * 1024):.1f} MB" if total_bytes else "?",
            "speed": f"{speed_mb:.1f} MB/s",
            "last_time": curr_time,
            "last_bytes": downloaded_bytes,
        })

    try:
        if message.from_user:
            await send_analytics(
                user_id=message.from_user.id,
                chat_type=message.chat.type,
                action_name="universal",
            )
        bot_url = await get_bot_url(bot)

        res = await download_universal_media(url, on_progress=on_progress)
        if not res or not res.media_list:
            if status_message:
                await safe_delete_message(status_message)
            await handle_download_error(message, business_id=business_id)
            await react_to_message(message, "👎", business_id=business_id)
            return

        if post_id in UNIVERSAL_PROGRESS:
            UNIVERSAL_PROGRESS[post_id]["status"] = "uploading"

        if status_message:
            await safe_edit_text(status_message, bm.uploading_status(lang=user_lang))

        user_captions_mode = user_settings.get("captions", "on")
        caption = bm.captions(user_captions_mode, res.description, bot_url)

        media_count = len(res.media_list)
        first_item = res.media_list[0] if media_count > 0 else None
        preview_url = (
            first_item.thumb
            if (first_item and hasattr(first_item, "thumb") and first_item.thumb)
            else None
        )

        url_hash = _store_url_hash(url)
        is_first_image = _is_image_item(first_item) if first_item else False

        audio_cb = f"audio:univ:{url_hash}" if not is_first_image else None
        doc_cb = f"doc:univ:{url_hash}"

        keyboard = kb.return_video_info_keyboard(
            None,
            None,
            None,
            None,
            preview_url,
            url,
            user_settings,
            audio_callback_data=audio_cb,
            file_callback_data=doc_cb,
        )

        local_files_to_clean = []
        sent_message: Optional[types.Message] = None

        if media_count == 1:
            item = res.media_list[0]
            is_local = os.path.exists(item.url)
            if is_local:
                local_files_to_clean.append(item.url)

            is_image = _is_image_item(item)
            if not is_image and item.type == "video":
                video_file = FSInputFile(item.url) if is_local else URLInputFile(item.url)
                thumb_file = (
                    FSInputFile(item.thumb)
                    if (item.thumb and os.path.exists(item.thumb))
                    else None
                )
                if item.thumb and os.path.exists(item.thumb):
                    local_files_to_clean.append(item.thumb)

                sent_message = await message.reply_video(
                    video=video_file,
                    thumbnail=thumb_file,
                    caption=caption or None,
                    parse_mode=ParseMode.HTML,
                    width=item.width or 1280,
                    height=item.height or 720,
                    duration=item.duration or None,
                    supports_streaming=True,
                    reply_markup=keyboard,
                )
                if sent_message and sent_message.video:
                    doc_cache_key = build_media_cache_key("universal", variant=f"doc:{url_hash}")
                    await db.add_file(doc_cache_key, sent_message.video.file_id, "video")
            else:
                photo_file = FSInputFile(item.url) if is_local else URLInputFile(item.url)
                sent_message = await message.reply_photo(
                    photo=photo_file,
                    caption=caption or None,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )
                if sent_message and sent_message.photo:
                    doc_cache_key = build_media_cache_key("universal", variant=f"doc:{url_hash}")
                    await db.add_file(doc_cache_key, sent_message.photo[-1].file_id, "photo")

        else:
            media_group = []
            for idx, item in enumerate(res.media_list[:10]):
                is_local = os.path.exists(item.url)
                if is_local:
                    local_files_to_clean.append(item.url)

                item_caption = caption if idx == 0 else None
                is_image = _is_image_item(item)
                if not is_image and item.type == "video":
                    file_input = (
                        FSInputFile(item.url) if is_local else URLInputFile(item.url)
                    )
                    media_group.append(
                        InputMediaVideo(
                            media=file_input,
                            caption=item_caption,
                            parse_mode=ParseMode.HTML,
                            width=item.width or 1280,
                            height=item.height or 720,
                            duration=item.duration or None,
                            supports_streaming=True,
                        )
                    )
                else:
                    file_input = (
                        FSInputFile(item.url) if is_local else URLInputFile(item.url)
                    )
                    media_group.append(
                        InputMediaPhoto(
                            media=file_input,
                            caption=item_caption,
                            parse_mode=ParseMode.HTML,
                        )
                    )

            if media_group:
                await message.reply_media_group(media=media_group)

        if status_message:
            await safe_delete_message(status_message)

        if request_lease is not None:
            request_lease.mark_success()
        await maybe_delete_user_message(message, user_settings.get("delete_message"))

        await asyncio.sleep(2)
        for fpath in local_files_to_clean:
            try:
                if os.path.exists(fpath):
                    os.remove(fpath)
            except Exception:
                pass

    except Exception as exc:
        logging.exception("Error processing universal download: %s", exc)
        if status_message:
            await safe_delete_message(status_message)
        await react_to_message(message, "👎", business_id=business_id)
        await handle_download_error(message, business_id=business_id)
    finally:
        if post_id and post_id in UNIVERSAL_PROGRESS:
            UNIVERSAL_PROGRESS.pop(post_id, None)
        if request_lease is not None:
            request_lease.finish()


@router.callback_query(F.data.startswith("doc:univ:"))
async def download_universal_doc_callback(call: types.CallbackQuery):
    if not call.message:
        return
    await call.answer()
    url_hash = call.data.removeprefix("doc:univ:")
    doc_cache_key = build_media_cache_key("universal", variant=f"doc:{url_hash}")
    cached_file_id = await db.get_file_id(doc_cache_key)
    if cached_file_id:
        try:
            await call.message.reply_document(
                document=cached_file_id,
                caption="📄 Original file",
                disable_content_type_detection=True,
            )
            return
        except TelegramBadRequest:
            pass

    original_url = _get_url_from_hash(url_hash)
    if not original_url:
        user_lang = await db.get_language(call.from_user.id)
        await call.message.reply(bm.something_went_wrong(lang=user_lang))
        return

    status_message = await call.message.answer(bm.downloading_video_status())
    try:
        res = await download_universal_media(original_url)
        if not res or not res.media_list:
            await safe_delete_message(status_message)
            return

        first_item = res.media_list[0]
        is_local = os.path.exists(first_item.url)
        file_input = FSInputFile(first_item.url) if is_local else URLInputFile(first_item.url)

        sent = await call.message.reply_document(
            document=file_input,
            caption="📄 Original file",
            disable_content_type_detection=True,
        )
        if sent and sent.document:
            await db.add_file(doc_cache_key, sent.document.file_id, "document")

        if is_local:
            await remove_file(first_item.url)
    except Exception as exc:
        logging.error("Erro ao enviar doc:univ callback: %s", exc)
    finally:
        await safe_delete_message(status_message)


@router.callback_query(F.data.startswith("audio:univ:"))
async def download_universal_audio_callback(call: types.CallbackQuery):
    if not call.message:
        return
    await call.answer()

    url_hash = call.data.removeprefix("audio:univ:")
    original_url = _get_url_from_hash(url_hash)
    user_lang = await db.get_language(call.from_user.id)
    if not original_url:
        await call.message.reply(bm.something_went_wrong(lang=user_lang))
        return

    status_message = await call.message.answer(bm.downloading_audio_status(lang=user_lang))
    audio_cache_key = build_audio_cache_key(original_url)
    cached_audio_id = await db.get_file_id(audio_cache_key)

    bot_url = await get_bot_url(bot)
    bot_avatar = await get_bot_avatar_thumbnail(bot)

    if cached_audio_id:
        await safe_edit_text(status_message, bm.uploading_status(lang=user_lang))
        await send_chat_action_if_needed(bot, call.message.chat.id, "upload_audio", call.message.business_connection_id)
        await send_audio_with_thumbnail(
            call.message.reply_audio,
            audio=cached_audio_id,
            caption=bm.captions(None, None, bot_url),
            bot_avatar=bot_avatar,
            bot_url=bot_url,
            parse_mode="HTML",
        )
        await safe_delete_message(status_message)
        return

    try:
        res = await download_universal_media(original_url)
        if not res or not res.media_list:
            await safe_delete_message(status_message)
            await handle_download_error(call.message, lang=user_lang)
            return

        first_item = res.media_list[0]
        input_file_path = first_item.url

        mp3_filename = f"univ_{url_hash}_{int(asyncio.get_event_loop().time())}.mp3"
        output_mp3_path = os.path.join(OUTPUT_DIR, mp3_filename)

        cmd = [
            "ffmpeg", "-y", "-i", input_file_path,
            "-vn", "-acodec", "libmp3lame", "-q:a", "2",
            output_mp3_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await proc.communicate()

        if not os.path.exists(output_mp3_path) or os.path.getsize(output_mp3_path) == 0:
            await safe_delete_message(status_message)
            await handle_download_error(call.message, lang=user_lang)
            return

        if os.path.getsize(output_mp3_path) >= MAX_FILE_SIZE:
            await call.message.reply(bm.audio_too_large(lang=user_lang))
            await remove_file(output_mp3_path)
            await safe_delete_message(status_message)
            return

        await safe_edit_text(status_message, bm.uploading_status(lang=user_lang))
        await send_chat_action_if_needed(bot, call.message.chat.id, "upload_audio", call.message.business_connection_id)

        sent_audio = await send_audio_with_thumbnail(
            call.message.reply_audio,
            audio=FSInputFile(output_mp3_path),
            title=res.description or "Universal Audio",
            caption=bm.captions(None, None, bot_url),
            audio_path=output_mp3_path,
            bot_avatar=bot_avatar,
            bot_url=bot_url,
            parse_mode="HTML",
        )

        if sent_audio and sent_audio.audio:
            await db.add_file(audio_cache_key, sent_audio.audio.file_id, "audio")

        await remove_file(output_mp3_path)
        if os.path.exists(input_file_path):
            await remove_file(input_file_path)

    except Exception as exc:
        logging.exception("Erro ao processar audio:univ: %s", exc)
        await handle_download_error(call.message, lang=user_lang)
    finally:
        await safe_delete_message(status_message)
