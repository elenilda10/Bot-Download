from config import OUTPUT_DIR
from services.platforms.twitter_media import download_twitter_media
import asyncio
import os
import re
from typing import Optional

from aiogram import types
from aiogram.types import FSInputFile

import keyboards as kb
import messages as bm
from handlers.deps import HandlerDependencies
from handlers.utils import (
    build_inline_album_result,
    build_start_deeplink_url,
    get_bot_url,
    remove_file,
    safe_answer_inline_query,
    safe_edit_inline_media,
    safe_edit_inline_text,
)
from services.logger import logger as logging
from services.inline.album_links import create_inline_album_request
from services.inline.send_flow import (
    InlineFlowState,
    StatusEditor,
    deliver_inline_photo,
    deliver_inline_video,
    ensure_album_preview_file_id,
    run_inline_send_flow,
)
from services.inline.service_icons import get_inline_service_icon
from services.inline.video_requests import (
    create_inline_video_request,
    reset_inline_video_request,
)
from services.platforms.twitter_media import download_twitter_media

logging = logging.bind(service="twitter_inline")


async def handle_twitter_inline_query(
    query: types.InlineQuery,
    *,
    deps: HandlerDependencies,
    twitter_link_regex: str,
    channel_id: Optional[int],
    get_bot_url_fn=get_bot_url,
    safe_answer_inline_query_fn=safe_answer_inline_query,
) -> None:
    try:
        await deps.send_analytics(
            user_id=query.from_user.id,
            chat_type=query.chat_type,
            action_name="inline_twitter_media",
        )
        match = re.search(twitter_link_regex, query.query or "")
        if not match:
            await query.answer([], cache_time=1, is_personal=True)
            return

        source_url = match.group(0)
        user_settings = await deps.db.user_settings(query.from_user.id)
        bot_url = await get_bot_url_fn(deps.bot)

        result_data = await download_twitter_media(source_url)
        if not result_data or not result_data.media_list:
            await query.answer([], cache_time=1, is_personal=True)
            return

        media_items = result_data.media_list

        if len(media_items) > 1:
            album_token = create_inline_album_request(query.from_user.id, "twitter", source_url)
            deep_link = build_start_deeplink_url(bot_url, f"album_{album_token}")

            first_media = media_items[0]
            preview_url = first_media.thumb or (first_media.url if first_media.type == "photo" else get_inline_service_icon("twitter"))
            preview_file_id = None

            results = [
                build_inline_album_result(
                    "twitter",
                    f"{result_data.id}_album",
                    deep_link,
                    len(media_items),
                    preview_url=preview_url,
                    preview_file_id=preview_file_id,
                    description=result_data.description or None,
                )
            ]
            await safe_answer_inline_query_fn(query, results, cache_time=10, is_personal=True)
            return

        item = media_items[0]
        kind = item.type
        preview_url = item.thumb or (item.url if kind == "photo" else get_inline_service_icon("twitter"))
        token = create_inline_video_request("twitter", source_url, query.from_user.id, user_settings)
        title = "X / Twitter Vídeo" if kind == "video" else "X / Twitter Foto"
        prompt_text = (
            bm.inline_send_video_prompt("X / Twitter")
            if kind == "video"
            else "A foto do X / Twitter está sendo preparada...\nSe não iniciar automaticamente, toque no botão abaixo."
        )
        button_text = "Enviar vídeo inline" if kind == "video" else "Enviar foto inline"

        results = [
            types.InlineQueryResultArticle(
                id=f"twitter_inline:{token}",
                title=title,
                description=result_data.description or f"Toque no botão para enviar este {kind} inline.",
                thumbnail_url=preview_url,
                input_message_content=types.InputTextMessageContent(message_text=prompt_text),
                reply_markup=kb.inline_send_media_keyboard(
                    button_text,
                    f"inline:twitter:{token}",
                ),
            )
        ]
        await safe_answer_inline_query_fn(query, results, cache_time=10, is_personal=True)

    except Exception as exc:
        logging.exception("Error handling Twitter inline query: %s", exc)
        await query.answer([], cache_time=1, is_personal=True)


async def send_inline_twitter_media(
    *,
    token: str,
    inline_message_id: str,
    actor_name: str,
    actor_user_id: int,
    request_event_id: str,
    duplicate_handler: str,
    deps: HandlerDependencies,
    channel_id: Optional[int],
    max_file_size: int,
    get_bot_url_fn=get_bot_url,
    safe_edit_inline_media_fn=safe_edit_inline_media,
    safe_edit_inline_text_fn=safe_edit_inline_text,
) -> None:
    async def _plan(request, edit_status: StatusEditor, state: InlineFlowState) -> None:
        source_url = request.source_url
        user_settings = request.user_settings
        bot_url = await get_bot_url_fn(deps.bot)

        result_data = await download_twitter_media(source_url)
        if not result_data or not result_data.media_list:
            reset_inline_video_request(token)
            await edit_status(bm.something_went_wrong(), with_retry_button=True)
            return

        item = result_data.media_list[0]
        kind = item.type

        async def _build_caption() -> Optional[str]:
            return bm.captions(
                user_settings.get("captions", "on"),
                result_data.description,
                bot_url,
            )

        reply_markup = kb.return_video_info_keyboard(
            None,
            result_data.likes,
            result_data.replies,
            result_data.retweets,
            None,
            source_url,
            user_settings,
        )

        cache_key = f"twitter:{result_data.id}:{kind}"

        if kind == "video":
            async def _download(on_progress):
                res = await download_twitter_media(source_url, output_dir=OUTPUT_DIR)
                if not res or not res.media_list:
                    return None
                target = res.media_list[0]
                file_path = target.url
                if not os.path.exists(file_path):
                    return None
                class Metrics:
                    def __init__(self, p):
                        self.path = p
                        self.size = os.path.getsize(p)
                        self.duration = 0
                return Metrics(file_path)

            await deliver_inline_video(
                state=state,
                deps=deps,
                token=token,
                inline_message_id=inline_message_id,
                channel_id=channel_id,
                max_file_size=max_file_size,
                service_name="X / Twitter",
                cache_key=cache_key,
                channel_caption=f"Twitter Video from {actor_name}",
                download_fn=_download,
                progress_label="Twitter video",
                build_caption=_build_caption,
                reply_markup=reply_markup,
                edit_status=edit_status,
                safe_edit_inline_media_fn=safe_edit_inline_media_fn,
                metrics_log_key="twitter_inline",
                log=logging,
            )
        else:
            await deliver_inline_photo(
                deps=deps,
                token=token,
                inline_message_id=inline_message_id,
                channel_id=channel_id,
                service_name="X / Twitter",
                cache_key=cache_key,
                photo_url=item.url,
                channel_caption=f"Twitter Photo from {actor_name}",
                build_caption=_build_caption,
                reply_markup=reply_markup,
                edit_status=edit_status,
                safe_edit_inline_media_fn=safe_edit_inline_media_fn,
                log=logging,
            )

    await run_inline_send_flow(
        token=token,
        inline_message_id=inline_message_id,
        actor_user_id=actor_user_id,
        duplicate_handler=duplicate_handler,
        deps=deps,
        service_name="X / Twitter",
        callback_data=f"inline:twitter:{token}",
        plan_fn=_plan,
        safe_edit_inline_text_fn=safe_edit_inline_text_fn,
        log=logging,
    )
