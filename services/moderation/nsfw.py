"""Opt-in Telegram group moderation, isolated from media download workers."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import io
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import urlsplit

from aiogram import BaseMiddleware, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.filters import Command
from aiogram.types import Message
import httpx
from sqlalchemy import text

from app_context import db
from services.logger import logger as logging
from .policy import find_violation

router = Router(name="nsfw")
API_URL = os.getenv("NSFW_API_URL", "").strip()
API_KEY = os.getenv("NSFW_API_KEY", "").strip()
_url = urlsplit(API_URL)
READY = bool(API_KEY and API_URL and (
    _url.scheme == "https" or
    (_url.scheme == "http" and _url.hostname in {"127.0.0.1", "localhost", "::1"})
))
PHOTO_LIMIT = 8 * 1024 * 1024
VIDEO_LIMIT = 20 * 1024 * 1024
BAN_THRESHOLD = 3
TTL_SECONDS = 20
_settings_cache = {}
_queue = None
_workers = []
_client = None
_bot = None


@dataclass(frozen=True)
class ScanJob:
    chat_id: int
    message_id: int
    user_id: int
    kind: str
    file_id: str
    size: int
    duration: int


async def get_settings(chat_id):
    cached = _settings_cache.get(chat_id)
    now = time.monotonic()
    if cached and now - cached[0] < TTL_SECONDS:
        return dict(cached[1])
    result = {"enabled": False, "photo": True, "video": True, "gif": True,
              "sensitivity": "balanced", "action": "ban"}
    async with db.SessionLocal() as session:
        row = (await session.execute(
            text("SELECT enabled, photo, video, gif, sensitivity, action "
                 "FROM nsfw_group_settings WHERE chat_id=:id"), {"id": chat_id}
        )).mappings().first()
    if row:
        result.update(row)
    _settings_cache[chat_id] = (now, dict(result))
    if len(_settings_cache) > 10000:
        _settings_cache.clear()
    return result


async def set_setting(chat_id, field, value):
    if field not in {"enabled", "photo", "video", "gif", "sensitivity", "action"}:
        raise ValueError("Unknown NSFW setting")
    sql = ("INSERT INTO nsfw_group_settings (chat_id, " + field + ") "
           "VALUES (:chat_id, :value) ON CONFLICT (chat_id) "
           "DO UPDATE SET " + field + " = EXCLUDED." + field)
    async with db.SessionLocal() as session:
        async with session.begin():
            await session.execute(text(sql), {"chat_id": chat_id, "value": value})
    _settings_cache.pop(chat_id, None)


async def _strike(chat_id, user_id):
    # Atomic UPSERT prevents a second worker from losing an infraction.
    async with db.SessionLocal() as session:
        async with session.begin():
            r = await session.execute(text(
                "INSERT INTO nsfw_strikes (chat_id,user_id,strikes,updated_at) "
                "VALUES (:chat,:user,1,NOW()) "
                "ON CONFLICT (chat_id,user_id) DO UPDATE SET "
                "strikes=CASE WHEN nsfw_strikes.updated_at < NOW() - INTERVAL '30 days' "
                "THEN 1 ELSE nsfw_strikes.strikes + 1 END, updated_at=NOW() "
                "RETURNING strikes"
            ), {"chat": chat_id, "user": user_id})
            return int(r.scalar_one())


def _get_media(message):
    if message.photo:
        return "photo", message.photo[-1]
    if message.animation:
        return "gif", message.animation
    if message.video:
        return "video", message.video
    document = message.document
    if document:
        mime = (document.mime_type or "").lower()
        if mime in {"image/jpeg", "image/png", "image/webp"}:
            return "photo", document
        if mime == "image/gif":
            return "gif", document
        if mime.startswith("video/"):
            return "video", document
    return None


class NSFWGroupMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if not READY or not isinstance(event, Message):
            return await handler(event, data)
        if event.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
            return await handler(event, data)
        if not event.from_user or event.from_user.is_bot:
            return await handler(event, data)
        media = _get_media(event)
        if media is None:
            return await handler(event, data)
        try:
            prefs = await get_settings(event.chat.id)
            if prefs["enabled"] and prefs.get(media[0], False):
                kind, item = media
                job = ScanJob(
                    event.chat.id, event.message_id, event.from_user.id,
                    kind, item.file_id, int(item.file_size or 0),
                    int(getattr(item, "duration", 0) or 0)
                )
                if _queue is not None:
                    try:
                        _queue.put_nowait(job)
                        return None
                    except asyncio.QueueFull:
                        logging.warning("NSFW queue full in chat %s", event.chat.id)
        except Exception as exc:
            logging.warning("NSFW group precheck failed: %s", type(exc).__name__)
        return await handler(event, data)


async def _detect(image_bytes):
    response = await _client.post(
        API_URL, content=image_bytes,
        headers={"Authorization": "Bearer " + API_KEY,
                 "Content-Type": "application/octet-stream"}
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("detections"), list):
        raise ValueError("Invalid NSFW detector response")
    return data["detections"]


async def _extract_frame(source, destination, position):
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-ss", str(position), "-i", str(source), "-an",
        "-frames:v", "1", "-vf", "scale=640:-2", "-y", str(destination),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        await asyncio.wait_for(process.communicate(), timeout=10)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        return False
    return process.returncode == 0 and destination.exists() and destination.stat().st_size > 0


async def _scan(job, sensitivity):
    upper = PHOTO_LIMIT if job.kind == "photo" else VIDEO_LIMIT
    if job.size <= 0 or job.size > upper:
        # Reject unknown sizes rather than download an unbounded file.
        return None
    if job.kind == "photo":
        buf = io.BytesIO()
        await asyncio.wait_for(_bot.download(job.file_id, destination=buf), timeout=30)
        raw = buf.getvalue()
        if len(raw) > PHOTO_LIMIT:
            return None
        return find_violation(await _detect(raw), sensitivity)
    with tempfile.TemporaryDirectory(prefix="savevid-nsfw-") as folder:
        src = Path(folder) / "media"
        await asyncio.wait_for(_bot.download(job.file_id, destination=src), timeout=35)
        if not src.exists() or src.stat().st_size > VIDEO_LIMIT:
            return None
        duration = max(0, job.duration)
        timestamps = [0, duration * .5, duration * .9] if duration else [0, 3, 10]
        best = None
        for index, seconds in enumerate(timestamps):
            out = Path(folder) / ("frame-" + str(index) + ".jpg")
            if not await _extract_frame(src, out, seconds):
                continue
            result = find_violation(await _detect(out.read_bytes()), sensitivity)
            if result and (best is None or result[1] > best[1]):
                best = result
        return best


async def _moderate(job):
    try:
        prefs = await get_settings(job.chat_id)
        if not prefs["enabled"] or not prefs.get(job.kind, False):
            return
        member = await _bot.get_chat_member(job.chat_id, job.user_id)
        # Never automatically punish a group owner or administrator.
        if member.status in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}:
            return
        violation = await _scan(job, prefs["sensitivity"])
        if violation is None:
            return
        await _bot.delete_message(job.chat_id, job.message_id)
        strikes = None
        if prefs["action"] == "ban" and violation[1] >= .90:
            strikes = await _strike(job.chat_id, job.user_id)
            if strikes >= BAN_THRESHOLD:
                await _bot.ban_chat_member(job.chat_id, job.user_id)
                logging.info("NSFW repeat user removed from chat %s", job.chat_id)
                return
        logging.info("NSFW media removed chat=%s class=%s score=%.2f strike=%s",
                     job.chat_id, violation[0], violation[1], strikes)
    except Exception as exc:
        # Failing detector, Telegram API or DB cannot stop downloading features.
        logging.warning("NSFW scan failed chat=%s error=%s", job.chat_id, type(exc).__name__)


async def _worker():
    while True:
        job = await _queue.get()
        try:
            await _moderate(job)
        finally:
            _queue.task_done()


async def start_nsfw(bot):
    global _queue, _workers, _bot, _client
    if not READY or _queue is not None:
        return
    _bot = bot
    _client = httpx.AsyncClient(timeout=httpx.Timeout(12, connect=5), trust_env=False)
    _queue = asyncio.Queue(maxsize=80)
    _workers = [asyncio.create_task(_worker(), name="nsfw-" + str(n)) for n in range(2)]


async def stop_nsfw():
    global _queue, _workers, _bot, _client
    for task in _workers:
        task.cancel()
    if _workers:
        await asyncio.gather(*_workers, return_exceptions=True)
    if _client:
        await _client.aclose()
    _workers, _queue, _bot, _client = [], None, None, None


@router.message(Command("nsfw"))
async def nsfw_settings(message: Message):
    if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await message.answer("Configure a proteção com /nsfw em um grupo.")
        return
    if not message.from_user:
        return
    requester = await message.bot.get_chat_member(message.chat.id, message.from_user.id)
    if requester.status not in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}:
        await message.reply("Somente administradores podem alterar a proteção.")
        return
    parts = (message.text or "").split()
    cmd = parts[1].lower() if len(parts) > 1 else "status"
    if cmd in {"on", "off"}:
        is_on = cmd == "on"
        if is_on:
            if not READY:
                await message.reply("Detector indisponível: configure NSFW_API_URL e NSFW_API_KEY no servidor.")
                return
            member = await message.bot.get_chat_member(message.chat.id, message.bot.id)
            if not getattr(member, "can_delete_messages", False):
                await message.reply("Preciso da permissão de apagar mensagens.")
                return
            if not getattr(member, "can_restrict_members", False):
                await message.reply("Preciso da permissão de banir membros.")
                return
        await set_setting(message.chat.id, "enabled", is_on)
    elif cmd in {"fotos", "videos", "gifs"} and len(parts) >= 3:
        field = {"fotos": "photo", "videos": "video", "gifs": "gif"}[cmd]
        if parts[2].lower() not in {"on", "off"}:
            await message.reply("Use on ou off.")
            return
        await set_setting(message.chat.id, field, parts[2].lower() == "on")
    elif cmd == "sensibilidade" and len(parts) >= 3:
        value = parts[2].lower()
        if value not in {"strict", "balanced", "relaxed"}:
            await message.reply("Use strict, balanced ou relaxed.")
            return
        await set_setting(message.chat.id, "sensitivity", value)
    elif cmd != "status":
        await message.reply("Use /nsfw on, /nsfw off, /nsfw status, /nsfw fotos on, "
                            "/nsfw videos on, /nsfw gifs on, /nsfw sensibilidade balanced.")
        return
    s = await get_settings(message.chat.id)
    await message.reply(
        "<b>Proteção NSFW</b>\n"
        "Status: <b>" + ("ativada" if s["enabled"] else "desativada") + "</b>\n"
        "Fotos: " + ("on" if s["photo"] else "off") + "\n"
        "Vídeos: " + ("on" if s["video"] else "off") + "\n"
        "GIFs: " + ("on" if s["gif"] else "off") + "\n"
        "Sensibilidade: " + s["sensitivity"] + "\n"
        "Ação: banir após 3 infrações confiáveis em até 30 dias",
        parse_mode="HTML"
    )
