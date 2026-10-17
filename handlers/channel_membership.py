import logging

from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ChatMemberUpdated

from services.runtime.channel_subscription_notices import (
    list_user_notices,
    remove_notice,
)

CHANNEL_USERNAME = "savevideodl"


def _is_member(member):
    status = getattr(member.status, "value", member.status)
    return (
        status in {"creator", "administrator", "member"}
        or (
            status == "restricted"
            and getattr(member, "is_member", False)
        )
    )


def _confirmation(lang):
    if str(lang).lower().startswith("pt"):
        return (
            '✅ '
            '<b>Inscrição confirmada!</b>\n\n'
            'Seu acesso ao Save Vídeo DL Bot foi liberado.'
        )

    return (
        '✅ '
        '<b>Subscription confirmed!</b>\n\n'
        'Your access to Save Vídeo DL Bot has been unlocked.'
    )


async def handle_channel_membership(event: ChatMemberUpdated, bot):
    if event.chat.type != ChatType.CHANNEL:
        return

    if (event.chat.username or "").lower() != CHANNEL_USERNAME:
        return

    from services.runtime.channel_subscription_coordination import (
        membership_cache,
        mark_subscribed,
        resuming_users,
        resume_pending_request,
    )

    # Atualiza o cache imediatamente quando o usuário sai.
    if not _is_member(event.new_chat_member):
        membership_cache.pop(event.new_chat_member.user.id, None)
        return

    # Ignora alterações que não representam uma nova entrada.
    if _is_member(event.old_chat_member):
        return

    user = event.new_chat_member.user
    user_id = user.id

    # Entrada confirmada pelo próprio Telegram.
    mark_subscribed(user_id)

    notices = list_user_notices(user_id)

    for notice in notices:
        text = _confirmation(notice.lang)

        try:
            if notice.ephemeral_message_id is not None:
                await bot.edit_ephemeral_message_text(
                    chat_id=notice.chat_id,
                    receiver_user_id=user_id,
                    ephemeral_message_id=notice.ephemeral_message_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=None,
                )
            elif notice.message_id > 0:
                await bot.edit_message_text(
                    chat_id=notice.chat_id,
                    message_id=notice.message_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=None,
                )
            else:
                continue

            remove_notice(user_id, notice)

        except TelegramAPIError as exc:
            logging.warning(
                "Não foi possível editar aviso de inscrição: "
                "user=%s chat=%s error=%s",
                user_id,
                notice.chat_id,
                exc,
            )

    logging.info(
        "Entrada confirmada no canal: user_id=%s avisos=%s",
        user_id,
        len(notices),
    )

    # Retoma o link pendente sem aguardar o botão Verificar.
    from services.runtime.pending_requests import get_pending

    pending = get_pending(user_id)
    if pending is None or user_id in resuming_users:
        return

    resuming_users.add(user_id)

    try:
        result = await resume_pending_request(bot, user, pending)

        if result == "resumed":
            logging.info(
                "Download retomado após entrada no canal: user_id=%s",
                user_id,
            )

        elif result in {"dm_required", "invalid"}:
            from aiogram.types import EphemeralMessageParameters
            import keyboards as kb

            lang = notices[-1].lang if notices else "pt"
            is_pt = str(lang).lower().startswith("pt")
            chat_id = pending.source_chat_id or user_id

            kwargs = {}
            if chat_id < 0:
                kwargs["ephemeral_message_parameters"] = (
                    EphemeralMessageParameters(
                        receiver_user_id=user_id,
                    )
                )

            if result == "dm_required":
                bot_info = await bot.get_me()
                text = (
                    "✅ Inscrição confirmada! Abra o privado do bot "
                    "para continuar seu download."
                    if is_pt else
                    "✅ Subscription confirmed! Open the bot's "
                    "private chat to continue your download."
                )
                keyboard = kb.start_private_chat_keyboard(
                    bot_info.username,
                    lang=lang,
                )
            else:
                text = (
                    "✅ Inscrição confirmada! Envie o link novamente."
                    if is_pt else
                    "✅ Subscription confirmed! Please send the link again."
                )
                keyboard = None

            await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
                **kwargs,
            )

    except Exception:
        logging.exception(
            "Falha na retomada automática após entrada no canal: "
            "user_id=%s",
            user_id,
        )
    finally:
        resuming_users.discard(user_id)
