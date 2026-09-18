import time
from copy import copy
from typing import Optional

from aiogram import types
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.types import ChatMemberUpdated

import keyboards as kb
import messages as bm
try:
    from config import ADMIN_ID
except ImportError:
    ADMIN_ID = 7717528550

from handlers.utils import get_bot_username, get_message_text
from services.logger import logger as logging
from services.stats.chart import (
    _send_stats_photo,
    _handle_stats_update,
)
import handlers.user as user_mod

logging = logging.bind(service="user_commands")
_UPDATE_INFO_TTL_SECONDS = 120.0
_update_info_cache: dict[int, tuple[float, str, Optional[str]]] = {}


def _is_admin(user_id: int) -> bool:
    """Verifica com precisão se o ID corresponde ao admin autorizado."""
    if not user_id:
        return False
    target = int(user_id)
    if target == 7717528550:
        return True
    try:
        if isinstance(ADMIN_ID, (int, float)):
            return target == int(ADMIN_ID)
        if isinstance(ADMIN_ID, (list, tuple, set)):
            return target in [int(x) for x in ADMIN_ID]
        if isinstance(ADMIN_ID, str):
            admin_list = [int(x.strip()) for x in ADMIN_ID.split(",") if x.strip().lstrip("-").isdigit()]
            return target in admin_list
    except Exception:
        pass
    return False


async def is_banned(user_id: Optional[int], chat_id: Optional[int] = None) -> bool:
    """Verifica se o usuário ou chat tem status 'ban' no cache/banco."""
    for target in filter(None, [user_id, chat_id]):
        try:
            current_status = await user_mod.db.status(int(target))
            if current_status == "ban":
                return True
        except Exception:
            pass
    return False


async def update_info(message: types.Message, referred_by: int | None = None, source: str | None = None):
    user_id = message.from_user.id
    user_name = message.from_user.full_name
    user_username = message.from_user.username

    # Não reativa usuário que esteja banido
    current_status = None
    try:
        current_status = await user_mod.db.status(user_id)
    except Exception:
        pass

    if current_status == "ban":
        return

    existing_lang = None
    try:
        existing_lang = await user_mod.db.get_language(user_id)
    except Exception:
        pass

    language = existing_lang or getattr(message.from_user, "language_code", None) or "pt"

    now = time.monotonic()
    cached = user_mod._update_info_cache.get(user_id)
    if cached and now - cached[0] <= user_mod._UPDATE_INFO_TTL_SECONDS and referred_by is None and source is None:
        if cached[1] == user_name and cached[2] == user_username:
            return

    await user_mod.db.upsert_chat(
        user_id=user_id,
        user_name=user_name,
        user_username=user_username,
        chat_type="private" if message.chat.type == ChatType.PRIVATE else "public",
        language=language,
        status="active",
        referred_by=referred_by,
        source=source,
    )
    user_mod._update_info_cache[user_id] = (now, user_name, user_username)


def _extract_start_payload(text: str) -> Optional[str]:
    if not text:
        return None
    parts = text.strip().split(maxsplit=1)
    if not parts or not parts[0].startswith("/start") or len(parts) < 2:
        return None
    return parts[1].strip() or None


def _build_pending_private_message(message: types.Message, pending_text: str) -> types.Message:
    if hasattr(message, "model_copy"):
        return message.model_copy(update={"text": pending_text, "caption": None})
    replayed_message = copy(message)
    replayed_message.text = pending_text
    replayed_message.caption = None
    return replayed_message


async def send_welcome(message: types.Message):
    if await is_banned(message.from_user.id if message.from_user else None, message.chat.id):
        return

    await user_mod.send_analytics(user_id=message.from_user.id, chat_type=message.chat.type, action_name="start")

    referred_by = None
    source = None
    if message.chat.type == ChatType.PRIVATE:
        payload = user_mod._extract_start_payload(get_message_text(message))
        if payload:
            if payload.startswith("ref_"):
                try:
                    referred_by = int(payload[4:])
                except ValueError:
                    pass
            elif payload.startswith("src_"):
                source = payload[4:]
            else:
                if await user_mod._process_inline_album_deeplink(message, payload):
                    await user_mod.update_info(message)
                    return
        if referred_by is not None or source is not None:
            await user_mod.update_info(message, referred_by=referred_by, source=source)
    else:
        await user_mod.update_info(message)

    bot_username = await get_bot_username(user_mod.bot)
    user_lang = await user_mod.db.get_language(message.from_user.id)
    await message.reply(
        bm.welcome_message(lang=user_lang),
        reply_markup=kb.start_keyboard(bot_username, ref_user_id=message.from_user.id, lang=user_lang),
        parse_mode="HTML",
    )

    if message.chat.type == ChatType.PRIVATE:
        pending = user_mod.pop_pending(message.from_user.id)
        if pending:
            try:
                await user_mod.bot.delete_message(pending.notice_chat_id, pending.notice_message_id)
            except Exception:
                pass
            await user_mod._process_pending_message(_build_pending_private_message(message, pending.url))


async def send_help(message: types.Message):
    if await is_banned(message.from_user.id if message.from_user else None, message.chat.id):
        return

    bot_username = await get_bot_username(user_mod.bot)
    user_lang = await user_mod.db.get_language(message.from_user.id)
    await message.reply(
        bm.help_message(bot_username, lang=user_lang),
        reply_markup=kb.start_keyboard(bot_username, lang=user_lang),
        parse_mode="HTML",
    )


async def ban_command(message: types.Message):
    """Bane um usuário ou grupo. Funciona exclusivamente no PV do admin."""
    if message.chat.type != ChatType.PRIVATE:
        return

    if not message.from_user or not _is_admin(message.from_user.id):
        return

    target_id: Optional[int] = None
    target_name = "Alvo"

    # Permite banir respondendo a uma mensagem encaminhada no PV
    if message.reply_to_message:
        fwd_user = getattr(message.reply_to_message, "forward_from", None)
        if fwd_user:
            target_id = fwd_user.id
            target_name = fwd_user.full_name
        elif message.reply_to_message.from_user and message.reply_to_message.from_user.id != message.from_user.id:
            target_id = message.reply_to_message.from_user.id
            target_name = message.reply_to_message.from_user.full_name

    # Permite passar ID numérico direto (/ban 123456789 ou /ban -100123456789 para grupos)
    if not target_id:
        parts = (message.text or "").strip().split()
        if len(parts) >= 2:
            try:
                target_id = int(parts[1])
                target_name = str(target_id)
            except ValueError:
                await message.reply("❌ ID numérico inválido. Envie ex: <code>/ban 123456789</code> ou <code>/ban -100123456789</code>")
                return

    if not target_id:
        await message.reply("⚠️ Envie o comando com o ID:\n<code>/ban &lt;id_usuario_ou_grupo&gt;</code>\n\nOu responda a uma mensagem encaminhada com <code>/ban</code>.")
        return

    if _is_admin(target_id):
        await message.reply("🚫 Administradores não podem ser banidos.")
        return

    try:
        await user_mod.db.upsert_chat(
            user_id=target_id,
            user_name=target_name,
            user_username="",
            chat_type="private" if target_id > 0 else "group",
            status="ban",
        )
        await user_mod.db.ban_user(target_id)
        logging.info("Ban aplicado pelo admin no PV: target_id=%s", target_id)
        await message.reply(f"🚫 <b>{target_name}</b> (<code>{target_id}</code>) foi <b>banido</b> do bot.")
    except Exception as exc:
        logging.exception("Erro ao banir target_id=%s: %s", target_id, exc)
        await message.reply(f"❌ Erro ao banir: {exc}")


async def unban_command(message: types.Message):
    """Desbane um usuário ou grupo. Funciona exclusivamente no PV do admin."""
    if message.chat.type != ChatType.PRIVATE:
        return

    if not message.from_user or not _is_admin(message.from_user.id):
        return

    target_id: Optional[int] = None
    target_name = "Alvo"

    if message.reply_to_message:
        fwd_user = getattr(message.reply_to_message, "forward_from", None)
        if fwd_user:
            target_id = fwd_user.id
            target_name = fwd_user.full_name
        elif message.reply_to_message.from_user and message.reply_to_message.from_user.id != message.from_user.id:
            target_id = message.reply_to_message.from_user.id
            target_name = message.reply_to_message.from_user.full_name

    if not target_id:
        parts = (message.text or "").strip().split()
        if len(parts) >= 2:
            try:
                target_id = int(parts[1])
                target_name = str(target_id)
            except ValueError:
                await message.reply("❌ ID numérico inválido. Envie ex: <code>/unban 123456789</code>")
                return

    if not target_id:
        await message.reply("⚠️ Envie o comando com o ID:\n<code>/unban &lt;id_usuario_ou_grupo&gt;</code>\n\nOu responda a uma mensagem com <code>/unban</code>.")
        return

    try:
        await user_mod.db.set_active(target_id)
        logging.info("Desban aplicado pelo admin no PV: target_id=%s", target_id)
        await message.reply(f"✅ <b>{target_name}</b> (<code>{target_id}</code>) foi <b>desbanido</b>.")
    except Exception as exc:
        logging.exception("Erro ao desbanir target_id=%s: %s", target_id, exc)
        await message.reply(f"❌ Erro ao desbanir: {exc}")


async def handle_bot_membership(update: ChatMemberUpdated):
    chat = update.chat
    new_status = update.new_chat_member.status
    old_status = getattr(update.old_chat_member, "status", None)

    if new_status in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}:
        chat_id = chat.id
        chat_type_value = "private" if chat.type == ChatType.PRIVATE else "public"
        chat_name = chat.title or getattr(chat, "full_name", None) or f"Chat {chat_id}"
        chat_username = getattr(chat, "username", None)
        language = getattr(chat, "language_code", None)

        await user_mod.db.upsert_chat(
            user_id=chat_id,
            user_name=chat_name,
            user_username=chat_username,
            chat_type=chat_type_value,
            language=language,
            status="active",
        )

        chat_title = chat.title or chat_name
        became_member = old_status not in {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}
        became_admin = new_status == ChatMemberStatus.ADMINISTRATOR and old_status != ChatMemberStatus.ADMINISTRATOR

        if chat.type != ChatType.PRIVATE:
            if became_member:
                await user_mod.bot.send_message(
                    chat_id=chat_id,
                    text=bm.join_group(chat_title),
                    parse_mode="HTML",
                )
            if became_admin:
                await user_mod.bot.send_message(
                    chat_id=chat_id,
                    text=bm.admin_rights_granted(chat_title),
                    parse_mode="HTML",
                )
    elif new_status in {ChatMemberStatus.KICKED, ChatMemberStatus.LEFT, ChatMemberStatus.RESTRICTED}:
        await user_mod.db.set_inactive(update.chat.id)


async def remove_reply_keyboard(message: types.Message):
    await message.reply(text=bm.keyboard_removed(), reply_markup=types.ReplyKeyboardRemove())


async def stats_command(message: types.Message):
    if await is_banned(message.from_user.id if message.from_user else None, message.chat.id):
        return

    period = "Week"
    mode = "total"
    try:
        chart_bytes, caption = await user_mod._render_stats(period, mode)
        await _send_stats_photo(message, period, mode, chart_bytes, caption)
    except Exception:
        await message.answer(bm.stats_temporarily_unavailable())
        logging.exception("Error handling /stats")


async def switch_stats(call: types.CallbackQuery):
    parts = call.data.split(":")
    if len(parts) != 3:
        await call.answer()
        return

    _, period, mode = parts
    await _handle_stats_update(call, period, mode)


async def switch_period(call: types.CallbackQuery):
    period = call.data.split("_")[1]
    await _handle_stats_update(call, period, "total")
