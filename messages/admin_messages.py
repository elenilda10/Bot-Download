DEFAULT_LANG = "pt"


def _normalize_lang(lang: str | None) -> str:
    if not lang:
        return DEFAULT_LANG
    lang = lang.lower().strip()
    return "pt" if lang.startswith("pt") else "en"


def admin_panel(
    total_count,
    private_count,
    group_count,
    active_user_count,
    inactive_user_count,
    lang: str = DEFAULT_LANG,
):
    if _normalize_lang(lang) == "pt":
        return (
            "<b>Olá, este é o painel de administração.</b>\n\n"
            "👥 Total de conversas: <b>{total_count}</b>\n"
            "👤 Usuários privados: <b>{private_count}</b>\n"
            "🏘 Grupos: <b>{group_count}</b>\n\n"
            "✅ Ativos: <b>{active_user_count}</b>\n"
            "🚫 Inativos: <b>{inactive_user_count}</b>"
        ).format(
            total_count=total_count,
            private_count=private_count,
            group_count=group_count,
            active_user_count=active_user_count,
            inactive_user_count=inactive_user_count,
        )
    return (
        "<b>Hello, this is the admin panel.</b>\n\n"
        "👥 Total chats: <b>{total_count}</b>\n"
        "👤 Private users: <b>{private_count}</b>\n"
        "🏘 Groups: <b>{group_count}</b>\n\n"
        "✅ Active: <b>{active_user_count}</b>\n"
        "🚫 Inactive: <b>{inactive_user_count}</b>"
    ).format(
        total_count=total_count,
        private_count=private_count,
        group_count=group_count,
        active_user_count=active_user_count,
        inactive_user_count=inactive_user_count,
    )


def not_groups(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "Este comando não pode ser usado em grupos!"
    return "This command cannot be used in a group!"


def finish_mailing(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "Envio de mensagens em massa concluído!"
    return "Mailing is complete!"


def start_mailing(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "Iniciando envio em massa..."
    return "Starting mailing..."


def mailing_audience_preview(
    total_users,
    active_users,
    inactive_users,
    banned_users,
    private_users,
    group_users,
    lang: str = DEFAULT_LANG,
):
    if _normalize_lang(lang) == "pt":
        return (
            "<b>Prévia do público do envio</b>\n"
            "Usuários a processar: <b>{total_users}</b>\n"
            "Ativos: <b>{active_users}</b>\n"
            "Inativos: <b>{inactive_users}</b>\n"
            "Banidos: <b>{banned_users}</b>\n"
            "Chats privados: <b>{private_users}</b>\n"
            "Grupos: <b>{group_users}</b>\n\n"
            "Digite a mensagem para enviar:"
        ).format(
            total_users=total_users,
            active_users=active_users,
            inactive_users=inactive_users,
            banned_users=banned_users,
            private_users=private_users,
            group_users=group_users,
        )
    return (
        "<b>Mailing audience preview</b>\n"
        "Users to process: <b>{total_users}</b>\n"
        "Active: <b>{active_users}</b>\n"
        "Inactive: <b>{inactive_users}</b>\n"
        "Banned: <b>{banned_users}</b>\n"
        "Private chats: <b>{private_users}</b>\n"
        "Groups: <b>{group_users}</b>\n\n"
        "Enter the message to send:"
    ).format(
        total_users=total_users,
        active_users=active_users,
        inactive_users=inactive_users,
        banned_users=banned_users,
        private_users=private_users,
        group_users=group_users,
    )


def canceled(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "Ação cancelada!"
    return "Action canceled!"


def your_message_sent(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "Sua mensagem foi enviada!"
    return "Your message sent!"


def something_went_wrong(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "Algo deu errado, verifique os logs para mais informações!"
    return "Something went wrong, see log for more information!"


def please_type_message(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "Por favor, digite a mensagem:"
    return "Please type message:"


def log_deleted(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "Log excluído, iniciando a gravação de um novo."
    return "Log deleted, starting to write a new one."


def active_users_check_started(total_users, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return f"Iniciando verificação de disponibilidade para {total_users} usuários..."
    return f"Starting availability check for {total_users} users..."


def active_users_check_completed(
    total_users, reachable_users, unreachable_users, lang: str = DEFAULT_LANG
):
    if _normalize_lang(lang) == "pt":
        return (
            "<b>Verificação de disponibilidade concluída.</b>\n"
            "Total de usuários processados: <b>{total_users}</b>\n"
            "Alcançáveis: <b>{reachable_users}</b>\n"
            "Inalcançáveis: <b>{unreachable_users}</b>"
        ).format(
            total_users=total_users,
            reachable_users=reachable_users,
            unreachable_users=unreachable_users,
        )
    return (
        "<b>Availability check finished.</b>\n"
        "Total users processed: <b>{total_users}</b>\n"
        "Reachable: <b>{reachable_users}</b>\n"
        "Unreachable: <b>{unreachable_users}</b>"
    ).format(
        total_users=total_users,
        reachable_users=reachable_users,
        unreachable_users=unreachable_users,
    )


def active_users_check_no_targets(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "Não há usuários disponíveis para verificação."
    return "There are no users available for checking."


def enter_chat_id(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "Digite o ID do chat (por exemplo, -1001234567890):"
    return "Enter the chat ID (for example, -1001234567890):"


def invalid_chat_id(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "O ID do chat deve ser um número como -1001234567890. Tente novamente ou clique em Cancelar."
    return "Chat ID must be a number like -1001234567890. Try again or tap Cancel."


def enter_chat_message(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "Digite a mensagem que deseja enviar para este chat:"
    return "Enter the message you want to send to this chat:"


def known_chat_target(
    chat_id, chat_name, chat_username, status, lang: str = DEFAULT_LANG
):
    if _normalize_lang(lang) == "pt":
        return (
            "<b>Destino de chat conhecido</b>\n"
            "ID: <b>{chat_id}</b>\n"
            "Nome: <b>{chat_name}</b>\n"
            "Nome de usuário: <b>{chat_username}</b>\n"
            "Status: <b>{status}</b>"
        ).format(
            chat_id=chat_id,
            chat_name=chat_name,
            chat_username=chat_username or "—",
            status=status or "desconhecido",
        )
    return (
        "<b>Known chat target</b>\n"
        "ID: <b>{chat_id}</b>\n"
        "Name: <b>{chat_name}</b>\n"
        "Username: <b>{chat_username}</b>\n"
        "Status: <b>{status}</b>"
    ).format(
        chat_id=chat_id,
        chat_name=chat_name,
        chat_username=chat_username or "—",
        status=status or "unknown",
    )


def unknown_chat_target(chat_id, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return (
            "O chat {chat_id} ainda não está no banco de dados local. "
            "Ainda tentarei enviar a mensagem se o bot tiver acesso."
        ).format(chat_id=chat_id)
    return (
        "Chat {chat_id} is not in the local database yet. "
        "I'll still try to send the message if the bot has access."
    ).format(chat_id=chat_id)


def chat_message_sent(chat_id, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return f"Mensagem entregue ao chat {chat_id}."
    return f"Message delivered to chat {chat_id}."


def chat_message_failed(chat_id, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return f"Falha ao enviar mensagem para o chat {chat_id}. Certifique-se de que o bot é membro e tem permissão para escrever."
    return f"Failed to send message to chat {chat_id}. Make sure the bot is a member and can write there."


def chat_message_sending(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "Enviando mensagem..."
    return "Sending message..."


def downloads_cleanup_blocked(active_jobs, queued_jobs, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return (
            "Limpeza ignorada pois downloads ainda estão em execução. "
            "active_jobs={active_jobs}, queued_jobs={queued_jobs}."
        ).format(active_jobs=active_jobs, queued_jobs=queued_jobs)
    return (
        "Cleanup skipped because downloads are still running. "
        "active_jobs={active_jobs}, queued_jobs={queued_jobs}."
    ).format(active_jobs=active_jobs, queued_jobs=queued_jobs)


def downloads_cleanup_finished(
    removed_files, removed_dirs, skipped_recent_files, lang: str = DEFAULT_LANG
):
    if _normalize_lang(lang) == "pt":
        return (
            "Limpeza de downloads concluída. Removidos {removed_files} arquivos e {removed_dirs} diretórios; "
            "ignorados {skipped_recent_files} arquivos recentes."
        ).format(
            removed_files=removed_files,
            removed_dirs=removed_dirs,
            skipped_recent_files=skipped_recent_files,
        )
    return (
        "Downloads cleanup finished. Removed {removed_files} files and {removed_dirs} directories; "
        "skipped {skipped_recent_files} recent files."
    ).format(
        removed_files=removed_files,
        removed_dirs=removed_dirs,
        skipped_recent_files=skipped_recent_files,
    )
