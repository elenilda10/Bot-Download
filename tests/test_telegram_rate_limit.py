"""429 retries preserve requests, isolate conversations, and release cancelled tasks."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import (
    EditMessageText,
    GetMe,
    SendAudio,
    SendMediaGroup,
    SendMessage,
)
from aiogram.types import InputMediaPhoto

from services.runtime.telegram_rate_limit import TelegramRateLimitMiddleware


class Clock:
    def __init__(self):
        self.now = 100.0
        self.waits = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


def configured(**kwargs):
    clock = Clock()
    return TelegramRateLimitMiddleware(clock=clock, sleep=clock.sleep, **kwargs), clock


def flood(method, seconds=2):
    return TelegramRetryAfter(method, 'Too Many Requests', seconds)


@pytest.mark.asyncio
@pytest.mark.parametrize('method', [
    SendAudio(chat_id=1, audio='existing-file-id'),
    EditMessageText(chat_id=1, message_id=2, text='Uploading'),
    SendMediaGroup(chat_id=-100, media=[InputMediaPhoto(media='a'), InputMediaPhoto(media='b')]),
    GetMe(),
])
async def test_waits_for_server_and_replays_exact_request_only_after_429(method):
    middleware, clock = configured()
    request = AsyncMock(side_effect=[flood(method), 'sent'])
    bot = SimpleNamespace(id=7)
    assert await middleware(request, bot, method) == 'sent'
    assert request.await_count == 2
    assert clock.waits[0] >= 2
    assert all(call.args == (bot, method) for call in request.await_args_list)


@pytest.mark.asyncio
async def test_local_and_global_pacing_and_album_weight():
    middleware, clock = configured()
    request = AsyncMock(return_value=True)
    bot = SimpleNamespace(id=7)
    album = SendMediaGroup(chat_id=-100, media=[InputMediaPhoto(media='a'), InputMediaPhoto(media='b')])
    await middleware(request, bot, album)
    await middleware(request, bot, SendMessage(chat_id='-100', text='Done'))
    assert clock.waits == [pytest.approx(6.2)]
    await middleware(request, bot, SendMessage(chat_id=2, text='Other chat'))
    assert clock.waits[-1] == pytest.approx(0.04)


@pytest.mark.asyncio
async def test_other_chat_and_callback_can_continue_during_cooldown():
    started, release = asyncio.Event(), asyncio.Event()
    async def sleep(seconds):
        if seconds >= 10:
            started.set()
            await release.wait()
        else:
            await asyncio.sleep(0)
    middleware = TelegramRateLimitMiddleware(sleep=sleep, global_interval=0, private_interval=0)
    method = SendMessage(chat_id=1, text='One')
    attempts = []
    async def request(bot, outgoing):
        attempts.append(outgoing)
        if outgoing is method and attempts.count(method) == 1:
            raise flood(outgoing, 10)
        return True
    bot = SimpleNamespace(id=7)
    waiting = asyncio.create_task(middleware(request, bot, method))
    await asyncio.wait_for(started.wait(), 1)
    same_chat = asyncio.create_task(middleware(request, bot, SendMessage(chat_id=1, text='Queued')))
    assert await asyncio.wait_for(middleware(request, bot, SendMessage(chat_id=2, text='Other')), 1)
    assert not same_chat.done() and not waiting.done()
    release.set()
    assert await asyncio.wait_for(waiting, 1)
    assert await asyncio.wait_for(same_chat, 1)
    assert not any(state.users for state in middleware._destinations.values())


@pytest.mark.asyncio
async def test_cancelled_wait_releases_chat_lock():
    started = asyncio.Event()
    async def sleep(seconds):
        started.set()
        await asyncio.Event().wait()
    middleware = TelegramRateLimitMiddleware(sleep=sleep)
    method = SendAudio(chat_id=1, audio='file')
    task = asyncio.create_task(middleware(AsyncMock(side_effect=flood(method)), SimpleNamespace(id=7), method))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    state = next(iter(middleware._destinations.values()))
    assert state.users == 0 and not state.lock.locked()


@pytest.mark.asyncio
async def test_attempt_and_wait_budgets_prevent_infinite_retries():
    middleware, clock = configured(retries=2)
    method = SendMessage(chat_id=1, text='One')
    error = flood(method)
    request = AsyncMock(side_effect=error)
    with pytest.raises(TelegramRetryAfter) as caught:
        await middleware(request, SimpleNamespace(id=7), method)
    assert caught.value is error
    assert request.await_count == 3
    assert sum(clock.waits) == pytest.approx(4.5)
    # A very long restriction remains recorded, but doesn't hang the handler.
    middleware, clock = configured(max_wait=30)
    error = flood(method, 600)
    request = AsyncMock(side_effect=error)
    for _ in range(2):
        with pytest.raises(TelegramRetryAfter):
            await middleware(request, SimpleNamespace(id=7), method)
    assert request.await_count == 1 and not clock.waits


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [TimeoutError('unknown upload outcome'), TelegramBadRequest(SendMessage(chat_id=1, text='a'), 'Forbidden text')])
async def test_does_not_retry_ambiguous_or_non_429_errors(error):
    middleware, clock = configured()
    request = AsyncMock(side_effect=error)
    with pytest.raises(type(error)):
        await middleware(request, SimpleNamespace(id=7), SendMessage(chat_id=1, text='One'))
    assert request.await_count == 1 and not clock.waits


@pytest.mark.asyncio
async def test_idle_cache_is_bounded_without_discarding_pending_cooldowns():
    middleware, clock = configured(cache_size=2)
    bot = SimpleNamespace(id=7)
    for chat in range(10):
        await middleware(AsyncMock(return_value=True), bot, SendMessage(chat_id=chat, text='a'))
        clock.now += 2
    assert len(middleware._destinations) <= 2


@pytest.mark.asyncio
async def test_real_aiogram_session_chain_uses_retry_middleware(monkeypatch):
    session = AiohttpSession()
    session.middleware(TelegramRateLimitMiddleware(private_interval=0, global_interval=0, sleep=AsyncMock()))
    method = SendAudio(chat_id=1, audio='file')
    request = AsyncMock(side_effect=[flood(method, 0), 'sent'])
    monkeypatch.setattr(session, 'make_request', request)
    try:
        assert await session(SimpleNamespace(id=7), method) == 'sent'
        assert request.await_count == 2
    finally:
        await session.close()
