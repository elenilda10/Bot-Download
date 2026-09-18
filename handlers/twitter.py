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
from config import CHANNEL_ID, MAX_FILE_SIZE, OUTPUT_DIR
from handlers.deps import build_handler_dependencies
from handlers.inline_utils import register_inline_send_handlers
from handlers.request_dedupe import claim_message_request
from handlers.twitter_inline import handle_twitter_inline_query, send_inline_twitter_media
from handlers.utils import (
    handle_download_error,
    get_bot_url,
    get_message_text,
    load_user_settings,
    maybe_delete_user_message,
    react_to_message,
    safe_delete_message,
    safe_edit_text,
    should_skip_duplicate_business_message,
    with_message_logging,
)
from services.logger import logger as logging
from services.platforms.twitter_media import download_twitter_media

logging = logging.bind(service="twitter")
_TWITTER_LINK_REGEX = r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)"

router = Router()


@router.message(
    F.text.regexp(_TWITTER_LINK_REGEX) | (F.caption & F.caption.regexp(_TWITTER_LINK_REGEX))
)
@with_message_logging("twitter", "message")
async def handle_twitter_message(message: types.Message, **kwargs):
    business_id = getattr(message, "business_connection_id", None)
    if await should_skip_duplicate_business_message(message, bot, service_name="Twitter", logger=logging):
        return

    text = get_message_text(message)
    match = re.search(_TWITTER_LINK_REGEX, text)
    if not match:
        return

    url = match.group(0)
    user_settings = await load_user_settings(db, message)
    bot_url = await get_bot_url(bot)
    request_lease = None

    status_message = None
    local_files_to_clean = []

    try:
        request_lease = await claim_message_request(message, service="twitter", url=url)
        if request_lease is None:
            return

        if business_id:
            await bot.send_chat_action(message.chat.id, ChatAction.RECORD_VIDEO, business_connection_id=business_id)
        else:
            await bot.send_chat_action(message.chat.id, ChatAction.RECORD_VIDEO)

        status_message = await message.reply(bm.downloading_video_status())

        media_result = await download_twitter_media(url, output_dir=OUTPUT_DIR)
        if not media_result or not media_result.media_list:
            if status_message: await safe_delete_message(status_message)
            await handle_download_error(message, business_id=business_id)
            await react_to_message(message, "👎", business_id=business_id)
            return

        for item in media_result.media_list:
            if os.path.exists(item.url):
                local_files_to_clean.append(item.url)
            if item.thumb and os.path.exists(item.thumb):
                local_files_to_clean.append(item.thumb)

        caption = bm.captions(
            user_settings.get("captions", "on"),
            media_result.description,
            bot_url,
        )

        reply_markup = kb.return_video_info_keyboard(
            None,
            media_result.likes,
            media_result.replies,
            media_result.retweets,
            None,
            url,
            user_settings,
        )

        if len(media_result.media_list) == 1:
            item = media_result.media_list[0]
            is_local = os.path.exists(item.url)
            file_input = FSInputFile(item.url) if is_local else URLInputFile(item.url)

            if item.type == "video":
                thumb_input = FSInputFile(item.thumb) if item.thumb and os.path.exists(item.thumb) else None
                await message.reply_video(
                    video=file_input,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    thumbnail=thumb_input,
                    width=item.width or 1280,
                    height=item.height or 720,
                    duration=item.duration or 0,
                    reply_markup=reply_markup,
                    supports_streaming=True,
                )
            else:
                await message.reply_photo(
                    photo=file_input,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
        else:
            media_group = []
            for idx, item in enumerate(media_result.media_list):
                is_local = os.path.exists(item.url)
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
        logging.exception("Error handling Twitter media: %s", exc)
        if status_message:
            await safe_delete_message(status_message)
        await react_to_message(message, "👎", business_id=business_id)
        await handle_download_error(message, business_id=business_id)
    finally:
        if request_lease is not None:
            request_lease.finish()


@router.inline_query(F.query.regexp(_TWITTER_LINK_REGEX))
async def twitter_inline_query_handler(query: types.InlineQuery) -> None:
    deps = build_handler_dependencies(bot=bot, db=db, send_analytics=send_analytics)
    await handle_twitter_inline_query(
        query,
        deps=deps,
        twitter_link_regex=_TWITTER_LINK_REGEX,
        channel_id=CHANNEL_ID,
    )


async def _send_inline_twitter_media(
    *,
    token: str,
    inline_message_id: str,
    actor_name: str,
    actor_user_id: int,
    request_event_id: str,
    duplicate_handler: str,
) -> None:
    deps = build_handler_dependencies(bot=bot, db=db, send_analytics=send_analytics)
    await send_inline_twitter_media(
        token=token,
        inline_message_id=inline_message_id,
        actor_name=actor_name,
        actor_user_id=actor_user_id,
        request_event_id=request_event_id,
        duplicate_handler=duplicate_handler,
        deps=deps,
        channel_id=CHANNEL_ID,
        max_file_size=MAX_FILE_SIZE,
    )


chosen_inline_twitter_result, send_inline_twitter_media_callback = (
    register_inline_send_handlers(
        router,
        service="twitter",
        result_prefix="twitter_inline:",
        callback_prefix="inline:twitter:",
        send_fn=_send_inline_twitter_media,
        missing_inline_message_warning=(
            "Chosen inline Twitter result is missing inline_message_id"
        ),
        chosen_flow="twitter_chosen_inline",
        callback_flow="twitter_inline_callback",
        chosen_handler_name="chosen_inline_twitter_result",
        callback_handler_name="send_inline_twitter_media_callback",
    )
)


# Alias for universal downloader compatibility
handle_tweet_links = handle_twitter_message
