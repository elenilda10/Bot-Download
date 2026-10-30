"""Dedicated Reddit messages, albums, scoped cache and media actions."""
from __future__ import annotations
from i18n import t as _t

import asyncio
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from aiogram import F, Router, types
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile

import keyboards as kb
import messages as bm
from app_context import bot, db, send_analytics
from config import MAX_FILE_SIZE, OUTPUT_DIR
from handlers import commands as cmd_mod
from handlers.request_dedupe import claim_message_request
from handlers.utils import (
    get_bot_avatar_thumbnail, get_bot_url, get_message_text, handle_download_error,
    load_user_settings, maybe_delete_user_message, react_to_message,
    safe_delete_message, safe_edit_text, send_chat_action_if_needed,
    should_skip_duplicate_business_message, with_message_logging,
)
from services.links.reddit import REDDIT_URL_REGEX, extract_reddit_url
from services.logger import logger as logging
from services.media.delivery import send_audio_with_thumbnail
from services.media.video_metadata import build_video_send_kwargs
from services.platforms.firefox_cookies import firefox_profile
from services.platforms.reddit_download import download_reddit_media
from utils.media_cache import build_media_cache_key

router = Router(name='reddit_handler')
logging = logging.bind(service='reddit')


class PartialRedditDeliveryError(Exception):
    """Avoid resending an entire album after an earlier batch succeeded."""


def _download_status(lang, settings, kind='unknown', count=1):
    if settings.get('as_document') == 'on':
        return bm.downloading_file_status(lang=lang, platform='Reddit')
    return bm.downloading_video_status(lang=lang, media_kind=kind, count=count, platform='Reddit')


def _token(url):
    return hashlib.blake2s(url.encode(), digest_size=8).hexdigest()


def _scope(message):
    if firefox_profile('reddit'):
        return f'owner:{message.from_user.id}:chat:{message.chat.id}'
    return 'public'


def _cache_key(url, scope, kind):
    return build_media_cache_key('reddit', variant=f'v1:{scope}:{kind}:{_token(url)}')


def _keyboard(url, settings, lang, is_video):
    markup = kb.return_video_info_keyboard(
        views=None, likes=None, comments=None, shares=None, music_play_url=None,
        video_url=url, user_settings=settings, lang=lang or 'pt',
        has_video=is_video, platform='reddit',
        audio_callback_data=f'reddit:audio:{_token(url)}' if is_video else None,
        file_callback_data=f'reddit:doc:{_token(url)}',
    )
    # Keep callback recovery working after a process restart.
    if not any(button.url == url for row in markup.inline_keyboard for button in row):
        markup.inline_keyboard.append([types.InlineKeyboardButton(text=_t('handlers.reddit._keyboard.0c341d06', lang), url=url)])
    return markup


def _sent_entry(message, kind, as_document):
    if as_document and message.document:
        return {'kind': kind, 'file_id': message.document.file_id}
    if kind == 'video' and message.video:
        return {'kind': kind, 'file_id': message.video.file_id}
    if kind == 'photo' and message.photo:
        return {'kind': kind, 'file_id': message.photo[-1].file_id}
    raise ValueError('Telegram returned no Reddit media file_id')


async def _send_entries(message, entries, *, caption, as_document, markup=None):
    saved = []
    for offset in range(0, len(entries), 10):
        batch = entries[offset:offset + 10]
        prepared = []
        for index, entry in enumerate(batch):
            source = entry.get('file_id') or FSInputFile(entry['path'])
            kwargs = {'media': source}
            if offset == 0 and index == 0:
                kwargs.update(caption=caption or None, parse_mode='HTML')
            if entry['kind'] == 'video' and not as_document and entry.get('path'):
                video_kwargs, _ = await build_video_send_kwargs(entry['path'])
                kwargs.update(video_kwargs)
            prepared.append(kwargs)
        reply = {'reply_to_message_id': message.message_id, 'allow_sending_without_reply': True}
        try:
            if len(batch) == 1:
                kwargs = prepared[0]
                source = kwargs.pop('media')
                if len(entries) == 1:
                    kwargs['reply_markup'] = markup
                if as_document:
                    response = await message.answer_document(document=source, **kwargs, **reply)
                elif batch[0]['kind'] == 'video':
                    response = await message.answer_video(video=source, **kwargs, **reply)
                else:
                    response = await message.answer_photo(photo=source, **kwargs, **reply)
                responses = [response]
            else:
                group = []
                for entry, kwargs in zip(batch, prepared):
                    media_class = (types.InputMediaDocument if as_document else
                                   types.InputMediaVideo if entry['kind'] == 'video' else types.InputMediaPhoto)
                    group.append(media_class(**kwargs))
                responses = await message.answer_media_group(media=group, **reply)
            if not responses or len(responses) != len(batch) or not all(responses):
                raise ValueError('Incomplete Reddit album delivery')
        except Exception as exc:
            if saved:
                raise PartialRedditDeliveryError('Reddit album partly delivered') from exc
            raise
        saved.extend(_sent_entry(sent, entry['kind'], as_document)
                     for sent, entry in zip(responses, batch))
    return saved


async def _try_cache(message, url, settings, lang, bot_url, scope):
    as_document = settings.get('as_document') == 'on'
    raw = await db.get_file_id(_cache_key(url, scope, 'documents' if as_document else 'media'))
    if not raw:
        return False
    try:
        data = json.loads(raw)
        entries = data['entries']
        if (not isinstance(entries, list) or not 1 <= len(entries) <= 100
                or any(not isinstance(e, dict) or e.get('kind') not in {'photo', 'video'}
                       or not isinstance(e.get('file_id'), str) for e in entries)):
            return False
        caption = bm.captions(settings.get('captions', 'on'), data.get('caption', ''), bot_url)
        await _send_entries(message, entries, caption=caption, as_document=as_document,
                            markup=_keyboard(url, settings, lang, entries[0]['kind'] == 'video'))
        return True
    except (TelegramBadRequest, ValueError, KeyError, TypeError):
        return False


async def _fresh(message, url, settings, lang, bot_url, scope, directory, status_message=None):
    async def on_kind(kind, count):
        await safe_edit_text(status_message, _download_status(lang, settings, kind, count), parse_mode='HTML')
    result = await download_reddit_media(url, output_dir=directory, max_file_size=MAX_FILE_SIZE, on_kind=on_kind)
    if not result:
        return False
    await safe_edit_text(status_message, bm.uploading_status(lang=lang, platform='Reddit'), parse_mode='HTML')
    entries = [{'kind': item.type, 'path': item.url} for item in result.media_list]
    if len(entries) > 100:
        raise ValueError('Reddit post exceeds media count limit')
    as_document = settings.get('as_document') == 'on'
    caption = bm.captions(settings.get('captions', 'on'), result.description, bot_url)
    saved = await _send_entries(
        message, entries, caption=caption, as_document=as_document,
        markup=_keyboard(url, settings, lang, entries[0]['kind'] == 'video'),
    )
    if saved:
        await db.add_file(_cache_key(url, scope, 'documents' if as_document else 'media'),
                          json.dumps({'entries': saved, 'caption': result.description}), 'album')
    return bool(saved)


@router.message((F.text.regexp(REDDIT_URL_REGEX, mode='search')
                 | F.caption.regexp(REDDIT_URL_REGEX, mode='search')) & ~F.text.startswith('/'))
@router.business_message((F.text.regexp(REDDIT_URL_REGEX, mode='search')
                          | F.caption.regexp(REDDIT_URL_REGEX, mode='search')) & ~F.text.startswith('/'))
@with_message_logging('reddit', 'message')
async def process_reddit(message: types.Message, direct_url: str | None = None):
    user = getattr(message, 'from_user', None)
    if not user or await cmd_mod.is_banned(user.id, message.chat.id):
        return
    business_id = getattr(message, 'business_connection_id', None)
    if await should_skip_duplicate_business_message(message, bot, service_name='Reddit', logger=logging):
        return
    url = extract_reddit_url(direct_url or get_message_text(message))
    if not url:
        return
    lease = await claim_message_request(message, service='reddit', url=url)
    if lease is None:
        return
    status = None
    directory = None
    try:
        settings = await load_user_settings(db, message)
        lang = settings.get('language') or await db.get_language(user.id)
        bot_url = await get_bot_url(bot)
        scope = _scope(message)
        await send_analytics(user_id=user.id, chat_type=message.chat.type, action_name='reddit')
        await react_to_message(message, '👾', business_id=business_id)
        await send_chat_action_if_needed(bot, message.chat.id, ChatAction.UPLOAD_VIDEO, business_id)
        if await _try_cache(message, url, settings, lang, bot_url, scope):
            lease.mark_success()
            await maybe_delete_user_message(message, settings.get('delete_message'))
            return
        if business_id is None:
            status = await message.answer(_download_status(lang, settings), parse_mode='HTML',
                                          reply_to_message_id=message.message_id, allow_sending_without_reply=True)
        Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
        directory = tempfile.mkdtemp(prefix='reddit-', dir=OUTPUT_DIR)
        if await _fresh(message, url, settings, lang, bot_url, scope, directory, status_message=status):
            lease.mark_success()
            await maybe_delete_user_message(message, settings.get('delete_message'))
        else:
            await handle_download_error(message, business_id=business_id, lang=lang)
    except Exception as exc:
        logging.exception('Reddit request failed: %s', type(exc).__name__)
        await handle_download_error(message, business_id=business_id)
    finally:
        await safe_delete_message(status)
        if directory:
            shutil.rmtree(directory, ignore_errors=True)
        lease.finish()


def _callback_source(message, token):
    for row in getattr(getattr(message, 'reply_markup', None), 'inline_keyboard', []):
        for button in row:
            url = extract_reddit_url(button.url)
            if url and _token(url) == token:
                return url
    return None


@router.callback_query(F.data.startswith('reddit:doc:') | F.data.startswith('reddit:audio:'))
async def reddit_media_action(call: types.CallbackQuery):
    if not isinstance(call.message, types.Message):
        await call.answer()
        return
    await call.answer()
    if await cmd_mod.is_banned(call.from_user.id, call.message.chat.id):
        return
    _, kind, token = (call.data or '').split(':', 2)
    url = _callback_source(call.message, token)
    if not url:
        return
    directory = None
    status = None
    try:
        chat_type = getattr(call.message.chat.type, 'value', call.message.chat.type)
        settings = await db.user_settings(call.from_user.id if chat_type == 'private' else call.message.chat.id)
        lang = await db.get_language(call.from_user.id)
        bot_url = await get_bot_url(bot)
        scope = (f'owner:{call.from_user.id}:chat:{call.message.chat.id}'
                 if firefox_profile('reddit') else 'public')
        if kind == 'doc':
            settings = {**settings, 'as_document': 'on'}
            if await _try_cache(call.message, url, settings, lang, bot_url, scope):
                return
        else:
            cached = await db.get_file_id(_cache_key(url, scope, 'audio'))
            if cached:
                try:
                    await call.message.answer_audio(audio=cached, reply_to_message_id=call.message.message_id,
                                                    allow_sending_without_reply=True)
                    return
                except TelegramBadRequest:
                    pass
        status = await call.message.answer(
            bm.downloading_audio_status(lang=lang, platform='Reddit') if kind == 'audio' else bm.downloading_file_status(lang=lang, platform='Reddit'),
            parse_mode='HTML',
                                           reply_to_message_id=call.message.message_id, allow_sending_without_reply=True)
        Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
        directory = tempfile.mkdtemp(prefix='reddit-action-', dir=OUTPUT_DIR)
        if kind == 'doc':
            if not await _fresh(call.message, url, settings, lang, bot_url, scope, directory, status_message=status):
                await handle_download_error(call.message)
            return
        result = await download_reddit_media(url, output_dir=directory, max_file_size=MAX_FILE_SIZE)
        video = next((item for item in result.media_list if item.type == 'video'), None) if result else None
        if not video:
            await handle_download_error(call.message)
            return
        audio = str(Path(directory) / 'reddit.mp3')
        proc = await asyncio.create_subprocess_exec(
            'ffmpeg', '-nostdin', '-y', '-v', 'error', '-i', video.url,
            '-vn', '-codec:a', 'libmp3lame', '-q:a', '2', audio,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=180)
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.communicate()
        if proc.returncode or not Path(audio).is_file() or not 0 < Path(audio).stat().st_size < MAX_FILE_SIZE:
            await handle_download_error(call.message)
            return
        await safe_edit_text(status, bm.uploading_status(lang=lang, platform='Reddit'), parse_mode='HTML')
        sent = await send_audio_with_thumbnail(
            call.message.answer_audio, audio=FSInputFile(audio), audio_path=audio,
            bot_avatar=await get_bot_avatar_thumbnail(bot), bot_url=bot_url,
            caption=bm.captions(settings.get('captions', 'on'), result.description, bot_url), parse_mode='HTML',
            reply_to_message_id=call.message.message_id, allow_sending_without_reply=True,
        )
        if sent and sent.audio:
            await db.add_file(_cache_key(url, scope, 'audio'), sent.audio.file_id, 'audio')
    except Exception as exc:
        logging.exception('Reddit action failed: %s', type(exc).__name__)
        await handle_download_error(call.message)
    finally:
        await safe_delete_message(status)
        if directory:
            shutil.rmtree(directory, ignore_errors=True)
