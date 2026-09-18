import asyncio
import html
import os
import re
import aiohttp
from collections import namedtuple
from typing import Optional
from aiogram import types
from aiogram.types import FSInputFile

import keyboards as kb
import messages as bm
from handlers.deps import HandlerDependencies
from handlers.inline_utils import (
    build_inline_status_editor,
    safe_answer_inline_query,
)
from handlers.utils import (
    get_bot_url,
    remove_file,
    safe_edit_inline_media,
    safe_edit_inline_text,
)
from services.logger import logger as logging, summarize_text_for_log
from services.inline.service_icons import get_inline_service_icon
from services.inline.video_requests import (
    claim_inline_video_request_for_send,
    complete_inline_video_request,
    create_inline_video_request,
    reset_inline_video_request,
)
from services.media.delivery import (
    AUDIO_CACHE_VARIANT,
    build_bot_audio_performer,
    coerce_audio_duration_seconds,
    send_audio_with_thumbnail,
)
from services.media.audio_flow import run_audio_flow
from services.media.audio_metadata import build_audio_filename
from services.platforms.deezer_media import download_deezer_track
from utils.media_cache import build_media_cache_key

logging = logging.bind(service="deezer_inline")

DEEZER_SEARCH_API = "https://api.deezer.com/search"
DEEZER_TRACK_API = "https://api.deezer.com/track"
DEEZER_TRACK_REGEX = r"https?://(?:www\.)?deezer\.com/(?:[a-z]{2}/)?track/([0-9]+)"
DEEZER_GENERIC_REGEX = r"https?://(?:www\.)?deezer\.com/(?:[a-z]{2}/)?(?:track|album|playlist|episode)/[0-9]+|https?://deezer\.page\.link/[a-zA-Z0-9]+"

PAGE_SIZE = 25
DOWNLOAD_TIMEOUT_SECONDS = 90

DownloadedAudioMetrics = namedtuple("DownloadedAudioMetrics", ["path", "size"])


class DeezerMetadataWrapper:
    def __init__(self, track):
        self.track = track
        self.thumbnail_path = getattr(track, "thumb_path", None) if track else None

    def cleanup(self):
        if self.thumbnail_path and os.path.exists(self.thumbnail_path):
            try:
                os.remove(self.thumbnail_path)
            except Exception:
                pass


def _extract_track_id(text: str) -> Optional[str]:
    match = re.search(DEEZER_TRACK_REGEX, text or "")
    return match.group(1) if match else None


def _is_deezer_url(text: str) -> bool:
    return bool(re.search(DEEZER_GENERIC_REGEX, text or ""))


async def handle_deezer_inline_query(
    query: types.InlineQuery,
    *,
    deps: HandlerDependencies,
    channel_id: Optional[int],
    safe_answer_inline_query_fn=safe_answer_inline_query,
) -> None:
    raw_query = (query.query or "").strip()
    if not raw_query:
        await safe_answer_inline_query_fn(query, [], cache_time=2, is_personal=True)
        return

    # Se for link que não é Deezer, não interfere
    if raw_query.startswith(("http://", "https://")) and not _is_deezer_url(raw_query):
        return

    # Lógica 1: LINK direto -> Retorna card com botão para gerar inline_message_id no Telegram
    if _is_deezer_url(raw_query):
        if not channel_id:
            logging.error("CHANNEL_ID is not configured; Deezer inline is disabled")
            await safe_answer_inline_query_fn(query, [], cache_time=2, is_personal=True)
            return

        track_id = _extract_track_id(raw_query)
        title = "Deezer Music"
        artist_name = "Deezer"
        duration_str = ""
        album_cover = get_inline_service_icon("deezer")
        target_url = raw_query

        if track_id:
            try:
                timeout = aiohttp.ClientTimeout(total=4)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(f"{DEEZER_TRACK_API}/{track_id}") as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if "error" not in data:
                                title = data.get("title", title)
                                artist_name = data.get("artist", {}).get("name", artist_name)
                                duration_sec = data.get("duration", 0)
                                if duration_sec:
                                    duration_str = f"{duration_sec // 60}:{duration_sec % 60:02d}"
                                album_cover = (
                                    data.get("album", {}).get("cover_medium")
                                    or data.get("album", {}).get("cover_small")
                                    or album_cover
                                )
                                target_url = data.get("link") or target_url
            except Exception:
                pass

        user_settings = await deps.db.user_settings(query.from_user.id)
        token = create_inline_video_request("deezer", target_url, query.from_user.id, user_settings)

        desc = f"{artist_name} • {duration_str}" if duration_str else artist_name
        results = [
            types.InlineQueryResultArticle(
                id=f"deezer_inline:{token}",
                title=title,
                description=desc,
                thumbnail_url=album_cover,
                input_message_content=types.InputTextMessageContent(
                    message_text=bm.inline_send_audio_prompt("Deezer"),
                ),
                reply_markup=kb.inline_send_media_keyboard(
                    "Enviar áudio inline",
                    f"inline:deezer:{token}",
                ),
            )
        ]
        await safe_answer_inline_query_fn(query, results, cache_time=10, is_personal=True)
        return

    # Lógica 2: BUSCA POR TEXTO -> Devolve link no chat para o bot baixar na conversa
    offset = int(query.offset) if query.offset and query.offset.isdigit() else 0
    results = []
    next_offset = ""

    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            params = {
                "q": raw_query,
                "index": offset,
                "limit": PAGE_SIZE,
            }
            async with session.get(DEEZER_SEARCH_API, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    tracks = data.get("data", [])
                    total = data.get("total", 0)

                    for track in tracks:
                        track_id = str(track.get("id"))
                        title = track.get("title", "Música")
                        artist_name = track.get("artist", {}).get("name", "Deezer")
                        duration_sec = track.get("duration", 0)
                        duration_str = f"{duration_sec // 60}:{duration_sec % 60:02d}"

                        track_url = track.get("link") or f"https://www.deezer.com/track/{track_id}"
                        album_cover = (
                            track.get("album", {}).get("cover_medium")
                            or track.get("album", {}).get("cover_small")
                            or get_inline_service_icon("deezer")
                        )

                        results.append(
                            types.InlineQueryResultArticle(
                                id=f"deezer_search:{track_id}",
                                title=title,
                                description=f"{artist_name} • {duration_str}",
                                thumbnail_url=album_cover,
                                input_message_content=types.InputTextMessageContent(
                                    message_text=track_url,
                                ),
                            )
                        )

                    if (offset + len(tracks)) < total:
                        next_offset = str(offset + len(tracks))
    except Exception as exc:
        logging.exception(
            "Error processing Deezer search inline: user_id=%s query=%s error=%s",
            query.from_user.id,
            summarize_text_for_log(query.query),
            exc,
        )

    await query.answer(
        results,
        cache_time=15,
        is_personal=True,
        next_offset=next_offset,
    )


async def send_inline_deezer_music(
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
    output_dir: str,
    get_bot_avatar_thumbnail_fn=None,
    get_bot_url_fn=get_bot_url,
    safe_edit_inline_media_fn=safe_edit_inline_media,
    safe_edit_inline_text_fn=safe_edit_inline_text,
) -> None:
    request = claim_inline_video_request_for_send(
        token,
        duplicate_handler=duplicate_handler,
        actor_user_id=actor_user_id,
    )
    if request is None:
        return

    _edit_inline_status = build_inline_status_editor(
        bot=deps.bot,
        inline_message_id=inline_message_id,
        callback_data_factory=lambda _media_kind: f"inline:deezer:{token}",
        safe_edit_inline_text_fn=safe_edit_inline_text_fn,
        button_text="Tentar novamente",
    )

    downloaded_track = None

    try:
        cache_key = build_media_cache_key(request.source_url, variant=AUDIO_CACHE_VARIANT)
        bot_url = await get_bot_url_fn(deps.bot)

        async def _send_cached(_file_id: str):
            await _edit_inline_status(bm.uploading_status())
            return None

        async def _download_audio():
            nonlocal downloaded_track
            await _edit_inline_status(bm.downloading_audio_status())

            download_coro = download_deezer_track(request.source_url, output_dir=output_dir)
            if asyncio.iscoroutine(download_coro):
                downloaded_track = await asyncio.wait_for(download_coro, timeout=DOWNLOAD_TIMEOUT_SECONDS)
            else:
                downloaded_track = await asyncio.wait_for(
                    asyncio.to_thread(download_deezer_track, request.source_url, output_dir=output_dir),
                    timeout=DOWNLOAD_TIMEOUT_SECONDS,
                )

            if not downloaded_track or not getattr(downloaded_track, "file_path", None) or not os.path.exists(downloaded_track.file_path):
                return None

            size_bytes = os.path.getsize(downloaded_track.file_path)
            return DownloadedAudioMetrics(path=downloaded_track.file_path, size=size_bytes)

        async def _on_missing_audio():
            reset_inline_video_request(token)
            await _edit_inline_status(bm.something_went_wrong(), with_retry_button=True)

        async def _on_too_large():
            complete_inline_video_request(token)
            await _edit_inline_status(bm.audio_too_large())

        async def _prepare_metadata(path: str):
            return DeezerMetadataWrapper(downloaded_track)

        async def _send_downloaded(path: str, meta_wrapper):
            await _edit_inline_status(bm.uploading_status())
            meta = meta_wrapper.track if meta_wrapper else downloaded_track
            bot_avatar = await get_bot_avatar_thumbnail_fn(deps.bot) if get_bot_avatar_thumbnail_fn else None
            audio_thumbnail = (
                FSInputFile(meta.thumb_path, filename="cover.jpg")
                if (meta and getattr(meta, "thumb_path", None) and os.path.exists(meta.thumb_path))
                else bot_avatar
            )
            title = getattr(meta, "title", "Deezer Track")
            performer = getattr(meta, "artist", "Deezer")
            duration = coerce_audio_duration_seconds(getattr(meta, "duration", 0))

            return await send_audio_with_thumbnail(
                deps.bot.send_audio,
                chat_id=channel_id,
                audio=FSInputFile(path, filename=build_audio_filename(title)),
                title=title,
                performer=performer,
                caption=f"Deezer Music from {actor_name}",
                audio_path=path,
                bot_avatar=audio_thumbnail,
                bot_url=bot_url,
                duration=duration,
                embed_thumbnail=False,
            )

        async def _cleanup(path: str):
            if path:
                await remove_file(path)
            if downloaded_track and getattr(downloaded_track, "thumb_path", None):
                await remove_file(downloaded_track.thumb_path)

        result = await run_audio_flow(
            cache_key=cache_key,
            db_service=deps.db,
            send_cached=_send_cached,
            download_audio=_download_audio,
            on_missing_audio=_on_missing_audio,
            max_file_size=max_file_size,
            on_too_large=_on_too_large,
            prepare_metadata=_prepare_metadata,
            send_downloaded=_send_downloaded,
            cleanup_path=_cleanup,
        )

        if result is None:
            return

        title = getattr(downloaded_track, "title", "Deezer Track")
        performer = getattr(downloaded_track, "artist", build_bot_audio_performer(bot_url))
        duration = coerce_audio_duration_seconds(getattr(downloaded_track, "duration", 0))

        edited = await safe_edit_inline_media_fn(
            deps.bot,
            inline_message_id,
            types.InputMediaAudio(
                media=result.file_id,
                caption=bm.captions(request.user_settings.get("captions", "on"), None, bot_url),
                performer=performer,
                title=title,
                duration=duration,
                parse_mode="HTML",
            ),
        )

        if edited:
            complete_inline_video_request(token)
            return

        reset_inline_video_request(token)
        await _edit_inline_status(bm.something_went_wrong(), with_retry_button=True)

    except Exception as exc:
        logging.exception(
            "Error sending inline Deezer audio: inline_message_id=%s token=%s error=%s",
            inline_message_id,
            token,
            exc,
        )
        reset_inline_video_request(token)
        await _edit_inline_status(bm.something_went_wrong(), with_retry_button=True)
