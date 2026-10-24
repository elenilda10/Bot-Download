"""Dedicated Kwai Telegram handler.

Owns message/business routing, file_id caching and MP3/document actions.
Reuses the verified public Kwai downloader, ffprobe and thumbnail utilities
from the existing media pipeline without restarting or spawning another bot.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import Optional

from aiogram import F, Router, types
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InlineKeyboardButton

import keyboards as kb
import messages as bm
from app_context import bot, db, send_analytics
from config import MAX_FILE_SIZE, OUTPUT_DIR
from handlers import commands as cmd_mod
from handlers.request_dedupe import claim_message_request
from handlers.utils import (
    get_bot_avatar_thumbnail,
    get_bot_url,
    get_message_text,
    handle_download_error,
    handle_video_too_large,
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
from services.logger import logger as logging, summarize_url_for_log
from services.media.delivery import send_audio_with_thumbnail
from services.platforms.kwai_media import is_kwai_url
from services.platforms.universal_extractor import download_universal_media
from utils.media_cache import build_media_cache_key

router = Router(name="kwai_handler")
logging = logging.bind(service="kwai")

_KWAI_LINK_REGEX = r"https?://(?:[a-zA-Z0-9-]+\.)*(?:kwai\.com|kwai-video\.com|kw\.ai)(?:/[^\s<>'\"]*)?"
_KWAI_CALLBACK_HASH = re.compile(r"^[0-9a-f]{16}$")


def _extract_kwai_url(text: str | None) -> str | None:
    if not text:
        return None
    for match in re.finditer(_KWAI_LINK_REGEX, text, re.IGNORECASE):
        candidate = match.group(0).rstrip(".,;!?)\"']")
        if is_kwai_url(candidate):
            return candidate
    return None


def _url_hash(url: str) -> str:
    return hashlib.blake2s(url.encode("utf-8"), digest_size=8).hexdigest()


def _cache_key(url_hash: str, kind: str) -> str:
    return build_media_cache_key("kwai", variant=f"v1:{kind}:{url_hash}")


def _kwai_keyboard(url: str, url_hash: str, settings: dict, lang: str | None):
    markup = kb.return_video_info_keyboard(
        views=None, likes=None, comments=None, shares=None,
        music_play_url=None, video_url=url,
        user_settings=settings, lang=lang or "pt", has_video=True,
        platform="kwai",
        audio_callback_data=f"audio:kwai:{url_hash}",
        file_callback_data=f"doc:kwai:{url_hash}",
    )
    # Telegram callback_data only contains the hash. A visible source link
    # lets callback handlers recover the URL after a bot restart, even when
    # the user has turned the optional URL button off.
    needs_source = any(
        btn.callback_data in {f"audio:kwai:{url_hash}", f"doc:kwai:{url_hash}"}
        for row in markup.inline_keyboard for btn in row
    )
    has_source = any(
        btn.url and _extract_kwai_url(btn.url) == url
        for row in markup.inline_keyboard for btn in row
    )
    if needs_source and not has_source:
        is_pt = not str(lang or "pt").lower().startswith("en")
        markup.inline_keyboard.append([
            InlineKeyboardButton(
                text="🔗 Abrir no Kwai" if is_pt else "🔗 Open Kwai", url=url
            )
        ])
    return markup


def _callback_url(message: types.Message, url_hash: str) -> str | None:
    markup = getattr(message, "reply_markup", None)
    if markup:
        for row in markup.inline_keyboard:
            for button in row:
                url = _extract_kwai_url(button.url)
                if url and _url_hash(url) == url_hash:
                    return url

    # Some Telegram clients preserve the original link as a replied message.
    original_message = getattr(message, "reply_to_message", None)
    if original_message:
        url = _extract_kwai_url(get_message_text(original_message))
        if url and _url_hash(url) == url_hash:
            return url
    return None


def _file_path(result) -> tuple[str | None, str | None]:
    if not result or not result.media_list:
        return None, None
    media = result.media_list[0]
    path = media.url
    if media.type != "video" or not os.path.isfile(path):
        return None, None
    return path, media.thumb


def _temp_file_id() -> str:
    # Different users can request the same post concurrently.
    return "kwai_" + uuid.uuid4().hex[:16]


async def _download(url: str):
    return await download_universal_media(
        url, output_dir=OUTPUT_DIR, post_id=_temp_file_id()
    )


async def _cleanup(result) -> None:
    if not result:
        return
    for item in result.media_list:
        for path in (item.url, item.thumb):
            if path and os.path.isfile(path):
                await remove_file(path)


async def _try_cache(
    message: types.Message,
    url: str,
    token: str,
    settings: dict,
    lang: str | None,
    bot_url: str,
) -> bool:
    as_document = settings.get("as_document") == "on"
    kind = "document" if as_document else "video"
    file_id = await db.get_file_id(_cache_key(token, kind))
    if not file_id:
        return False

    markup = _kwai_keyboard(url, token, settings, lang)
    caption = bm.captions(settings.get("captions", "on"), None, bot_url)
    try:
        if as_document:
            sent = await message.answer_document(
                document=file_id,
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
                disable_content_type_detection=True,
                allow_sending_without_reply=True,
            )
            return bool(sent and sent.document)

        sent = await message.answer_video(
            video=file_id,
            caption=caption or None,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
            supports_streaming=True,
            allow_sending_without_reply=True,
        )
        return bool(sent and sent.video)
    except TelegramBadRequest:
        logging.warning("Kwai cached Telegram file_id was rejected")
        return False


@router.message(
    (F.text.regexp(_KWAI_LINK_REGEX, mode="search")
     | F.caption.regexp(_KWAI_LINK_REGEX, mode="search"))
    & ~F.text.startswith("/")
)
@router.business_message(
    (F.text.regexp(_KWAI_LINK_REGEX, mode="search")
     | F.caption.regexp(_KWAI_LINK_REGEX, mode="search"))
    & ~F.text.startswith("/")
)
@with_message_logging("kwai", "message")
async def process_kwai_link(
    message: types.Message, direct_url: Optional[str] = None
) -> None:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if user_id is None:
        return
    if await cmd_mod.is_banned(user_id, message.chat.id):
        return

    if await should_skip_duplicate_business_message(
        message, bot, service_name="Kwai", logger=logging
    ):
        return

    url = _extract_kwai_url(direct_url or get_message_text(message))
    if not url:
        return
    token = _url_hash(url)
    lease = await claim_message_request(message, service="kwai", url=url)
    if lease is None:
        return

    business_id = getattr(message, "business_connection_id", None)
    status_message = None
    result = None
    try:
        logging.info("Kwai request: user_id=%s url=%s", user_id, summarize_url_for_log(url))
        await send_analytics(
            user_id=user_id, chat_type=message.chat.type, action_name="kwai"
        )
        await react_to_message(message, "👾", business_id=business_id)
        await send_chat_action_if_needed(
            bot, message.chat.id, ChatAction.UPLOAD_VIDEO, business_id
        )
        settings = await load_user_settings(db, message)
        lang = settings.get("language") or await db.get_language(user_id)
        bot_url = await get_bot_url(bot)

        if await _try_cache(message, url, token, settings, lang, bot_url):
            lease.mark_success()
            await maybe_delete_user_message(message, settings.get("delete_message"))
            return

        if business_id is None:
            status_message = await message.answer(
                bm.downloading_video_status(lang=lang)
            )

        result = await _download(url)
        path, thumb = _file_path(result)
        if not path:
            await handle_download_error(message, business_id=business_id, lang=lang)
            return

        if os.path.getsize(path) >= MAX_FILE_SIZE:
            await handle_video_too_large(message, business_id=business_id, lang=lang)
            return

        await safe_edit_text(status_message, bm.uploading_status(lang=lang))
        item = result.media_list[0]
        markup = _kwai_keyboard(url, token, settings, lang)
        caption = bm.captions(settings.get("captions", "on"), result.description, bot_url)

        if settings.get("as_document") == "on":
            sent = await message.answer_document(
                document=FSInputFile(path, filename=f"kwai_{token}.mp4"),
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
                disable_content_type_detection=True,
                allow_sending_without_reply=True,
            )
            if sent and sent.document:
                await db.add_file(
                    _cache_key(token, "document"), sent.document.file_id, "document"
                )
                lease.mark_success()
        else:
            preview = thumb if thumb and os.path.isfile(thumb) else None
            sent = await message.answer_video(
                video=FSInputFile(path, filename=f"kwai_{token}.mp4"),
                thumbnail=(
                    FSInputFile(preview, filename="thumbnail.jpg") if preview else None
                ),
                cover=FSInputFile(preview, filename="cover.jpg") if preview else None,
                width=item.width or None,
                height=item.height or None,
                duration=item.duration or None,
                supports_streaming=True,
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
                allow_sending_without_reply=True,
            )
            if sent and sent.video:
                await db.add_file(
                    _cache_key(token, "video"), sent.video.file_id, "video"
                )
                lease.mark_success()

        if sent:
            await maybe_delete_user_message(
                message, settings.get("delete_message")
            )

    except Exception as exc:
        logging.exception("Kwai message delivery failed: %s", type(exc).__name__)
        await handle_download_error(message, business_id=business_id)
    finally:
        await safe_delete_message(status_message)
        await _cleanup(result)
        lease.finish()


async def _callback_request(call: types.CallbackQuery, kind: str) -> None:
    if not call.message or not isinstance(call.message, types.Message):
        await call.answer()
        return

    prefix = "audio:kwai:" if kind == "audio" else "doc:kwai:"
    token = (call.data or "").removeprefix(prefix)
    if not _KWAI_CALLBACK_HASH.fullmatch(token):
        await call.answer()
        return

    await call.answer()
    lang = await db.get_language(call.from_user.id)
    source = _callback_url(call.message, token)
    cached_id = await db.get_file_id(_cache_key(token, kind))
    if not source and not cached_id:
        await call.message.answer(
            "Envie o link do Kwai novamente para baixar."
            if not str(lang or "pt").lower().startswith("en")
            else "Please send the Kwai link again to download.",
            allow_sending_without_reply=True,
        )
        return

    status_message = None
    result = None
    mp3_path = None
    try:
        status_message = await call.message.answer(
            bm.downloading_audio_status(lang=lang)
            if kind == "audio" else bm.downloading_video_status(lang=lang),
            allow_sending_without_reply=True,
        )
        bot_url = await get_bot_url(bot)

        if cached_id:
            try:
                if kind == "document":
                    sent = await call.message.answer_document(
                        document=cached_id,
                        allow_sending_without_reply=True,
                        disable_content_type_detection=True,
                    )
                    if sent and sent.document:
                        return
                else:
                    avatar = await get_bot_avatar_thumbnail(bot)
                    sent = await send_audio_with_thumbnail(
                        call.message.answer_audio,
                        audio=cached_id,
                        caption=bm.captions(None, None, bot_url),
                        bot_avatar=avatar,
                        bot_url=bot_url,
                        parse_mode="HTML",
                    )
                    if sent and sent.audio:
                        return
            except TelegramBadRequest:
                logging.warning("Kwai cached %s ID invalid", kind)

        if not source:
            await call.message.answer(
                "Envie o link do Kwai novamente."
                if not str(lang or "pt").lower().startswith("en")
                else "Please send the Kwai link again.",
                allow_sending_without_reply=True,
            )
            return

        result = await _download(source)
        path, _ = _file_path(result)
        if not path:
            await handle_download_error(call.message, lang=lang)
            return

        if kind == "document":
            if os.path.getsize(path) >= MAX_FILE_SIZE:
                await handle_video_too_large(call.message, lang=lang)
                return
            sent = await call.message.answer_document(
                document=FSInputFile(path, filename=f"kwai_{token}.mp4"),
                disable_content_type_detection=True,
                allow_sending_without_reply=True,
            )
            if sent and sent.document:
                await db.add_file(
                    _cache_key(token, "document"), sent.document.file_id, "document"
                )
            return

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        mp3_path = str(Path(OUTPUT_DIR) / f"kwai_{uuid.uuid4().hex}.mp3")
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-nostdin", "-y", "-i", path,
            "-vn", "-acodec", "libmp3lame", "-q:a", "2", mp3_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=180)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise

        if proc.returncode != 0 or not os.path.isfile(mp3_path):
            await handle_download_error(call.message, lang=lang)
            return
        if os.path.getsize(mp3_path) >= MAX_FILE_SIZE:
            await call.message.answer(
                bm.audio_too_large(lang=lang), allow_sending_without_reply=True
            )
            return

        await safe_edit_text(status_message, bm.uploading_status(lang=lang))
        avatar = await get_bot_avatar_thumbnail(bot)
        sent = await send_audio_with_thumbnail(
            call.message.answer_audio,
            audio=FSInputFile(mp3_path),
            title=result.description or "Kwai",
            caption=bm.captions(None, None, bot_url),
            audio_path=mp3_path,
            bot_avatar=avatar,
            bot_url=bot_url,
            parse_mode="HTML",
        )
        if sent and sent.audio:
            await db.add_file(
                _cache_key(token, "audio"), sent.audio.file_id, "audio"
            )
    except Exception as exc:
        logging.exception("Kwai %s callback failed: %s", kind, type(exc).__name__)
        await handle_download_error(call.message, lang=lang)
    finally:
        await safe_delete_message(status_message)
        await _cleanup(result)
        if mp3_path and os.path.isfile(mp3_path):
            await remove_file(mp3_path)


@router.callback_query(F.data.startswith("doc:kwai:"))
async def send_kwai_document(call: types.CallbackQuery) -> None:
    await _callback_request(call, "document")


@router.callback_query(F.data.startswith("audio:kwai:"))
async def send_kwai_mp3(call: types.CallbackQuery) -> None:
    await _callback_request(call, "audio")
