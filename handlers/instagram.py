import asyncio
import os
import re
from typing import Optional

from aiogram import F, Router, types
from aiogram.enums import ChatAction, ParseMode
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
from services.platforms.instagram_media import fetch_instagram_media, get_instagram_preview_url

logging = logging.bind(service="instagram")
_INSTAGRAM_LINK_REGEX = (
    r"(https?://(www\.)?instagram\.com/(p|reels|reel|share|stories/[^/?#&]+)/[\w-]+)"
)

router = Router()

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

    status_message: Optional[types.Message] = None
    if business_id is None:
        status_message = await message.answer(bm.downloading_video_status())

    try:
        await send_analytics(user_id=message.from_user.id, chat_type=message.chat.type, action_name="instagram")
        user_settings = await load_user_settings(db, message)
        bot_url = await get_bot_url(bot)

        video_data = await fetch_instagram_media(url, output_dir=OUTPUT_DIR)
        if not video_data or not video_data.media_list:
            if status_message:
                await safe_delete_message(status_message)
            await handle_download_error(message, business_id=business_id)
            await react_to_message(message, "👎", business_id=business_id)
            return

        if status_message:
            await safe_edit_text(status_message, bm.uploading_status())

        user_captions_mode = user_settings.get("captions", "on")
        caption = bm.captions(user_captions_mode, video_data.description, bot_url)

        media_count = len(video_data.media_list)
        preview_url = get_instagram_preview_url(video_data.media_list[0]) if media_count > 0 else None

        keyboard = kb.return_video_info_keyboard(
            None,
            None,
            None,
            None,
            preview_url,
            url,
            user_settings,
        )

        local_files_to_clean = []

        # 1. Envio de Mídia Única
        if media_count == 1:
            item = video_data.media_list[0]
            is_local = os.path.exists(item.url)
            if is_local:
                local_files_to_clean.append(item.url)

            if item.type == "video":
                video_file = FSInputFile(item.url) if is_local else URLInputFile(item.url)
                thumb_file = FSInputFile(item.thumb) if (item.thumb and os.path.exists(item.thumb)) else None
                if item.thumb and os.path.exists(item.thumb):
                    local_files_to_clean.append(item.thumb)

                await message.reply_video(
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
            else:
                photo_file = FSInputFile(item.url) if is_local else URLInputFile(item.url)
                await message.reply_photo(
                    photo=photo_file,
                    caption=caption or None,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )

        # 2. Envio de Carrossel
        else:
            media_group = []
            for idx, item in enumerate(video_data.media_list[:10]):
                is_local = os.path.exists(item.url)
                if is_local:
                    local_files_to_clean.append(item.url)

                item_caption = caption if idx == 0 else None
                if item.type == "video":
                    file_input = FSInputFile(item.url) if is_local else URLInputFile(item.url)
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
                    file_input = FSInputFile(item.url) if is_local else URLInputFile(item.url)
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
        logging.exception("Error processing Instagram: %s", exc)
        if status_message:
            await safe_delete_message(status_message)
        await react_to_message(message, "👎", business_id=business_id)
        await handle_download_error(message, business_id=business_id)
    finally:
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
