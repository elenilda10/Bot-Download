from i18n import get_locale as _get_locale
from i18n import normalize_locale as _normalize_locale
from i18n import t as _t
from urllib.parse import quote
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

DEFAULT_LANG = None


def _normalize_lang(lang: str | None) -> str:
    return _normalize_locale(lang if lang is not None else _get_locale())


def language_keyboard(current_lang: str | None = DEFAULT_LANG) -> InlineKeyboardMarkup:
    from i18n import LOCALE_NAMES, SUPPORTED_LOCALES
    locale = _normalize_lang(current_lang)
    buttons = [InlineKeyboardButton(
        text=("✅ " if code == locale else "") + LOCALE_NAMES[code],
        callback_data="set_lang:" + code,
    ) for code in SUPPORTED_LOCALES]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text=_t('language.back', locale), callback_data='back_to_settings')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def start_keyboard(
    bot_username: str | None = None,
    ref_user_id: int | None = None,
    lang: str = DEFAULT_LANG,
) -> InlineKeyboardMarkup:
    username = bot_username or "SaveVidDLBot"
    base_link = f"https://t.me/{username}"

    share_text = (
        _t('keyboards.inline_keyboards.start_keyboard.4f709810', lang)
    )
    share_url = f"https://t.me/share/url?url={quote(base_link)}&text={quote(share_text)}"
    add_to_group_url = f"https://t.me/{username}?startgroup=true"

    txt_inline = _t('keyboards.inline_keyboards.start_keyboard.27958719', lang)
    txt_settings = _t('keyboards.inline_keyboards.start_keyboard.ed2a3ac4', lang)
    txt_share = _t('keyboards.inline_keyboards.start_keyboard.14a43343', lang)
    txt_add = _t('keyboards.inline_keyboards.start_keyboard.1ead3001', lang)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=txt_inline, switch_inline_query_current_chat=""
                ),
                InlineKeyboardButton(
                    text=txt_settings, callback_data="back_to_settings"
                ),
            ],
            [
                InlineKeyboardButton(text=txt_share, url=share_url),
                InlineKeyboardButton(text=txt_add, url=add_to_group_url),
            ],
        ]
    )


def cancel_keyboard(lang: str = DEFAULT_LANG) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=_t('keyboards.inline_keyboards.cancel_keyboard.5387009b', lang),
        callback_data="cancel_action",
    )
    return builder.as_markup()


def format_number(value: int) -> str | None:
    if value is None:
        return None
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


FIELD_CATEGORY_MAP = {
    "video_quality": "media",
    "as_document": "media",
    "audio_format": "media",
    "captions": "appearance",
    "info_buttons": "appearance",
    "audio_button": "appearance",
    "music_button": "appearance",
    "file_button": "appearance",
    "url_button": "appearance",
    "delete_message": "chat",
}


def return_settings_categories_keyboard(
    lang: str = DEFAULT_LANG,
) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=_t('keyboards.inline_keyboards.return_settings_categories_keyboard.8a25df2a', lang),
                callback_data="settings_cat:media",
            )
        ],
        [
            InlineKeyboardButton(
                text=_t('keyboards.inline_keyboards.return_settings_categories_keyboard.3488f6e3', lang),
                callback_data="settings_cat:appearance",
            )
        ],
        [
            InlineKeyboardButton(
                text=_t('keyboards.inline_keyboards.return_settings_categories_keyboard.321c8d6b', lang),
                callback_data="settings_cat:chat",
            )
        ],
        [
            InlineKeyboardButton(
                text=_t('keyboards.inline_keyboards.return_settings_categories_keyboard.fd617ec9', lang),
                callback_data="settings_lang",
            )
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def return_category_settings_keyboard(
    category: str, lang: str = DEFAULT_LANG
) -> InlineKeyboardMarkup:

    if category == "media":
        fields = [
            (
                _t('keyboards.inline_keyboards.return_category_settings_keyboard.212dd6c3', lang),
                "video_quality",
            ),
            (
                _t('keyboards.inline_keyboards.return_category_settings_keyboard.61d6b99f', lang),
                "as_document",
            ),
            (
                _t('keyboards.inline_keyboards.return_category_settings_keyboard.c35ace5e', lang),
                "audio_format",
            ),
        ]
    elif category == "appearance":
        fields = [
            (_t('keyboards.inline_keyboards.return_category_settings_keyboard.c1a8466f', lang), "captions"),
            (_t('keyboards.inline_keyboards.return_category_settings_keyboard.1fade508', lang), "info_buttons"),
            (_t('keyboards.inline_keyboards.return_category_settings_keyboard.6d4e17cf', lang), "audio_button"),
            (_t('keyboards.inline_keyboards.return_category_settings_keyboard.2270c082', lang), "music_button"),
            (_t('keyboards.inline_keyboards.return_category_settings_keyboard.ae6c66c0', lang), "file_button"),
            (_t('keyboards.inline_keyboards.return_category_settings_keyboard.225133b6', lang), "url_button"),
        ]
    else:
        fields = [
            (
                _t('keyboards.inline_keyboards.return_category_settings_keyboard.72a83146', lang),
                "delete_message",
            ),
        ]

    buttons = [
        [InlineKeyboardButton(text=text, callback_data=f"settings:{field}")]
        for text, field in fields
    ]
    buttons.append(
        [
            InlineKeyboardButton(
                text=_t('keyboards.inline_keyboards.return_category_settings_keyboard.d3a31fe9', lang),
                callback_data="back_to_settings",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def return_field_keyboard(
    field: str, value: str | None, lang: str = DEFAULT_LANG
):
    val = (value or "").strip().lower()
    cat = FIELD_CATEGORY_MAP.get(field, "media")
    back_cb = f"settings_cat:{cat}"
    is_pt = _normalize_lang(lang) == "pt"
    back_text = _t('keyboards.inline_keyboards.return_field_keyboard.5dd22918', lang)

    if field == "video_quality":
        current = val or "best"
        lbl_best = _t('keyboards.inline_keyboards.return_field_keyboard.0de92430', lang)
        lbl_bal = _t('keyboards.inline_keyboards.return_field_keyboard.4ec9edf5', lang)
        lbl_sav = _t('keyboards.inline_keyboards.return_field_keyboard.62d289e2', lang)

        opt_best = f"✅ 🏆 {lbl_best}" if current == "best" else f"🏆 {lbl_best}"
        opt_bal = f"✅ ⚖️ {lbl_bal}" if current == "balanced" else f"⚖️ {lbl_bal}"
        opt_saver = f"✅ ⚡ {lbl_sav}" if current == "saver" else f"⚡ {lbl_sav}"

        buttons = [
            [
                InlineKeyboardButton(
                    text=opt_best, callback_data="setting:video_quality:best"
                )
            ],
            [
                InlineKeyboardButton(
                    text=opt_bal, callback_data="setting:video_quality:balanced"
                )
            ],
            [
                InlineKeyboardButton(
                    text=opt_saver, callback_data="setting:video_quality:saver"
                )
            ],
            [InlineKeyboardButton(text=back_text, callback_data=back_cb)],
        ]
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    if field == "audio_format":
        current = val or "mp3"
        lbl_mp3 = _t('keyboards.inline_keyboards.return_field_keyboard.33f3d5ca', lang)
        lbl_m4a = _t('keyboards.inline_keyboards.return_field_keyboard.122722ab', lang)
        lbl_flac = _t('keyboards.inline_keyboards.return_field_keyboard.6a6947ff', lang)

        opt_mp3 = f"✅ 🎧 {lbl_mp3}" if current == "mp3" else f"🎧 {lbl_mp3}"
        opt_m4a = f"✅ 📱 {lbl_m4a}" if current == "m4a" else f"📱 {lbl_m4a}"
        opt_best = f"✅ 🎼 {lbl_flac}" if current == "best" else f"🎼 {lbl_flac}"

        buttons = [
            [
                InlineKeyboardButton(
                    text=opt_mp3, callback_data="setting:audio_format:mp3"
                )
            ],
            [
                InlineKeyboardButton(
                    text=opt_m4a, callback_data="setting:audio_format:m4a"
                )
            ],
            [
                InlineKeyboardButton(
                    text=opt_best, callback_data="setting:audio_format:best"
                )
            ],
            [InlineKeyboardButton(text=back_text, callback_data=back_cb)],
        ]
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    is_enabled = val == "on"
    if is_pt:
        status_text = (
            _t('keyboards.inline_keyboards.return_field_keyboard.0eddd19c', lang) if is_enabled else _t('keyboards.inline_keyboards.return_field_keyboard.72f0ee85', lang)
        )
        action_text = _t('keyboards.inline_keyboards.return_field_keyboard.0b5bc6d9', lang) if is_enabled else _t('keyboards.inline_keyboards.return_field_keyboard.aae5cba9', lang)
    else:
        status_text = (
            _t('keyboards.inline_keyboards.return_field_keyboard.0eddd19c', lang) if is_enabled else _t('keyboards.inline_keyboards.return_field_keyboard.72f0ee85', lang)
        )
        action_text = _t('keyboards.inline_keyboards.return_field_keyboard.0b5bc6d9', lang) if is_enabled else _t('keyboards.inline_keyboards.return_field_keyboard.aae5cba9', lang)

    next_value = "off" if is_enabled else "on"
    buttons = [
        [InlineKeyboardButton(text=status_text, callback_data="noop")],
        [
            InlineKeyboardButton(
                text=action_text, callback_data=f"setting:{field}:{next_value}"
            )
        ],
        [InlineKeyboardButton(text=back_text, callback_data=back_cb)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def return_settings_keyboard(lang: str = DEFAULT_LANG):
    return return_settings_categories_keyboard(lang=lang)


def stats_keyboard(
    current_period: str = "Week", mode: str = "total", lang: str = DEFAULT_LANG
):
    is_pt = _normalize_lang(lang) == "pt"
    periods_data = [
        (_t("stats.period.Week", lang), "Week"),
        (_t("stats.period.Month", lang), "Month"),
        (_t("stats.period.Year", lang), "Year"),
    ]
    period_buttons = [
        InlineKeyboardButton(
            text=f"[{label}]" if key == current_period else label,
            callback_data=f"stats:{key}:{mode}",
        )
        for label, key in periods_data
    ]

    toggle_target = "split" if mode == "total" else "total"
    if is_pt:
        toggle_label = (
            _t('keyboards.inline_keyboards.stats_keyboard.5413cca6', lang) if mode == "total" else _t('keyboards.inline_keyboards.stats_keyboard.3563ec14', lang)
        )
    else:
        toggle_label = _t('keyboards.inline_keyboards.stats_keyboard.5413cca6', lang) if mode == "total" else _t('keyboards.inline_keyboards.stats_keyboard.3563ec14', lang)

    buttons = [
        period_buttons,
        [
            InlineKeyboardButton(
                text=toggle_label,
                callback_data=f"stats:{current_period}:{toggle_target}",
            )
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_keyboard(lang: str = DEFAULT_LANG):
    txt_health = _t('keyboards.inline_keyboards.admin_keyboard.8b7c5961', lang)
    txt_runtime = _t('keyboards.inline_keyboards.admin_keyboard.aba430a5', lang)
    txt_refresh = _t('keyboards.inline_keyboards.admin_keyboard.d58ef935', lang)
    txt_active_users = _t('keyboards.inline_keyboards.admin_keyboard.12fcc45b', lang)
    txt_mailing = _t('keyboards.inline_keyboards.admin_keyboard.da65b090', lang)
    txt_msg_chat = _t('keyboards.inline_keyboards.admin_keyboard.2a590dea', lang)
    txt_view_log = _t('keyboards.inline_keyboards.admin_keyboard.8327ef78', lang)
    txt_del_log = _t('keyboards.inline_keyboards.admin_keyboard.b46312b3', lang)

    buttons = [
        [
            InlineKeyboardButton(text=txt_health, callback_data="admin_ops"),
            InlineKeyboardButton(
                text=txt_runtime, callback_data="admin_runtime_storage"
            ),
        ],
        [InlineKeyboardButton(text=txt_refresh, callback_data="admin_refresh")],
        [
            InlineKeyboardButton(
                text=txt_active_users, callback_data="check_active_users"
            )
        ],
        [InlineKeyboardButton(text=txt_mailing, callback_data="send_to_all")],
        [InlineKeyboardButton(text=txt_msg_chat, callback_data="message_chat_id")],
        [
            InlineKeyboardButton(text=txt_view_log, callback_data="download_log"),
            InlineKeyboardButton(text=txt_del_log, callback_data="delete_log"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_detail_keyboard(refresh_callback: str, lang: str = DEFAULT_LANG):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t('keyboards.inline_keyboards.admin_keyboard.d58ef935', lang),
                    callback_data=refresh_callback,
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t('keyboards.inline_keyboards.return_field_keyboard.5dd22918', lang),
                    callback_data="back_to_admin",
                )
            ],
        ]
    )


def downloads_admin_keyboard(
    can_cleanup: bool = True,
    refresh_callback: str = "admin_downloads",
    lang: str = DEFAULT_LANG,
):
    buttons = [
        [
            InlineKeyboardButton(
                text=_t('keyboards.inline_keyboards.admin_keyboard.d58ef935', lang),
                callback_data=refresh_callback,
            )
        ]
    ]
    if can_cleanup:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=_t('keyboards.inline_keyboards.downloads_admin_keyboard.2d1ff93c', lang),
                    callback_data="admin_cleanup_downloads",
                )
            ]
        )
    buttons.append(
        [
            InlineKeyboardButton(
                text=_t('keyboards.inline_keyboards.return_field_keyboard.5dd22918', lang),
                callback_data="back_to_admin",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def return_back_to_admin_keyboard(lang: str = DEFAULT_LANG):
    back_button = [
        [
            InlineKeyboardButton(
                text=_t('keyboards.inline_keyboards.return_field_keyboard.5dd22918', lang),
                callback_data="back_to_admin",
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=back_button)


def start_private_chat_keyboard(bot_username: str, lang: str = DEFAULT_LANG):
    url = f"https://t.me/{bot_username}?start=from_group"
    button = [
        [
            InlineKeyboardButton(
                text=_t('keyboards.inline_keyboards.start_private_chat_keyboard.da1fac2e', lang),
                url=url,
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=button)


def return_audio_download_keyboard(platform, url, lang: str = DEFAULT_LANG):
    audio_button = [
        [
            InlineKeyboardButton(
                text=_t('handlers.facebook._facebook_keyboard.2432fa18', lang),
                callback_data=f"{platform}_audio_{url}",
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=audio_button)


def inline_send_video_keyboard(
    token: str, lang: str = DEFAULT_LANG
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t('keyboards.inline_keyboards.inline_send_video_keyboard.3375997f', lang),
                    callback_data=f"inline:tiktok:{token}",
                )
            ]
        ]
    )


def inline_send_media_keyboard(text: str, callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=callback_data)]
        ]
    )


def return_user_info_keyboard(nickname, followers, videos, likes, url):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=nickname, url=url))

    row1 = []
    if followers is not None:
        row1.append(
            InlineKeyboardButton(
                text=f"👥 {format_number(followers)}",
                callback_data=f"followers_{format_number(followers)}",
            )
        )
    if videos is not None:
        row1.append(
            InlineKeyboardButton(
                text=f"🎬 {format_number(videos)}",
                callback_data=f"videos_{format_number(videos)}",
            )
        )
    if likes is not None:
        row1.append(
            InlineKeyboardButton(
                text=f"❤️ {format_number(likes)}",
                callback_data=f"likes_{format_number(likes)}",
            )
        )

    if row1:
        builder.row(*row1)
    return builder.as_markup()


def return_video_info_keyboard(
    views,
    likes,
    comments,
    shares,
    music_play_url,
    video_url,
    user_settings,
    audio_callback_data: str | None = None,
    file_callback_data: str | None = None,
    lang: str = DEFAULT_LANG,
    has_video: bool = True,
    platform: str | None = None,
):
    builder = InlineKeyboardBuilder()

    if user_settings.get("info_buttons") == "on":
        row1 = []
        if views is not None:
            formatted_views = format_number(views)
            row1.append(
                InlineKeyboardButton(
                    text=f"👁 {formatted_views}",
                    callback_data=f"views_{formatted_views}",
                )
            )
        if likes is not None:
            formatted_likes = format_number(likes)
            row1.append(
                InlineKeyboardButton(
                    text=f"❤️ {formatted_likes}",
                    callback_data=f"likes_{formatted_likes}",
                )
            )
        if comments is not None:
            formatted_comments = format_number(comments)
            row1.append(
                InlineKeyboardButton(
                    text=f"💬 {formatted_comments}",
                    callback_data=f"comments_{formatted_comments}",
                )
            )
        if shares is not None:
            formatted_shares = format_number(shares)
            row1.append(
                InlineKeyboardButton(
                    text=f"🔁 {formatted_shares}",
                    callback_data=f"shares_{formatted_shares}",
                )
            )
        if row1:
            builder.row(*row1)

    # Condicionado às configurações do usuário (padrão 'off') e só se for vídeo
    # Shazam somente para vídeos do Instagram e TikTok.
    # Nunca exibir em imagens ou em outras plataformas.
    music_supported = (platform or "").lower() in {"instagram", "tiktok"}
    if has_video and music_supported and user_settings.get("music_button", "off") == "on":
        builder.row(
            InlineKeyboardButton(
                text=_t('keyboards.inline_keyboards.return_video_info_keyboard.44d7f8be', lang),
                callback_data="identify_music",
            )
        )

    if user_settings.get("audio_button") == "on" and audio_callback_data:
        builder.row(
            InlineKeyboardButton(
                text=_t('handlers.facebook._facebook_keyboard.2432fa18', lang),
                callback_data=audio_callback_data,
            )
        )

    if (
        user_settings.get("file_button") == "on"
        and user_settings.get("as_document") != "on"
        and file_callback_data
    ):
        builder.row(
            InlineKeyboardButton(
                text=_t('handlers.facebook._facebook_keyboard.d7fd5aab', lang),
                callback_data=file_callback_data,
            )
        )

    if user_settings.get("url_button") == "on" and video_url:
        builder.row(InlineKeyboardButton(text=_t('keyboards.inline_keyboards.return_video_info_keyboard.e1a659b6', lang), url=video_url))

    return builder.as_markup()


def _stats_keyboard_legacy_bottom(
    current_period: str = "Week", mode: str = "total", lang: str = DEFAULT_LANG
):
    is_pt = _normalize_lang(lang) == "pt"
    periods_data = [
        (_t("stats.period.Week", lang), "Week"),
        (_t("stats.period.Month", lang), "Month"),
        (_t("stats.period.Year", lang), "Year"),
    ]
    period_buttons = [
        InlineKeyboardButton(
            text=f"{'· ' if key == current_period else ''}{label}",
            callback_data=f"stats:{key}:{mode}",
        )
        for label, key in periods_data
    ]

    toggle_target = "split" if mode == "total" else "total"
    if is_pt:
        toggle_label = _t('keyboards.inline_keyboards._stats_keyboard_legacy_bottom.f91511a7', lang, p0='Ativada' if mode == 'split' else 'Desativada')
    else:
        toggle_label = _t('keyboards.inline_keyboards._stats_keyboard_legacy_bottom.f91511a7', lang, p0='On' if mode == 'split' else 'Off')

    buttons = [
        period_buttons,
        [
            InlineKeyboardButton(
                text=toggle_label,
                callback_data=f"stats:{current_period}:{toggle_target}",
            )
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def channel_subscription_keyboard(lang: str = DEFAULT_LANG) -> InlineKeyboardMarkup:

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t('keyboards.inline_keyboards.channel_subscription_keyboard.c920ce5f', lang),
                    url="https://t.me/NexoHubDev",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t('keyboards.inline_keyboards.channel_subscription_keyboard.dfd4cbcf', lang),
                    callback_data="channel_sub:verify",
                )
            ],
        ]
    )
