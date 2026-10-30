"""Dedicated Facebook Telegram flow: resolve, download, caption and send.

Facebook-specific downloads never enter the Universal message handler. Shared
download binaries are used only by the services/platforms layer. Firefox
cookies are opt-in for the administrator's ordinary private conversation.
No Facebook content is stored in the shared Telegram file_id cache.
"""
from __future__ import annotations
from i18n import t as _t

import asyncio
import hashlib
import os
import re
import shutil
import tempfile
import time
from typing import Optional

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramBadRequest
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
from services.media.delivery import send_audio_with_thumbnail
from services.platforms import facebook_cache as fc
from services.platforms.facebook_media import (
    FACEBOOK_LINK_REGEX, _is_facebook_url, _is_facebook_media_link,
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


def _facebook_keyboard(
    url: str, lang: str | None,
    settings: dict | None = None,
    token: str | None = None,
    *, has_video: bool = False, single: bool = True,
) -> InlineKeyboardMarkup | None:
    portuguese = not lang or lang.lower().startswith("pt")
    settings = settings or {}
    buttons = []
    if token and single and has_video and settings.get("audio_button") == "on":
        buttons.append([InlineKeyboardButton(
            text=_t('handlers.facebook._facebook_keyboard.2432fa18', lang) if portuguese else _t('handlers.facebook._facebook_keyboard.2432fa18', lang),
            callback_data=f"fba:{token}",
        )])
    if (
        token and single
        and settings.get("file_button") == "on"
        and settings.get("as_document") != "on"
    ):
        buttons.append([InlineKeyboardButton(
            text=_t('handlers.facebook._facebook_keyboard.d7fd5aab', lang) if portuguese else _t('handlers.facebook._facebook_keyboard.d7fd5aab', lang),
            callback_data=f"fbd:{token}",
        )])
    # Existing source button remains available. Never add direct authenticated
    # media URLs or reusable file IDs to a callback payload.
    if settings.get("url_button", "on") != "off":
        buttons.append([InlineKeyboardButton(
            text=_t('handlers.facebook._facebook_keyboard.b11161d2', lang) if portuguese else _t('handlers.facebook._facebook_keyboard.73305390', lang),
            url=url,
        )])
    return InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None


async def _load_cache(url: str, scope: str, kind: str) -> dict | None:
    raw = await db.get_file_id(fc.key_for(url, scope, kind))
    payload = fc.decode(raw)
    if payload and payload.get("url") == url and payload.get("scope") == scope:
        if fc.media_entries(payload):
            return payload
    return None


async def _save_cache(url: str, scope: str, kind: str, description: str,
                      items: list[dict]) -> None:
    record = {
        "version": fc.CACHE_VERSION,
        "url": url, "scope": scope,
        "description": description or "",
        "items": items,
    }
    if fc.media_entries(record):
        await db.add_file(
            fc.key_for(url, scope, kind), fc.encode(record),
            f"facebook_{kind}",
        )


def _file_id_entries(sent) -> list[dict]:
    if not sent:
        return []
    messages = sent if isinstance(sent, list) else [sent]
    found = []
    for message in messages:
        video = getattr(message, "video", None)
        photo = getattr(message, "photo", None)
        document = getattr(message, "document", None)
        audio = getattr(message, "audio", None)
        if video:
            found.append({"kind": "video", "file_id": video.file_id})
        elif photo:
            found.append({"kind": "photo", "file_id": photo[-1].file_id})
        elif document:
            found.append({"kind": "document", "file_id": document.file_id})
        elif audio:
            found.append({"kind": "audio", "file_id": audio.file_id})
        else:
            return []
    return found


async def _send_cached(message: types.Message, cache: dict,
                       keyboard: InlineKeyboardMarkup, settings: dict,
                       bot_url: str):
    entries = fc.media_entries(cache)
    if not entries:
        return None
    caption = bm.captions(
        settings.get("captions", "on"), cache.get("description", ""), bot_url,
    )
    if len(entries) == 1:
        item = entries[0]
        if item["kind"] == "video":
            return await message.answer_video(
                video=item["file_id"], caption=caption or None,
                parse_mode=ParseMode.HTML, supports_streaming=True,
                reply_markup=keyboard, allow_sending_without_reply=True,
            )
        if item["kind"] == "photo":
            return await message.answer_photo(
                photo=item["file_id"], caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard, allow_sending_without_reply=True,
            )
        if item["kind"] == "document":
            return await message.answer_document(
                document=item["file_id"], caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard, allow_sending_without_reply=True,
            )
        return None
    album = []
    for index, entry in enumerate(entries):
        if entry["kind"] not in {"video", "photo"}:
            return None
        item_caption = caption if index == 0 else None
        cls = InputMediaVideo if entry["kind"] == "video" else InputMediaPhoto
        album.append(cls(
            media=entry["file_id"], caption=item_caption,
            parse_mode=ParseMode.HTML,
        ))
    sent = await message.answer_media_group(
        media=album, allow_sending_without_reply=True,
    )
    # Telegram doesn't support inline buttons on media groups. The user
    # opted to omit an extra source-link message after a Facebook album.
    return sent


async def _create_callback_token(
    url: str, scope: str, owner_id: int | None, chat_id: int,
) -> str:
    token = fc.new_token()
    await db.add_file(
        fc.callback_key(token),
        fc.encode(fc.callback_record(url, scope, owner_id, chat_id)),
        "facebook_callback",
    )
    return token


def _auth_profile(user_id: int | None, chat_type: str, business_id=None) -> str | None:
    if user_id == ADMIN_ID and chat_type == "private" and business_id is None:
        return os.environ.get("FACEBOOK_FIREFOX_PROFILE", "").strip() or None
    return None


def _facebook_gallery_first_enabled() -> bool:
    """Never enable unverified Facebook media in normal user/callback flows.

    FB_GALLERY_FIRST was previously on by default and could deliver photos
    from unrelated posts. Even FB_GALLERY_FIRST=1 must not override this
    safety requirement. Only explicit private-admin /fbteste can bypass the
    post identity check, and its output is visibly marked unverified.
    """
    return False


def _progress_update(key: str, downloaded: int, total: int | None, speed: float | None):
    info = _PROGRESS.get(key)
    if not info:
        return
    now = time.monotonic()
    elapsed = max(now - info["last_time"], 0.001)
    calculated = max(downloaded - info["last_bytes"], 0) / elapsed
    speed = speed if speed and speed > 0 else calculated
    info.update({
        "percent": f"{downloaded / total * 100:.1f}%" if total else _t('handlers.facebook._progress_update.8be1f22d', None),
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
        await call.answer(_t('handlers.facebook.facebook_progress_callback.2517d389', None), show_alert=True)
        return
    if state.get("status") == "uploading":
        await call.answer(_t('handlers.facebook.facebook_progress_callback.13f73340', None), show_alert=True)
        return
    message = (
        _t('handlers.facebook.facebook_progress_callback.e81bb164', None, p0=state['percent'], p1=state['downloaded'], p2=state['total'], p3=state['speed'])
    )
    await call.answer(message, show_alert=True)


@router.callback_query(
    F.data.startswith("fba:") | F.data.startswith("fbd:")
)
async def facebook_format_callback(call: types.CallbackQuery) -> None:
    """Send Facebook MP3 or source document, enforcing persisted privacy scope.

    Callback tokens are random and records expire after seven days. An owner
    record also requires the same Telegram account AND private chat, and is
    never redeemed using an anonymous cache or by a forwarded recipient.
    """
    data = call.data or ""
    kind = "audio" if data.startswith("fba:") else "document"
    token = data[4:]
    try:
        key = fc.callback_key(token)
    except ValueError:
        await call.answer(_t('handlers.facebook.facebook_format_callback.a7070282', None), show_alert=True)
        return

    actor = call.from_user.id if call.from_user else None
    chat = call.message.chat if call.message else None
    if actor is None or chat is None or await cmd_mod.is_banned(actor, chat.id):
        await call.answer(_t('handlers.facebook.facebook_format_callback.44809136', None), show_alert=True)
        return

    record = fc.decode(await db.get_file_id(key))
    if not fc.callback_allowed(record, actor, chat.id, chat.type):
        await call.answer(
            _t('handlers.facebook.facebook_format_callback.0dbd14ec', None),
            show_alert=True,
        )
        return
    url = record.get("url")
    if not isinstance(url, str) or not _is_facebook_media_link(url):
        await call.answer(_t('handlers.facebook.facebook_format_callback.988aa82f', None), show_alert=True)
        return

    user_settings = await db.user_settings(actor)
    option = "audio_button" if kind == "audio" else "file_button"
    if user_settings.get(option) != "on":
        await call.answer(
            _t('handlers.facebook.facebook_format_callback.ada9b656', None),
            show_alert=True,
        )
        return
    scope = record["scope"]
    profile = (
        _auth_profile(actor, chat.type, getattr(call.message, "business_connection_id", None))
        if scope != "public" else None
    )
    if scope != "public" and not profile:
        await call.answer(_t('handlers.facebook.facebook_format_callback.22eb1900', None), show_alert=True)
        return

    await call.answer()
    status = None
    work_dir = None
    try:
        bot_url = await get_bot_url(bot)
        cached = None if _facebook_gallery_first_enabled() else await _load_cache(url, scope, kind)
        if cached and len(cached["items"]) == 1:
            entry = cached["items"][0]
            if entry["kind"] == kind:
                try:
                    caption = bm.captions(
                        user_settings.get("captions", "on"),
                        cached.get("description", ""), bot_url,
                    )
                    if kind == "document":
                        sent_cached = await call.message.answer_document(
                            document=entry["file_id"], caption=caption,
                            parse_mode=ParseMode.HTML, allow_sending_without_reply=True,
                        )
                    else:
                        sent_cached = await send_audio_with_thumbnail(
                            call.message.answer_audio,
                            audio=entry["file_id"], caption=caption,
                            bot_url=bot_url, parse_mode=ParseMode.HTML,
                        )
                    if sent_cached:
                        return
                except TelegramBadRequest:
                    logging.warning("Facebook cached conversion file ID rejected")

        status = await call.message.answer(
            bm.downloading_audio_status(lang=await db.get_language(actor), platform='Facebook')
            if kind == "audio"
            else bm.downloading_video_status(lang=await db.get_language(actor), platform='Facebook')
        )
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        work_dir = tempfile.mkdtemp(prefix="facebook-action-", dir=OUTPUT_DIR)
        result = await download_facebook_media(
            url, output_dir=work_dir, facebook_browser_profile=profile,
            facebook_gallery_first=_facebook_gallery_first_enabled(),
        )
        if not result or len(result.media_list) != 1:
            await call.message.answer(
                _t('handlers.facebook.facebook_format_callback.52d58f7c', None),
                allow_sending_without_reply=True,
            )
            return
        item = result.media_list[0]
        if (
            not os.path.isfile(item.url)
            or os.path.getsize(item.url) <= 0
            or os.path.getsize(item.url) >= MAX_FILE_SIZE
        ):
            await call.message.answer(
                _t('handlers.facebook.facebook_format_callback.acad8aba', None),
                allow_sending_without_reply=True,
            )
            return
        output_path = item.url
        if kind == "audio":
            if item.type != "video":
                await call.message.answer(
                    _t('handlers.facebook.facebook_format_callback.57448b90', None),
                    allow_sending_without_reply=True,
                )
                return
            output_path = os.path.join(work_dir, "facebook.mp3")
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-y", "-i", item.url, "-vn", "-codec:a", "libmp3lame",
                "-q:a", "2", output_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.communicate(), timeout=120)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                logging.warning("Facebook MP3 conversion timed out")
                await call.message.answer(
                    _t('handlers.facebook.facebook_format_callback.8f8d2e48', None),
                    allow_sending_without_reply=True,
                )
                return
            if proc.returncode != 0:
                logging.warning("Facebook MP3 conversion failed: %s", proc.returncode)
                await call.message.answer(
                    _t('handlers.facebook.facebook_format_callback.10e7f62f', None),
                    allow_sending_without_reply=True,
                )
                return
        if (
            not os.path.isfile(output_path)
            or os.path.getsize(output_path) <= 0
            or os.path.getsize(output_path) >= MAX_FILE_SIZE
        ):
            await call.message.answer(
                _t('handlers.facebook.facebook_format_callback.b9a04fe6', None),
                allow_sending_without_reply=True,
            )
            return

        await safe_edit_text(
            status, bm.uploading_status(lang=await db.get_language(actor), platform='Facebook'),
        )
        caption = bm.captions(
            user_settings.get("captions", "on"),
            result.description, bot_url,
        )
        await bot.send_chat_action(
            chat_id=chat.id,
            action=ChatAction.UPLOAD_AUDIO if kind == "audio" else ChatAction.UPLOAD_DOCUMENT,
        )
        if kind == "document":
            sent = await call.message.answer_document(
                document=FSInputFile(output_path, filename=os.path.basename(output_path)),
                caption=caption, parse_mode=ParseMode.HTML,
                disable_content_type_detection=True,
                allow_sending_without_reply=True,
            )
        else:
            sent = await send_audio_with_thumbnail(
                call.message.answer_audio,
                audio=FSInputFile(output_path, filename="facebook.mp3"),
                title=(result.description or _t('handlers.facebook.facebook_format_callback.97e012c2', None))[:128],
                bot_url=bot_url, caption=caption,
                parse_mode=ParseMode.HTML,
            )
        entries = _file_id_entries(sent)
        if (not _facebook_gallery_first_enabled() and entries
                and len(entries) == 1 and entries[0]["kind"] == kind):
            await _save_cache(url, scope, kind, result.description, entries)
    except Exception as exc:
        logging.exception(
            "Facebook %s callback failed: %s", kind, type(exc).__name__,
        )
        await call.message.answer(
            _t('handlers.facebook.facebook_format_callback.3b4dab0a', None),
            allow_sending_without_reply=True,
        )
    finally:
        if status:
            await safe_delete_message(status)
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


@router.message(Command("fbteste"))
async def facebook_gallery_first_test_command(message: types.Message) -> None:
    """Run a gallery-first extraction only in the admin's private chat.

    This intentionally skips post/photo association for diagnostic delivery.
    Public requests and the normal Facebook handler remain strict.
    """
    user_id = message.from_user.id if message.from_user else None
    chat = message.chat
    profile = _auth_profile(
        user_id, chat.type if chat else "",
        getattr(message, "business_connection_id", None),
    )
    if not profile:
        await message.answer(_t('handlers.facebook.facebook_gallery_first_test_command.5a51ced6', None))
        return
    if not _extract_facebook_link(get_message_text(message)):
        await message.answer(
            _t('handlers.facebook.facebook_gallery_first_test_command.11806b7c', None)
        )
        return
    await process_facebook_link(message, facebook_gallery_first_test=True)


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
async def process_facebook_link(
    message: types.Message, *, facebook_gallery_first_test: bool = False,
) -> None:
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
    settings = {}
    try:
        target = await _resolve_facebook_short_link(source)
        if not target:
            await message.answer(
                _t('handlers.facebook.process_facebook_link.e91a31a3', None),
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

        profile = _auth_profile(user_id, message.chat.type, business_id)
        # Never use experimental gallery mode for anonymous or public chats.
        if facebook_gallery_first_test and not profile:
            await message.answer(_t('handlers.facebook.process_facebook_link.6d057cc1', None))
            return
        scope = fc.scope_for(profile, user_id)
        cache_kind = "document" if settings.get("as_document") == "on" else "media"
        # Bypass is only reachable via the explicit admin/private /fbteste.
        # Never deliver unverified media to ordinary users.
        use_gallery_first = facebook_gallery_first_test
        token = (
            None if facebook_gallery_first_test else
            await _create_callback_token(target, scope, user_id, chat_id)
        )

        # This route can receive mismatched media from Facebook. Do not use
        # or populate any persistent Telegram file_id cache until verified.
        cached = None if use_gallery_first else await _load_cache(target, scope, cache_kind)
        if cached:
            cached_items = fc.media_entries(cached)
            cached_keyboard = _facebook_keyboard(
                target, lang, settings, token,
                has_video=cached.get("has_video", any(
                    item["kind"] == "video" for item in cached_items
                )),
                single=len(cached_items) == 1,
            )
            try:
                sent_cached = await _send_cached(
                    message, cached, cached_keyboard, settings, bot_url,
                )
                if sent_cached:
                    logging.info("Facebook scoped cache hit: kind=%s scope=%s",
                                 cache_kind, "public" if scope == "public" else "private")
                    lease.mark_success()
                    try:
                        await maybe_delete_user_message(
                            message, settings.get("delete_message"),
                        )
                    except Exception:
                        pass
                    return
            except TelegramBadRequest:
                logging.warning("Facebook cached Telegram file ID was rejected")

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
        # No Facebook progress button: keep only the editable status message.
        if business_id is None:
            status = await message.answer(bm.downloading_video_status(lang=lang, platform='Facebook'))

        result = await download_facebook_media(
            target,
            output_dir=work_dir,
            facebook_browser_profile=profile,
            facebook_gallery_first_test=facebook_gallery_first_test,
            facebook_gallery_first=use_gallery_first,
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
            await safe_edit_text(status, bm.uploading_status(lang=lang, platform='Facebook'))

        caption = bm.captions(
            settings.get("captions", "on"), result.description, bot_url,
        )
        if facebook_gallery_first_test:
            # Keep this visible even if the user's normal caption preference is off.
            aviso = _t('handlers.facebook.process_facebook_link.28cbcc7f', None)
            caption = f"{aviso}\n\n{caption}" if caption else aviso
        has_video = any(item.type == "video" for item in result.media_list)
        keyboard = _facebook_keyboard(
            target, lang, settings, token,
            has_video=has_video, single=len(result.media_list) == 1,
        )
        sent_media = None
        if len(result.media_list) == 1:
            item = result.media_list[0]
            file = FSInputFile(item.url, filename=os.path.basename(item.url))
            if settings.get("as_document") == "on":
                sent_media = await message.answer_document(
                    document=file, caption=caption or None,
                    parse_mode=ParseMode.HTML,
                    disable_content_type_detection=True,
                    reply_markup=keyboard, allow_sending_without_reply=True,
                )
            elif item.type == "video":
                preview_path = item.thumb if item.thumb and os.path.isfile(item.thumb) else None
                sent_media = await message.answer_video(
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
                sent_media = await message.answer_photo(
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
            sent_media = await message.answer_media_group(
                media=album, allow_sending_without_reply=True,
            )
            # Album has no inline keyboard; no extra source-link message.

        # Cache only the proven scope: anonymous/public or authenticated/owner.
        # Never promote Firefox-authenticated content into a public cache.
        entries = [] if use_gallery_first else _file_id_entries(sent_media)
        if entries:
            try:
                cache_record = {
                    "version": fc.CACHE_VERSION, "url": target,
                    "scope": scope, "description": result.description or "",
                    "has_video": has_video, "items": entries,
                }
                if fc.media_entries(cache_record):
                    await db.add_file(
                        fc.key_for(target, scope, cache_kind),
                        fc.encode(cache_record),
                        f"facebook_{cache_kind}",
                    )
            except Exception as exc:
                logging.warning("Facebook cache write failed: %s", type(exc).__name__)

        lease.mark_success()
        try:
            if user_id is not None:
                await send_analytics(
                    user_id=user_id, chat_type=message.chat.type,
                    action_name="universal",
                )
            await maybe_delete_user_message(
                message, settings.get("delete_message"),
            )
        except Exception as exc:
            # A successful media upload must not be reported as failed because
            # an optional analytics/write/delete operation later raised.
            logging.warning("Facebook post-delivery housekeeping failed: %s",
                            type(exc).__name__)
    except Exception as exc:
        logging.exception("Facebook delivery failed: %s", type(exc).__name__)
        await handle_download_error(message, error=exc, business_id=business_id)
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
