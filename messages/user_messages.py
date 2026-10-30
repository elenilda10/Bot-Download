from i18n import get_locale as _get_locale
from i18n import normalize_locale as _normalize_locale
from i18n import t as _t
import html

DEFAULT_LANG = None


def _normalize_lang(lang: str | None) -> str:
    return _normalize_locale(lang if lang is not None else _get_locale())


def cancel(lang: str = DEFAULT_LANG):
    return _t('messages.user_messages.cancel.80f1a33f', lang)


def welcome_message(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return (
            _t('messages.user_messages.welcome_message.3a38a165', lang)
        )
    return (
        _t('messages.user_messages.welcome_message.3a38a165', lang)
    )


def settings(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return (
            _t('messages.user_messages.settings.918d2a64', lang)
        )
    return (
        _t('messages.user_messages.settings.918d2a64', lang)
    )


def settings_private_only(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.settings_private_only.8f9b59fe', lang)
    return _t('messages.user_messages.settings_private_only.8f9b59fe', lang)


def get_field_text(field: str, lang: str = DEFAULT_LANG):
    lang_code = _normalize_lang(lang)
    texts_pt = {
        "captions": (
            _t('messages.user_messages.get_field_text.feb36852', lang)
        ),
        "delete_message": (
            _t('messages.user_messages.get_field_text.7f94496f', lang)
        ),
        "info_buttons": (
            _t('messages.user_messages.get_field_text.7f6129d4', lang)
        ),
        "url_button": (
            _t('messages.user_messages.get_field_text.4e1f9b46', lang)
        ),
        "audio_button": (
            _t('messages.user_messages.get_field_text.d03f091c', lang)
        ),
        "music_button": (
            _t('messages.user_messages.get_field_text.88c9daba', lang)
        ),
        "file_button": (
            _t('messages.user_messages.get_field_text.9a258f03', lang)
        ),
        "video_quality": (
            _t('messages.user_messages.get_field_text.8accbb18', lang)
        ),
        "as_document": (
            _t('messages.user_messages.get_field_text.3589a96c', lang)
        ),
        "audio_format": (
            _t('messages.user_messages.get_field_text.5b27c1b3', lang)
        ),
    }

    texts_en = {
        "captions": (
            _t('messages.user_messages.get_field_text.feb36852', lang)
        ),
        "delete_message": (
            _t('messages.user_messages.get_field_text.7f94496f', lang)
        ),
        "info_buttons": (
            _t('messages.user_messages.get_field_text.7f6129d4', lang)
        ),
        "url_button": (
            _t('messages.user_messages.get_field_text.4e1f9b46', lang)
        ),
        "audio_button": (
            _t('messages.user_messages.get_field_text.d03f091c', lang)
        ),
        "music_button": (
            _t('messages.user_messages.get_field_text.88c9daba', lang)
        ),
        "file_button": (
            _t('messages.user_messages.get_field_text.9a258f03', lang)
        ),
        "video_quality": (
            _t('messages.user_messages.get_field_text.8accbb18', lang)
        ),
        "as_document": (
            _t('messages.user_messages.get_field_text.3589a96c', lang)
        ),
        "audio_format": (
            _t('messages.user_messages.get_field_text.5b27c1b3', lang)
        ),
    }

    if lang_code == "pt":
        return texts_pt.get(field, _t('messages.user_messages.get_field_text.074f52d4', lang))
    return texts_en.get(field, _t('messages.user_messages.get_field_text.074f52d4', lang))


def captions(user_captions, post_caption, bot_url, *, limit: int = 1024):
    def _truncate_escaped(value: str, max_len: int) -> str:
        if max_len <= 0:
            return ""
        if len(value) <= max_len:
            return value
        cut = value[:max_len]
        amp = cut.rfind("&")
        semi = cut.rfind(";")
        if amp > semi:
            cut = cut[:amp]
        return cut

    escaped_url = html.escape(bot_url or "")
    footer = _t('caption.footer', url=escaped_url)

    if user_captions == "on" and post_caption:
        body = html.escape(str(post_caption))
        sep = "\n\n"
        budget = limit - len(sep) - len(footer)
        if budget <= 0:
            return _truncate_escaped(footer, limit)

        if len(body) > budget:
            suffix = "…"
            body = _truncate_escaped(body, max(0, budget - len(suffix))).rstrip() + suffix

        return f"{body}{sep}{footer}"

    return _truncate_escaped(footer, limit)


def downloading_file_status(lang: str = DEFAULT_LANG, *, platform: str | None = None):
    if platform is not None:
        return media_transfer_status(platform, uploading=False, lang=lang)
    emoji = '<tg-emoji emoji-id="5854908544712707500">📦</tg-emoji>'
    return (
        _t('messages.user_messages.downloading_file_status.8b01351d', lang, p0=emoji)
    )


def uploading_file_status(lang: str = DEFAULT_LANG, *, platform: str | None = None):
    if platform is not None:
        return media_transfer_status(platform, uploading=True, lang=lang)
    emoji = '<tg-emoji emoji-id="5866355487255039002">🚀</tg-emoji>'
    return (
        _t('messages.user_messages.uploading_file_status.82c4258f', lang, p0=emoji)
    )


def mediafire_document_caption(lang: str = DEFAULT_LANG):
    emoji = '<tg-emoji emoji-id="5431420628978641216">📄</tg-emoji>'
    return (
        _t('messages.user_messages.mediafire_document_caption.5c39bac9', lang, p0=emoji)
    )


def downloading_audio_status(lang: str = DEFAULT_LANG, *, platform: str | None = None):
    if platform is not None:
        return media_transfer_status(platform, uploading=False, lang=lang)
    emoji = '<tg-emoji emoji-id="6024055893111345718">😄</tg-emoji>'
    return _t('messages.user_messages.downloading_audio_status.541f28ad', lang, p0=emoji)


def downloading_video_status(lang: str = DEFAULT_LANG, *, media_kind: str = "video", count: int = 1, platform: str | None = None):
    if platform is not None:
        return media_transfer_status(platform, uploading=False, lang=lang)
    emoji = '<tg-emoji emoji-id="5375464961822695044">🎬</tg-emoji>'
    if media_kind == "unknown":
        return _t('messages.user_messages.downloading_video_status.89e178d6', lang, p0=emoji)
    if media_kind == "photo":
        if _normalize_lang(lang) == "pt":
            return _t('messages.user_messages.downloading_video_status.810ef308', lang, p0=emoji) if count > 1 else _t('messages.user_messages.downloading_video_status.5628b1df', lang, p0=emoji)
        return _t('messages.user_messages.downloading_video_status.810ef308', lang, p0=emoji) if count > 1 else _t('messages.user_messages.downloading_video_status.5628b1df', lang, p0=emoji)
    return _t('messages.user_messages.downloading_video_status.8efb7c69', lang, p0=emoji)


def uploading_status(lang: str = DEFAULT_LANG, *, platform: str | None = None):
    if platform is not None:
        return media_transfer_status(platform, uploading=True, lang=lang)
    emoji = '<tg-emoji emoji-id="5866355487255039002">🚀</tg-emoji>'
    return _t('messages.user_messages.uploading_file_status.82c4258f', lang, p0=emoji)


def retrying_again_status(next_attempt: int, total_attempts: int, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.retrying_again_status.d71e3176', lang, p0=next_attempt, p1=total_attempts)
    return _t('messages.user_messages.retrying_again_status.d71e3176', lang, p0=next_attempt, p1=total_attempts)


def dm_start_required(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.dm_start_required.696ff0fc', lang)
    return _t('messages.user_messages.dm_start_required.696ff0fc', lang)


def duplicate_link_processing(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.duplicate_link_processing.17e7b36b', lang)
    return _t('messages.user_messages.duplicate_link_processing.17e7b36b', lang)


def duplicate_link_recently_processed(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.duplicate_link_recently_processed.4a226f59', lang)
    return _t('messages.user_messages.duplicate_link_recently_processed.4a226f59', lang)


def settings_admin_only(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.settings_admin_only.a65eb7a7', lang)
    return _t('messages.user_messages.settings_admin_only.a65eb7a7', lang)


def invalid_settings_option(lang: str = DEFAULT_LANG):
    return _t('messages.user_messages.invalid_settings_option.b9659ef8', lang)


def join_group(chat_title: str, lang: str = DEFAULT_LANG) -> str:
    if _normalize_lang(lang) == "pt":
        return (
            _t('messages.user_messages.join_group.2f22e10f', lang)
        ).format(chat_title=chat_title)
    return (
        _t('messages.user_messages.join_group.2f22e10f', lang)
    ).format(chat_title=chat_title)


def admin_rights_granted(chat_title: str, lang: str = DEFAULT_LANG) -> str:
    if _normalize_lang(lang) == "pt":
        return (
            _t('messages.user_messages.admin_rights_granted.937753ca', lang)
        ).format(chat_title=chat_title)
    return (
        _t('messages.user_messages.admin_rights_granted.937753ca', lang)
    ).format(chat_title=chat_title)


def keyboard_removed(lang: str = DEFAULT_LANG):
    return _t('messages.user_messages.keyboard_removed.6ad6a688', lang)


def tiktok_live_not_supported(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.tiktok_live_not_supported.8bc518aa', lang)
    return _t('messages.user_messages.tiktok_live_not_supported.8bc518aa', lang)


def delete_permission_warning(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.delete_permission_warning.2ecbc803', lang)
    return _t('messages.user_messages.delete_permission_warning.2ecbc803', lang)


def stats_temporarily_unavailable(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.stats_temporarily_unavailable.195d4b5b', lang)
    return _t('messages.user_messages.stats_temporarily_unavailable.195d4b5b', lang)


def no_queue_metrics_yet(lang: str = DEFAULT_LANG):
    return _t('messages.user_messages.no_queue_metrics_yet.694604d3', lang)


def open_bot_for_audio(lang: str = DEFAULT_LANG):
    return _t('messages.user_messages.open_bot_for_audio.1fae42ac', lang)


def audio_fetch_failed(lang: str = DEFAULT_LANG):
    return _t('messages.user_messages.audio_fetch_failed.54a50e5e', lang)


def audio_download_failed(lang: str = DEFAULT_LANG):
    return _t('messages.user_messages.audio_download_failed.ff719ff2', lang)


def spotify_metadata_failed(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.spotify_metadata_failed.480184fd', lang)
    return _t('messages.user_messages.spotify_metadata_failed.480184fd', lang)


def spotify_source_not_found(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.spotify_source_not_found.8b705fec', lang)
    return _t('messages.user_messages.spotify_source_not_found.8b705fec', lang)


def inline_album_link_invalid(lang: str = DEFAULT_LANG):
    return _t('messages.user_messages.inline_album_link_invalid.8d2d8799', lang)


def inline_photo_title(service_name: str, lang: str = DEFAULT_LANG):
    return _t('messages.user_messages.inline_photo_title.714f2b56', lang, p0=service_name)


def inline_photo_description(lang: str = DEFAULT_LANG):
    return _t('messages.user_messages.inline_photo_description.bb7bd65f', lang)


def inline_album_title(service_name: str, lang: str = DEFAULT_LANG):
    return _t('messages.user_messages.inline_album_title.44a7cb63', lang, p0=service_name)


def inline_album_description(lang: str = DEFAULT_LANG):
    return _t('messages.user_messages.inline_album_description.504ad33c', lang)


def inline_open_full_album_button(lang: str = DEFAULT_LANG):
    return _t('messages.user_messages.inline_open_full_album_button.f5ce3ba7', lang)


def inline_photos_title(service_name: str, lang: str = DEFAULT_LANG):
    return _t('messages.user_messages.inline_photos_title.2af6816f', lang, p0=service_name)


def inline_photos_not_supported(service_name: str, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.inline_photos_not_supported.f84f3b40', lang, p0=service_name)
    return _t('messages.user_messages.inline_photos_not_supported.f84f3b40', lang, p0=service_name)


def inline_send_video_button(lang: str = DEFAULT_LANG):
    return _t('keyboards.inline_keyboards.inline_send_video_keyboard.3375997f', lang)


def inline_video_title(service_name: str, lang: str = DEFAULT_LANG):
    return _t('messages.user_messages.inline_video_title.cc2d0fd3', lang, p0=service_name)


def inline_send_video_description(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.inline_send_video_description.bbccdc63', lang)
    return _t('messages.user_messages.inline_send_video_description.bbccdc63', lang)


def inline_send_photo_description(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.inline_send_photo_description.89b2b9d5', lang)
    return _t('messages.user_messages.inline_send_photo_description.89b2b9d5', lang)


def inline_send_photo_prompt(service_name: str, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.inline_send_photo_prompt.eae8529e', lang, p0=service_name)
    return _t('messages.user_messages.inline_send_photo_prompt.eae8529e', lang, p0=service_name)


def inline_send_video_prompt(service_name: str, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.inline_send_video_prompt.87cdb267', lang, p0=service_name)
    return _t('messages.user_messages.inline_send_video_prompt.87cdb267', lang, p0=service_name)


def inline_send_audio_prompt(service_name: str, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.inline_send_audio_prompt.5a1ea930', lang, p0=service_name)
    return _t('messages.user_messages.inline_send_audio_prompt.5a1ea930', lang, p0=service_name)


def inline_video_already_processing(lang: str = DEFAULT_LANG):
    return _t('messages.user_messages.inline_video_already_processing.31db6958', lang)


def inline_video_already_sent(lang: str = DEFAULT_LANG):
    return _t('messages.user_messages.inline_video_already_sent.68262216', lang)


def supported_sites_message(bot_username: str | None = None, lang: str = DEFAULT_LANG):
    return help_message(bot_username, lang=lang)


def category_settings_text(category: str, lang: str = DEFAULT_LANG) -> str:
    lang_code = _normalize_lang(lang)
    if category == "media":
        if lang_code == "pt":
            return (
                _t('messages.user_messages.category_settings_text.3e5e3eee', lang)
            )
        return (
            _t('messages.user_messages.category_settings_text.3e5e3eee', lang)
        )
    if category == "appearance":
        if lang_code == "pt":
            return (
                _t('messages.user_messages.category_settings_text.cb64f58b', lang)
            )
        return (
            _t('messages.user_messages.category_settings_text.cb64f58b', lang)
        )
    if category == "chat":
        if lang_code == "pt":
            return (
                _t('messages.user_messages.category_settings_text.d1cf9a94', lang)
            )
        return (
            _t('messages.user_messages.category_settings_text.d1cf9a94', lang)
        )
    return settings(lang=lang)


def help_message(bot_username: str | None = None, lang: str = DEFAULT_LANG) -> str:
    username = bot_username or "SaveVidDLBot"
    if _normalize_lang(lang) == "pt":
        return (
            _t('messages.user_messages.help_message.9640b060', lang, p0=username)
        )
    return (
        _t('messages.user_messages.help_message.9640b060', lang, p0=username)
    )


def referral_message(bot_username: str, user_id: int, invited_count: int, lang: str = DEFAULT_LANG) -> str:
    username = bot_username or "SaveVidDLBot"
    ref_link = f"https://t.me/{username}?start=ref_{user_id}"
    if _normalize_lang(lang) == "pt":
        return (
            _t('messages.user_messages.referral_message.9012a5e9', lang, p0=ref_link, p1=invited_count)
        )
    return (
        _t('messages.user_messages.referral_message.9012a5e9', lang, p0=ref_link, p1=invited_count)
    )


def batch_links_started(processed_total: int, detected_total: int | None = None, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        if detected_total is not None and detected_total > processed_total:
            return (
                _t('messages.user_messages.batch_links_started.9ca42ef8', lang, p0=detected_total, p1=processed_total)
            )
        return _t('messages.user_messages.batch_links_started.202f1982', lang, p0=processed_total)
    if detected_total is not None and detected_total > processed_total:
        return (
            _t('messages.user_messages.batch_links_started.a75f7fec', lang, p0=detected_total, p1=processed_total)
        )
    return _t('messages.user_messages.batch_links_started.f44054ae', lang, p0=processed_total)


def batch_link_progress(current: int, total: int, service_name: str, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.batch_link_progress.0fc96f97', lang, p0=current, p1=total, p2=service_name)
    return _t('messages.user_messages.batch_link_progress.0fc96f97', lang, p0=current, p1=total, p2=service_name)


def batch_links_finished(total: int, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.batch_links_finished.7d94657d', lang, p0=total)
    return _t('messages.user_messages.batch_links_finished.7d94657d', lang, p0=total)


def timeout_error(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.timeout_error.727ad602', lang)
    return _t('messages.user_messages.timeout_error.727ad602', lang)


def something_went_wrong(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return (
            _t('messages.user_messages.something_went_wrong.d2e288ca', lang)
        )
    return (
        _t('messages.user_messages.something_went_wrong.d2e288ca', lang)
    )


def video_too_large(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.video_too_large.1fbef359', lang)
    return _t('messages.user_messages.video_too_large.1fbef359', lang)


def audio_too_large(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.audio_too_large.e963ea5a', lang)
    return _t('messages.user_messages.audio_too_large.e963ea5a', lang)


def nothing_found(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.nothing_found.ac1e1f92', lang)
    return _t('messages.user_messages.nothing_found.ac1e1f92', lang)


def inline_open_result(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.inline_open_result.ddf133ea', lang)
    return _t('messages.user_messages.inline_open_result.ddf133ea', lang)


def inline_media_title(lang: str = DEFAULT_LANG):
    return _t('messages.user_messages.inline_media_title.d357175c', lang)


def inline_only_button_warning(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.inline_only_button_warning.dc3040b2', lang)
    return _t('messages.user_messages.inline_only_button_warning.dc3040b2', lang)


def inline_send_photo_button(lang: str = DEFAULT_LANG):
    return _t('messages.user_messages.inline_send_photo_button.1855ed29', lang)


def inline_send_audio_button(lang: str = DEFAULT_LANG):
    return _t('messages.user_messages.inline_send_audio_button.eaf6404b', lang)


def inline_send_audio_description(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.inline_send_audio_description.e4313522', lang)
    return _t('messages.user_messages.inline_send_audio_description.e4313522', lang)


def inline_single_video_only(service_name: str, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.inline_single_video_only.a8e0a14e', lang, p0=service_name)
    return _t('messages.user_messages.inline_single_video_only.a8e0a14e', lang, p0=service_name)


def open_bot_to_download_mp3(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.open_bot_to_download_mp3.a09899db', lang)
    return _t('messages.user_messages.open_bot_to_download_mp3.a09899db', lang)

def unknown_data(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.unknown_data.709e425b', lang)
    return _t('messages.user_messages.unknown_data.709e425b', lang)

def callback_processing_error(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.callback_processing_error.577ed7a7', lang)
    return _t('messages.user_messages.callback_processing_error.577ed7a7', lang)

def tiktok_stat_label(stat: str, lang: str = DEFAULT_LANG):
    labels = {
        "followers": _t('messages.user_messages.tiktok_stat_label.a145ab34', lang),
        "videos": _t('messages.user_messages.tiktok_stat_label.c9a96394', lang),
        "likes": _t('messages.user_messages.tiktok_stat_label.cca0662f', lang),
        "views": _t('messages.user_messages.tiktok_stat_label.69c40459', lang),
        "comments": _t('messages.user_messages.tiktok_stat_label.355f79f2', lang),
        "shares": _t('messages.user_messages.tiktok_stat_label.d4ea333d', lang),
    }
    return labels.get(stat, stat)



# ============================================================
# /trad
# ============================================================

def translation_usage(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return (
            _t('messages.user_messages.translation_usage.de61c325', lang)
        )

    return (
        _t('messages.user_messages.translation_usage.de61c325', lang)
    )


def translation_invalid_language(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return (
            _t('messages.user_messages.translation_invalid_language.db2baa18', lang)
        )
    return (
        _t('messages.user_messages.translation_invalid_language.db2baa18', lang)
    )


def translation_text_too_long(limit: int, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.translation_text_too_long.ec745c0c', lang, p0=limit)
    return _t('messages.user_messages.translation_text_too_long.ec745c0c', lang, p0=limit)


def translation_service_unavailable(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.translation_service_unavailable.37e7194a', lang)
    return _t('messages.user_messages.translation_service_unavailable.37e7194a', lang)


def translation_timeout(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.translation_timeout.43852865', lang)
    return _t('messages.user_messages.translation_timeout.43852865', lang)


def translation_failed(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.translation_failed.89834e7d', lang)
    return _t('messages.user_messages.translation_failed.89834e7d', lang)


def translation_error(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.translation_error.25624904', lang)
    return _t('messages.user_messages.translation_error.25624904', lang)



def translation_result(
    text: str,
    source_lang: str,
    target_lang: str,
    bot_username: str | None = None,
):
    source = html.escape(
        str(source_lang or "AUTO").replace("_", "-").upper()
    )
    target = html.escape(
        str(target_lang or "").replace("_", "-").upper()
    )

    translated_text = html.escape(str(text or ""))

    translator_icon = (
        '<tg-emoji emoji-id="6017109689748164760">🌐</tg-emoji>'
    )

    rocket_icon = (
        '<tg-emoji emoji-id="5283080528818360566">🚀</tg-emoji>'
    )

    username = (bot_username or "").lstrip("@").strip()

    if username:
        safe_username = html.escape(username, quote=True)
        footer = (
            _t('messages.user_messages.translation_result.098e3e47', None, p0=rocket_icon, p1=safe_username)
        )
    else:
        footer = (
            _t('messages.user_messages.translation_result.5385fea9', None, p0=rocket_icon)
        )

    return (
        f"{translator_icon} | "
        f"<b>{source} ➡️ {target}</b>\n\n"
        f"{translated_text}\n\n"
        f"{footer}"
    )



# ============================================================
# /ping
# ============================================================

def ping_message(
    latency_ms: int,
    status: str,
    lang: str = DEFAULT_LANG,
    bot_username: str | None = None,
):
    is_pt = _normalize_lang(lang) == "pt"

    # Premium / Custom Emoji IDs
    pong_icon = (
        '<tg-emoji emoji-id="6320820489539295521">🏓</tg-emoji>'
    )
    ping_icon = (
        '<tg-emoji emoji-id="6321040305965505933">🧭</tg-emoji>'
    )
    status_icon = (
        '<tg-emoji emoji-id="6320953010755214903">💨</tg-emoji>'
    )
    green_icon = (
        '<tg-emoji emoji-id="6321328167558585449">🟢</tg-emoji>'
    )
    note_icon = (
        '<tg-emoji emoji-id="5408905620139028881">📝</tg-emoji>'
    )
    delete_icon = (
        '<tg-emoji emoji-id="5408832111773757273">🗑️</tg-emoji>'
    )

    rocket_icon = (
        '<tg-emoji emoji-id="5283080528818360566">🚀</tg-emoji>'
    )

    if status == "good":
        status_emoji = green_icon
        status_text = _t('messages.user_messages.ping_message.c939327c', lang)

    elif status == "fair":
        status_emoji = "🟡"
        status_text = _t('messages.user_messages.ping_message.f7b19afc', lang)

    else:
        status_emoji = "🔴"
        status_text = _t('messages.user_messages.ping_message.4b2bebbf', lang)

    if is_pt:
        note_text = (
            _t('messages.user_messages.ping_message.4bf7ae0f', lang)
        )
        delete_text = (
            _t('messages.user_messages.ping_message.6df83bbf', lang)
        )
        status_label = _t('messages.user_messages.ping_message.920e413c', lang)
    else:
        note_text = (
            _t('messages.user_messages.ping_message.4bf7ae0f', lang)
        )
        delete_text = (
            _t('messages.user_messages.ping_message.6df83bbf', lang)
        )
        status_label = _t('messages.user_messages.ping_message.920e413c', lang)

    username = (bot_username or "").lstrip("@").strip()

    if username:
        safe_username = html.escape(username, quote=True)
        footer = (
            _t('messages.user_messages.translation_result.098e3e47', lang, p0=rocket_icon, p1=safe_username)
        )
    else:
        footer = (
            _t('messages.user_messages.translation_result.5385fea9', lang, p0=rocket_icon)
        )

    return (
        _t('messages.user_messages.ping_message.5f0c2aa6', lang, p0=pong_icon, p1=ping_icon, p2=int(latency_ms), p3=status_icon, p4=status_label, p5=status_emoji, p6=status_text, p7=note_icon, p8='Nota' if is_pt else 'Note', p9=html.escape(note_text), p10=delete_icon, p11=html.escape(delete_text), p12=footer)
    )


def ping_unavailable(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.ping_unavailable.8196132a', lang)
    return _t('messages.user_messages.ping_unavailable.8196132a', lang)


def guest_no_link_title(lang: str = DEFAULT_LANG) -> str:
    return (
        _t('messages.user_messages.guest_no_link_title.46f00e42', lang)
    )


def guest_no_link_description(lang: str = DEFAULT_LANG) -> str:
    return (
        _t('messages.user_messages.guest_no_link_description.2a8613ae', lang)
    )


def guest_no_link_message(lang: str = DEFAULT_LANG) -> str:
    if _normalize_lang(lang) == "pt":
        return (
            _t('messages.user_messages.guest_no_link_message.c98f3840', lang)
        )
    return (
        _t('messages.user_messages.guest_no_link_message.c98f3840', lang)
    )


def guest_open_private_button(lang: str = DEFAULT_LANG) -> str:
    return (
        _t('messages.user_messages.guest_open_private_button.4c9393ea', lang)
    )


def guest_unavailable_title(
    service_name: str,
    lang: str = DEFAULT_LANG,
) -> str:
    service = service_name or _t('messages.user_messages.guest_unavailable_title.e33214d1', lang)
    if _normalize_lang(lang) == "pt":
        return _t('messages.user_messages.guest_unavailable_title.44aaa969', lang, p0=service)
    return _t('messages.user_messages.guest_unavailable_title.44aaa969', lang, p0=service)


def guest_unavailable_description(lang: str = DEFAULT_LANG) -> str:
    return (
        _t('messages.user_messages.guest_unavailable_description.a9ad5227', lang)
    )


def guest_unavailable_message(
    service_name: str,
    lang: str = DEFAULT_LANG,
) -> str:
    service = service_name or _t('messages.user_messages.guest_unavailable_message.cf3bfbe9', lang)
    if _normalize_lang(lang) == "pt":
        return (
            _t('messages.user_messages.guest_unavailable_message.72f01872', lang, p0=service)
        )
    return (
        _t('messages.user_messages.guest_unavailable_message.72f01872', lang, p0=service)
    )


def guest_slow_down_title(lang: str = DEFAULT_LANG) -> str:
    return (
        _t('messages.user_messages.guest_slow_down_title.802600d1', lang)
    )


def guest_slow_down_description(lang: str = DEFAULT_LANG) -> str:
    return (
        _t('messages.user_messages.guest_slow_down_description.ce2d7955', lang)
    )


def guest_slow_down_message(lang: str = DEFAULT_LANG) -> str:
    return (
        _t('messages.user_messages.guest_slow_down_message.7c65c576', lang)
    )


def guest_restricted_service_name(lang: str = DEFAULT_LANG) -> str:
    return (
        _t('messages.user_messages.guest_restricted_service_name.60bbf043', lang)
    )


def channel_subscription_required(lang: str = DEFAULT_LANG) -> str:
    if _normalize_lang(lang) == "pt":
        return (
            _t('messages.user_messages.channel_subscription_required.c5f0e4af', lang)
        )

    return (
        _t('messages.user_messages.channel_subscription_required.c5f0e4af', lang)
    )


def media_transfer_status(platform: str, *, uploading: bool = False, lang: str = DEFAULT_LANG):
    emoji = (
        '<tg-emoji emoji-id="5866355487255039002">🚀</tg-emoji>' if uploading
        else '<tg-emoji emoji-id="5435977112538324435">⬇️</tg-emoji>'
    )
    platform = "X/Twitter" if platform in {"Twitter", "X / Twitter", "X"} else platform
    label = html.escape(platform)
    if _normalize_lang(lang) == "pt":
        action = _t('messages.user_messages.media_transfer_status.2eabdcaf', lang) if uploading else _t('messages.user_messages.media_transfer_status.cc5d009a', lang)
    else:
        action = _t('messages.user_messages.media_transfer_status.2eabdcaf', lang) if uploading else _t('messages.user_messages.media_transfer_status.cc5d009a', lang)
    return f"{emoji} {label}: {action}"


def download_failure_message(reason='unknown', *, platform=None, reference=None, lang=None, diagnostic=None):
    """Show the original extractor error as code, without a generated JSON wrapper."""
    from utils.download_errors import REASONS
    reason = reason if reason in REASONS else 'unknown'
    label = str(platform) if platform else _t('download_error.generic_platform', lang)
    header = 'download_error.delivery_header' if reason == 'delivery' else 'download_error.header'
    detail = diagnostic.message if diagnostic and diagnostic.message else _t('download_error.reason.' + reason, lang)
    return (_t(header, lang, platform=html.escape(label)) +
            '\n\n<pre><code>' + html.escape(detail) + '</code></pre>')
