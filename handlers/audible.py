"""Per-user Audible authorization and book delivery, exclusively in private chats."""

import asyncio
import re
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass

from aiogram import F, Router, types
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

import messages as bm
from app_context import bot, db
from config import MAX_FILE_SIZE
from handlers.utils import (
    get_bot_avatar_thumbnail,
    get_bot_url,
    get_bot_username,
    load_user_settings,
    maybe_delete_user_message,
    remove_file,
    safe_delete_message,
    safe_edit_text,
    send_chat_action_if_needed,
)
from i18n import t
from services.links.audible import MARKETS, book_identity, book_url, extract_audible_url
from services.media.audio_flow import run_audio_flow
from services.media.audio_metadata import PreparedAudioMetadata, build_audio_filename
from services.media.delivery import send_audio_with_thumbnail
from services.platforms.audible_service import AudibleError, service
from utils.download_manager import DownloadMetrics

router = Router(name="audible")


@dataclass
class Flow:
    market: str
    asin: str = ""
    step: str = "consent"
    deadline: float = 0
    status: types.Message | None = None
    source: types.Message | None = None
    login_markup: InlineKeyboardMarkup | None = None


_flows = {}


def _private(message):
    return bool(
        message.from_user
        and message.chat.type == "private"
        and message.chat.id == message.from_user.id
        and not getattr(message, "business_connection_id", None)
    )


def _keyboard(market="br", asin="", *, connected=False):
    buttons = (
        []
        if connected
        else [
            [
                InlineKeyboardButton(
                    text=t("audible.connect"),
                    callback_data=f"audible:connect:{market}:{asin}",
                )
            ]
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(
                text=t("audible.disconnect"), callback_data="audible:logout"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _explain(message, market="br", asin=""):
    owner = message.from_user.id
    for user, flow in list(_flows.items()):
        if flow.deadline < time.monotonic():
            _flows.pop(user, None)
    if len(_flows) >= 256 and owner not in _flows:
        await _failure(message, "busy")
        return
    previous = _flows.get(owner)
    if previous and previous.step not in {"consent", "email"}:
        await _failure(message, "busy", flow=previous)
        return
    flow = Flow(market, asin, "consent", time.monotonic() + 600, source=message)
    if previous and previous.status:
        flow.status = previous.status
        await safe_edit_text(
            flow.status,
            t("audible.explain"),
            reply_markup=_keyboard(market, asin),
            parse_mode="HTML",
        )
    else:
        flow.status = await message.reply(
            t("audible.explain"),
            reply_markup=_keyboard(market, asin),
            parse_mode="HTML",
        )
    _flows[owner] = flow


async def _failure(message, code, *, flow=None):
    known = {
        "busy",
        "unavailable",
        "session",
        "library",
        "timeout",
        "too_large",
        "email",
        "expired",
        "response",
        "login",
        "download",
    }
    text = t("audible.error." + (code if code in known else "download"))
    if flow and flow.status:
        if code == "email":
            text += "\n\n" + t("audible.email")
            markup = _cancel_keyboard()
        elif code == "response":
            text += "\n\n" + t("audible.browser")
            markup = flow.login_markup or _cancel_keyboard()
        else:
            markup = _keyboard(flow.market, flow.asin)
        await safe_edit_text(flow.status, text, reply_markup=markup, parse_mode="HTML")
    else:
        await message.answer(text)


@router.business_message(Command("audible_logout"))
@router.message(Command("audible_logout"))
async def disconnect(message):
    if not _private(message):
        await message.answer(t("audible.private"))
        return
    _flows.pop(message.from_user.id, None)
    await service.disconnect(message.from_user.id)
    await message.answer(t("audible.disconnected"))


@router.business_message(Command("audible"))
@router.message(Command("audible"))
async def account_menu(message):
    if not _private(message):
        await message.answer(t("audible.private"))
        return
    parts = (message.text or "").split()
    market = parts[1].lower() if len(parts) == 2 else "br"
    if market not in MARKETS.values():
        await message.answer(t("audible.market"))
        return
    if service.connected(message.from_user.id, market):
        await message.answer(
            t("audible.connected"), reply_markup=_keyboard(market, connected=True)
        )
    else:
        await _explain(message, market)


@router.message(F.text.regexp(r"^/start(?:@\w+)? audible_[a-z]{2}_[A-Z0-9]{10}$"))
async def private_book_start(message):
    payload = message.text.split()[-1].split("_")
    if payload[1] in MARKETS.values():
        await process_audible(message, direct_url=book_url(payload[1], payload[2]))


@router.callback_query(F.data.startswith("audible:"))
async def account_action(call):
    if (
        not call.message
        or call.message.chat.type != "private"
        or call.message.chat.id != call.from_user.id
        or getattr(call.message, "business_connection_id", None)
    ):
        await call.answer(t("audible.private"), show_alert=True)
        return
    owner = call.from_user.id
    action = (call.data or "").split(":")
    await call.answer()
    if action[1] in {"logout", "cancel"}:
        _flows.pop(owner, None)
        if action[1] == "logout":
            await service.disconnect(owner)
            await safe_edit_text(
                call.message, t("audible.disconnected"), reply_markup=None
            )
        else:
            await service.cancel(owner)
            await safe_edit_text(
                call.message, t("audible.cancelled"), reply_markup=None
            )
        return
    if len(action) != 4 or action[1] != "connect" or action[2] not in MARKETS.values():
        return
    if action[3] and not re.fullmatch("[A-Z0-9]{10}", action[3]):
        return
    if owner in service.jobs or owner in service.starting:
        await _failure(call.message, "busy")
        return
    await service.cancel_login(owner)
    for user, flow in list(_flows.items()):
        if flow.deadline < time.monotonic():
            _flows.pop(user, None)
    if len(_flows) >= 256 and owner not in _flows:
        await _failure(call.message, "busy")
        return
    previous = _flows.get(owner)
    source = (
        previous.source if previous else getattr(call.message, "reply_to_message", None)
    )
    _flows[owner] = Flow(
        action[2],
        action[3],
        "email",
        time.monotonic() + 600,
        status=call.message,
        source=source,
    )
    await safe_edit_text(
        call.message, t("audible.email"), reply_markup=_cancel_keyboard()
    )


def _cancel_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("audible.cancel"), callback_data="audible:cancel"
                )
            ]
        ]
    )


async def _delete_input(message):
    try:
        await message.delete()
    except TelegramAPIError:
        pass


def _auth_input(message):
    if not message.from_user or not message.text or message.text.startswith("/"):
        return False
    return (
        message.from_user.id in _flows
        and _private(message)
        and _flows[message.from_user.id].step != "consent"
    ) or "openid.oa2.authorization_code=" in message.text


@router.business_message(_auth_input)
@router.message(_auth_input)
async def authorization_input(message):
    # Never decorate this handler with message-content/error logging. Never quote
    # the return URL in replies. Delete sensitive input before processing it.
    await _delete_input(message)
    if not _private(message):
        await message.answer(t("audible.private"))
        return
    owner = message.from_user.id
    flow = _flows.get(owner)
    if not flow or flow.deadline < time.monotonic():
        _flows.pop(owner, None)
        await service.cancel_login(owner)
        await _failure(message, "expired", flow=flow)
        return
    try:
        if flow.step == "email":
            flow.step = "starting"
            try:
                url = await service.begin_login(
                    owner, flow.market, message.text.strip()
                )
            except AudibleError:
                flow.step = "email"
                raise
            if not url:
                _flows.pop(owner, None)
                await _failure(message, "login", flow=flow)
                return
            flow.step = "response"
            flow.deadline = time.monotonic() + 600
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=t("audible.amazon"), url=url)],
                    [
                        InlineKeyboardButton(
                            text=t("audible.cancel"), callback_data="audible:cancel"
                        )
                    ],
                ]
            )
            flow.login_markup = keyboard
            await safe_edit_text(
                flow.status,
                t("audible.browser"),
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        elif flow.step == "response":
            flow.step = "finishing"
            try:
                await service.finish_login(owner, message.text.strip())
            except AudibleError as exc:
                flow.step = "response" if str(exc) == "response" else "email"
                raise
            _flows.pop(owner, None)
            if flow.asin:
                source = (
                    flow.source if flow.source and _private(flow.source) else message
                )
                await process_audible(
                    source,
                    direct_url=book_url(flow.market, flow.asin),
                    status_message=flow.status,
                )
            else:
                await safe_edit_text(
                    flow.status,
                    t("audible.connected"),
                    reply_markup=_keyboard(flow.market, connected=True),
                )
        else:
            await _failure(message, "busy", flow=flow)
    except AudibleError as exc:
        await _failure(message, str(exc), flow=flow)
    except TimeoutError:
        _flows.pop(owner, None)
        await _failure(message, "expired", flow=flow)
    except OSError:
        _flows.pop(owner, None)
        await service.cancel_login(owner)
        await _failure(message, "unavailable", flow=flow)


@router.business_message(
    lambda message: bool(extract_audible_url(message.text or message.caption))
)
@router.message(
    lambda message: bool(extract_audible_url(message.text or message.caption))
)
async def process_audible(
    message: types.Message, direct_url=None, *, status_message=None
):
    url = direct_url or extract_audible_url(message.text or message.caption)
    identity = book_identity(url or "")
    if not identity or not message.from_user:
        return False
    market, asin = identity
    if not _private(message):
        username = await get_bot_username(bot)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("audible.open_private"),
                        url=f"https://t.me/{username}?start=audible_{market}_{asin}",
                    )
                ]
            ]
        )
        await message.answer(t("audible.private"), reply_markup=keyboard)
        return False
    owner = message.from_user.id
    if not service.connected(owner, market):
        await _explain(message, market, asin)
        return False
    status = status_message
    sent = False
    try:
        settings = await load_user_settings(db, message)
        text = bm.downloading_audio_status(platform="Audible")
        if status:
            await safe_edit_text(status, text, reply_markup=_cancel_keyboard())
        else:
            status = await message.reply(text, reply_markup=_cancel_keyboard())
        async with AsyncExitStack() as stack:
            audio = {}

            async def download_audio():
                path, title = await stack.enter_async_context(
                    service.book(owner, market, asin, max_size=MAX_FILE_SIZE)
                )
                audio["title"] = title
                return DownloadMetrics(
                    url=book_url(market, asin),
                    path=str(path),
                    size=path.stat().st_size,
                    elapsed=0,
                    used_multipart=False,
                    resumed=False,
                )

            async def prepare_metadata(_path):
                # Libation already prepared the M4A. Do not write MP3 ID3 tags.
                return PreparedAudioMetadata(tagged=False)

            async def send_downloaded(path, _metadata):
                await safe_edit_text(
                    status,
                    bm.uploading_status(platform="Audible"),
                    reply_markup=_cancel_keyboard(),
                )
                await send_chat_action_if_needed(bot, owner, "upload_audio", None)
                bot_url = await get_bot_url(bot)
                source = extract_audible_url(message.text or message.caption)
                reply = book_identity(source or "") == identity
                sender = message.reply_audio if reply else message.answer_audio
                return await send_audio_with_thumbnail(
                    sender,
                    audio=FSInputFile(
                        path,
                        filename=build_audio_filename(audio["title"])[:-4] + ".m4a",
                    ),
                    title=audio["title"][:100],
                    audio_path=path,
                    bot_avatar=await get_bot_avatar_thumbnail(bot),
                    bot_url=bot_url,
                    caption=bm.captions(settings.get("captions", "on"), None, bot_url),
                    embed_thumbnail=False,
                    parse_mode="HTML",
                )

            async def unavailable(*_args):
                raise AudibleError("library")

            async def too_large():
                raise AudibleError("too_large")

            result = await run_audio_flow(
                cache_key=f"audible:{owner}:{market}:{asin}",
                db_service=db,
                cache_enabled=False,
                send_cached=unavailable,
                download_audio=download_audio,
                on_missing_audio=unavailable,
                max_file_size=MAX_FILE_SIZE,
                on_too_large=too_large,
                prepare_metadata=prepare_metadata,
                send_downloaded=send_downloaded,
                cleanup_path=remove_file,
            )
            sent = bool(result)
        if sent:
            await maybe_delete_user_message(message, settings.get("delete_message"))
        return sent
    except AudibleError as exc:
        if str(exc) == "session":
            (service.profile(owner, market) / ".connected").unlink(missing_ok=True)
        flow = Flow(market, asin, status=status, source=message)
        await _failure(message, str(exc), flow=flow)
    except asyncio.CancelledError:
        raise
    except (OSError, TimeoutError, TelegramAPIError):
        await _failure(message, "download", flow=Flow(market, asin, status=status))
    finally:
        if sent:
            await safe_delete_message(status)
    return False
