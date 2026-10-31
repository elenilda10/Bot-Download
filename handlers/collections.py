"""Playlist/album orchestration; single-item handlers retain cache and delivery logic."""

from __future__ import annotations

import asyncio
import html
import os
import secrets
import time
from dataclasses import dataclass, field

from aiogram import F, Router, types
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import messages as bm
from app_context import bot, db
from handlers.request_dedupe import claim_message_request
from handlers.user import update_info
from handlers.utils import (
    handle_download_error,
    load_user_settings,
    maybe_delete_user_message,
    safe_edit_text,
    should_skip_duplicate_business_message,
)
from i18n import t
from services.logger import logger
from services.media.collection_flow import run_collection
from services.platforms.media_collections import (
    collection_limit,
    get_spotify_collection,
    get_youtube_collection,
)
from utils.download_errors import reset_failures
from utils.download_manager import DownloadQueueBusyError, DownloadRateLimitError

router = Router(name="media_collections")


@dataclass
class Job:
    owner: int
    chat: int
    business: str | None
    token: str
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    status_id: int | None = None


_jobs: dict[str, Job] = {}
_owners: dict[int, str] = {}


def _active_limit():
    try:
        return max(1, min(10, int(os.getenv("COLLECTION_MAX_ACTIVE", "3"))))
    except ValueError:
        return 3


def _keyboard(token):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("collection.cancel"),
                    callback_data="collection:cancel:" + token,
                )
            ]
        ]
    )


@router.callback_query(F.data.startswith("collection:cancel:"))
async def cancel_collection(call: types.CallbackQuery):
    token = (call.data or "").removeprefix("collection:cancel:")
    job = _jobs.get(token)
    message = call.message
    if not job or not message:
        await call.answer(t("collection.expired"), show_alert=True)
    elif (
        call.from_user.id != job.owner
        or message.chat.id != job.chat
        or message.message_id != job.status_id
        or getattr(message, "business_connection_id", None) != job.business
    ):
        await call.answer(t("collection.owner_only"), show_alert=True)
    else:
        job.cancelled.set()
        await call.answer(t("collection.cancelling"), show_alert=True)


async def process_collection(message, url, *, platform, audio=False):
    if not message.from_user:
        return False
    if await should_skip_duplicate_business_message(
        message, bot, service_name=platform, logger=logger
    ):
        return False
    user_id = message.from_user.id
    if user_id in _owners or len(_jobs) >= _active_limit():
        await message.reply(t("collection.busy"))
        return False
    token = secrets.token_hex(8)
    job = Job(
        user_id,
        message.chat.id,
        getattr(message, "business_connection_id", None),
        token,
    )
    _jobs[token], _owners[user_id] = job, token
    status = None
    lease = None
    try:
        lease = await claim_message_request(
            message, service=platform.lower() + "_collection", url=url
        )
        if lease is None:
            return False
        settings = await load_user_settings(db, message)
        status = await message.reply(
            t("collection.fetching", platform=platform),
            reply_markup=_keyboard(token),
            parse_mode="HTML",
        )
        job.status_id = status.message_id
        resolver = (
            get_spotify_collection if platform == "Spotify" else get_youtube_collection
        )
        listing = await resolver(url, limit=collection_limit())
        if not listing.entries:
            await safe_edit_text(status, t("collection.empty"), reply_markup=None)
            return False
        title = html.escape(listing.title[:150])
        notes = []
        if listing.limited:
            notes.append(t("collection.limit", limit=len(listing.entries)))
        if listing.partial:
            notes.append(t("collection.partial"))
        extra = "\n" + "\n".join(notes) if notes else ""
        started = time.monotonic()

        async def progress(index, entry):
            await safe_edit_text(
                status,
                bm.media_transfer_status(platform)
                + "\n"
                + t(
                    "collection.progress",
                    title=title,
                    index=index,
                    count=len(listing.entries),
                    item=html.escape(entry.title[:100]),
                )
                + extra,
                reply_markup=_keyboard(token),
                parse_mode="HTML",
            )

        async def deliver(entry):
            reset_failures()
            if platform == "Spotify":
                from handlers.spotify import process_spotify

                return await process_spotify(
                    message,
                    direct_url=entry.url,
                    collection_item=True,
                    collection_track=entry.metadata,
                )
            from handlers.youtube import download_music, download_video

            function = download_music if audio else download_video
            return await function(message, direct_url=entry.url, collection_item=True)

        def cancelled():
            return job.cancelled.is_set() or time.monotonic() - started > 3600

        async def on_error(index, exc):
            logger.warning(
                "Collection item failed: index=%s error=%s", index, type(exc).__name__
            )

        result = await run_collection(
            listing,
            deliver,
            progress,
            cancelled,
            stop_on=(DownloadQueueBusyError, DownloadRateLimitError, TelegramAPIError),
            on_error=on_error,
        )
        key = (
            "collection.cancelled"
            if result.cancelled
            else "collection.stopped"
            if result.stopped
            else "collection.finished"
        )
        await safe_edit_text(
            status,
            t(
                key,
                title=title,
                sent=result.sent,
                failed=result.failed,
                unavailable=result.unavailable,
                remaining=len(listing.entries)
                - result.sent
                - result.failed
                - result.unavailable,
            )
            + extra,
            reply_markup=None,
            parse_mode="HTML",
        )
        if result.sent:
            # Partial/cancelled jobs can be retried rather than suppressed as completed.
            if not result.cancelled and not result.stopped and not result.failed:
                lease.mark_success()
            await maybe_delete_user_message(message, settings.get("delete_message"))
        return bool(result.sent)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if status:
            await safe_edit_text(status, t("collection.error"), reply_markup=None)
        await handle_download_error(
            message, error=exc, platform=platform, business_id=job.business
        )
        return False
    finally:
        _jobs.pop(token, None)
        _owners.pop(user_id, None)
        if lease:
            lease.finish()
        await update_info(message)
