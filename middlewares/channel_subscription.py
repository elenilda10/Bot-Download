from i18n import t as _t
import logging
import time
from datetime import datetime, timezone

from aiogram import BaseMiddleware
from aiogram.exceptions import (
    TelegramAPIError, TelegramBadRequest,
    TelegramForbiddenError, TelegramNotFound,
)
from aiogram.types import (
    CallbackQuery,
    InlineQuery,
    InlineQueryResultsButton,
    EphemeralMessageParameters,
    Message,
    Chat,
)

import config
import keyboards as kb
import messages as bm
from app_context import db

CHANNEL_ID = "@NexoHubDev"
VERIFY_CALLBACK = "channel_sub:verify"


class ChannelSubscriptionMiddleware(BaseMiddleware):
    def __init__(self):
        super().__init__()
        from services.runtime.channel_subscription_coordination import (
            membership_cache,
            resuming_users,
        )
        self._cache = membership_cache
        self._notices = {}
        self._resuming = resuming_users

    async def _language(self, user):
        from i18n import normalize_locale, request_locale
        if request_locale() is not None:
            return request_locale()
        fallback = normalize_locale(getattr(user, 'language_code', None))
        try:
            record = await db.get_user_info(user_id=user.id)
            if record and getattr(record, 'language', None):
                return normalize_locale(record.language)
        except Exception:
            logging.exception('Failed to load user locale: user_id=%s', user.id)
        return fallback

    async def _is_member(self, bot, user_id, *, fresh=False):
        now = time.monotonic()

        if not fresh:
            cached = self._cache.get(user_id)
            if cached and cached[0] > now:
                return cached[1]

        try:
            member = await bot.get_chat_member(
                chat_id=CHANNEL_ID,
                user_id=user_id,
            )
        except TelegramAPIError as exc:
            logging.warning(
                "Falha ao verificar canal: user_id=%s error=%s",
                user_id, exc,
            )
            return None

        status = getattr(member.status, 'value', member.status)
        subscribed = (
            status in {"creator", "administrator", "member"}
            or (
                status == "restricted"
                and getattr(member, "is_member", False)
            )
        )

        self._cache[user_id] = (
            now + (90 if subscribed else 20),
            subscribed,
        )

        if len(self._cache) > 4096:
            self._cache.clear()

        return subscribed

    async def _notify(self, message, bot, user, lang):
        key = (message.chat.id, user.id)
        now = time.monotonic()

        cached = self._notices.get(key)
        if cached and cached[0] > now:
            return cached[1], cached[2]

        text = bm.channel_subscription_required(lang=lang)
        keyboard = kb.channel_subscription_keyboard(lang=lang)

        try:
            if message.chat.type == "private":
                notice = await message.answer(
                    text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
            else:
                # Mensagem efêmera: somente o destinatário vê no grupo.
                try:
                    notice = await bot.send_message(
                        chat_id=message.chat.id,
                        text=text,
                        reply_markup=keyboard,
                        parse_mode="HTML",
                        ephemeral_message_parameters=EphemeralMessageParameters(
                            receiver_user_id=user.id,
                        ),
                        message_thread_id=(
                            message.message_thread_id
                            if getattr(message, "is_topic_message", False)
                            else None
                        ),
                    )
                except TelegramAPIError as exc:
                    # Caso o grupo não permita mensagens efêmeras,
                    # tenta o privado, sem publicar avisos no grupo.
                    logging.warning(
                        "Aviso efêmero indisponível: chat=%s user=%s error=%s",
                        message.chat.id,
                        user.id,
                        exc,
                    )
                    notice = await bot.send_message(
                        chat_id=user.id,
                        text=text,
                        reply_markup=keyboard,
                        parse_mode="HTML",
                    )
        except TelegramAPIError as exc:
            logging.warning(
                "Falha ao mostrar inscrição obrigatória: %s",
                exc,
            )
            return None

        if notice is None:
            return None

        result = (notice.chat.id, notice.message_id)

        # Guarda os identificadores para editar o aviso
        # quando o usuário entrar no canal.
        try:
            from services.runtime.channel_subscription_notices import (
                remember_notice,
            )

            if not remember_notice(user.id, notice, lang):
                logging.warning(
                    "Aviso sem identificador editável: "
                    "chat_id=%s user_id=%s",
                    notice.chat.id,
                    user.id,
                )
        except Exception:
            logging.exception(
                "Falha ao registrar aviso de inscrição: user_id=%s",
                user.id,
            )

        self._notices[key] = (now + 90, *result)

        if len(self._notices) > 4096:
            self._notices.clear()

        return result

    async def _send_in_context(
        self, event, bot, user, text, keyboard=None
    ):
        callback_message = getattr(event, "message", None)
        chat = getattr(callback_message, "chat", None)
        chat_id = getattr(chat, "id", None) or user.id

        kwargs = {}
        if chat_id < 0:
            kwargs["ephemeral_message_parameters"] = (
                EphemeralMessageParameters(
                    receiver_user_id=user.id,
                    callback_query_id=event.id,
                )
            )

        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
            **kwargs,
        )

    async def _welcome_after_verify(self, event, bot, user, lang):
        info = await bot.get_me()
        await self._send_in_context(
            event,
            bot,
            user,
            bm.welcome_message(lang=lang),
            kb.start_keyboard(
                info.username,
                ref_user_id=user.id,
                lang=lang,
            ),
        )

    async def _resume_after_verify(
        self, event, bot, user, lang, pending
    ):
        from services.runtime.channel_subscription_coordination import (
            resume_pending_request,
        )

        result = await resume_pending_request(bot, user, pending)

        if result == "invalid":
            await self._send_in_context(
                event, bot, user,
                _t('middlewares.channel_subscription._resume_after_verify.3697e4d3', lang),
            )
        elif result == "dm_required":
            info = await bot.get_me()
            await self._send_in_context(
                event,
                bot,
                user,
                bm.dm_start_required(lang=lang),
                kb.start_private_chat_keyboard(
                    info.username,
                    lang=lang,
                ),
            )

    async def _verify(self, event, bot, user, lang):

        subscribed = await self._is_member(
            bot, user.id, fresh=True
        )

        if subscribed is None:
            await event.answer(
                _t('middlewares.channel_subscription._verify.d416f349', lang),
                show_alert=True,
            )
            return

        if not subscribed:
            await event.answer(
                _t('middlewares.channel_subscription._verify.78ab70b3', lang),
                show_alert=True,
            )
            return

        if user.id in self._resuming:
            await event.answer(
                _t('middlewares.channel_subscription._verify.f5951464', lang),
            )
            return

        self._resuming.add(user.id)

        try:
            from services.runtime.pending_requests import get_pending

            pending = get_pending(user.id)

            await event.answer(
                _t('middlewares.channel_subscription._verify.e3133c9a', lang),
            )

            if pending is not None:
                await self._resume_after_verify(
                    event, bot, user, lang, pending
                )
            else:
                await self._welcome_after_verify(
                    event, bot, user, lang
                )

        except Exception:
            logging.exception(
                "Falha ao continuar após inscrição: user_id=%s",
                user.id,
            )
            try:
                await self._send_in_context(
                    event, bot, user,
                    _t('middlewares.channel_subscription._verify.5e6165cc', lang),
                )
            except TelegramAPIError:
                logging.exception(
                    "Falha ao avisar usuário após erro de retomada"
                )
        finally:
            self._resuming.discard(user.id)

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        bot = data.get("bot")

        if not user or user.is_bot or bot is None:
            return await handler(event, data)

        admin_id = getattr(config, "ADMIN_ID", None)

        is_verify = (
            isinstance(event, CallbackQuery)
            and event.data == VERIFY_CALLBACK
        )

        if not is_verify and admin_id is not None and str(user.id) == str(admin_id):
            return await handler(event, data)

        if isinstance(event, Message):
            if event.chat.type == "channel":
                return None

            if event.chat.type != "private":
                from services.links.detection import extract_supported_link

                text = event.text or event.caption or ""
                if extract_supported_link(text) is None:
                    return await handler(event, data)

        lang = await self._language(user)

        if is_verify:
            await self._verify(event, bot, user, lang)
            return None

        subscribed = await self._is_member(bot, user.id)

        # Erro na API: não bloquear usuários indevidamente.
        if subscribed is None or subscribed:
            return await handler(event, data)

        if isinstance(event, Message):
            notice = await self._notify(event, bot, user, lang)

            if notice is not None:
                from services.links.detection import extract_supported_link
                from services.runtime.pending_requests import (
                    PendingRequest,
                    set_pending,
                )

                detected = extract_supported_link(
                    event.text or event.caption or ""
                )

                if detected is not None:
                    service, url = detected

                    try:
                        set_pending(
                            user.id,
                            PendingRequest(
                                service=service,
                                url=url,
                                notice_chat_id=notice[0],
                                notice_message_id=notice[1],
                                source_chat_id=event.chat.id,
                                source_message_id=event.message_id,
                            ),
                        )
                    except Exception:
                        logging.exception(
                            "Falha ao preservar download pendente"
                        )

        elif isinstance(event, CallbackQuery):
            if isinstance(event.message, Message):
                await self._notify(event.message, bot, user, lang)

            await event.answer(
                _t('middlewares.channel_subscription.__call__.b9856f8c', None),
                show_alert=True,
            )

        elif isinstance(event, InlineQuery):
            await event.answer(
                results=[],
                cache_time=0,
                is_personal=True,
                button=InlineQueryResultsButton(
                    text=(
                        _t('middlewares.channel_subscription.__call__.9a1bd4d8', None)
                    ),
                    start_parameter="channel_sub",
                ),
            )

        return None
