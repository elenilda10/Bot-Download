import asyncio
import hashlib
import json
import os
import re
import time
from typing import Optional
from urllib.parse import urlparse
from weakref import WeakValueDictionary

from aiogram import F, Router, types
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo, URLInputFile

import keyboards as kb
import messages as bm
from app_context import bot, db, send_analytics
from config import OUTPUT_DIR
from handlers.request_dedupe import claim_message_request
from handlers.utils import (
    get_bot_url,
    get_message_text,
    handle_download_error,
    load_user_settings,
    maybe_delete_user_message,
    react_to_message,
    safe_delete_message,
    safe_edit_text,
    should_skip_duplicate_business_message,
    with_message_logging,
)
from services.logger import logger as logging, summarize_text_for_log
from services.platforms.instagram_media import fetch_instagram_media, get_instagram_preview_url, strip_instagram_url
from utils.media_cache import build_media_cache_key

logging = logging.bind(service="instagram")
_INSTAGRAM_LINK_REGEX = (
    r"(https?://(www\.)?instagram\.com/(p|reels|reel|share|stories/[^/?#&]+)/[\w-]+)"
)

router = Router()

# A source post can be sent from Telegram without a second Instagram request.
# Lock only matching post IDs; other users' different posts stay concurrent.
_POST_LOCKS: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def _instagram_identity(url: str) -> str:
    path = urlparse(strip_instagram_url(url)).path.strip("/")
    segments = path.split("/")
    if len(segments) >= 2 and segments[0] in {"p", "reel", "reels"}:
        return f"post:{segments[1]}"
    if len(segments) >= 3 and segments[0] == "stories":
        return f"story:{segments[1]}:{segments[2]}"
    return path or strip_instagram_url(url)


def _cache_key(url: str, kind: str) -> str:
    identity = _instagram_identity(url)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return build_media_cache_key("instagram", variant=f"message-v1:{kind}:{digest}")


def _get_post_lock(url: str) -> asyncio.Lock:
    identity = _instagram_identity(url)
    lock = _POST_LOCKS.get(identity)
    if lock is None:
        lock = asyncio.Lock()
        _POST_LOCKS[identity] = lock
    return lock


def _cached_keyboard(url: str, settings: dict, kind: str, lang: str | None):
    return kb.return_video_info_keyboard(
        views=None, likes=None, comments=None, shares=None,
        music_play_url=None, video_url=url, user_settings=settings,
        has_video=kind == "video", platform="instagram",
        lang=lang or "pt",
    )


async def _try_instagram_cache(
    message: types.Message, url: str, settings: dict,
    bot_url: str, lang: str | None,
) -> bool:
    """Never mix video/photo/album file_id types."""
    caption = bm.captions(settings.get("captions", "on"), None, bot_url)
    for kind in ("video", "photo"):
        cached_id = await db.get_file_id(_cache_key(url, kind))
        if not cached_id:
            continue
        try:
            keyboard = _cached_keyboard(url, settings, kind, lang)
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
                logging.info("Instagram cache hit: %s", kind)
                return True
        except TelegramBadRequest:
            logging.warning("Instagram cached %s file_id rejected", kind)

    manifest = await db.get_file_id(_cache_key(url, "album"))
    if not manifest:
        return False
    try:
        data = json.loads(manifest)
        entries = data.get("items")
        if (data.get("version") != 1 or not isinstance(entries, list)
                or not 2 <= len(entries) <= 10):
            return False
        group = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                return False
            kind, file_id = entry.get("kind"), entry.get("file_id")
            if not isinstance(file_id, str) or not file_id:
                return False
            common = dict(
                media=file_id, parse_mode=ParseMode.HTML,
                caption=(caption or None) if index == 0 else None,
            )
            if kind == "video":
                group.append(InputMediaVideo(**common, supports_streaming=True))
            elif kind == "photo":
                group.append(InputMediaPhoto(**common))
            else:
                return False
        sent_group = await message.reply_media_group(
            media=group, allow_sending_without_reply=True,
        )
        if sent_group and len(sent_group) == len(group):
            logging.info("Instagram cache hit: album size=%s", len(group))
            return True
    except (TelegramBadRequest, ValueError, TypeError, AttributeError):
        logging.warning("Instagram cached album invalid; downloading again")
    return False



@router.message(
    F.text.regexp(_INSTAGRAM_LINK_REGEX, mode="search")
    | F.caption.regexp(_INSTAGRAM_LINK_REGEX, mode="search")
)
@router.business_message(
    F.text.regexp(_INSTAGRAM_LINK_REGEX, mode="search")
    | F.caption.regexp(_INSTAGRAM_LINK_REGEX, mode="search")
)
@with_message_logging("instagram", "message")
async def process_instagram(message: types.Message, direct_url: Optional[str] = None):
    request_lease = None
    business_id = getattr(message, "business_connection_id", None)
    text = direct_url or get_message_text(message)

    logging.info(
        "Instagram request: user_id=%s url=%s",
        message.from_user.id if message.from_user else "unknown",
        summarize_text_for_log(text),
    )

    if await should_skip_duplicate_business_message(message, bot, service_name="Instagram", logger=logging):
        return

    request_url_match = re.search(_INSTAGRAM_LINK_REGEX, text or "")
    if not request_url_match:
        return

    url = request_url_match.group(0).strip()
    request_lease = await claim_message_request(message, service="instagram", url=url)
    if request_lease is None:
        return

    await react_to_message(message, "👾", business_id=business_id)
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_VIDEO, business_connection_id=business_id)

    user_lang = await db.get_language(message.from_user.id)

    status_message: Optional[types.Message] = None
    if business_id is None:
        status_message = await message.answer(
            bm.downloading_video_status(lang=user_lang)
        )

    local_files_to_clean: list[str] = []
    try:
        await send_analytics(user_id=message.from_user.id, chat_type=message.chat.type, action_name="instagram")
        user_settings = await load_user_settings(db, message)
        bot_url = await get_bot_url(bot)

        async with _get_post_lock(url):
            # Cache first: reusing a Telegram file_id avoids extraction,
            # downloading, re-uploading and expensive thumbnail processing.
            cache_started = time.monotonic()
            cached = await _try_instagram_cache(
                message, url, user_settings, bot_url, user_lang
            )
            logging.info(
                "Instagram timing: stage=cache_lookup seconds=%.2f hit=%s",
                time.monotonic() - cache_started, cached,
            )
            if cached:
                if request_lease is not None:
                    request_lease.mark_success()
                await maybe_delete_user_message(
                    message, user_settings.get("delete_message")
                )
                return

            extract_started = time.monotonic()
            video_data = await fetch_instagram_media(url, output_dir=OUTPUT_DIR)
            logging.info(
                "Instagram timing: stage=extract seconds=%.2f items=%s",
                time.monotonic() - extract_started,
                len(video_data.media_list) if video_data else 0,
            )
            if not video_data or not video_data.media_list:
                await handle_download_error(message, business_id=business_id)
                await react_to_message(message, "👎", business_id=business_id)
                return

            await safe_edit_text(status_message, bm.uploading_status(lang=user_lang))
            caption = bm.captions(
                user_settings.get("captions", "on"), video_data.description, bot_url
            )
            media_count = len(video_data.media_list)
            first_item = video_data.media_list[0]
            preview_url = get_instagram_preview_url(first_item)
            keyboard = kb.return_video_info_keyboard(
                views=None, likes=None, comments=None, shares=None,
                music_play_url=preview_url, video_url=url,
                user_settings=user_settings,
                has_video=(media_count == 1 and first_item.type == "video"),
                platform="instagram", lang=user_lang or "pt",
            )

            for item in video_data.media_list:
                if os.path.isfile(item.url):
                    local_files_to_clean.append(item.url)
                if item.thumb and os.path.isfile(item.thumb):
                    local_files_to_clean.append(item.thumb)

            upload_started = time.monotonic()
            if media_count == 1:
                item = first_item
                is_local = os.path.isfile(item.url)
                file_input = (
                    FSInputFile(item.url) if is_local else URLInputFile(item.url)
                )
                if item.type == "video":
                    thumbnail = (
                        FSInputFile(item.thumb)
                        if item.thumb and os.path.isfile(item.thumb) else None
                    )
                    sent = await message.reply_video(
                        video=file_input, thumbnail=thumbnail,
                        caption=caption or None, parse_mode=ParseMode.HTML,
                        width=item.width or 1280, height=item.height or 720,
                        duration=item.duration or None, supports_streaming=True,
                        reply_markup=keyboard, allow_sending_without_reply=True,
                    )
                    if sent and sent.video:
                        await db.add_file(
                            _cache_key(url, "video"), sent.video.file_id, "video"
                        )
                        logging.info("Instagram cache stored: video")
                else:
                    sent = await message.reply_photo(
                        photo=file_input, caption=caption or None,
                        parse_mode=ParseMode.HTML, reply_markup=keyboard,
                        allow_sending_without_reply=True,
                    )
                    if sent and sent.photo:
                        await db.add_file(
                            _cache_key(url, "photo"), sent.photo[-1].file_id, "photo"
                        )
                        logging.info("Instagram cache stored: photo")
            else:
                group = []
                for index, item in enumerate(video_data.media_list[:10]):
                    file_input = (
                        FSInputFile(item.url) if os.path.isfile(item.url)
                        else URLInputFile(item.url)
                    )
                    common = dict(
                        media=file_input,
                        caption=(caption or None) if index == 0 else None,
                        parse_mode=ParseMode.HTML,
                    )
                    if item.type == "video":
                        group.append(InputMediaVideo(
                            **common,
                            width=item.width or 1280,
                            height=item.height or 720,
                            duration=item.duration or None,
                            supports_streaming=True,
                        ))
                    else:
                        group.append(InputMediaPhoto(**common))

                sent_group = await message.reply_media_group(
                    media=group, allow_sending_without_reply=True
                )
                if sent_group and len(sent_group) == len(group):
                    entries = []
                    for sent in sent_group:
                        if sent.video:
                            entries.append({
                                "kind": "video", "file_id": sent.video.file_id
                            })
                        elif sent.photo:
                            entries.append({
                                "kind": "photo", "file_id": sent.photo[-1].file_id
                            })
                    if len(entries) == len(sent_group):
                        await db.add_file(
                            _cache_key(url, "album"),
                            json.dumps(
                                {"version": 1, "items": entries},
                                separators=(",", ":"),
                            ),
                            "album_manifest",
                        )
                        logging.info("Instagram cache stored: album size=%s", len(entries))
            logging.info(
                "Instagram timing: stage=telegram_upload seconds=%.2f",
                time.monotonic() - upload_started,
            )
            if request_lease is not None:
                request_lease.mark_success()
            await maybe_delete_user_message(
                message, user_settings.get("delete_message")
            )

    except Exception as exc:
        logging.exception("Error processing Instagram: %s", exc)
        if status_message:
            await safe_delete_message(status_message)
        await react_to_message(message, "👎", business_id=business_id)
        await handle_download_error(message, business_id=business_id)
    finally:
        await safe_delete_message(status_message)
        for path in set(local_files_to_clean):
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass
        if request_lease is not None:
            request_lease.finish()

from config import CHANNEL_ID, MAX_FILE_SIZE
from handlers.deps import HandlerDependencies
from handlers.inline_utils import register_inline_send_handlers
from handlers.instagram_inline import handle_instagram_inline_query, send_inline_instagram_media

_instagram_deps = HandlerDependencies(bot=bot, db=db, send_analytics=send_analytics)

@router.inline_query(F.query.regexp(_INSTAGRAM_LINK_REGEX, mode="search"))
async def _instagram_inline_query(query: types.InlineQuery) -> None:
    await handle_instagram_inline_query(
        query,
        deps=_instagram_deps,
        channel_id=CHANNEL_ID,
    )

async def _send_inline_instagram_video(
    token: str,
    inline_message_id: str,
    actor_name: str,
    actor_user_id: int,
    request_event_id: str,
    duplicate_handler: str,
) -> None:
    await send_inline_instagram_media(
        token=token,
        inline_message_id=inline_message_id,
        actor_name=actor_name,
        actor_user_id=actor_user_id,
        request_event_id=request_event_id,
        duplicate_handler=duplicate_handler,
        deps=_instagram_deps,
        channel_id=CHANNEL_ID,
        max_file_size=MAX_FILE_SIZE,
    )

chosen_inline_instagram_result, send_inline_instagram_video_callback = (
    register_inline_send_handlers(
        router,
        service="instagram",
        result_prefix="instagram_inline:",
        callback_prefix="inline:instagram:",
        send_fn=_send_inline_instagram_video,
        missing_inline_message_warning="Missing inline_message_id in Instagram chosen result",
        chosen_handler_name="chosen_inline_instagram_result",
        callback_handler_name="send_inline_instagram_video_callback",
    )
)
