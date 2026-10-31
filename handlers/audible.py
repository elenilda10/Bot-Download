"""Per-user Audible authorization and book delivery, exclusively in private chats."""

import asyncio
import html
import re
import time
from dataclasses import dataclass

from aiogram import F, Router, types
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

import messages as bm
from app_context import bot
from config import MAX_FILE_SIZE
from handlers.utils import get_bot_url, get_bot_username, safe_edit_text
from i18n import t
from services.links.audible import MARKETS, book_identity, book_url, extract_audible_url
from services.platforms.audible_service import AudibleError, service

router = Router(name="audible")


@dataclass
class Flow:
    market: str
    asin: str = ""
    step: str = "consent"
    deadline: float = 0


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
    await message.answer(
        t("audible.explain"), reply_markup=_keyboard(market, asin), parse_mode="HTML"
    )


async def _failure(message, code):
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
    await message.answer(t("audible.error." + (code if code in known else "download")))


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
            await call.message.answer(t("audible.disconnected"))
        else:
            await service.cancel(owner)
            await call.message.answer(t("audible.cancelled"))
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
    _flows[owner] = Flow(action[2], action[3], "email", time.monotonic() + 600)
    await call.message.answer(t("audible.email"), reply_markup=_cancel_keyboard())


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
        message.from_user.id in _flows and _private(message)
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
        await _failure(message, "expired")
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
                await _failure(message, "login")
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
            await message.answer(
                t("audible.browser"), reply_markup=keyboard, parse_mode="HTML"
            )
        elif flow.step == "response":
            flow.step = "finishing"
            try:
                await service.finish_login(owner, message.text.strip())
            except AudibleError as exc:
                flow.step = "response" if str(exc) == "response" else "email"
                raise
            _flows.pop(owner, None)
            await message.answer(
                t("audible.connected"),
                reply_markup=_keyboard(flow.market, connected=True),
            )
            if flow.asin:
                await process_audible(
                    message, direct_url=book_url(flow.market, flow.asin)
                )
        else:
            await _failure(message, "busy")
    except AudibleError as exc:
        await _failure(message, str(exc))
    except TimeoutError:
        _flows.pop(owner, None)
        await _failure(message, "expired")
    except OSError:
        _flows.pop(owner, None)
        await service.cancel_login(owner)
        await _failure(message, "unavailable")


@router.business_message(
    lambda message: bool(extract_audible_url(message.text or message.caption))
)
@router.message(
    lambda message: bool(extract_audible_url(message.text or message.caption))
)
async def process_audible(message: types.Message, direct_url=None):
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
    status = await message.answer(
        t("audible.checking"), reply_markup=_cancel_keyboard()
    )
    try:
        async with service.book(owner, market, asin, max_size=MAX_FILE_SIZE) as (
            path,
            title,
        ):
            await safe_edit_text(
                status,
                bm.uploading_status(platform="Audible"),
                reply_markup=_cancel_keyboard(),
            )
            bot_url = await get_bot_url(bot)
            await message.answer_audio(
                audio=FSInputFile(path, filename=asin + ".m4a"),
                title=title[:100],
                caption=bm.captions("on", html.escape(title[:600]), bot_url),
                parse_mode="HTML",
            )
        await safe_edit_text(status, t("audible.finished"), reply_markup=None)
        return True
    except AudibleError as exc:
        if str(exc) == "session":
            (service.profile(owner, market) / ".connected").unlink(missing_ok=True)
        await safe_edit_text(status, t("audible.stopped"), reply_markup=None)
        await _failure(message, str(exc))
    except asyncio.CancelledError:
        raise
    except (OSError, TimeoutError, TelegramAPIError):
        await safe_edit_text(status, t("audible.stopped"), reply_markup=None)
        await _failure(message, "download")
    return False
