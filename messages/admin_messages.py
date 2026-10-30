from i18n import get_locale as _get_locale
from i18n import normalize_locale as _normalize_locale
from i18n import t as _t
DEFAULT_LANG = None


def _normalize_lang(lang: str | None) -> str:
    return _normalize_locale(lang if lang is not None else _get_locale())


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
            _t('messages.admin_messages.admin_panel.e57c5508', lang)
        ).format(
            total_count=total_count,
            private_count=private_count,
            group_count=group_count,
            active_user_count=active_user_count,
            inactive_user_count=inactive_user_count,
        )
    return (
        _t('messages.admin_messages.admin_panel.e57c5508', lang)
    ).format(
        total_count=total_count,
        private_count=private_count,
        group_count=group_count,
        active_user_count=active_user_count,
        inactive_user_count=inactive_user_count,
    )


def not_groups(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.admin_messages.not_groups.acedf6d6', lang)
    return _t('messages.admin_messages.not_groups.acedf6d6', lang)


def finish_mailing(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.admin_messages.finish_mailing.eeb0d3d8', lang)
    return _t('messages.admin_messages.finish_mailing.eeb0d3d8', lang)


def start_mailing(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.admin_messages.start_mailing.bd3869fa', lang)
    return _t('messages.admin_messages.start_mailing.bd3869fa', lang)


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
            _t('messages.admin_messages.mailing_audience_preview.e947c329', lang)
        ).format(
            total_users=total_users,
            active_users=active_users,
            inactive_users=inactive_users,
            banned_users=banned_users,
            private_users=private_users,
            group_users=group_users,
        )
    return (
        _t('messages.admin_messages.mailing_audience_preview.e947c329', lang)
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
        return _t('messages.admin_messages.canceled.6b393aaf', lang)
    return _t('messages.admin_messages.canceled.6b393aaf', lang)


def your_message_sent(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.admin_messages.your_message_sent.674c8272', lang)
    return _t('messages.admin_messages.your_message_sent.674c8272', lang)


def something_went_wrong(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.admin_messages.something_went_wrong.084416cc', lang)
    return _t('messages.admin_messages.something_went_wrong.084416cc', lang)


def please_type_message(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.admin_messages.please_type_message.433f4098', lang)
    return _t('messages.admin_messages.please_type_message.433f4098', lang)


def log_deleted(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.admin_messages.log_deleted.68f12c61', lang)
    return _t('messages.admin_messages.log_deleted.68f12c61', lang)


def active_users_check_started(total_users, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.admin_messages.active_users_check_started.a6dae91f', lang, p0=total_users)
    return _t('messages.admin_messages.active_users_check_started.a6dae91f', lang, p0=total_users)


def active_users_check_completed(
    total_users, reachable_users, unreachable_users, lang: str = DEFAULT_LANG
):
    if _normalize_lang(lang) == "pt":
        return (
            _t('messages.admin_messages.active_users_check_completed.bb320f4d', lang)
        ).format(
            total_users=total_users,
            reachable_users=reachable_users,
            unreachable_users=unreachable_users,
        )
    return (
        _t('messages.admin_messages.active_users_check_completed.bb320f4d', lang)
    ).format(
        total_users=total_users,
        reachable_users=reachable_users,
        unreachable_users=unreachable_users,
    )


def active_users_check_no_targets(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.admin_messages.active_users_check_no_targets.2fedfd2d', lang)
    return _t('messages.admin_messages.active_users_check_no_targets.2fedfd2d', lang)


def enter_chat_id(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.admin_messages.enter_chat_id.cb7daa80', lang)
    return _t('messages.admin_messages.enter_chat_id.cb7daa80', lang)


def invalid_chat_id(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.admin_messages.invalid_chat_id.d2c2ee63', lang)
    return _t('messages.admin_messages.invalid_chat_id.d2c2ee63', lang)


def enter_chat_message(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.admin_messages.enter_chat_message.82bec69a', lang)
    return _t('messages.admin_messages.enter_chat_message.82bec69a', lang)


def known_chat_target(
    chat_id, chat_name, chat_username, status, lang: str = DEFAULT_LANG
):
    if _normalize_lang(lang) == "pt":
        return (
            _t('messages.admin_messages.known_chat_target.53c5897f', lang)
        ).format(
            chat_id=chat_id,
            chat_name=chat_name,
            chat_username=chat_username or "—",
            status=status or "desconhecido",
        )
    return (
        _t('messages.admin_messages.known_chat_target.53c5897f', lang)
    ).format(
        chat_id=chat_id,
        chat_name=chat_name,
        chat_username=chat_username or "—",
        status=status or "unknown",
    )


def unknown_chat_target(chat_id, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return (
            _t('messages.admin_messages.unknown_chat_target.70b3b7ce', lang)
        ).format(chat_id=chat_id)
    return (
        _t('messages.admin_messages.unknown_chat_target.70b3b7ce', lang)
    ).format(chat_id=chat_id)


def chat_message_sent(chat_id, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.admin_messages.chat_message_sent.f0d437f3', lang, p0=chat_id)
    return _t('messages.admin_messages.chat_message_sent.f0d437f3', lang, p0=chat_id)


def chat_message_failed(chat_id, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.admin_messages.chat_message_failed.00a0d249', lang, p0=chat_id)
    return _t('messages.admin_messages.chat_message_failed.00a0d249', lang, p0=chat_id)


def chat_message_sending(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.admin_messages.chat_message_sending.ad3bc129', lang)
    return _t('messages.admin_messages.chat_message_sending.ad3bc129', lang)


def downloads_cleanup_blocked(active_jobs, queued_jobs, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return (
            _t('messages.admin_messages.downloads_cleanup_blocked.70ff67f8', lang)
        ).format(active_jobs=active_jobs, queued_jobs=queued_jobs)
    return (
        _t('messages.admin_messages.downloads_cleanup_blocked.70ff67f8', lang)
    ).format(active_jobs=active_jobs, queued_jobs=queued_jobs)


def downloads_cleanup_finished(
    removed_files, removed_dirs, skipped_recent_files, lang: str = DEFAULT_LANG
):
    if _normalize_lang(lang) == "pt":
        return (
            _t('messages.admin_messages.downloads_cleanup_finished.d2d2bf55', lang)
        ).format(
            removed_files=removed_files,
            removed_dirs=removed_dirs,
            skipped_recent_files=skipped_recent_files,
        )
    return (
        _t('messages.admin_messages.downloads_cleanup_finished.d2d2bf55', lang)
    ).format(
        removed_files=removed_files,
        removed_dirs=removed_dirs,
        skipped_recent_files=skipped_recent_files,
    )
