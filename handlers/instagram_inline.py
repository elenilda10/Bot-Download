import asyncio
import datetime
import os
import re
from typing import Optional

from aiogram import types
from aiogram.types import FSInputFile

import keyboards as kb
import messages as bm
from config import OUTPUT_DIR
from handlers.deps import HandlerDependencies
from handlers.utils import (
    build_inline_album_result,
    build_start_deeplink_url,
    get_bot_url,
    safe_answer_inline_query,
    safe_edit_inline_media,
    safe_edit_inline_text,
)
from services.logger import logger as logging, summarize_text_for_log, summarize_url_for_log
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
    complete_inline_video_request,
    create_inline_video_request,
    reset_inline_video_request,
)
from services.platforms.instagram_media import (
    fetch_instagram_media,
    get_instagram_preview_url as _get_instagram_preview_url,
    strip_instagram_url,
)
from utils.media_cache import build_media_cache_key

logging = logging.bind(service="instagram_inline")


async def handle_instagram_inline_query(
    query: types.InlineQuery,
    *,
    deps: HandlerDependencies,
    channel_id: Optional[int],
    get_bot_url_fn=get_bot_url,
    safe_answer_inline_query_fn=safe_answer_inline_query,
) -> None:
    try:
        await deps.send_analytics(
            user_id=query.from_user.id,
            chat_type=query.chat_type,
            action_name="inline_instagram_video",
        )
        logging.info(
            "Inline Instagram request: user_id=%s query=%s",
            query.from_user.id,
            summarize_text_for_log(query.query),
        )

        user_settings = await deps.db.user_settings(query.from_user.id)
        bot_url = await get_bot_url_fn(deps.bot)

        url_match = re.search(r"(https?://(www\.)?instagram\.com/(p|reels|reel|share|stories/[^/?#&]+)/[\w-]+)", query.query or "")
        if not url_match:
            await query.answer([], cache_time=1, is_personal=True)
            return

        original_url = strip_instagram_url(url_match.group(0))
        data = await fetch_instagram_media(original_url, output_dir=OUTPUT_DIR)
        if not data or not data.media_list:
            logging.warning("Inline Instagram fetch failed: url=%s", summarize_url_for_log(original_url))
            await query.answer([], cache_time=1, is_personal=True)
            return

        results = []

        # 1. Vídeo Único (Reels / Post / Stories)
        if len(data.media_list) == 1 and data.media_list[0].type == "video":
            token = create_inline_video_request("instagram", original_url, query.from_user.id, user_settings)
            preview_url = _get_instagram_preview_url(data.media_list[0]) or get_inline_service_icon("instagram")
            results.append(
                types.InlineQueryResultArticle(
                    id=f"instagram_inline:{token}",
                    title="Instagram Vídeo",
                    description=data.description or "Toque no botão para enviar este vídeo inline.",
                    thumbnail_url=preview_url,
                    input_message_content=types.InputTextMessageContent(
                        message_text=bm.inline_send_video_prompt("Instagram"),
                    ),
                    reply_markup=kb.inline_send_media_keyboard(
                        "Enviar vídeo inline",
                        f"inline:instagram:{token}",
                    ),
                )
            )
            await safe_answer_inline_query_fn(query, results, cache_time=10, is_personal=True)
            return

        # 2. Foto Única
        first_item = data.media_list[0] if data.media_list else None
        photo_items = [item for item in data.media_list if item.type == "photo"]
        first_photo = photo_items[0] if photo_items else None
        first_preview = _get_instagram_preview_url(first_item) if first_item else None

        if len(data.media_list) == 1 and first_photo:
            token = create_inline_video_request("instagram", original_url, query.from_user.id, user_settings)
            results.append(
                types.InlineQueryResultArticle(
                    id=f"instagram_inline:{token}",
                    title="Instagram Foto",
                    description=data.description or "Toque no botão para enviar esta foto inline.",
                    thumbnail_url=first_preview or first_photo.url,
                    input_message_content=types.InputTextMessageContent(
                        message_text="A foto do Instagram está sendo preparada...\nSe não iniciar automaticamente, toque no botão abaixo.",
                    ),
                    reply_markup=kb.inline_send_media_keyboard(
                        "Enviar foto inline",
                        f"inline:instagram:{token}",
                    ),
                )
            )
            await safe_answer_inline_query_fn(query, results, cache_time=10, is_personal=True)
            return

        # 3. Carrossel / Álbum
        if len(data.media_list) > 1:
            preview_file_id = None
            if first_photo:
                preview_file_id = await ensure_album_preview_file_id(
                    deps=deps,
                    channel_id=channel_id,
                    photo_url=first_photo.url,
                    cache_key=build_media_cache_key(original_url, item_index=0, item_kind="photo"),
                    service_name="Instagram",
                    source_url=original_url,
                    log=logging,
                )
            token = create_inline_album_request(query.from_user.id, "instagram", original_url)
            deep_link = build_start_deeplink_url(bot_url, f"album_instagram_{token}")
            results.append(
                build_inline_album_result(
                    "instagram",
                    token,
                    deep_link,
                    len(data.media_list),
                    preview_url=first_preview or (first_photo.url if first_photo else None),
                    preview_file_id=preview_file_id,
                    description=data.description,
                )
            )
            await safe_answer_inline_query_fn(query, results, cache_time=10, is_personal=True)
            return

    except Exception as exc:
        logging.exception("Error handling Instagram inline query: %s", exc)
        await query.answer([], cache_time=1, is_personal=True)


async def send_inline_instagram_media(
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

        data = await fetch_instagram_media(source_url, output_dir=OUTPUT_DIR)
        if not data or not data.media_list:
            reset_inline_video_request(token)
            await edit_status(bm.something_went_wrong(), with_retry_button=True)
            return

        first_item = data.media_list[0]

        async def _build_caption() -> Optional[str]:
            return bm.captions(
                user_settings.get("captions", "on"),
                data.description,
                bot_url,
            )

        reply_markup = kb.return_video_info_keyboard(
            None,
            None,
            None,
            None,
            None,
            source_url,
            user_settings,
            audio_callback_data=None,
        )

        if first_item.type == "photo":
            await deliver_inline_photo(
                deps=deps,
                token=token,
                inline_message_id=inline_message_id,
                channel_id=channel_id,
                service_name="Instagram",
                cache_key=f"{source_url}#photo",
                photo_url=first_item.url,
                channel_caption=f"Instagram Photo from {actor_name}",
                build_caption=_build_caption,
                reply_markup=reply_markup,
                edit_status=edit_status,
                safe_edit_inline_media_fn=safe_edit_inline_media_fn,
                log=logging,
            )
            return

        class LocalMetrics:
            def __init__(self, path: str):
                self.path = path
                self.size = os.path.getsize(path) if os.path.exists(path) else 0

        async def _download(on_progress):
            if os.path.exists(first_item.url):
                return LocalMetrics(first_item.url)
            return None

        await deliver_inline_video(
            deps=deps,
            token=token,
            inline_message_id=inline_message_id,
            channel_id=channel_id,
            max_file_size=max_file_size,
            service_name="Instagram",
            cache_key=source_url,
            channel_caption=f"Instagram Video from {actor_name}",
            download_fn=_download,
            progress_label="Instagram video",
            build_caption=_build_caption,
            reply_markup=reply_markup,
            edit_status=edit_status,
            state=state,
            safe_edit_inline_media_fn=safe_edit_inline_media_fn,
            metrics_log_key="instagram_inline",
            log=logging,
        )

    await run_inline_send_flow(
        token=token,
        inline_message_id=inline_message_id,
        actor_user_id=actor_user_id,
        duplicate_handler=duplicate_handler,
        deps=deps,
        service_name="Instagram",
        callback_data=f"inline:instagram:{token}",
        plan_fn=_plan,
        safe_edit_inline_text_fn=safe_edit_inline_text_fn,
        log=logging,
    )
