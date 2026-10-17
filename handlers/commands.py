import asyncio
import re
import shutil
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
            if pending.notice_message_id > 0:
                try:
                    await user_mod.bot.delete_message(
                        pending.notice_chat_id,
                        pending.notice_message_id,
                    )
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

    user_lang = await user_mod.db.get_language(message.from_user.id)
    is_en = str(user_lang).lower().startswith("en")

    target_id: Optional[int] = None
    target_name = "Target" if is_en else "Alvo"

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
                await message.reply(
                        "❌ Invalid numeric ID. Example: <code>/ban 123456789</code> "
                        "or <code>/ban -100123456789</code>"
                        if is_en
                        else
                        "❌ ID numérico inválido. Exemplo: <code>/ban 123456789</code> "
                        "ou <code>/ban -100123456789</code>"
                    )
                return

    if not target_id:
        await message.reply(
            "⚠️ Send the command with the ID:\n"
            "<code>/ban &lt;user_or_group_id&gt;</code>\n\n"
            "Or reply to a forwarded message with <code>/ban</code>."
            if is_en
            else
            "⚠️ Envie o comando com o ID:\n"
            "<code>/ban &lt;id_usuario_ou_grupo&gt;</code>\n\n"
            "Ou responda a uma mensagem encaminhada com <code>/ban</code>."
        )
        return

    if _is_admin(target_id):
        await message.reply(
            "🚫 Administrators cannot be banned."
            if is_en
            else "🚫 Administradores não podem ser banidos."
        )
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
        await message.reply(
            f"🚫 <b>{target_name}</b> (<code>{target_id}</code>) "
            f"was <b>banned</b> from the bot."
            if is_en
            else
            f"🚫 <b>{target_name}</b> (<code>{target_id}</code>) "
            f"foi <b>banido</b> do bot."
        )
    except Exception as exc:
        logging.exception("Erro ao banir target_id=%s: %s", target_id, exc)
        await message.reply(
            f"❌ Error banning: {exc}"
            if is_en
            else f"❌ Erro ao banir: {exc}"
        )


async def unban_command(message: types.Message):
    """Desbane um usuário ou grupo. Funciona exclusivamente no PV do admin."""
    if message.chat.type != ChatType.PRIVATE:
        return

    if not message.from_user or not _is_admin(message.from_user.id):
        return

    user_lang = await user_mod.db.get_language(message.from_user.id)
    is_en = str(user_lang).lower().startswith("en")

    target_id: Optional[int] = None
    target_name = "Target" if is_en else "Alvo"

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
                await message.reply(
                        "❌ Invalid numeric ID. Example: <code>/unban 123456789</code>"
                        if is_en
                        else
                        "❌ ID numérico inválido. Exemplo: <code>/unban 123456789</code>"
                    )
                return

    if not target_id:
        await message.reply(
            "⚠️ Send the command with the ID:\n"
            "<code>/unban &lt;user_or_group_id&gt;</code>\n\n"
            "Or reply to a message with <code>/unban</code>."
            if is_en
            else
            "⚠️ Envie o comando com o ID:\n"
            "<code>/unban &lt;id_usuario_ou_grupo&gt;</code>\n\n"
            "Ou responda a uma mensagem com <code>/unban</code>."
        )
        return

    try:
        await user_mod.db.set_active(target_id)
        logging.info("Desban aplicado pelo admin no PV: target_id=%s", target_id)
        await message.reply(
            f"✅ <b>{target_name}</b> (<code>{target_id}</code>) was <b>unbanned</b>."
            if is_en
            else
            f"✅ <b>{target_name}</b> (<code>{target_id}</code>) foi <b>desbanido</b>."
        )
    except Exception as exc:
        logging.exception("Erro ao desbanir target_id=%s: %s", target_id, exc)
        await message.reply(
            f"❌ Error unbanning: {exc}"
            if is_en
            else f"❌ Erro ao desbanir: {exc}"
        )


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
                    text=bm.join_group(chat_title, lang=language),
                    parse_mode="HTML",
                )
            if became_admin:
                await user_mod.bot.send_message(
                    chat_id=chat_id,
                    text=bm.admin_rights_granted(chat_title, lang=language),
                    parse_mode="HTML",
                )
    elif new_status in {ChatMemberStatus.KICKED, ChatMemberStatus.LEFT, ChatMemberStatus.RESTRICTED}:
        await user_mod.db.set_inactive(update.chat.id)


async def remove_reply_keyboard(message: types.Message):
    user_lang = await user_mod.db.get_language(message.from_user.id)
    await message.reply(
        text=bm.keyboard_removed(lang=user_lang),
        reply_markup=types.ReplyKeyboardRemove(),
    )


async def stats_command(message: types.Message):
    if await is_banned(message.from_user.id if message.from_user else None, message.chat.id):
        return

    period = "Week"
    mode = "total"
    try:
        user_lang = await user_mod.db.get_language(message.from_user.id)
        chart_bytes, caption = await user_mod._render_stats(
            period,
            mode,
            user_lang,
        )
        await _send_stats_photo(
            message,
            period,
            mode,
            chart_bytes,
            caption,
            user_lang,
        )
    except Exception:
        user_lang = await user_mod.db.get_language(message.from_user.id)
        await message.answer(
            bm.stats_temporarily_unavailable(lang=user_lang)
        )
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



# ============================================================
# /trad
# ============================================================

_TRAD_MAX_INPUT = 3500
_TRAD_TIMEOUT_SECONDS = 25

_TRAD_LANGUAGE_RE = re.compile(
    r"^[A-Za-z]{2,3}(?:[-_][A-Za-z]{2,4})?$"
)


def _normalize_translation_language(value: str) -> Optional[str]:
    value = (value or "").strip()

    if not _TRAD_LANGUAGE_RE.fullmatch(value):
        return None

    parts = value.replace("_", "-").split("-")

    if len(parts) == 1:
        return parts[0].lower()

    return f"{parts[0].lower()}-{parts[1].upper()}"


async def _identify_translation_language(
    trans_bin: str,
    text: str,
) -> str:
    proc = None

    try:
        proc = await asyncio.create_subprocess_exec(
            trans_bin,
            "-4",
            "-no-bidi",
            "-no-ansi",
            "-e",
            "google",
            "-brief",
            "-identify",
            text,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=10,
        )

        stderr_text = stderr.decode(
            "utf-8",
            errors="ignore",
        ).strip()

        if stderr_text:
            logging.debug(
                "Translate identify stderr: %s",
                stderr_text[:300],
            )

        identified = stdout.decode(
            "utf-8",
            errors="ignore",
        ).strip()

        for line in reversed(identified.splitlines()):
            candidate = line.strip()

            if re.fullmatch(
                r"[A-Za-z]{2,3}(?:[-_][A-Za-z]{2,4})?",
                candidate,
            ):
                return candidate.replace("_", "-")

    except asyncio.TimeoutError:
        if proc is not None:
            proc.kill()
            await proc.communicate()

        logging.debug(
            "Language identification timed out"
        )

    except Exception as exc:
        logging.debug(
            "Language identification failed: %s",
            exc,
        )

    return "AUTO"




def _clean_translation_input(text: str) -> str:
    """Remove elementos gerados pelo próprio bot antes da tradução."""
    lines = (text or "").splitlines()
    cleaned = []

    for line in lines:
        stripped = line.strip()

        # Não manda o nosso próprio rodapé novamente ao tradutor.
        if (
            "Powered by Save Vídeo DL Bot" in stripped
            or "Powered by Save Video DL Bot" in stripped
        ):
            continue

        cleaned.append(line)

    return "\n".join(cleaned).strip()


def _translation_chunks(text: str, max_chars: int = 700) -> list[str]:
    """Divide textos maiores preservando parágrafos sempre que possível."""
    text = (text or "").strip()

    if not text:
        return []

    paragraphs = text.split("\n")
    chunks = []
    current = ""

    for paragraph in paragraphs:
        paragraph = paragraph.strip()

        if not paragraph:
            if current:
                current += "\n"
            continue

        candidate = (
            f"{current.rstrip()}\n{paragraph}".strip()
            if current.strip()
            else paragraph
        )

        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current.strip():
            chunks.append(current.strip())
            current = ""

        # Caso um único parágrafo seja muito grande.
        while len(paragraph) > max_chars:
            cut = paragraph.rfind(" ", 0, max_chars)

            if cut < max_chars // 2:
                cut = max_chars

            chunks.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()

        current = paragraph

    if current.strip():
        chunks.append(current.strip())

    return chunks


def _should_preserve_translation_line(text: str) -> bool:
    """Preserva créditos, URLs e menções isoladas."""
    value = (text or "").strip()

    if not value:
        return True

    if value.startswith(("http://", "https://")):
        return True

    # Créditos típicos:
    # 🗞 @usuario
    # 📸 São Paulo FC
    if value.startswith(("🗞", "📸")):
        return True

    # @username isolado
    if value.startswith("@") and " " not in value:
        return True

    return False


async def _translate_text_chunk(
    trans_bin: str,
    text: str,
    source_language: str,
    target_language: str,
) -> str:
    source = (
        source_language.lower()
        if source_language and source_language.upper() != "AUTO"
        else ""
    )

    language_pair = (
        f"{source}:{target_language}"
        if source
        else f":{target_language}"
    )

    proc = await asyncio.create_subprocess_exec(
        trans_bin,
        "-4",
        "-no-bidi",
        "-no-ansi",
        "-e",
        "google",
        "-brief",
        language_pair,
        text,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await asyncio.wait_for(
        proc.communicate(),
        timeout=_TRAD_TIMEOUT_SECONDS,
    )

    stderr_text = stderr.decode(
        "utf-8",
        errors="ignore",
    ).strip()

    if stderr_text:
        logging.debug(
            "Translate chunk stderr: %s",
            stderr_text[:500],
        )

    translated = stdout.decode(
        "utf-8",
        errors="ignore",
    ).strip()

    if proc.returncode != 0 or not translated:
        raise RuntimeError(
            f"Translate Shell failed with returncode={proc.returncode}"
        )

    return translated


async def _translate_long_text(
    trans_bin: str,
    text: str,
    source_language: str,
    target_language: str,
) -> str:
    """
    Traduz por blocos e preserva a estrutura da mensagem original.

    Exemplo:
    título

    parágrafo

    parágrafo

    créditos
    """
    text = (text or "").strip()

    if not text:
        return ""

    # Cada bloco é separado por uma ou mais linhas vazias.
    blocks = re.split(r"\n\s*\n", text)

    translated_blocks = []

    for block in blocks:
        block = block.strip()

        if not block:
            continue

        lines = block.splitlines()

        # Bloco formado somente por créditos/menções/links:
        # preserva exatamente como veio.
        if all(
            _should_preserve_translation_line(line)
            for line in lines
            if line.strip()
        ):
            translated_blocks.append(
                "\n".join(line.strip() for line in lines if line.strip())
            )
            continue

        # Caso haja créditos misturados no mesmo bloco,
        # traduz o texto normal e preserva as linhas de crédito.
        normal_lines = []
        result_lines = []

        async def flush_normal_lines():
            if not normal_lines:
                return

            normal_text = "\n".join(normal_lines).strip()

            translated = await _translate_text_chunk(
                trans_bin,
                normal_text,
                source_language,
                target_language,
            )

            result_lines.append(translated.strip())
            normal_lines.clear()

        for line in lines:
            stripped = line.strip()

            if not stripped:
                continue

            if _should_preserve_translation_line(stripped):
                await flush_normal_lines()
                result_lines.append(stripped)
            else:
                normal_lines.append(stripped)

        await flush_normal_lines()

        if result_lines:
            translated_blocks.append("\n".join(result_lines))

    # Recria exatamente a separação visual entre os blocos.
    return "\n\n".join(translated_blocks)


async def translate_command(message: types.Message):
    if await is_banned(
        message.from_user.id if message.from_user else None,
        message.chat.id,
    ):
        return

    # Idioma da interface do bot.
    target_id = (
        message.from_user.id
        if message.chat.type == ChatType.PRIVATE
        else message.chat.id
    )

    try:
        user_lang = await user_mod.db.get_language(target_id)
    except Exception:
        user_lang = None

    user_lang = (
        user_lang
        or getattr(message.from_user, "language_code", None)
        or "pt"
    )

    raw_text = (
        message.text
        or message.caption
        or ""
    ).strip()

    parts = raw_text.split(maxsplit=2)

    if len(parts) < 2:
        await message.reply(
            bm.translation_usage(lang=user_lang)
        )
        return

    target_language = _normalize_translation_language(
        parts[1]
    )

    if not target_language:
        await message.reply(
            bm.translation_invalid_language(
                lang=user_lang
            )
        )
        return

    text_to_translate = ""
    translated_from_reply = False

    # /trad pt-BR Hello
    if len(parts) >= 3:
        text_to_translate = parts[2].strip()

    # Respondendo uma mensagem:
    # /trad pt-BR
    if (
        not text_to_translate
        and message.reply_to_message
    ):
        text_to_translate = (
            message.reply_to_message.text
            or message.reply_to_message.caption
            or ""
        ).strip()

        if text_to_translate:
            translated_from_reply = True

    text_to_translate = _clean_translation_input(
        text_to_translate
    )

    if not text_to_translate:
        await message.reply(
            bm.translation_usage(lang=user_lang)
        )
        return

    if len(text_to_translate) > _TRAD_MAX_INPUT:
        await message.reply(
            bm.translation_text_too_long(
                _TRAD_MAX_INPUT,
                lang=user_lang,
            )
        )
        return

    trans_bin = shutil.which("trans")

    if not trans_bin:
        logging.error(
            "Translate Shell executable 'trans' not found"
        )

        await message.reply(
            bm.translation_service_unavailable(
                lang=user_lang
            )
        )
        return

    try:
        source_language = (
            await _identify_translation_language(
                trans_bin,
                text_to_translate,
            )
        )

        translated = await _translate_long_text(
            trans_bin,
            text_to_translate,
            source_language,
            target_language,
        )

        stderr_text = ""

        if not translated:
            logging.warning(
                "Translation returned no text: "
                "target=%s returncode=%s stderr=%s",
                target_language,
                proc.returncode,
                stderr_text[:300],
            )

            await message.reply(
                bm.translation_failed(
                    lang=user_lang
                )
            )
            return

        try:
            bot_username = await get_bot_username(
                user_mod.bot
            )
        except Exception:
            bot_username = None

        if message.from_user:
            try:
                await user_mod.send_analytics(
                    user_id=message.from_user.id,
                    chat_type=message.chat.type,
                    action_name="translate",
                )
            except Exception:
                pass

        # Mantemos margem para o HTML,
        # cabeçalho e rodapé do Telegram.
        chunks = [
            translated[i:i + 3000]
            for i in range(
                0,
                len(translated),
                3000,
            )
        ]

        for index, chunk in enumerate(chunks):
            result_text = bm.translation_result(
                chunk,
                source_language,
                target_language,
                bot_username,
            )

            if index == 0:
                # Se /trad foi usado como reply,
                # responde diretamente à mensagem original.
                if (
                    translated_from_reply
                    and message.reply_to_message
                ):
                    await message.reply_to_message.reply(
                        result_text,
                        parse_mode="HTML",
                        link_preview_options=types.LinkPreviewOptions(
                            is_disabled=True
                        ),
                    )
                else:
                    await message.reply(
                        result_text,
                        parse_mode="HTML",
                        link_preview_options=types.LinkPreviewOptions(
                            is_disabled=True
                        ),
                    )

            else:
                await message.answer(
                    result_text,
                    parse_mode="HTML",
                    link_preview_options=types.LinkPreviewOptions(
                        is_disabled=True
                    ),
                )

    except Exception as exc:
        logging.exception(
            "Error handling /trad: %s",
            exc,
        )

        await message.reply(
            bm.translation_error(
                lang=user_lang
            )
        )




# ============================================================
# /ping
# ============================================================

async def _delete_ping_later(
    command_message: types.Message,
    response_message: types.Message,
    delay: int = 300,
):
    await asyncio.sleep(delay)

    # Primeiro apaga a resposta do bot.
    try:
        await response_message.delete()
    except Exception:
        pass

    # Depois tenta apagar o comando do usuário.
    # Em grupos isso depende das permissões do bot.
    try:
        await command_message.delete()
    except Exception:
        pass


async def ping_command(message: types.Message):
    if await is_banned(
        message.from_user.id if message.from_user else None,
        message.chat.id,
    ):
        return

    # Segue o idioma configurado no bot.
    target_id = (
        message.from_user.id
        if message.chat.type == ChatType.PRIVATE
        else message.chat.id
    )

    try:
        user_lang = await user_mod.db.get_language(target_id)
    except Exception:
        user_lang = None

    user_lang = (
        user_lang
        or getattr(message.from_user, "language_code", None)
        or "pt"
    )

    try:
        # Mede o round-trip real entre o bot e sua API Telegram.
        started = time.perf_counter()

        await user_mod.bot.get_me()

        latency_ms = max(
            1,
            round(
                (time.perf_counter() - started) * 1000
            ),
        )

        if latency_ms <= 250:
            status = "good"
        elif latency_ms <= 600:
            status = "fair"
        else:
            status = "slow"

        try:
            bot_username = await get_bot_username(
                user_mod.bot
            )
        except Exception:
            bot_username = None

        response = await message.reply(
            bm.ping_message(
                latency_ms,
                status,
                lang=user_lang,
                bot_username=bot_username,
            ),
            parse_mode="HTML",
            link_preview_options=types.LinkPreviewOptions(
                is_disabled=True
            ),
        )

        if message.from_user:
            try:
                await user_mod.send_analytics(
                    user_id=message.from_user.id,
                    chat_type=message.chat.type,
                    action_name="ping",
                )
            except Exception:
                pass

        # Apaga o /ping e a resposta após 5 minutos,
        # sem bloquear o handler.
        asyncio.create_task(
            _delete_ping_later(
                message,
                response,
                300,
            )
        )

    except Exception as exc:
        logging.exception(
            "Error handling /ping: %s",
            exc,
        )

        await message.reply(
            bm.ping_unavailable(lang=user_lang)
        )
