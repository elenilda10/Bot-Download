import asyncio
import html
import os
import re
from typing import Optional
from aiogram import F, Router, types
from aiogram.enums import ChatAction, ParseMode
from aiogram.types import FSInputFile

import messages as bm
from app_context import bot, db, send_analytics
from config import CHANNEL_ID, MAX_FILE_SIZE, OUTPUT_DIR
from handlers.deps import build_handler_dependencies
from handlers.deezer_inline import handle_deezer_inline_query, send_inline_deezer_music
from handlers.inline_utils import register_inline_send_handlers, safe_answer_inline_query
from handlers.logging_utils import with_inline_send_logging
from handlers.request_dedupe import claim_message_request
from handlers.utils import (
    get_bot_url,
    get_message_text,
    handle_download_error,
    load_user_settings,
    maybe_delete_user_message,
    react_to_message,
    safe_delete_message,
    safe_edit_inline_media,
    safe_edit_inline_text,
    safe_edit_text,
    should_skip_duplicate_business_message,
    with_message_logging,
)
from services.logger import logger as logging, summarize_text_for_log
from services.platforms.deezer_media import download_deezer_track

logging = logging.bind(service="deezer")

_DEEZER_LINK_REGEX = r"(https?://(?:www\.)?deezer\.com/(?:[a-z]{2}/)?(?:track|album|playlist|episode)/[0-9]+|https?://deezer\.page\.link/[a-zA-Z0-9]+)"

router = Router(name=__name__)


def _format_artists(artist_raw: str) -> str:
    if not artist_raw:
        return "Deezer"
    artists = [a.strip() for a in re.split(r"[/;]+", artist_raw) if a.strip()]
    if not artists:
        return artist_raw
    if len(artists) == 1:
        return artists[0]
    return ", ".join(artists[:-1]) + " & " + artists[-1]


@router.message(
    F.text.regexp(_DEEZER_LINK_REGEX, mode="search") | F.caption.regexp(_DEEZER_LINK_REGEX, mode="search")
)
@router.business_message(
    F.text.regexp(_DEEZER_LINK_REGEX, mode="search") | F.caption.regexp(_DEEZER_LINK_REGEX, mode="search")
)
@with_message_logging("deezer", "message")
async def process_deezer(message: types.Message, direct_url: Optional[str] = None):
    request_lease = None
    business_id = getattr(message, "business_connection_id", None)
    text = direct_url or get_message_text(message)

    logging.info(
        "Deezer request: user_id=%s url=%s",
        message.from_user.id if message.from_user else "unknown",
        summarize_text_for_log(text),
    )

    if await should_skip_duplicate_business_message(message, bot, service_name="Deezer", logger=logging):
        return

    request_url_match = re.search(_DEEZER_LINK_REGEX, text or "")
    if not request_url_match:
        return

    url = request_url_match.group(0).strip()
    request_lease = await claim_message_request(message, service="deezer", url=url)
    if request_lease is None:
        return

    await react_to_message(message, "🎧", business_id=business_id)
    await bot.send_chat_action(
        chat_id=message.chat.id,
        action=ChatAction.RECORD_VOICE,
        business_connection_id=business_id,
    )

    status_message: Optional[types.Message] = None
    if business_id is None:
        status_message = await message.answer(bm.downloading_audio_status())

    try:
        await send_analytics(user_id=message.from_user.id, chat_type=message.chat.type, action_name="deezer")
        user_settings = await load_user_settings(db, message)
        bot_url = await get_bot_url(bot)

        track = await download_deezer_track(url, output_dir=OUTPUT_DIR)
        if not track or not os.path.exists(track.file_path):
            if status_message:
                await safe_delete_message(status_message)
            await handle_download_error(message, business_id=business_id)
            await react_to_message(message, "👎", business_id=business_id)
            return

        if status_message:
            await safe_edit_text(status_message, bm.uploading_status())

        audio_file = FSInputFile(track.file_path)
        thumb_file = FSInputFile(track.thumb_path) if (track.thumb_path and os.path.exists(track.thumb_path)) else None

        clean_artist = _format_artists(track.artist)
        bot_username = bot_url.split("/")[-1]

        caption = (
            f"🎵 <b>{html.escape(track.title)}</b>\n"
            f"👤 <b>{html.escape(clean_artist)}</b>\n\n"
            f"📥 @{bot_username}"
        )

        await message.reply_audio(
            audio=audio_file,
            title=track.title,
            performer=clean_artist,
            duration=track.duration or None,
            thumbnail=thumb_file,
            caption=caption,
            parse_mode=ParseMode.HTML,
        )

        if status_message:
            await safe_delete_message(status_message)

        if request_lease is not None:
            request_lease.mark_success()

        await maybe_delete_user_message(message, user_settings.get("delete_message"))

        await asyncio.sleep(2)
        for path in [track.file_path, track.thumb_path]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

    except Exception as exc:
        logging.exception("Error processing Deezer: %s", exc)
        if status_message:
            await safe_delete_message(status_message)
        await react_to_message(message, "👎", business_id=business_id)
        await handle_download_error(message, business_id=business_id)
    finally:
        if request_lease is not None:
            request_lease.finish()


@router.inline_query()
async def deezer_inline_query_handler(query: types.InlineQuery) -> None:
    deps = build_handler_dependencies(bot=bot, db=db, send_analytics=send_analytics)
    await handle_deezer_inline_query(
        query,
        deps=deps,
        channel_id=CHANNEL_ID,
        safe_answer_inline_query_fn=safe_answer_inline_query,
    )


@with_inline_send_logging("deezer", "music_inline_send")
async def _send_inline_deezer_music(
    *,
    token: str,
    inline_message_id: str,
    actor_name: str,
    actor_user_id: int,
    request_event_id: str,
    duplicate_handler: str,
) -> None:
    deps = build_handler_dependencies(bot=bot, db=db, send_analytics=send_analytics)
    await send_inline_deezer_music(
        token=token,
        inline_message_id=inline_message_id,
        actor_name=actor_name,
        actor_user_id=actor_user_id,
        request_event_id=request_event_id,
        duplicate_handler=duplicate_handler,
        deps=deps,
        channel_id=CHANNEL_ID,
        max_file_size=MAX_FILE_SIZE,
        output_dir=OUTPUT_DIR,
        get_bot_avatar_thumbnail_fn=None,
        get_bot_url_fn=get_bot_url,
        safe_edit_inline_media_fn=safe_edit_inline_media,
        safe_edit_inline_text_fn=safe_edit_inline_text,
    )


chosen_inline_deezer_music_result, send_inline_deezer_music_callback = (
    register_inline_send_handlers(
        router,
        service="deezer",
        result_prefix="deezer_inline:",
        callback_prefix="inline:deezer:",
        send_fn=_send_inline_deezer_music,
        missing_inline_message_warning="Chosen inline Deezer result is missing inline_message_id",
        chosen_flow="music_chosen_inline",
        callback_flow="music_inline_callback",
        chosen_handler_name="chosen_inline_deezer_music_result",
        callback_handler_name="send_inline_deezer_music_callback",
    )
)
