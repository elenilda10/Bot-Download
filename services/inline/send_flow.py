"""Shared inline send flow used by the *_inline handler modules.

The cached-photo delivery, video download+send mechanics, album-preview
caching and the canonical error tail (rate limit / queue busy / timeout /
generic failure) live here exactly once. Each platform module supplies
fetch/caption/keyboard callables and keeps its public entry points as thin
orchestrations on top of these helpers.

NOTE: helpers from the handlers package are imported lazily (see
``_handler_utils``) because ``handlers/__init__.py`` imports every handler
module and those modules import this one.
"""

from __future__ import annotations
import os
import subprocess
from aiogram.types import FSInputFile

import asyncio
import datetime
from typing import Any, Awaitable, Callable, List, Optional

from aiogram import types

import keyboards as kb
import messages as bm
from services.logger import logger, summarize_text_for_log, summarize_url_for_log
from services.inline.album_links import create_inline_album_request
from services.inline.service_icons import get_inline_service_icon
from services.inline.video_requests import (
    claim_inline_video_request_for_send,
    complete_inline_video_request,
    create_inline_video_request,
    reset_inline_video_request,
)
from services.media.video_metadata import build_video_send_kwargs
from utils.download_manager import (
    DownloadQueueBusyError,
    DownloadRateLimitError,
    log_download_metrics,
)
from utils.media_cache import build_media_cache_key

logging = logger.bind(service="inline_send_flow")

StatusEditor = Callable[..., Awaitable[None]]

#: Sentinel a ``download_fn`` may return to signal that the media is known to
#: exceed the size limit before (or instead of) downloading it.
VIDEO_TOO_LARGE = object()


def _handler_utils():
    """Deferred import of handler helpers to avoid a circular import."""
    from handlers import utils as handler_utils

    return handler_utils


def _optimize_video_for_streaming(video_path: str) -> None:
    """Move o moov atom para o início do arquivo MP4 (+faststart) para renderizar direto no Telegram."""
    if not isinstance(video_path, str) or not video_path.endswith(".mp4") or not os.path.exists(video_path):
        return
    tmp_path = f"{video_path}.faststart.mp4"
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-c", "copy",
        "-movflags", "+faststart",
        tmp_path,
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        if res.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
            os.replace(tmp_path, video_path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


class InlineFlowState:
    """Mutable state shared between a platform plan and the flow cleanup."""

    def __init__(self) -> None:
        self.cleanup_paths: List[str] = []
        self.cleanup_callbacks: List[Callable[[], Any]] = []

    def register_download_path(self, path: Optional[str]) -> None:
        if path:
            self.cleanup_paths.append(path)

    def register_cleanup(self, callback: Callable[[], Any]) -> None:
        self.cleanup_callbacks.append(callback)


async def run_inline_send_flow(
    *,
    token: str,
    inline_message_id: str,
    actor_user_id: int,
    duplicate_handler: str,
    deps,
    service_name: str,
    callback_data: str,
    plan_fn: Callable[[Any, StatusEditor, InlineFlowState], Awaitable[None]],
    safe_edit_inline_text_fn,
    button_text: Optional[str] = None,
    log=None,
) -> None:
    """Claim the inline request, run ``plan_fn`` and apply the canonical
    error tail (rate limit / queue busy / timeout / generic failure)."""
    request = claim_inline_video_request_for_send(
        token,
        duplicate_handler=duplicate_handler,
        actor_user_id=actor_user_id,
    )
    if request is None:
        return

    log = log or logging
    utils = _handler_utils()
    state = InlineFlowState()
    edit_status = utils.build_inline_status_editor(
        bot=deps.bot,
        inline_message_id=inline_message_id,
        callback_data_factory=lambda _media_kind: callback_data,
        safe_edit_inline_text_fn=safe_edit_inline_text_fn,
        button_text=button_text,
    )

    try:
        await plan_fn(request, edit_status, state)
    except DownloadRateLimitError as exc:
        reset_inline_video_request(token)
        await edit_status(utils.build_rate_limit_text(exc.retry_after), with_retry_button=True)
    except DownloadQueueBusyError as exc:
        reset_inline_video_request(token)
        await edit_status(utils.build_queue_busy_text(exc.position), with_retry_button=True)
    except asyncio.TimeoutError:
        reset_inline_video_request(token)
        await edit_status(bm.timeout_error(), with_retry_button=True)
    except Exception as exc:
        log.exception(
            "Error sending inline %s media: inline_message_id=%s token=%s error=%s",
            service_name,
            inline_message_id,
            token,
            exc,
        )
        reset_inline_video_request(token)
        await edit_status(bm.something_went_wrong(), with_retry_button=True)
    finally:
        for callback in state.cleanup_callbacks:
            callback()
        for path in state.cleanup_paths:
            await utils.remove_file(path)


async def deliver_inline_photo(
    *,
    deps,
    token: str,
    inline_message_id: str,
    channel_id: Optional[int],
    service_name: str,
    cache_key: str,
    photo_url: str,
    channel_caption: str,
    build_caption: Callable[[], Awaitable[Optional[str]]],
    reply_markup,
    edit_status: StatusEditor,
    safe_edit_inline_media_fn,
    log=None,
) -> None:
    """Serve a single photo inline, uploading it to the cache channel once."""
    log = log or logging
    db_id = await deps.db.get_file_id(cache_key)
    if not db_id:
        if not channel_id:
            log.error("CHANNEL_ID is not configured; %s inline upload is disabled", service_name)
            reset_inline_video_request(token)
            await edit_status(bm.something_went_wrong(), with_retry_button=True, media_kind="photo")
            return

        await edit_status(bm.uploading_status(), media_kind="photo")
        sent = await deps.bot.send_photo(
            chat_id=channel_id,
            photo=FSInputFile(photo_url) if (isinstance(photo_url, str) and os.path.exists(photo_url)) else photo_url,
            caption=channel_caption,
        )
        if not sent.photo:
            reset_inline_video_request(token)
            await edit_status(bm.something_went_wrong(), with_retry_button=True, media_kind="photo")
            return
        db_id = sent.photo[-1].file_id
        await deps.db.add_file(cache_key, db_id, "photo")
    else:
        await edit_status(bm.uploading_status(), media_kind="photo")

    edited = await safe_edit_inline_media_fn(
        deps.bot,
        inline_message_id,
        types.InputMediaPhoto(
            media=db_id,
            caption=await build_caption(),
            parse_mode="HTML",
        ),
        reply_markup=reply_markup,
    )
    if edited:
        complete_inline_video_request(token)
        return

    reset_inline_video_request(token)
    await edit_status(bm.something_went_wrong(), with_retry_button=True, media_kind="photo")


async def deliver_inline_video(
    *,
    deps,
    token: str,
    inline_message_id: str,
    channel_id: Optional[int],
    max_file_size: int,
    service_name: str,
    cache_key: str,
    channel_caption: str,
    download_fn: Callable[[Callable[..., Awaitable[None]]], Awaitable[Any]],
    progress_label: str,
    build_caption: Callable[[], Awaitable[Optional[str]]],
    reply_markup,
    edit_status: StatusEditor,
    state: InlineFlowState,
    safe_edit_inline_media_fn,
    metrics_log_key: Optional[str] = None,
    log=None,
) -> None:
    """Serve a single video inline, downloading and uploading it to the cache
    channel once."""
    log = log or logging
    utils = _handler_utils()
    db_id = await deps.db.get_file_id(cache_key)
    video_meta = {}

    if not db_id:
        if not channel_id:
            log.error("CHANNEL_ID is not configured; %s inline upload is disabled", service_name)
            reset_inline_video_request(token)
            await edit_status(bm.something_went_wrong(), with_retry_button=True)
            return

        await edit_status(bm.downloading_video_status())
        # Inline messages keep a static download status. Avoid percentage/ETA edits
        # during the transfer to reduce Telegram Bot API edit traffic and 429 risk.
        async def _ignore_inline_progress(*_args, **_kwargs) -> None:
            return None

        metrics = await download_fn(_ignore_inline_progress)
        if metrics is VIDEO_TOO_LARGE:
            complete_inline_video_request(token)
            await edit_status(bm.video_too_large())
            return
        if not metrics:
            reset_inline_video_request(token)
            await edit_status(bm.something_went_wrong(), with_retry_button=True)
            return

        if metrics_log_key:
            log_download_metrics(metrics_log_key, metrics)
        state.register_download_path(metrics.path)
        if metrics.size >= max_file_size:
            complete_inline_video_request(token)
            await edit_status(bm.video_too_large())
            return

        await edit_status(bm.uploading_status())
        
        # Otimiza o container de vídeo para streaming instantâneo
        _optimize_video_for_streaming(metrics.path)

        raw_kwargs = build_video_send_kwargs(metrics.path)
        if asyncio.iscoroutine(raw_kwargs):
            video_kwargs = await raw_kwargs
        else:
            video_kwargs = raw_kwargs

        if not isinstance(video_kwargs, dict):
            video_kwargs = {}

        video_kwargs["supports_streaming"] = True
        video_meta = {
            "width": video_kwargs.get("width"),
            "height": video_kwargs.get("height"),
            "duration": video_kwargs.get("duration"),
        }

        sent = await deps.bot.send_video(
            chat_id=channel_id,
            video=FSInputFile(metrics.path),
            caption=channel_caption,
            **video_kwargs,
        )
        db_id = sent.video.file_id
        await deps.db.add_file(cache_key, db_id, "video")
        log.info(
            "Inline %s video cached: url=%s file_id=%s",
            service_name,
            summarize_url_for_log(cache_key),
            db_id,
        )
    else:
        await edit_status(bm.uploading_status())

    edited = await safe_edit_inline_media_fn(
        deps.bot,
        inline_message_id,
        types.InputMediaVideo(
            media=db_id,
            caption=await build_caption(),
            parse_mode="HTML",
            supports_streaming=True,
            width=video_meta.get("width"),
            height=video_meta.get("height"),
            duration=video_meta.get("duration"),
        ),
        reply_markup=reply_markup,
    )
    if edited:
        complete_inline_video_request(token)
        log.info(
            "Served inline %s video: inline_message_id=%s url=%s file_id=%s",
            service_name,
            inline_message_id,
            summarize_url_for_log(cache_key),
            db_id,
        )
        return

    reset_inline_video_request(token)
    await edit_status(bm.something_went_wrong(), with_retry_button=True)


async def ensure_album_preview_file_id(
    *,
    deps,
    channel_id: Optional[int],
    photo_url: Optional[str],
    cache_key: str,
    service_name: str,
    source_url: str,
    log=None,
) -> Optional[str]:
    """Return a cached Telegram file_id for an album preview photo."""
    if not channel_id or not photo_url:
        return None
    preview_file_id = await deps.db.get_file_id(cache_key)
    if preview_file_id:
        return preview_file_id
    try:
        sent = await deps.bot.send_photo(
            chat_id=channel_id,
            photo=photo_url,
            caption=f"{service_name} Album Preview",
        )
        if sent.photo:
            preview_file_id = sent.photo[-1].file_id
            await deps.db.add_file(cache_key, preview_file_id, "photo")
    except Exception as exc:
        (log or logging).warning(
            "Failed to cache %s album preview photo: url=%s error=%s",
            service_name,
            summarize_url_for_log(source_url),
            exc,
        )
    return preview_file_id


async def handle_post_inline_query(
    query: types.InlineQuery,
    *,
    deps,
    service,
    url_regex: str,
    strip_url_fn,
    channel_id: Optional[int],
    service_key: str,
    service_name: str,
    analytics_action: str,
    get_preview_url,
    allow_text_only: bool,
    get_bot_url_fn,
    safe_answer_inline_query_fn,
    log=None,
) -> None:
    import re

    log = log or logging
    try:
        await deps.send_analytics(
            user_id=query.from_user.id,
            chat_type=query.chat_type,
            action_name=analytics_action,
        )
        match = re.search(url_regex, query.query or "")
        if not match:
            await query.answer([], cache_time=1, is_personal=True)
            return
        source_url = strip_url_fn(match.group(0))
        user_settings = await deps.db.user_settings(query.from_user.id)
        bot_url = await get_bot_url_fn(deps.bot)

        post = await service.fetch_post(source_url)
        if not post:
            await query.answer([], cache_time=1, is_personal=True)
            return
        if not post.media_list:
            if not allow_text_only:
                await query.answer([], cache_time=1, is_personal=True)
                return
            results = [
                types.InlineQueryResultArticle(
                    id=f"{service_key}_text_{post.id}",
                    title=f"{service_name} Post",
                    description=post.description or f"Text post from {service_name}.",
                    thumbnail_url=get_inline_service_icon(service_key),
                    input_message_content=types.InputTextMessageContent(
                        message_text=bm.captions("on", post.description, bot_url, limit=4096),
                        parse_mode="HTML",
                    ),
                    reply_markup=kb.return_video_info_keyboard(
                        None, None, None, None, None, source_url, user_settings,
                        audio_callback_data=None,
                    ),
                )
            ]
            await safe_answer_inline_query_fn(query, results, cache_time=10, is_personal=True)
            return
        if not channel_id:
            log.error("CHANNEL_ID is not configured; %s inline media is disabled", service_name)
            await query.answer([], cache_time=1, is_personal=True)
            return
        first = post.media_list[0]
        photo_items = [item for item in post.media_list if item.type == "photo"]
        first_photo = photo_items[0] if photo_items else None
        first_preview = get_preview_url(first) or next(
            (get_preview_url(item) for item in post.media_list if get_preview_url(item)),
            None,
        )

        if len(post.media_list) == 1 and first_photo:
            token = create_inline_video_request(service_key, source_url, query.from_user.id, user_settings)
            results = [
                types.InlineQueryResultArticle(
                    id=f"{service_key}_inline:{token}",
                    title=f"{service_name} Photo",
                    description=post.description or "Press the button to send this photo inline.",
                    thumbnail_url=first_preview,
                    input_message_content=types.InputTextMessageContent(
                        message_text=f"{service_name} photo is being prepared...\nIf it does not start automatically, tap the button below.",
                    ),
                    reply_markup=kb.inline_send_media_keyboard(
                        "Enviar foto inline",
                        f"inline:{service_key}:{token}",
                    ),
                )
            ]
            await safe_answer_inline_query_fn(query, results, cache_time=10, is_personal=True)
            return

        if len(post.media_list) > 1:
            preview_file_id = None
            if first.type == "photo":
                preview_file_id = await ensure_album_preview_file_id(
                    deps=deps,
                    channel_id=channel_id,
                    photo_url=first.url,
                    cache_key=build_media_cache_key(source_url, item_index=0, item_kind="photo"),
                    service_name=service_name,
                    source_url=source_url,
                    log=log,
                )
            token = create_inline_album_request(query.from_user.id, service_key, source_url)
            utils = _handler_utils()
            deep_link = utils.build_start_deeplink_url(bot_url, f"album_{token}")
            results = [
                utils.build_inline_album_result(
                    result_id=f"{service_key}_album_{post.id}",
                    service_name=service_name,
                    deep_link=deep_link,
                    message_text=bm.captions(user_settings["captions"], post.description, bot_url),
                    preview_file_id=preview_file_id,
                    preview_url=first_preview,
                    thumbnail_url=first_preview or get_inline_service_icon(service_key),
                )
            ]
            await safe_answer_inline_query_fn(query, results, cache_time=10, is_personal=True)
            return

        if first.type != "video":
            if first_photo:
                preview_url = get_preview_url(first_photo)
                results = [
                    types.InlineQueryResultPhoto(
                        id=f"{service_key}_photo_{post.id}",
                        photo_url=first_photo.url,
                        thumbnail_url=preview_url,
                        title=bm.inline_photo_title(service_name),
                        description=post.description or bm.inline_photo_description(),
                        caption=bm.captions(user_settings["captions"], post.description, bot_url),
                        reply_markup=kb.return_video_info_keyboard(
                            None, None, None, None, None, source_url, user_settings,
                            audio_callback_data=None,
                        ),
                        parse_mode="HTML",
                    )
                ]
                await safe_answer_inline_query_fn(query, results, cache_time=10, is_personal=True)
                return

            results = [
                types.InlineQueryResultArticle(
                    id=f"unsupported_{service_key}_content",
                    title=f"{service_name} Content",
                    description="Only single videos are supported inline.",
                    input_message_content=types.InputTextMessageContent(
                        message_text=f"Only single {service_name} videos are supported inline.",
                    ),
                )
            ]
            await safe_answer_inline_query_fn(query, results, cache_time=10, is_personal=True)
            return

        token = create_inline_video_request(service_key, source_url, query.from_user.id, user_settings)
        preview_url = get_preview_url(first) or get_inline_service_icon(service_key)
        results = [
            types.InlineQueryResultArticle(
                id=f"{service_key}_inline:{token}",
                title=f"{service_name} Video",
                description=post.description or "Press the button to send this video inline.",
                thumbnail_url=preview_url,
                input_message_content=types.InputTextMessageContent(
                    message_text=bm.inline_send_video_prompt(service_name),
                ),
                reply_markup=kb.inline_send_media_keyboard(
                    "Enviar vídeo inline",
                    f"inline:{service_key}:{token}",
                ),
            )
        ]
        await safe_answer_inline_query_fn(query, results, cache_time=10, is_personal=True)
    except Exception as exc:
        log.exception(
            "Error processing %s inline query: user_id=%s query=%s error=%s",
            service_name,
            query.from_user.id,
            summarize_text_for_log(query.query),
            exc,
        )
        await query.answer([], cache_time=1, is_personal=True)


async def send_inline_post_media(
    *,
    token: str,
    inline_message_id: str,
    actor_name: str,
    actor_user_id: int,
    request_event_id: str,
    duplicate_handler: str,
    deps,
    service,
    channel_id: Optional[int],
    max_file_size: int,
    service_key: str,
    service_name: str,
    get_bot_url_fn,
    safe_edit_inline_media_fn,
    safe_edit_inline_text_fn,
    log=None,
) -> None:
    log = log or logging

    async def _plan(request, edit_status, state: InlineFlowState) -> None:
        post = await service.fetch_post(request.source_url)
        if not post or len(post.media_list) != 1:
            complete_inline_video_request(token)
            await edit_status(bm.inline_photos_not_supported(service_name))
            return

        async def _build_caption() -> Optional[str]:
            return bm.captions(
                request.user_settings["captions"],
                post.description,
                await get_bot_url_fn(deps.bot),
            )

        reply_markup = kb.return_video_info_keyboard(
            None,
            None,
            None,
            None,
            None,
            request.source_url,
            request.user_settings,
            audio_callback_data=None,
        )

        first_item = post.media_list[0]
        if first_item.type == "photo":
            await deliver_inline_photo(
                deps=deps,
                token=token,
                inline_message_id=inline_message_id,
                channel_id=channel_id,
                service_name=service_name,
                cache_key=f"{request.source_url}#photo",
                photo_url=first_item.url,
                channel_caption=f"{service_name} Photo from {actor_name}",
                build_caption=_build_caption,
                reply_markup=reply_markup,
                edit_status=edit_status,
                safe_edit_inline_media_fn=safe_edit_inline_media_fn,
                log=log,
            )
            return

        if first_item.type != "video":
            complete_inline_video_request(token)
            await edit_status(bm.inline_photos_not_supported(service_name))
            return

        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"{post.id}_{timestamp}_{service_key}_inline.mp4"

        async def _download(on_progress):
            return await service.download_media(
                first_item.url,
                filename,
                user_id=request.owner_user_id,
                request_id=f"{service_key}_inline:{request.owner_user_id}:{request_event_id}:{post.id}",
                on_progress=on_progress,
            )

        await deliver_inline_video(
            deps=deps,
            token=token,
            inline_message_id=inline_message_id,
            channel_id=channel_id,
            max_file_size=max_file_size,
            service_name=service_name,
            cache_key=request.source_url,
            channel_caption=f"{service_name} Video from {actor_name}",
            download_fn=_download,
            progress_label=f"{service_name} video",
            build_caption=_build_caption,
            reply_markup=reply_markup,
            edit_status=edit_status,
            state=state,
            safe_edit_inline_media_fn=safe_edit_inline_media_fn,
            metrics_log_key=f"{service_key}_inline",
            log=log,
        )

    await run_inline_send_flow(
        token=token,
        inline_message_id=inline_message_id,
        actor_user_id=actor_user_id,
        duplicate_handler=duplicate_handler,
        deps=deps,
        service_name=service_name,
        callback_data=f"inline:{service_key}:{token}",
        plan_fn=_plan,
        safe_edit_inline_text_fn=safe_edit_inline_text_fn,
        log=log,
    )
