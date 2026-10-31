"""Actual handler functions: consent, private-only OAuth and independent ownership."""

import importlib.util
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.platforms.audible_service import AudibleError

ROOT = Path(__file__).resolve().parents[1]
ASIN = "B0G3C6QT4Y"
URL = "https://www.audible.com.br/pd/Book/" + ASIN


def message(owner=1, text=URL, group=False):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=owner),
        chat=SimpleNamespace(
            id=-100 if group else owner, type="supergroup" if group else "private"
        ),
        text=text,
        caption=None,
        business_connection_id=None,
        delete=AsyncMock(),
        answer=AsyncMock(return_value=SimpleNamespace(edit_text=AsyncMock())),
        answer_audio=AsyncMock(),
        reply_audio=AsyncMock(),
        reply=AsyncMock(return_value=SimpleNamespace(edit_text=AsyncMock())),
    )


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    ui = ModuleType("handlers.utils")
    ui.get_bot_username = AsyncMock(return_value="testbot")
    ui.get_bot_url = AsyncMock(return_value="https://t.me/testbot")
    ui.safe_edit_text = AsyncMock()
    ui.safe_delete_message = AsyncMock()
    ui.load_user_settings = AsyncMock(
        return_value={"captions": "on", "delete_message": "off"}
    )
    ui.maybe_delete_user_message = AsyncMock()
    ui.remove_file = AsyncMock()
    ui.get_bot_avatar_thumbnail = AsyncMock(return_value=None)
    ui.send_chat_action_if_needed = AsyncMock()
    monkeypatch.setitem(sys.modules, "handlers.utils", ui)
    spec = importlib.util.spec_from_file_location(
        "isolated_audible_handler", ROOT / "handlers/audible.py"
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    backend = SimpleNamespace(
        jobs={},
        pending={},
        starting={},
        connected=lambda *args: False,
        cancel_login=AsyncMock(),
        cancel=AsyncMock(),
        disconnect=AsyncMock(),
        begin_login=AsyncMock(
            return_value="https://www.amazon.com.br/ap/signin?test=1"
        ),
        finish_login=AsyncMock(),
        profile=lambda *args: tmp_path,
    )
    module.service = backend

    async def send(sender, **kwargs):
        return await sender(**kwargs)

    module.send_audio_with_thumbnail = AsyncMock(side_effect=send)
    return module, backend, ui


@pytest.mark.asyncio
async def test_first_link_explains_why_login_is_needed_without_using_vps_account(
    runtime,
):
    module, backend, _ = runtime
    msg = message()
    assert not await module.process_audible(msg)
    text = msg.reply.call_args.args[0]
    assert "library" in text and "password" in text and "/audible_logout" in text
    assert module._flows[1].step == "consent"
    backend.begin_login.assert_not_awaited()
    assert (
        msg.reply.call_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        == f"audible:connect:br:{ASIN}"
    )


@pytest.mark.asyncio
async def test_private_callback_uses_clicking_owner_not_bot_message_author(runtime):
    module, backend, _ = runtime
    msg = message()
    msg.from_user.id = 999  # Telegram callback messages are authored by the bot.
    call = SimpleNamespace(
        message=msg,
        from_user=SimpleNamespace(id=1),
        data=f"audible:connect:br:{ASIN}",
        answer=AsyncMock(),
    )
    await module.account_action(call)
    assert module._flows[1].asin == ASIN and module._flows[1].step == "email"
    email = message(text="owner@example.test")
    await module.authorization_input(email)
    email.delete.assert_awaited_once()
    backend.begin_login.assert_awaited_once_with(1, "br", "owner@example.test")
    assert module._flows[1].step == "response"
    keyboard = module.safe_edit_text.call_args.kwargs["reply_markup"]
    assert len(keyboard.inline_keyboard) == 1
    assert keyboard.inline_keyboard[0][0].url.startswith("https://www.amazon.")
    email.answer.assert_not_awaited()
    assert "owner@example.test" not in str(module.safe_edit_text.await_args.args[1])


@pytest.mark.asyncio
async def test_other_user_cannot_complete_pending_login(runtime):
    module, backend, _ = runtime
    module._flows[1] = module.Flow("br", ASIN, "response", float("inf"))
    other = message(
        owner=2,
        text="https://www.audible.com.br/ap/maplanding?openid.oa2.authorization_code=secret",
    )
    assert module._auth_input(other)
    await module.authorization_input(other)
    backend.finish_login.assert_not_awaited()
    assert 1 in module._flows
    other.delete.assert_awaited_once()
    assert "secret" not in other.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_group_link_only_offers_private_chat_and_never_starts_login(runtime):
    module, backend, _ = runtime
    msg = message(group=True)
    assert not await module.process_audible(msg)
    assert (
        msg.answer.call_args.kwargs["reply_markup"]
        .inline_keyboard[0][0]
        .url.endswith("audible_br_" + ASIN)
    )
    backend.begin_login.assert_not_awaited()
    assert not module._flows


@pytest.mark.asyncio
async def test_return_message_is_deleted_and_original_book_retried_for_same_owner(
    runtime, tmp_path
):
    module, backend, _ = runtime
    module._flows[1] = module.Flow("br", ASIN, "response", float("inf"))
    backend.finish_login.side_effect = lambda owner, url: setattr(
        backend, "connected", lambda *args: True
    )
    cleaned = []

    @asynccontextmanager
    async def book(owner, market, asin, **kwargs):
        assert (owner, market, asin) == (1, "br", ASIN)
        path = tmp_path / "audio.m4a"
        path.write_bytes(b"audio")
        try:
            yield path, "Book"
        finally:
            path.unlink()
            cleaned.append(True)

    backend.book = book
    msg = message(
        text="https://www.audible.com.br/ap/maplanding?openid.oa2.authorization_code=private"
    )
    await module.authorization_input(msg)
    assert 1 not in module._flows and cleaned == [True]
    msg.delete.assert_awaited_once()
    msg.answer_audio.assert_awaited_once()
    module.safe_delete_message.assert_awaited_once()
    assert all("authorization_code" not in str(c) for c in msg.answer.call_args_list)
    assert msg.answer_audio.call_args.kwargs["title"] == "Book"


@pytest.mark.asyncio
async def test_explicit_logout_deletes_only_requesting_users_account(runtime):
    module, backend, _ = runtime
    module._flows[1] = module.Flow("br")
    module._flows[2] = module.Flow("us")
    await module.disconnect(message(text="/audible_logout"))
    backend.disconnect.assert_awaited_once_with(1)
    assert 1 not in module._flows and 2 in module._flows


@pytest.mark.asyncio
async def test_library_failure_is_explained_without_returning_raw_vendor_diagnostics(
    runtime,
):
    module, backend, _ = runtime
    backend.connected = lambda *args: True

    @asynccontextmanager
    async def book(*args, **kwargs):
        raise AudibleError("library")
        yield

    backend.book = book
    msg = message()
    assert not await module.process_audible(msg)
    assert "library" in module.safe_edit_text.call_args.args[1]
    assert module.safe_edit_text.call_args.kwargs["reply_markup"] is None
    backend.disconnect.assert_not_awaited()


@pytest.mark.asyncio
async def test_login_reuses_notice_and_delivers_in_reply_to_original_book(
    runtime, tmp_path
):
    module, backend, _ = runtime
    original = message()
    await module.process_audible(original)
    notice = module._flows[1].status
    call = SimpleNamespace(
        message=notice,
        from_user=SimpleNamespace(id=1),
        data=f"audible:connect:br:{ASIN}",
        answer=AsyncMock(),
    )
    notice.chat = original.chat
    notice.business_connection_id = None
    await module.account_action(call)
    email = message(text="owner@example.test")
    await module.authorization_input(email)
    backend.finish_login.side_effect = lambda *args: setattr(
        backend, "connected", lambda *args: True
    )

    @asynccontextmanager
    async def book(*args, **kwargs):
        path = tmp_path / "audio.m4a"
        path.write_bytes(b"audio")
        try:
            yield path, "Book"
        finally:
            path.unlink(missing_ok=True)

    backend.book = book
    handoff = message(
        text="https://www.audible.com.br/ap/maplanding?openid.oa2.authorization_code=private"
    )
    await module.authorization_input(handoff)
    original.reply.assert_awaited_once()  # only the initial consent notice
    email.answer.assert_not_awaited()
    handoff.answer.assert_not_awaited()
    assert all(c.args[0] is notice for c in module.safe_edit_text.await_args_list)
    original.reply_audio.assert_awaited_once()
    handoff.answer_audio.assert_not_awaited()
    handoff.reply_audio.assert_not_awaited()
    assert module.send_audio_with_thumbnail.await_args.args[0] is original.reply_audio
    assert (
        module.send_audio_with_thumbnail.await_args.kwargs["caption"].count("Book") == 0
    )
    module.safe_delete_message.assert_awaited_once_with(notice)
