"""Pace outgoing messages and retry explicit Telegram flood-control responses."""

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramRetryAfter

logger = logging.getLogger(__name__)


@dataclass
class _Destination:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0
    next_send: float = 0.0
    cooldown: float = 0.0
    last_error: TelegramRetryAfter | None = None


class TelegramRateLimitMiddleware(BaseRequestMiddleware):
    """Keep 429 waits asynchronous and local to the affected destination.

    Only explicit 429 responses are retried: a network timeout could follow a
    successful upload, so automatically replaying it might duplicate the media.
    """

    def __init__(
        self, *, retries=3, max_wait=300.0, private_interval=1.05,
        group_interval=3.1, global_interval=0.04, cache_size=4096,
        clock=time.monotonic, sleep=asyncio.sleep,
    ):
        self.retries = retries
        self.max_wait = max_wait
        self.private_interval = private_interval
        self.group_interval = group_interval
        self.global_interval = global_interval
        self.cache_size = cache_size
        self.clock = clock
        self.sleep = sleep
        self._destinations = OrderedDict()
        self._global_lock = asyncio.Lock()
        self._global_next = 0.0

    async def _global_slot(self, weight):
        # Reserve a short pacing slot; never hold this lock over network IO or
        # a Telegram-requested cooldown.
        async with self._global_lock:
            now = self.clock()
            scheduled = max(now, self._global_next)
            self._global_next = scheduled + self.global_interval * weight
        if scheduled > now:
            await self.sleep(scheduled - now)

    def _trim(self):
        if len(self._destinations) <= self.cache_size:
            return
        now = self.clock()
        for key, state in list(self._destinations.items()):
            # Never evict a lock with owners/waiters or forget an active cooldown.
            if not state.users and max(state.next_send, state.cooldown) <= now:
                del self._destinations[key]
            if len(self._destinations) <= self.cache_size:
                break

    async def _request(self, make_request, bot, method, state=None, interval=0.0, weight=1):
        waited = 0.0
        for attempt in range(self.retries + 1):
            if state is not None:
                delay = max(0.0, state.cooldown - self.clock())
                if delay:
                    if waited + delay > self.max_wait:
                        raise state.last_error
                    await self.sleep(delay)
                    waited += delay
                delay = max(0.0, state.next_send - self.clock())
                if delay:
                    await self.sleep(delay)
                await self._global_slot(weight)
                state.next_send = self.clock() + interval * weight
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as exc:
                delay = max(0.0, float(exc.retry_after)) + 0.25
                if state is not None:
                    state.cooldown = max(state.cooldown, self.clock() + delay)
                    state.last_error = exc
                logger.warning(
                    "Telegram flood control: method=%s retry_after=%s attempt=%s",
                    method.__api_method__, exc.retry_after, attempt + 1,
                )
                if attempt >= self.retries or waited + delay > self.max_wait:
                    raise
                if state is None:
                    await self.sleep(delay)
                    waited += delay
                # The next iteration waits for the shared chat cooldown.

    async def __call__(self, make_request, bot, method):
        name = method.__api_method__
        outgoing = name.startswith(("send", "editMessage", "editEphemeralMessage")) or name in {
            "copyMessage", "copyMessages", "forwardMessage", "forwardMessages",
            "deleteMessage", "deleteMessages", "setMessageReaction",
        }
        chat_id = getattr(method, "chat_id", None)
        if isinstance(chat_id, str) and chat_id.lstrip("-").isdigit():
            chat_id = int(chat_id)
        inline_id = getattr(method, "inline_message_id", None)
        destination = ("chat", chat_id) if chat_id is not None else ("inline", inline_id)
        if not outgoing or (chat_id is None and inline_id is None):
            return await self._request(make_request, bot, method)
        key = (bot.id, *destination)
        state = self._destinations.setdefault(key, _Destination())
        self._destinations.move_to_end(key)
        state.users += 1
        self._trim()
        group = (isinstance(chat_id, int) and chat_id < 0) or isinstance(chat_id, str)
        interval = self.group_interval if group else self.private_interval
        weight = 1
        if name == "sendMediaGroup":
            weight = max(1, len(method.media))
        elif name in {"copyMessages", "forwardMessages"}:
            weight = max(1, len(method.message_ids))
        try:
            async with state.lock:
                return await self._request(
                    make_request, bot, method, state, interval, weight
                )
        finally:
            state.users -= 1
            self._trim()
