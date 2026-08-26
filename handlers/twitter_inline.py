import os
from typing import Optional
from urllib.parse import urlsplit

from aiogram import types

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
from services.logger import logger as logging, summarize_text_for_log
from services.inline.album_links import create_inline_album_request
from services.inline.send_flow import (
    deliver_inline_photo,
    deliver_inline_video,
    ensure_album_preview_file_id,
    run_inline_send_flow,
)
from services.inline.service_icons import get_inline_service_icon
from services.inline.video_requests import (
    complete_inline_video_request,
    create_inline_video_request,
)

logging = logging.bind(service="twitter_inline")


async def handle_twitter_inline_query(
    query: types.InlineQuery,
    *,
    deps: HandlerDependencies,
    twitter_link_regex: str,
    channel_id: Optional[int],
    get_tweet_context_fn,
    extract_twitter_media_items_fn,
    normalize_twitter_media_kind_fn,
    build_twitter_media_cache_key_fn,
    get_twitter_media_preview_url_fn,
    build_twitter_open_in_bot_result_fn,
    get_bot_url_fn=get_bot_url,
    safe_answer_inline_query_fn=safe_answer_inline_query,
) -> None:
    try:
        await deps.send_analytics(
            user_id=query.from_user.id,
            chat_type=query.chat_type,
            action_name="inline_twitter_media",
        )
        import re

        match = re.search(twitter_link_regex, query.query or "")
        if not match:
            await query.answer([], cache_time=1, is_personal=True)
            return

        source_url = match.group(0)
        user_settings = await deps.db.user_settings(query.from_user.id)
        bot_url = await get_bot_url_fn(deps.bot)
        def _album_deep_link() -> str:
            album_token = create_inline_album_request(query.from_user.id, "twitter", source_url)
            return build_start_deeplink_url(bot_url, f"album_{album_token}")

        context = await get_tweet_context_fn(source_url)
        if not context:
            await safe_answer_inline_query_fn(
                query,
                [
                    build_twitter_open_in_bot_result_fn(
                        result_id="twitter_open_in_bot",
                        deep_link=_album_deep_link(),
                        description="Open this post in the bot.",
                    )
                ],
                cache_time=10,
                is_personal=True,
            )
            return

        tweet_id, tweet_media = context
        media_items = extract_twitter_media_items_fn(tweet_media)
        if len(media_items) > 1:
            preview_file_id = None
            first_media = media_items[0]
            first_media_kind = normalize_twitter_media_kind_fn(first_media.get("type"))
            if first_media_kind == "photo":
                preview_file_id = await ensure_album_preview_file_id(
                    deps=deps,
                    channel_id=channel_id,
                    photo_url=first_media["url"],
                    cache_key=build_twitter_media_cache_key_fn(source_url, 0, "photo", len(media_items)),
                    service_name="X / Twitter",
                    source_url=source_url,
                    log=logging,
                )
            preview_url = get_twitter_media_preview_url_fn(media_items[0], tweet_media) or next(
                (
                    get_twitter_media_preview_url_fn(item, tweet_media)
                    for item in media_items
                    if get_twitter_media_preview_url_fn(item, tweet_media)
                ),
                None,
            )
            results = [
                build_inline_album_result(
                    result_id=f"twitter_album_{tweet_id}",
                    service_name="Twitter",
                    deep_link=_album_deep_link(),
                    message_text=bm.captions(
                        user_settings["captions"],
                        tweet_media.get("text"),
                        bot_url,
                    ),
                    preview_file_id=preview_file_id,
                    preview_url=preview_url,
                    thumbnail_url=preview_url or get_inline_service_icon("twitter"),
                )
            ]
            await safe_answer_inline_query_fn(query, results, cache_time=10, is_personal=True)
            return

        if len(media_items) != 1:
            await safe_answer_inline_query_fn(
                query,
                [
                    build_twitter_open_in_bot_result_fn(
                        result_id=f"twitter_open_{tweet_id}",
                        deep_link=_album_deep_link(),
                        description="Inline preview is limited for this post. Open it in the bot.",
                    )
                ],
                cache_time=10,
                is_personal=True,
            )
            return

        media = media_items[0]
        media_kind = normalize_twitter_media_kind_fn(media.get("type"))
        if not media_kind:
            await safe_answer_inline_query_fn(
                query,
                [
                    build_twitter_open_in_bot_result_fn(
                        result_id=f"twitter_open_unknown_{tweet_id}",
                        deep_link=_album_deep_link(),
                        description="Open this post in the bot.",
                    )
                ],
                cache_time=10,
                is_personal=True,
            )
            return
        token = create_inline_video_request("twitter", source_url, query.from_user.id, user_settings)
        action_text = "Enviar foto inline" if media_kind == "photo" else "Enviar vídeo inline"
        prompt_text = (
            bm.inline_send_video_prompt("Twitter")
            if media_kind == "video"
            else "A foto do Twitter está sendo preparada...\nSe não iniciar automaticamente, toque no botão abaixo."
        )
        preview_url = get_twitter_media_preview_url_fn(media, tweet_media) or get_inline_service_icon("twitter")
        results = [
            types.InlineQueryResultArticle(
                id=f"twitter_inline:{token}",
                title="X / Twitter Post",
                description=tweet_media.get("text") or f"Press the button to send this {media_kind} inline.",
                thumbnail_url=preview_url,
                input_message_content=types.InputTextMessageContent(message_text=prompt_text),
                reply_markup=kb.inline_send_media_keyboard(action_text, f"inline:twitter:{token}"),
            )
        ]
        await safe_answer_inline_query_fn(query, results, cache_time=10, is_personal=True)
    except Exception as exc:
        logging.exception(
            "Error processing Twitter inline query: user_id=%s query=%s error=%s",
            query.from_user.id,
            summarize_text_for_log(query.query),
            exc,
        )
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
    twitter_downloader,
    get_tweet_context_fn,
    extract_twitter_media_items_fn,
    normalize_twitter_media_kind_fn,
    build_twitter_media_cache_key_fn,
    get_bot_url_fn=get_bot_url,
    safe_edit_inline_media_fn=safe_edit_inline_media,
    safe_edit_inline_text_fn=safe_edit_inline_text,
) -> None:
    async def _plan(request, edit_status, state) -> None:
        context = await get_tweet_context_fn(request.source_url)
        if not context:
            complete_inline_video_request(token)
            await edit_status("Only single photo or single video posts are supported inline.")
            return

        _, tweet_media = context
        media_items = extract_twitter_media_items_fn(tweet_media)
        if len(media_items) != 1:
            complete_inline_video_request(token)
            await edit_status("Only single photo or single video posts are supported inline.")
            return

        media = media_items[0]
        media_kind = normalize_twitter_media_kind_fn(media.get("type"))
        if not media_kind:
            complete_inline_video_request(token)
            await edit_status("Only single photo or single video posts are supported inline.")
            return
        post_url = tweet_media["tweetURL"]
        post_caption = tweet_media.get("text")
        likes = tweet_media.get("likes")
        comments = tweet_media.get("replies")
        retweets = tweet_media.get("retweets")

        async def _build_caption():
            return bm.captions(
                request.user_settings["captions"],
                post_caption,
                await get_bot_url_fn(deps.bot),
            )

        reply_markup = kb.return_video_info_keyboard(
            None,
            likes,
            comments,
            retweets,
            None,
            post_url,
            request.user_settings,
        )

        if media_kind == "photo":
            await deliver_inline_photo(
                deps=deps,
                token=token,
                inline_message_id=inline_message_id,
                channel_id=channel_id,
                service_name="X / Twitter",
                cache_key=build_twitter_media_cache_key_fn(post_url, 0, "photo", 1),
                photo_url=media["url"],
                channel_caption=f"X / Twitter Photo from {actor_name}",
                build_caption=_build_caption,
                reply_markup=reply_markup,
                edit_status=edit_status,
                safe_edit_inline_media_fn=safe_edit_inline_media_fn,
                log=logging,
            )
            return

        async def _download(on_progress):
            file_name = os.path.join(
                f"{tweet_media['conversationID']}_inline_{token}",
                os.path.basename(urlsplit(media["url"]).path),
            )
            return await twitter_downloader.download(
                media["url"],
                file_name,
                skip_if_exists=True,
                user_id=request.owner_user_id,
                request_id=f"twitter_inline:{request.owner_user_id}:{request_event_id}:{tweet_media['conversationID']}",
                max_size_bytes=max_file_size,
                on_progress=on_progress,
            )

        await deliver_inline_video(
            deps=deps,
            token=token,
            inline_message_id=inline_message_id,
            channel_id=channel_id,
            max_file_size=max_file_size,
            service_name="X / Twitter",
            cache_key=post_url,
            channel_caption=f"X / Twitter Video from {actor_name}",
            download_fn=_download,
            progress_label="X / Twitter video",
            build_caption=_build_caption,
            reply_markup=reply_markup,
            edit_status=edit_status,
            state=state,
            safe_edit_inline_media_fn=safe_edit_inline_media_fn,
            metrics_log_key="twitter_inline",
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
