import asyncio
import datetime
import re
from typing import Optional

from aiogram import types
from aiogram.types import InlineQueryResultArticle

import keyboards as kb
import messages as bm
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
from utils.media_cache import build_media_cache_key

logging = logging.bind(service="tiktok_inline")


async def handle_tiktok_inline_query(
    query: types.InlineQuery,
    *,
    deps: HandlerDependencies,
    channel_id: Optional[int],
    strip_tiktok_tracking,
    fetch_tiktok_data_with_retry_fn,
    video_info_fn,
    build_tiktok_video_url_fn,
    get_bot_url_fn=get_bot_url,
    safe_answer_inline_query_fn=safe_answer_inline_query,
) -> None:
    try:
        await deps.send_analytics(
            user_id=query.from_user.id,
            chat_type=query.chat_type,
            action_name="inline_tiktok_video",
        )
        logging.info(
            "Inline TikTok request: user_id=%s query=%s",
            query.from_user.id,
            summarize_text_for_log(query.query),
        )
        user_settings = await deps.db.user_settings(query.from_user.id)
        bot_url = await get_bot_url_fn(deps.bot)

        match = re.search(r"(https?://(?:www\.|vm\.|vt\.|vn\.)?tiktok\.com/\S+)", query.query)
        if not match:
            logging.debug("Inline TikTok query pattern not matched: query=%s", summarize_text_for_log(query.query))
            await query.answer([], cache_time=1, is_personal=True)
            return

        source_url = strip_tiktok_tracking(match.group(0))
        data = await fetch_tiktok_data_with_retry_fn(source_url)
        info = await video_info_fn(data)
        images = data.get("data", {}).get("images", []) if isinstance(data, dict) else []
        results = []

        if not images:
            if not info:
                await query.answer([], cache_time=1, is_personal=True)
                return

            db_video_url = build_tiktok_video_url_fn(info)
            cache_key = build_media_cache_key("tiktok", variant=f"video:{info.id}")
            db_id = await deps.db.get_file_id(cache_key) or await deps.db.get_file_id(db_video_url)
            if not db_id and not channel_id:
                logging.error("CHANNEL_ID is not configured; TikTok inline video send is disabled")
                await query.answer([], cache_time=1, is_personal=True)
                return

            token = create_inline_video_request("tiktok", source_url, query.from_user.id, user_settings)
            results.append(
                InlineQueryResultArticle(
                    id=f"tiktok_inline:{token}",
                    title="TikTok Vídeo",
                    description=info.description or "Toque no botão para enviar este vídeo inline.",
                    thumbnail_url=info.cover or get_inline_service_icon("tiktok"),
                    input_message_content=types.InputTextMessageContent(
                        message_text=bm.inline_send_video_prompt("TikTok"),
                    ),
                    reply_markup=kb.inline_send_media_keyboard(
                        "Enviar vídeo inline",
                        f"inline:tiktok:{token}",
                    ),
                )
            )
            await safe_answer_inline_query_fn(query, results, cache_time=10, is_personal=True)
            return

        first_photo = images[0] if images else None
        if first_photo and match:
            source_url = strip_tiktok_tracking(match.group(0))
            cache_key = build_media_cache_key(
                build_tiktok_video_url_fn(info) if info else source_url,
                item_index=0,
                item_kind="photo",
            )
            if len(images) == 1:
                db_id = await deps.db.get_file_id(cache_key)
                if not db_id and not channel_id:
                    logging.error("CHANNEL_ID is not configured; TikTok inline photo send is disabled")
                    await query.answer([], cache_time=1, is_personal=True)
                    return

                token = create_inline_video_request("tiktok", source_url, query.from_user.id, user_settings)
                results.append(
                    InlineQueryResultArticle(
                        id=f"tiktok_inline:{token}",
                        title="TikTok Foto",
                        description=info.description if info and info.description else "Toque no botão para enviar esta foto inline.",
                        thumbnail_url=first_photo,
                        input_message_content=types.InputTextMessageContent(
                            message_text="A foto do TikTok está sendo preparada...\nSe não iniciar automaticamente, toque no botão abaixo.",
                        ),
                        reply_markup=kb.inline_send_media_keyboard(
                            "Enviar foto inline",
                            f"inline:tiktok:{token}",
                        ),
                    )
                )
                await safe_answer_inline_query_fn(query, results, cache_time=10, is_personal=True)
                return

            token = create_inline_album_request(query.from_user.id, "tiktok", source_url)
            deep_link = build_start_deeplink_url(bot_url, f"album_{token}")
            preview_file_id = await ensure_album_preview_file_id(
                deps=deps,
                channel_id=channel_id,
                photo_url=first_photo,
                cache_key=build_media_cache_key(
                    build_tiktok_video_url_fn(info) if info else source_url,
                    item_index=0,
                    item_kind="photo",
                ),
                service_name="TikTok",
                source_url=source_url,
                log=logging,
            )
            results.append(
                build_inline_album_result(
                    "tiktok",
                    token,
                    deep_link,
                    len(images),
                    preview_url=first_photo,
                    preview_file_id=preview_file_id,
                    description=info.description if info else None,
                )
            )
            await safe_answer_inline_query_fn(query, results, cache_time=10, is_personal=True)
            return

    except Exception as exc:
        logging.exception("Error handling TikTok inline query: %s", exc)
        await query.answer([], cache_time=1, is_personal=True)


async def send_inline_tiktok_media(
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
    fetch_tiktok_data_with_retry_fn,
    video_info_fn,
    build_tiktok_video_url_fn,
    get_tiktok_audio_callback_data_fn=None,
    get_tiktok_size_hint_fn=None,
    tiktok_service=None,
    get_bot_url_fn=get_bot_url,
    safe_edit_inline_media_fn=safe_edit_inline_media,
    safe_edit_inline_text_fn=safe_edit_inline_text,
) -> None:
    async def _plan(request, edit_status: StatusEditor, state: InlineFlowState) -> None:
        source_url = request.source_url
        user_settings = request.user_settings
        bot_url = await get_bot_url_fn(deps.bot)

        data = await fetch_tiktok_data_with_retry_fn(source_url)
        info = await video_info_fn(data)
        if not info:
            reset_inline_video_request(token)
            await edit_status(bm.something_went_wrong(), with_retry_button=True)
            return

        db_video_url = build_tiktok_video_url_fn(info)
        media_cache_key = build_media_cache_key("tiktok", variant=f"video:{info.id}")
        size_hint = get_tiktok_size_hint_fn(data) if get_tiktok_size_hint_fn else None

        async def _build_caption() -> Optional[str]:
            return bm.captions(
                user_settings.get("captions", "on"),
                info.description,
                bot_url,
            )

        audio_btn = get_tiktok_audio_callback_data_fn(info) if get_tiktok_audio_callback_data_fn else None
        file_callback_data = f"doc:tiktok:{info.id}"
        reply_markup = kb.return_video_info_keyboard(
            info.views,
            info.likes,
            info.comments,
            info.shares,
            info.music_play_url,
            db_video_url,
            user_settings,
            audio_callback_data=audio_btn,
            file_callback_data=file_callback_data,
        )

        async def _download(on_progress):
            timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            download_name = f"tiktok_{info.id}_{timestamp}.mp4"
            if tiktok_service and hasattr(tiktok_service, "download_video"):
                return await tiktok_service.download_video(
                    db_video_url,
                    download_name,
                    download_data=data,
                    user_id=request.owner_user_id,
                    chat_id=None,
                    request_id=f"tiktok_inline:{request.owner_user_id}:{request_event_id}:{info.id}",
                    size_hint=size_hint,
                    on_progress=on_progress,
                )
            return None

        await deliver_inline_video(
            deps=deps,
            token=token,
            inline_message_id=inline_message_id,
            channel_id=channel_id,
            max_file_size=max_file_size,
            service_name="TikTok",
            cache_key=media_cache_key,
            channel_caption=f"TikTok Video from {actor_name}",
            download_fn=_download,
            progress_label="TikTok video",
            build_caption=_build_caption,
            reply_markup=reply_markup,
            edit_status=edit_status,
            state=state,
            safe_edit_inline_media_fn=safe_edit_inline_media_fn,
            metrics_log_key="tiktok_inline",
            log=logging,
        )

    await run_inline_send_flow(
        token=token,
        inline_message_id=inline_message_id,
        actor_user_id=actor_user_id,
        duplicate_handler=duplicate_handler,
        deps=deps,
        service_name="TikTok",
        callback_data=f"inline:tiktok:{token}",
        plan_fn=_plan,
        safe_edit_inline_text_fn=safe_edit_inline_text_fn,
        log=logging,
    )
