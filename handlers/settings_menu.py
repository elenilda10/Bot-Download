from aiogram import types
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest

import keyboards as kb
import messages as bm
from services.logger import logger as logging
from services.settings import (
    parse_setting_toggle_callback,
    parse_settings_view_callback,
)
import handlers.user as user_mod

logging = logging.bind(service="settings_menu")


def _admin_statuses() -> set[ChatMemberStatus]:
    statuses = {ChatMemberStatus.ADMINISTRATOR}
    owner = getattr(ChatMemberStatus, "OWNER", None)
    creator = getattr(ChatMemberStatus, "CREATOR", None)
    if owner:
        statuses.add(owner)
    if creator:
        statuses.add(creator)
    return statuses


async def _is_group_admin(chat_id: int, user_id: int) -> bool:
    try:
        member = await user_mod.bot.get_chat_member(chat_id, user_id)
    except Exception:
        return False
    return member.status in _admin_statuses()


def _is_message_not_modified_error(exc: Exception) -> bool:
    return any(
        marker in str(exc).lower()
        for marker in user_mod._MESSAGE_NOT_MODIFIED_MARKERS
    )


def _settings_chat_name(chat: types.Chat) -> str:
    title = getattr(chat, "title", None) or getattr(chat, "full_name", None)
    if title:
        return title
    first_name = getattr(chat, "first_name", None)
    last_name = getattr(chat, "last_name", None)
    name_parts = [part for part in (first_name, last_name) if part]
    if name_parts:
        return " ".join(name_parts)
    return f"Chat {chat.id}"


async def _ensure_settings_entities(
    message: types.Message | None,
    actor: types.User | None,
) -> None:
    if actor and not getattr(actor, "is_bot", False):
        await user_mod.db.upsert_chat(
            user_id=actor.id,
            user_name=getattr(actor, "full_name", None)
            or getattr(actor, "username", None)
            or str(actor.id),
            user_username=getattr(actor, "username", None),
            chat_type="private",
            language=getattr(actor, "language_code", None),
            status="active",
        )

    if message and message.chat and message.chat.type != "private":
        chat = message.chat
        await user_mod.db.upsert_chat(
            user_id=chat.id,
            user_name=_settings_chat_name(chat),
            user_username=getattr(chat, "username", None),
            chat_type="public",
            language=getattr(chat, "language_code", None),
            status="active",
        )


async def _resolve_settings_target(
    call: types.CallbackQuery, lang: str = "pt"
) -> int | None:
    if call.message and call.message.chat.type != "private":
        is_admin = await user_mod._is_group_admin(
            call.message.chat.id, call.from_user.id
        )
        if not is_admin:
            await call.answer(bm.settings_admin_only(lang=lang), show_alert=True)
            return None
        return call.message.chat.id
    return call.from_user.id


async def _get_chat_language(target_id: int, default_lang: str = "pt") -> str:
    try:
        chat = await user_mod.db.get_user_info(user_id=target_id)
        if chat and getattr(chat, "language", None):
            return str(chat.language).lower()
    except Exception:
        pass
    return default_lang


async def settings_menu(message: types.Message, lang: str = "pt"):
    await user_mod.send_analytics(
        user_id=message.from_user.id,
        chat_type=message.chat.type,
        action_name="settings",
    )
    target_id = message.chat.id if message.chat.type != "private" else message.from_user.id
    user_lang = await _get_chat_language(target_id, default_lang=lang)

    if message.chat.type != "private":
        is_admin = await user_mod._is_group_admin(
            message.chat.id, message.from_user.id
        )
        if not is_admin:
            await message.reply(bm.settings_admin_only(lang=user_lang))
            return

    await message.reply(
        text=bm.settings(lang=user_lang),
        reply_markup=kb.return_settings_categories_keyboard(lang=user_lang),
        parse_mode="HTML",
    )


async def back_to_settings(call: types.CallbackQuery, lang: str = "pt"):
    target_id = await _resolve_settings_target(call, lang=lang)
    if target_id is None:
        return
    user_lang = await _get_chat_language(target_id, default_lang=lang)

    await call.message.edit_text(
        text=bm.settings(lang=user_lang),
        reply_markup=kb.return_settings_categories_keyboard(lang=user_lang),
        parse_mode="HTML",
    )
    await call.answer()


async def open_language_menu(call: types.CallbackQuery, lang: str = "pt"):
    target_id = await _resolve_settings_target(call, lang=lang)
    if target_id is None:
        return
    user_lang = await _get_chat_language(target_id, default_lang=lang)

    prompt_text = (
        "🌐 <b>Escolha o idioma do bot:</b>"
        if user_lang.startswith("pt")
        else "🌐 <b>Choose the bot language:</b>"
    )

    await call.message.edit_text(
        text=prompt_text,
        reply_markup=kb.language_keyboard(current_lang=user_lang),
        parse_mode="HTML",
    )
    await call.answer()


async def set_language_setting(call: types.CallbackQuery, lang: str = "pt"):
    target_id = await _resolve_settings_target(call, lang=lang)
    if target_id is None:
        return

    # Extrai o código da linguagem (ex: 'setting:lang:pt' -> 'pt')
    new_lang = call.data.split(":")[-1]

    try:
        await user_mod._ensure_settings_entities(call.message, call.from_user)
        await user_mod.db.upsert_chat(
            user_id=target_id,
            user_name=_settings_chat_name(call.message.chat)
            if call.message and call.message.chat.type != "private"
            else (getattr(call.from_user, "full_name", None) or str(call.from_user.id)),
            chat_type="public"
            if call.message and call.message.chat.type != "private"
            else "private",
            language=new_lang,
            status="active",
        )

        feedback = (
            "✅ Idioma alterado para Português!"
            if new_lang == "pt"
            else "✅ Language changed to English!"
        )
        await call.answer(feedback, show_alert=False)

        prompt_text = (
            "🌐 <b>Escolha o idioma do bot:</b>"
            if new_lang == "pt"
            else "🌐 <b>Choose the bot language:</b>"
        )
        await call.message.edit_text(
            text=prompt_text,
            reply_markup=kb.language_keyboard(current_lang=new_lang),
            parse_mode="HTML",
        )
    except Exception as exc:
        logging.exception(
            "Failed to change language setting: target_id=%s new_lang=%s error=%s",
            target_id,
            new_lang,
            exc,
        )
        await call.answer(bm.something_went_wrong(lang=new_lang), show_alert=True)


async def open_category(call: types.CallbackQuery, lang: str = "pt"):
    if not call.data or not call.data.startswith("settings_cat:"):
        await call.answer()
        return
    cat = call.data.split(":", 1)[1]
    target_id = await _resolve_settings_target(call, lang=lang)
    if target_id is None:
        return
    user_lang = await _get_chat_language(target_id, default_lang=lang)

    await call.message.edit_text(
        text=bm.category_settings_text(cat, lang=user_lang),
        reply_markup=kb.return_category_settings_keyboard(cat, lang=user_lang),
        parse_mode="HTML",
    )
    await call.answer()


async def open_setting(call: types.CallbackQuery, lang: str = "pt"):
    field = parse_settings_view_callback(call.data)
    if field is None:
        await call.answer(bm.invalid_settings_option(lang=lang), show_alert=True)
        return
    target_id = await _resolve_settings_target(call, lang=lang)
    if target_id is None:
        return
    user_lang = await _get_chat_language(target_id, default_lang=lang)

    try:
        await user_mod._ensure_settings_entities(call.message, call.from_user)
        current_value = await user_mod.db.get_user_setting(
            user_id=target_id, field=field
        )
        keyboard = kb.return_field_keyboard(field, current_value, lang=user_lang)

        await call.message.edit_text(
            text=bm.get_field_text(field, lang=user_lang),
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        await call.answer()
    except Exception as exc:
        logging.exception(
            "Failed to open settings field: field=%s user_id=%s chat_id=%s error=%s",
            field,
            getattr(call.from_user, "id", None),
            getattr(getattr(call.message, "chat", None), "id", None),
            exc,
        )
        await call.answer(bm.something_went_wrong(lang=user_lang), show_alert=True)


async def change_setting(call: types.CallbackQuery, lang: str = "pt"):
    setting_payload = parse_setting_toggle_callback(call.data)
    if setting_payload is None:
        await call.answer(bm.invalid_settings_option(lang=lang), show_alert=True)
        return
    field, value = setting_payload
    target_id = await _resolve_settings_target(call, lang=lang)
    if target_id is None:
        return
    user_lang = await _get_chat_language(target_id, default_lang=lang)

    try:
        await user_mod._ensure_settings_entities(call.message, call.from_user)
        await user_mod.db.set_user_setting(
            user_id=target_id, field=field, value=value
        )
    except ValueError:
        await call.answer(bm.invalid_settings_option(lang=user_lang), show_alert=True)
        return
    except Exception as exc:
        logging.exception(
            "Failed to change setting: field=%s value=%s user_id=%s chat_id=%s error=%s",
            field,
            value,
            getattr(call.from_user, "id", None),
            getattr(getattr(call.message, "chat", None), "id", None),
            exc,
        )
        await call.answer(bm.something_went_wrong(lang=user_lang), show_alert=True)
        return

    try:
        current_value = await user_mod.db.get_user_setting(
            user_id=target_id, field=field
        )
        keyboard = kb.return_field_keyboard(field, current_value, lang=user_lang)

        await call.message.edit_reply_markup(reply_markup=keyboard)
        await call.answer()
    except Exception as exc:
        tb_exc = getattr(user_mod, "TelegramBadRequest", TelegramBadRequest)
        if isinstance(
            exc, (TelegramBadRequest, tb_exc)
        ) or user_mod._is_message_not_modified_error(exc):
            if user_mod._is_message_not_modified_error(exc):
                logging.info(
                    "Settings keyboard already up to date: field=%s user_id=%s chat_id=%s",
                    field,
                    getattr(call.from_user, "id", None),
                    getattr(getattr(call.message, "chat", None), "id", None),
                )
                await call.answer()
                return
            logging.exception(
                "Failed to refresh settings keyboard: field=%s user_id=%s chat_id=%s error=%s",
                field,
                getattr(call.from_user, "id", None),
                getattr(getattr(call.message, "chat", None), "id", None),
                exc,
            )
            await call.answer(
                "Não foi possível atualizar agora. Tente novamente mais tarde."
                if user_lang.startswith("pt")
                else "Couldn't update settings right now. Please try again later.",
                show_alert=True,
            )
            return
        logging.exception(
            "Failed to refresh settings keyboard: field=%s user_id=%s chat_id=%s error=%s",
            field,
            getattr(call.from_user, "id", None),
            getattr(getattr(call.message, "chat", None), "id", None),
            exc,
        )
        await call.answer(
            "Não foi possível atualizar agora. Tente novamente mais tarde."
            if user_lang.startswith("pt")
            else "Couldn't update settings right now. Please try again later.",
            show_alert=True,
        )


async def noop_callback(call: types.CallbackQuery):
    await call.answer()

# ==========================================
# 🌐 HANDLERS DE IDIOMA / LANGUAGE
# ==========================================

async def open_language_menu(call_or_msg, user_id: int, chat_id: int, lang: str):
    text = "🌐 <b>Escolha o seu idioma / Select your language:</b>" if lang == "pt" else "🌐 <b>Select your language / Escolha o seu idioma:</b>"
    keyboard = kb.language_keyboard(current_lang=lang)
    if isinstance(call_or_msg, types.CallbackQuery):
        try:
            await call_or_msg.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        except Exception:
            await call_or_msg.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await call_or_msg.answer()
    else:
        await call_or_msg.answer(text, reply_markup=keyboard, parse_mode="HTML")

async def settings_lang_callback(call: types.CallbackQuery):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    current_lang = (getattr(await user_mod.db.get_user_info(user_id=user_id), "language", None) or "pt")
    await open_language_menu(call, user_id, chat_id, current_lang)

async def set_language_callback(call: types.CallbackQuery):
    user_id = call.from_user.id
    new_lang = "pt" if "pt" in call.data else "en"
    
    try:
        await user_mod.db.set_user_setting(user_id=user_id, field="lang", value=new_lang)
    except Exception as exc:
        logging.warning("Não foi possível salvar lang no DB: %s", exc)
    
    confirm_text = "✅ Idioma alterado para <b>Português</b>!" if new_lang == "pt" else "✅ Language changed to <b>English</b>!"
    keyboard = kb.return_settings_categories_keyboard(lang=new_lang)
    
    try:
        await call.message.edit_text(
            bm.settings_menu_text(lang=new_lang) if hasattr(bm, "settings_menu_text") else confirm_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception:
        await call.message.edit_reply_markup(reply_markup=keyboard)
    
    await call.answer(confirm_text, show_alert=False)

# ==========================================
# 🌐 CONTROLE DE IDIOMA
# ==========================================

async def show_language_view(target, user_id: int, lang: str):
    text = (
        "🌐 <b>Escolha o seu idioma / Select your language:</b>"
        if lang == "pt"
        else "🌐 <b>Select your language / Escolha o seu idioma:</b>"
    )
    keyboard = kb.language_keyboard(current_lang=lang)
    if isinstance(target, types.CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        except Exception:
            await target.message.edit_reply_markup(reply_markup=keyboard)
        await target.answer()
    else:
        await target.answer(text, reply_markup=keyboard, parse_mode="HTML")


async def handle_language_category(call: types.CallbackQuery):
    target_id = await _resolve_settings_target(call, lang="pt")
    current_lang = await _get_chat_language(target_id or call.from_user.id, default_lang="pt")
    await show_language_view(call, call.from_user.id, current_lang)


async def handle_language_selection(call: types.CallbackQuery):
    chosen_lang = "pt" if call.data.endswith("pt") else "en"
    target_id = await _resolve_settings_target(call, lang=chosen_lang)
    chat_id = target_id or (call.message.chat.id if call.message and call.message.chat.type != "private" else call.from_user.id)
    chat_type = call.message.chat.type if call.message else "private"

    # 1. Salva o idioma na tabela de chats
    try:
        await user_mod.db.upsert_chat(
            user_id=chat_id,
            user_name=call.from_user.full_name,
            user_username=call.from_user.username,
            chat_type=chat_type,
            language=chosen_lang,
            status="active",
        )
        # Limpa cache de atualização se existir
        if hasattr(user_mod, "_update_info_cache") and chat_id in user_mod._update_info_cache:
            del user_mod._update_info_cache[chat_id]
    except Exception as exc:
        logging.warning("Erro ao salvar idioma no banco: %s", exc)

    # 2. Atualiza os botões mostrando o check no idioma ativo
    keyboard = kb.language_keyboard(current_lang=chosen_lang)
    text = (
        "🌐 <b>Escolha o seu idioma / Select your language:</b>"
        if chosen_lang == "pt"
        else "🌐 <b>Select your language / Escolha o seu idioma:</b>"
    )
    
    try:
        await call.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        try:
            await call.message.edit_reply_markup(reply_markup=keyboard)
        except Exception:
            pass
            
    alert_msg = "✅ Idioma alterado para Português!" if chosen_lang == "pt" else "✅ Language set to English!"
    await call.answer(alert_msg)


async def handle_back_to_settings(call: types.CallbackQuery):
    target_id = await _resolve_settings_target(call, lang="pt")
    current_lang = await _get_chat_language(target_id or call.from_user.id, default_lang="pt")
    
    title = bm.settings(lang=current_lang)
    keyboard = kb.return_settings_categories_keyboard(lang=current_lang)
    
    try:
        await call.message.edit_text(title, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        await call.message.edit_reply_markup(reply_markup=keyboard)
    await call.answer()
