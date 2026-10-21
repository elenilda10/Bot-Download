import asyncio
import hashlib
import json
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
from config import OUTPUT_DIR, MAX_FILE_SIZE, ADMIN_ID
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
from services.platforms.universal_extractor import download_universal_media, _is_facebook_url
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


def _recover_callback_url(message: types.Message, url_hash: str) -> str | None:
    """Resolve callback URLs even if the in-memory map was reset by restart."""
    original = _get_url_from_hash(url_hash)
    if original:
        return original
    replied = message.reply_to_message
    if replied:
        text = get_message_text(replied)
        match = re.search(_UNIVERSAL_LINK_REGEX, text or "")
        if match:
            candidate = match.group(0).strip()
            if hashlib.md5(candidate.encode()).hexdigest()[:12] == url_hash:
                _URL_CACHE[url_hash] = candidate
                return candidate
    return None


def _cache_key(url_hash: str, media_kind: str) -> str:
    """Keep audio/document caches; refresh old previewless videos and albums."""
    # Telegram file_ids from older uploads preserve their original thumbnails.
    # A new namespace makes the first post-fix request rebuild the preview.
    version = "v3" if media_kind in {"video", "album"} else "v2"
    return build_media_cache_key(
        "universal", variant=f"{version}:{media_kind}:{url_hash}"
    )


def _file_button_caption(lang: str | None) -> str:
    return "📄 Arquivo como documento" if _is_pt(lang) else "📄 File as document"


def _universal_keyboard(
    url: str, url_hash: str, user_settings: dict,
    *, lang: str | None, is_video: bool,
):
    if _is_facebook_url(url):
        # Facebook auth must not be reachable through forwarded callbacks.
        # Only the public source URL is shown; MP3/file callbacks stay off.
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="🔗 Abrir no Facebook" if _is_pt(lang) else "🔗 Open Facebook",
                url=url,
            ),
        ]])
    return kb.return_video_info_keyboard(
        views=None, likes=None, comments=None, shares=None,
        music_play_url=None, video_url=url,
        user_settings=user_settings,
        audio_callback_data=f"audio:univ:{url_hash}" if is_video else None,
        file_callback_data=f"doc:univ:{url_hash}",
        lang=lang or "pt", has_video=is_video,
    )


async def _try_cached_universal(
    message: types.Message, url: str, url_hash: str,
    settings: dict, *, lang: str | None, bot_url: str,
) -> bool:
    """Serve existing Telegram media without re-running gallery-dl or yt-dlp."""
    caption = bm.captions(settings.get("captions", "on"), None, bot_url)

    if settings.get("as_document") == "on":
        cached_document_id = await db.get_file_id(_cache_key(url_hash, "document"))
        if cached_document_id:
            try:
                sent = await message.reply_document(
                    document=cached_document_id,
                    caption=caption or None, parse_mode=ParseMode.HTML,
                    disable_content_type_detection=True,
                    allow_sending_without_reply=True,
                    reply_markup=_universal_keyboard(
                        url, url_hash, settings, lang=lang, is_video=False
                    ),
                )
                if sent and sent.document:
                    logging.info("Universal cache hit: document")
                    return True
            except TelegramBadRequest:
                logging.warning("Universal document cache ID rejected")
    else:
        for kind in ("video", "photo"):
            cached_id = await db.get_file_id(_cache_key(url_hash, kind))
            if not cached_id:
                continue
            keyboard = _universal_keyboard(
                url, url_hash, settings, lang=lang, is_video=(kind == "video")
            )
            try:
                if kind == "video":
                    sent = await message.reply_video(
                        video=cached_id, caption=caption or None,
                        parse_mode=ParseMode.HTML, supports_streaming=True,
                        reply_markup=keyboard, allow_sending_without_reply=True,
                    )
                    valid = bool(sent and sent.video)
                else:
                    sent = await message.reply_photo(
                        photo=cached_id, caption=caption or None,
                        parse_mode=ParseMode.HTML, reply_markup=keyboard,
                        allow_sending_without_reply=True,
                    )
                    valid = bool(sent and sent.photo)
                if valid:
                    logging.info("Universal cache hit: %s", kind)
                    return True
            except TelegramBadRequest:
                logging.warning("Universal %s cache ID rejected", kind)

    # Telegram media groups do not have inline keyboards; cache the complete
    # ordered album under a V2 manifest entry (never use as a Telegram ID).
    manifest_raw = await db.get_file_id(_cache_key(url_hash, "album"))
    if not manifest_raw:
        return False
    try:
        manifest = json.loads(manifest_raw)
        entries = manifest["items"]
        if (manifest.get("version") != 1 or not isinstance(entries, list)
                or not 2 <= len(entries) <= 10):
            return False
        album = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                return False
            kind, file_id = entry.get("kind"), entry.get("file_id")
            if not isinstance(file_id, str) or not file_id:
                return False
            item_caption = (caption or None) if index == 0 else None
            if kind == "video":
                album.append(InputMediaVideo(
                    media=file_id, caption=item_caption, parse_mode=ParseMode.HTML,
                    supports_streaming=True,
                ))
            elif kind == "photo":
                album.append(InputMediaPhoto(
                    media=file_id, caption=item_caption, parse_mode=ParseMode.HTML,
                ))
            else:
                return False
        sent_album = await message.reply_media_group(
            media=album, allow_sending_without_reply=True,
        )
        if sent_album:
            logging.info("Universal cache hit: album (%s items)", len(entries))
            return True
    except (ValueError, KeyError, TypeError, TelegramBadRequest):
        logging.warning("Universal cached album unavailable; downloading again")
    return False


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
    # Different signed URLs/query IDs may point to different media.
    progress_url = url.split("#", 1)[0].strip()
    post_id = hashlib.blake2s(progress_url.encode("utf-8"), digest_size=8).hexdigest()

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

    local_files_to_clean: list[str] = []
    try:
        if message.from_user:
            await send_analytics(
                user_id=message.from_user.id,
                chat_type=message.chat.type,
                action_name="universal",
            )
        bot_url = await get_bot_url(bot)

        url_hash = _store_url_hash(url)
        is_facebook = _is_facebook_url(url)
        # Never serve/cache content downloaded with an authenticated
        # Facebook session to other users via Telegram's reusable file_id.
        facebook_browser_profile = (
            os.environ.get("FACEBOOK_FIREFOX_PROFILE", "").strip() or None
        ) if (
            is_facebook and user_id == ADMIN_ID
            and message.chat.type == "private"
        ) else None
        if not is_facebook and await _try_cached_universal(
            message, url, url_hash, user_settings,
            lang=user_lang, bot_url=bot_url,
        ):
            if status_message:
                await safe_delete_message(status_message)
            if request_lease is not None:
                request_lease.mark_success()
            await maybe_delete_user_message(
                message, user_settings.get("delete_message")
            )
            return

        res = await download_universal_media(
            url, on_progress=on_progress,
            facebook_browser_profile=facebook_browser_profile,
        )
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

        is_first_image = _is_image_item(first_item) if first_item else False
        keyboard = _universal_keyboard(
            url, url_hash, user_settings, lang=user_lang,
            is_video=not is_first_image,
        )

        for media_item in res.media_list:
            if os.path.isfile(media_item.url):
                local_files_to_clean.append(media_item.url)
            if media_item.thumb and os.path.isfile(media_item.thumb):
                local_files_to_clean.append(media_item.thumb)
        sent_message: Optional[types.Message] = None

        if media_count == 1:
            item = res.media_list[0]
            is_local = os.path.exists(item.url)
            if is_local:
                local_files_to_clean.append(item.url)

            is_image = _is_image_item(item)
            as_document = user_settings.get("as_document") == "on"
            source_file = (
                FSInputFile(item.url, filename=os.path.basename(item.url))
                if is_local else URLInputFile(item.url)
            )

            if as_document:
                # A document is a new Telegram upload. A video/photo file_id
                # cannot be converted into a document merely by reusing it.
                sent_message = await message.reply_document(
                    document=source_file, caption=caption or None,
                    parse_mode=ParseMode.HTML,
                    disable_content_type_detection=True,
                    reply_markup=keyboard,
                    allow_sending_without_reply=True,
                )
                if sent_message and sent_message.document and not is_facebook:
                    await db.add_file(
                        _cache_key(url_hash, "document"),
                        sent_message.document.file_id, "document",
                    )
                    logging.info("Universal cache stored: document")
            elif not is_image and item.type == "video":
                preview_path = (
                    item.thumb if item.thumb and os.path.isfile(item.thumb)
                    else None
                )
                # Both Telegram fields matter: thumbnail for the file preview
                # and cover for its in-chat presentation.
                sent_message = await message.reply_video(
                    video=source_file,
                    allow_sending_without_reply=True,
                    thumbnail=(
                        FSInputFile(preview_path, filename="thumbnail.jpg")
                        if preview_path else None
                    ),
                    cover=(
                        FSInputFile(preview_path, filename="cover.jpg")
                        if preview_path else None
                    ),
                    caption=caption or None,
                    parse_mode=ParseMode.HTML,
                    width=item.width or None,
                    height=item.height or None,
                    duration=item.duration or None,
                    supports_streaming=True,
                    reply_markup=keyboard,
                )
                if sent_message and sent_message.video and not is_facebook:
                    await db.add_file(
                        _cache_key(url_hash, "video"),
                        sent_message.video.file_id, "video",
                    )
                    logging.info("Universal cache stored: video")
            else:
                sent_message = await message.reply_photo(
                    photo=source_file,
                    allow_sending_without_reply=True,
                    caption=caption or None,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )
                if sent_message and sent_message.photo and not is_facebook:
                    await db.add_file(
                        _cache_key(url_hash, "photo"),
                        sent_message.photo[-1].file_id, "photo",
                    )
                    logging.info("Universal cache stored: photo")

        else:
            media_group = []
            for idx, item in enumerate(res.media_list[:10]):
                is_local = os.path.exists(item.url)
                if is_local:
                    local_files_to_clean.append(item.url)
                if item.thumb and os.path.isfile(item.thumb):
                    local_files_to_clean.append(item.thumb)

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
                            thumbnail=(
                                FSInputFile(item.thumb, filename="thumbnail.jpg")
                                if item.thumb and os.path.isfile(item.thumb)
                                else None
                            ),
                            width=item.width or None,
                            height=item.height or None,
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
                sent_album = await message.reply_media_group(
                    media=media_group,
                    allow_sending_without_reply=True,
                )
                if sent_album and len(sent_album) == len(media_group):
                    album_entries = []
                    for item in sent_album:
                        if item.video:
                            album_entries.append({
                                "kind": "video", "file_id": item.video.file_id,
                            })
                        elif item.photo:
                            album_entries.append({
                                "kind": "photo", "file_id": item.photo[-1].file_id,
                            })
                        else:
                            break
                    if len(album_entries) == len(sent_album) and not is_facebook:
                        await db.add_file(
                            _cache_key(url_hash, "album"),
                            json.dumps(
                                {"version": 1, "items": album_entries},
                                separators=(",", ":"),
                            ),
                            "album_manifest",
                        )
                        logging.info("Universal cache stored: album (%s items)", len(album_entries))

        if status_message:
            await safe_delete_message(status_message)

        if request_lease is not None:
            request_lease.mark_success()
        await maybe_delete_user_message(message, user_settings.get("delete_message"))

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
        for fpath in set(local_files_to_clean):
            try:
                if os.path.isfile(fpath):
                    os.remove(fpath)
            except OSError:
                pass


@router.callback_query(F.data.startswith("doc:univ:"))
async def download_universal_doc_callback(call: types.CallbackQuery):
    if not call.message or not isinstance(call.message, types.Message):
        await call.answer()
        return
    await call.answer()
    user_lang = await db.get_language(call.from_user.id)
    url_hash = call.data.removeprefix("doc:univ:")
    if not re.fullmatch(r"[a-f0-9]{12}", url_hash):
        return

    # Legacy buttons must never use authenticated Facebook posts or a
    # previously cached file_id. Facebook MP3 and file buttons are disabled.
    callback_url = _recover_callback_url(call.message, url_hash)
    if callback_url and _is_facebook_url(callback_url):
        await call.message.reply(
            "Este botão ainda não está disponível para o Facebook.",
            allow_sending_without_reply=True,
        )
        return

    # Old doc:{hash} entries actually hold video/photo file_ids. Never reuse
    # them with reply_document; a separate key guarantees Telegram document.
    doc_cache_key = _cache_key(url_hash, "document")
    cached_file_id = await db.get_file_id(doc_cache_key)
    file_caption = _file_button_caption(user_lang)

    if cached_file_id:
        try:
            sent = await call.message.reply_document(
                document=cached_file_id,
                allow_sending_without_reply=True,
                caption=file_caption,
                disable_content_type_detection=True,
            )
            if sent and sent.document:
                logging.info("Universal file button: document cache hit")
                return
        except TelegramBadRequest:
            logging.warning("Universal file button cached document rejected")

    original_url = _recover_callback_url(call.message, url_hash)

    if not original_url:
        await call.message.reply(
            "Envie o link novamente para baixar como arquivo."
            if _is_pt(user_lang) else
            "Please send the link again to download it as a file.",
            allow_sending_without_reply=True,
        )
        return

    status_message = await call.message.answer(
        bm.downloading_video_status(lang=user_lang)
    )
    try:
        res = await download_universal_media(original_url)
        if not res or not res.media_list:
            await handle_download_error(call.message, lang=user_lang)
            return

        first_item = res.media_list[0]
        is_local = os.path.isfile(first_item.url)
        document = (
            FSInputFile(
                first_item.url, filename=os.path.basename(first_item.url)
            ) if is_local else URLInputFile(first_item.url)
        )
        try:
            sent = await call.message.reply_document(
                document=document,
                allow_sending_without_reply=True,
                caption=file_caption,
                disable_content_type_detection=True,
            )
            if sent and sent.document:
                await db.add_file(
                    doc_cache_key, sent.document.file_id, "document"
                )
                logging.info("Universal file button: document cached")
            else:
                logging.warning("Universal file button: non-document response")
        finally:
            if is_local:
                await remove_file(first_item.url)
            if first_item.thumb and os.path.isfile(first_item.thumb):
                await remove_file(first_item.thumb)
    except Exception as exc:
        logging.error("Universal file button failed: %s", type(exc).__name__)
        await handle_download_error(call.message, lang=user_lang)
    finally:
        await safe_delete_message(status_message)


@router.callback_query(F.data.startswith("audio:univ:"))
async def download_universal_audio_callback(call: types.CallbackQuery):
    if not call.message:
        return
    await call.answer()

    url_hash = call.data.removeprefix("audio:univ:")
    if not re.fullmatch(r"[a-f0-9]{12}", url_hash):
        return
    # Legacy buttons must never use authenticated Facebook posts or a
    # previously cached file_id. Facebook MP3 and file buttons are disabled.
    callback_url = _recover_callback_url(call.message, url_hash)
    if callback_url and _is_facebook_url(callback_url):
        await call.message.reply(
            "Este botão ainda não está disponível para o Facebook.",
            allow_sending_without_reply=True,
        )
        return

    user_lang = await db.get_language(call.from_user.id)
    original_url = _recover_callback_url(call.message, url_hash)
    stable_audio_key = _cache_key(url_hash, "audio")
    # The new stable key survives restarts even if the URL map is gone.
    cached_audio_id = await db.get_file_id(stable_audio_key)
    audio_cache_key = build_audio_cache_key(original_url) if original_url else None
    if not cached_audio_id and audio_cache_key:
        cached_audio_id = await db.get_file_id(audio_cache_key)

    if not cached_audio_id and not original_url:
        await call.message.reply(
            "Envie o link novamente para baixar o MP3."
            if _is_pt(user_lang) else
            "Please send the link again to download MP3.",
            allow_sending_without_reply=True,
        )
        return

    status_message = await call.message.answer(
        bm.downloading_audio_status(lang=user_lang)
    )
    bot_url = await get_bot_url(bot)
    bot_avatar = await get_bot_avatar_thumbnail(bot)

    if cached_audio_id:
        try:
            await safe_edit_text(
                status_message, bm.uploading_status(lang=user_lang)
            )
            await send_chat_action_if_needed(
                bot, call.message.chat.id, "upload_audio",
                call.message.business_connection_id,
            )
            sent_cached_audio = await send_audio_with_thumbnail(
                call.message.reply_audio,
                audio=cached_audio_id,
                caption=bm.captions(None, None, bot_url),
                bot_avatar=bot_avatar,
                bot_url=bot_url,
                parse_mode="HTML",
            )
            if sent_cached_audio and sent_cached_audio.audio:
                logging.info("Universal audio cache hit")
                await safe_delete_message(status_message)
                return
        except TelegramBadRequest:
            logging.warning("Universal cached audio ID rejected")
        if not original_url:
            await safe_delete_message(status_message)
            await call.message.reply(
                "Envie o link novamente para baixar o MP3."
                if _is_pt(user_lang) else
                "Please send the link again to download MP3.",
                allow_sending_without_reply=True,
            )
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
            await call.message.reply(bm.audio_too_large(lang=user_lang), allow_sending_without_reply=True)
            await remove_file(output_mp3_path)
            await safe_delete_message(status_message)
            return

        await safe_edit_text(status_message, bm.uploading_status(lang=user_lang))
        await send_chat_action_if_needed(bot, call.message.chat.id, "upload_audio", call.message.business_connection_id)

        sent_audio = await send_audio_with_thumbnail(
            call.message.reply_audio,
            audio=FSInputFile(output_mp3_path),
            title=res.description or (
                "Áudio"
                if not str(user_lang).lower().startswith("en")
                else "Universal Audio"
            ),
            caption=bm.captions(None, None, bot_url),
            audio_path=output_mp3_path,
            bot_avatar=bot_avatar,
            bot_url=bot_url,
            parse_mode="HTML",
        )

        if sent_audio and sent_audio.audio:
            await db.add_file(
                stable_audio_key, sent_audio.audio.file_id, "audio"
            )
            if audio_cache_key:
                await db.add_file(
                    audio_cache_key, sent_audio.audio.file_id, "audio"
                )
            logging.info("Universal audio cached in database")

        await remove_file(output_mp3_path)
        if os.path.exists(input_file_path):
            await remove_file(input_file_path)

    except Exception as exc:
        logging.exception("Erro ao processar audio:univ: %s", exc)
        await handle_download_error(call.message, lang=user_lang)
    finally:
        await safe_delete_message(status_message)
