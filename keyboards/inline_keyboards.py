from urllib.parse import quote
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

DEFAULT_LANG = "en"


def _normalize_lang(lang: str | None) -> str:
    if not lang:
        return DEFAULT_LANG
    lang = lang.lower().strip()
    return "pt" if lang.startswith("pt") else "en"


def start_keyboard(
    bot_username: str | None = None,
    ref_user_id: int | None = None,
    lang: str = DEFAULT_LANG,
) -> InlineKeyboardMarkup:
    username = bot_username or "SaveVidDLBot"
    base_link = f"https://t.me/{username}"

    is_pt = _normalize_lang(lang) == "pt"

    share_text = (
        "Bot rápido para baixar vídeos do Instagram, TikTok, YouTube e mais!"
        if is_pt
        else "Fast downloader bot for Instagram, TikTok, YouTube & more!"
    )
    share_url = f"https://t.me/share/url?url={quote(base_link)}&text={quote(share_text)}"
    add_to_group_url = f"https://t.me/{username}?startgroup=true"

    txt_inline = "⚡ Modo inline" if is_pt else "⚡ Try inline"
    txt_settings = "⚙️ Configurações" if is_pt else "⚙️ Settings"
    txt_share = "🚀 Compartilhar bot" if is_pt else "🚀 Share bot"
    txt_add = "➕ Adicionar a grupo" if is_pt else "➕ Add to group"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=txt_inline, switch_inline_query_current_chat=""),
                InlineKeyboardButton(text=txt_settings, callback_data="back_to_settings"),
            ],
            [
                InlineKeyboardButton(text=txt_share, url=share_url),
                InlineKeyboardButton(text=txt_add, url=add_to_group_url),
            ],
        ]
    )


def cancel_keyboard(lang: str = DEFAULT_LANG) -> InlineKeyboardMarkup:
    is_pt = _normalize_lang(lang) == "pt"
    builder = InlineKeyboardBuilder()
    builder.button(
        text="❌ Cancelar" if is_pt else "❌ Cancel", callback_data="cancel_action"
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
    "file_button": "appearance",
    "url_button": "appearance",
    "delete_message": "chat",
}


def return_settings_categories_keyboard(
    lang: str = DEFAULT_LANG,
) -> InlineKeyboardMarkup:
    is_pt = _normalize_lang(lang) == "pt"
    buttons = [
        [
            InlineKeyboardButton(
                text="🎬 Mídia & Qualidade" if is_pt else "🎬 Media & Quality",
                callback_data="settings_cat:media",
            )
        ],
        [
            InlineKeyboardButton(
                text="🎨 Aparência & Botões" if is_pt else "🎨 Appearance & Buttons",
                callback_data="settings_cat:appearance",
            )
        ],
        [
            InlineKeyboardButton(
                text="💬 Chat & Limpeza" if is_pt else "💬 Chat & Clean-up",
                callback_data="settings_cat:chat",
            )
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def return_category_settings_keyboard(
    category: str, lang: str = DEFAULT_LANG
) -> InlineKeyboardMarkup:
    is_pt = _normalize_lang(lang) == "pt"

    if category == "media":
        fields = [
            ("🎬 Qualidade do Vídeo" if is_pt else "🎬 Video Quality", "video_quality"),
            ("📄 Enviar como Arquivo" if is_pt else "📄 Send as File", "as_document"),
            ("🎵 Formato de Áudio" if is_pt else "🎵 Audio Format", "audio_format"),
        ]
    elif category == "appearance":
        fields = [
            ("📝 Legendas" if is_pt else "📝 Descriptions", "captions"),
            ("ℹ️ Botões de Info" if is_pt else "ℹ️ Info Buttons", "info_buttons"),
            ("🎧 Botão MP3" if is_pt else "🎧 MP3 Button", "audio_button"),
            ("📄 Botão de Arquivo" if is_pt else "📄 File Button", "file_button"),
            ("🔗 Botão de URL" if is_pt else "🔗 URL Button", "url_button"),
        ]
    else:
        fields = [
            ("🗑️ Apagar Mensagens" if is_pt else "🗑️ Delete Messages", "delete_message"),
        ]

    buttons = [
        [InlineKeyboardButton(text=text, callback_data=f"settings:{field}")]
        for text, field in fields
    ]
    buttons.append(
        [
            InlineKeyboardButton(
                text="⬅️ Voltar às Categorias" if is_pt else "⬅️ Back to Categories",
                callback_data="back_to_settings",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def return_field_keyboard(field: str, value: str | None, lang: str = DEFAULT_LANG):
    val = (value or "").strip().lower()
    cat = FIELD_CATEGORY_MAP.get(field, "media")
    back_cb = f"settings_cat:{cat}"
    is_pt = _normalize_lang(lang) == "pt"
    back_text = "⬅️ Voltar" if is_pt else "⬅️ Back"

    if field == "video_quality":
        current = val or "best"
        lbl_best = "Melhor (1080p+)" if is_pt else "Best (1080p+)"
        lbl_bal = "Equilibrada (720p)" if is_pt else "Balanced (720p)"
        lbl_sav = "Econômica (480p)" if is_pt else "Data Saver (480p)"

        opt_best = f"✅ 🏆 {lbl_best}" if current == "best" else f"🏆 {lbl_best}"
        opt_bal = f"✅ ⚖️ {lbl_bal}" if current == "balanced" else f"⚖️ {lbl_bal}"
        opt_saver = f"✅ ⚡ {lbl_sav}" if current == "saver" else f"⚡ {lbl_sav}"

        buttons = [
            [InlineKeyboardButton(text=opt_best, callback_data="setting:video_quality:best")],
            [InlineKeyboardButton(text=opt_bal, callback_data="setting:video_quality:balanced")],
            [InlineKeyboardButton(text=opt_saver, callback_data="setting:video_quality:saver")],
            [InlineKeyboardButton(text=back_text, callback_data=back_cb)],
        ]
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    if field == "audio_format":
        current = val or "mp3"
        lbl_mp3 = "Áudio MP3" if is_pt else "MP3 Audio"
        lbl_m4a = "M4A (AAC)"
        lbl_flac = "FLAC / Original"

        opt_mp3 = f"✅ 🎧 {lbl_mp3}" if current == "mp3" else f"🎧 {lbl_mp3}"
        opt_m4a = f"✅ 📱 {lbl_m4a}" if current == "m4a" else f"📱 {lbl_m4a}"
        opt_best = f"✅ 🎼 {lbl_flac}" if current == "best" else f"🎼 {lbl_flac}"

        buttons = [
            [InlineKeyboardButton(text=opt_mp3, callback_data="setting:audio_format:mp3")],
            [InlineKeyboardButton(text=opt_m4a, callback_data="setting:audio_format:m4a")],
            [InlineKeyboardButton(text=opt_best, callback_data="setting:audio_format:best")],
            [InlineKeyboardButton(text=back_text, callback_data=back_cb)],
        ]
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    is_enabled = val == "on"
    if is_pt:
        status_text = "🟢 Atualmente ATIVADO" if is_enabled else "🔴 Atualmente DESATIVADO"
        action_text = "🔴 DESATIVAR" if is_enabled else "🟢 ATIVAR"
    else:
        status_text = "🟢 Currently ON" if is_enabled else "🔴 Currently OFF"
        action_text = "🔴 Turn OFF" if is_enabled else "🟢 Turn ON"

    next_value = "off" if is_enabled else "on"

    buttons = [
        [InlineKeyboardButton(text=status_text, callback_data="noop")],
        [InlineKeyboardButton(text=action_text, callback_data=f"setting:{field}:{next_value}")],
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
        ("Semana" if is_pt else "Week", "Week"),
        ("Mês" if is_pt else "Month", "Month"),
        ("Ano" if is_pt else "Year", "Year"),
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
            "Ver: Por plataforma" if mode == "total" else "Ver: Visão Geral"
        )
    else:
        toggle_label = "View: By platform" if mode == "total" else "View: Overall"

    buttons = [
        period_buttons,
        [InlineKeyboardButton(text=toggle_label, callback_data=f"stats:{current_period}:{toggle_target}")],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_keyboard(lang: str = DEFAULT_LANG):
    is_pt = _normalize_lang(lang) == "pt"

    txt_health = "🩺 Saúde" if is_pt else "🩺 Health"
    txt_runtime = "📦 Armazenamento" if is_pt else "📦 Runtime"
    txt_refresh = "🔄 Atualizar" if is_pt else "🔄 Refresh"
    txt_active_users = "👥 Checar Usuários Ativos" if is_pt else "👥 Check Active Users"
    txt_mailing = "📬 Envio em Massa" if is_pt else "📬 Mailing"
    txt_msg_chat = "✉️ Mensagem por ID do Chat" if is_pt else "✉️ Message by Chat ID"
    txt_view_log = "📄 Ver Log" if is_pt else "📄 View Log"
    txt_del_log = "🗑️ Apagar Log" if is_pt else "🗑️ Delete Log"

    buttons = [
        [
            InlineKeyboardButton(text=txt_health, callback_data="admin_ops"),
            InlineKeyboardButton(text=txt_runtime, callback_data="admin_runtime_storage"),
        ],
        [InlineKeyboardButton(text=txt_refresh, callback_data="admin_refresh")],
        [InlineKeyboardButton(text=txt_active_users, callback_data="check_active_users")],
        [InlineKeyboardButton(text=txt_mailing, callback_data="send_to_all")],
        [InlineKeyboardButton(text=txt_msg_chat, callback_data="message_chat_id")],
        [
            InlineKeyboardButton(text=txt_view_log, callback_data="download_log"),
            InlineKeyboardButton(text=txt_del_log, callback_data="delete_log"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_detail_keyboard(refresh_callback: str, lang: str = DEFAULT_LANG):
    is_pt = _normalize_lang(lang) == "pt"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Atualizar" if is_pt else "🔄 Refresh",
                    callback_data=refresh_callback,
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Voltar" if is_pt else "⬅️ Back",
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
    is_pt = _normalize_lang(lang) == "pt"
    buttons = [
        [
            InlineKeyboardButton(
                text="🔄 Atualizar" if is_pt else "🔄 Refresh",
                callback_data=refresh_callback,
            )
        ]
    ]
    if can_cleanup:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="🧹 Limpar downloads" if is_pt else "🧹 Clean downloads",
                    callback_data="admin_cleanup_downloads",
                )
            ]
        )
    buttons.append(
        [
            InlineKeyboardButton(
                text="⬅️ Voltar" if is_pt else "⬅️ Back",
                callback_data="back_to_admin",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def return_back_to_admin_keyboard(lang: str = DEFAULT_LANG):
    is_pt = _normalize_lang(lang) == "pt"
    back_button = [
        [
            InlineKeyboardButton(
                text="⬅️ Voltar" if is_pt else "⬅️ Back",
                callback_data="back_to_admin",
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=back_button)


def start_private_chat_keyboard(bot_username: str, lang: str = DEFAULT_LANG):
    is_pt = _normalize_lang(lang) == "pt"
    url = f"https://t.me/{bot_username}?start=from_group"
    button = [
        [
            InlineKeyboardButton(
                text="💬 Abrir conversa com o bot" if is_pt else "💬 Open bot chat",
                url=url,
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=button)


def return_audio_download_keyboard(platform, url, lang: str = DEFAULT_LANG):
    is_pt = _normalize_lang(lang) == "pt"
    audio_button = [
        [
            InlineKeyboardButton(
                text="🎧 Baixar MP3" if is_pt else "🎧 Download MP3",
                callback_data=f"{platform}_audio_{url}",
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=audio_button)


def inline_send_video_keyboard(
    token: str, lang: str = DEFAULT_LANG
) -> InlineKeyboardMarkup:
    is_pt = _normalize_lang(lang) == "pt"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Enviar vídeo inline" if is_pt else "Send video inline",
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
):
    builder = InlineKeyboardBuilder()
    is_pt = _normalize_lang(lang) == "pt"

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

    if user_settings.get("audio_button") == "on" and audio_callback_data:
        builder.row(
            InlineKeyboardButton(
                text="🎧 Baixar MP3" if is_pt else "🎧 Download MP3",
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
                text="📄 Baixar Arquivo" if is_pt else "📄 Download File",
                callback_data=file_callback_data,
            )
        )

    if user_settings.get("url_button") == "on" and video_url:
        builder.row(InlineKeyboardButton(text="🔗 URL", url=video_url))

    return builder.as_markup()


def _stats_keyboard_legacy_bottom(
    current_period: str = "Week", mode: str = "total", lang: str = DEFAULT_LANG
):
    is_pt = _normalize_lang(lang) == "pt"

    periods_data = [
        ("Semana" if is_pt else "Week", "Week"),
        ("Mês" if is_pt else "Month", "Month"),
        ("Ano" if is_pt else "Year", "Year"),
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
        toggle_label = f"Divisão por plataforma: {'Ativada' if mode == 'split' else 'Desativada'}"
    else:
        toggle_label = f"Split view: {'On' if mode == 'split' else 'Off'}"

    buttons = [
        period_buttons,
        [InlineKeyboardButton(text=toggle_label, callback_data=f"stats:{current_period}:{toggle_target}")],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)
