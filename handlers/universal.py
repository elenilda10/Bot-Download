import asyncio
import os
import shutil
import uuid
from typing import Optional

from aiogram import F, Router, types
from aiogram.types import FSInputFile
from aiogram.utils.media_group import MediaGroupBuilder

import keyboards as kb
import messages as bm
from config import MAX_FILE_SIZE, OUTPUT_DIR
from handlers.request_dedupe import claim_message_request
from handlers.user import update_info
from handlers.utils import (
    get_bot_url,
    get_message_text,
    load_user_settings,
    react_to_message,
    safe_delete_message,
)
from services.logger import logger as logging, summarize_url_for_log
from services.media.video_metadata import build_video_send_kwargs
from services.platforms.universal_extractor import download_universal_media

logging = logging.bind(service="universal_handler")
router = Router(name=__name__)

URL_REGEX = r"https?://(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b[-a-zA-Z0-9()@:%_\+.~#?&//=]*"


def _build_universal_keyboard(url: str, user_settings: dict, lang: str = "pt"):
    audio_cb = f"audio:univ:{url}"
    return kb.return_video_info_keyboard(
        views=None,
        likes=None,
        comments=None,
        shares=None,
        music_play_url=None,
        video_url=url,
        user_settings=user_settings,
        audio_callback_data=audio_cb,
        lang=lang,
    )


@router.message(F.text.regexp(URL_REGEX, mode="search") | F.caption.regexp(URL_REGEX, mode="search"))
@router.business_message(F.text.regexp(URL_REGEX, mode="search") | F.caption.regexp(URL_REGEX, mode="search"))
async def handle_universal_url(message: types.Message, db=None):
    text = get_message_text(message)
    if not text:
        return

    import re
    match = re.search(URL_REGEX, text)
    if not match:
        return
    url = match.group(0)

    # Ignora domínios que já possuem roteadores dedicados
    ignored_domains = (
        "tiktok.com", "douyin.com",
        "youtube.com", "youtu.be",
        "instagram.com",
        "twitter.com", "x.com",
        "spotify.com", "open.spotify.com",
        "soundcloud.com",
        "pinterest.com", "pin.it",
        "threads.net",
    )
    if any(d in url.lower() for d in ignored_domains):
        return

    business_id = message.business_connection_id
    logging.info(
        "Universal extractor triggered: user_id=%s url=%s",
        message.from_user.id,
        summarize_url_for_log(url),
    )

    request_lease = await claim_message_request(message, service="universal", url=url)
    if request_lease is None:
        return

    await react_to_message(message, "👾", business_id=business_id)

    user_settings = {}
    if db:
        try:
            user_settings = await load_user_settings(db, message)
        except Exception:
            pass

    user_captions = user_settings.get("captions", "on")
    user_lang = user_settings.get("language") or "pt"
    video_quality = user_settings.get("video_quality")
    bot_url = await get_bot_url(message.bot)

    status_msg = await message.answer(bm.downloading_video_status(lang=user_lang))

    try:
        media_result = await download_universal_media(url, video_quality=video_quality)
        if not media_result or not media_result.files:
            await status_msg.edit_text(bm.nothing_found(lang=user_lang))
            return

        caption_text = bm.captions(user_captions, media_result.title or "Media", bot_url)
        reply_markup = _build_universal_keyboard(url, user_settings, lang=user_lang)

        # 1. Envio de álbum (fotos/vídeos múltiplos)
        if len(media_result.files) > 1:
            media_group = MediaGroupBuilder(caption=caption_text)
            for f in media_result.files[:10]:
                if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    media_group.add_photo(media=FSInputFile(f))
                elif f.lower().endswith((".mp4", ".mov")):
                    media_group.add_video(media=FSInputFile(f))
            await message.reply_media_group(media=media_group.build())

        # 2. Envio de vídeo único
        elif media_result.media_type == "video" or media_result.files[0].lower().endswith((".mp4", ".mov", ".mkv", ".webm")):
            file_path = media_result.files[0]
            video_kwargs, thumb_path = await build_video_send_kwargs(file_path)
            try:
                await message.reply_video(
                    video=FSInputFile(file_path),
                    caption=caption_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                    **video_kwargs,
                )
            finally:
                if thumb_path and os.path.exists(thumb_path):
                    try:
                        os.unlink(thumb_path)
                    except Exception:
                        pass

        # 3. Envio de foto única
        else:
            await message.reply_photo(
                photo=FSInputFile(media_result.files[0]),
                caption=caption_text,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )

        await safe_delete_message(status_msg)

        dir_to_clean = os.path.dirname(media_result.files[0])
        shutil.rmtree(dir_to_clean, ignore_errors=True)

    except Exception as exc:
        logging.error("Universal handler error: %s", exc, exc_info=True)
        try:
            await status_msg.edit_text(bm.nothing_found(lang=user_lang))
        except Exception:
            pass


@router.callback_query(F.data.startswith("audio:univ:"))
async def handle_universal_audio(call: types.CallbackQuery, db=None):
    url = call.data.replace("audio:univ:", "")
    user_settings = {}
    if db:
        try:
            user_settings = await load_user_settings(db, call.message)
        except Exception:
            pass
    user_lang = user_settings.get("language") or "pt"
    user_captions = user_settings.get("captions", "on")
    bot_url = await get_bot_url(call.bot)

    await call.answer()
    status_msg = await call.message.answer(bm.downloading_audio_status(lang=user_lang))

    task_id = str(uuid.uuid4())[:8]
    task_dir = os.path.join(OUTPUT_DIR, f"univ_audio_{task_id}")
    os.makedirs(task_dir, exist_ok=True)
    out_template = os.path.join(task_dir, "%(title).80s_%(id)s.%(ext)s")

    cmd = [
        "yt-dlp",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "--referer", url,
        "-o", out_template,
        url,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        mp3_files = [
            os.path.join(task_dir, f) for f in os.listdir(task_dir) if f.endswith(".mp3")
        ]
        if not mp3_files:
            await status_msg.edit_text(bm.nothing_found(lang=user_lang))
            return

        audio_path = mp3_files[0]
        title = os.path.splitext(os.path.basename(audio_path))[0]
        caption = bm.captions(user_captions, title, bot_url)

        await call.message.reply_audio(
            audio=FSInputFile(audio_path),
            title=title,
            caption=caption,
            parse_mode="HTML",
        )
        await safe_delete_message(status_msg)
    except Exception as exc:
        logging.error("Universal audio error: %s", exc, exc_info=True)
        await status_msg.edit_text(bm.nothing_found(lang=user_lang))
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)
