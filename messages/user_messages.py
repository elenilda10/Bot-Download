import html

DEFAULT_LANG = "en"


def _normalize_lang(lang: str | None) -> str:
    if not lang:
        return DEFAULT_LANG
    lang = lang.lower().strip()
    return "pt" if lang.startswith("pt") else "en"


def cancel(lang: str = DEFAULT_LANG):
    return "✖️ Cancelar" if _normalize_lang(lang) == "pt" else "✖️ Cancel"


def welcome_message(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return (
            '<b>Bem-vindo ao MaxLoad <tg-emoji emoji-id="5420141555233071341">❤️</tg-emoji></b>\n\n'
            "Envie um link ou cole vários links em uma única mensagem e eu baixarei o que puder.\n\n"
            "<b>Plataformas suportadas</b>\n"
            '<tg-emoji emoji-id="5233671414023753035">📷</tg-emoji> Instagram\n'
            '<tg-emoji emoji-id="5370693953236539466">🧵</tg-emoji> Threads\n'
            '<tg-emoji emoji-id="5233597424622144804">🎵</tg-emoji> TikTok\n'
            '<tg-emoji emoji-id="5233311027612913110">▶️</tg-emoji> YouTube\n'
            '<tg-emoji emoji-id="5231309843435919433">🐦</tg-emoji> X / Twitter\n'
            '<tg-emoji emoji-id="5233448977667492819">🎧</tg-emoji> SoundCloud\n'
            '<tg-emoji emoji-id="5391001065418172193">🟢</tg-emoji> Spotify\n'
            '<tg-emoji emoji-id="5233210422298974231">📌</tg-emoji> Pinterest\n\n'
            "Use os botões abaixo para testar o modo inline, ajustar configurações ou compartilhar o bot."
        )
    return (
        '<b>Welcome to MaxLoad <tg-emoji emoji-id="5420141555233071341">❤️</tg-emoji></b>\n\n'
        "Send one link, or paste several links in one message, and I'll download what I can.\n\n"
        "<b>Supported sites</b>\n"
        '<tg-emoji emoji-id="5233671414023753035">📷</tg-emoji> Instagram\n'
        '<tg-emoji emoji-id="5370693953236539466">🧵</tg-emoji> Threads\n'
        '<tg-emoji emoji-id="5233597424622144804">🎵</tg-emoji> TikTok\n'
        '<tg-emoji emoji-id="5233311027612913110">▶️</tg-emoji> YouTube\n'
        '<tg-emoji emoji-id="5231309843435919433">🐦</tg-emoji> X / Twitter\n'
        '<tg-emoji emoji-id="5233448977667492819">🎧</tg-emoji> SoundCloud\n'
        '<tg-emoji emoji-id="5391001065418172193">🟢</tg-emoji> Spotify\n'
        '<tg-emoji emoji-id="5233210422298974231">📌</tg-emoji> Pinterest\n\n'
        "Use the buttons below to try inline mode, tune settings, or share the bot."
    )


def settings(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return (
            "<b>⚙️ Configurações</b>\n"
            "Use os botões abaixo para personalizar como os downloads são enviados. "
            "Essas alterações se aplicam apenas à sua conta."
        )
    return (
        "<b>⚙️ Settings</b>\n"
        "Use the buttons below to customize how downloads are sent. "
        "These changes apply only to your account."
    )


def settings_private_only(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "As configurações estão disponíveis apenas no chat privado. Abra uma conversa direta com o bot para alterar suas preferências."
    return "Settings are available only in private chat. Open DM with the bot to change preferences."


def get_field_text(field: str, lang: str = DEFAULT_LANG):
    lang_code = _normalize_lang(lang)
    texts_pt = {
        "captions": (
            "<b>📝 Legendas</b>\n"
            "Mostrar ou ocultar legendas da publicação na mídia baixada. "
            "Algumas fontes podem não fornecer legendas."
        ),
        "delete_message": (
            "<b>🗑️ Apagar Mensagens</b>\n"
            "Excluir automaticamente o seu link após o download ser processado."
        ),
        "info_buttons": (
            "<b>ℹ️ Botões de Informação</b>\n"
            "Ativar ou desativar botões extras de informação abaixo da mídia baixada."
        ),
        "url_button": (
            "<b>🔗 Botão do Link</b>\n"
            "Mostrar ou ocultar um botão com o link original da publicação."
        ),
        "audio_button": (
            "<b>🎧 Botão MP3</b>\n"
            "Ativar ou desativar o botão 'Baixar MP3' quando o áudio estiver disponível."
        ),
        "file_button": (
            "<b>📄 Botão de Arquivo</b>\n"
            "Mostrar ou ocultar o botão 'Baixar Arquivo' abaixo dos vídeos para obter o arquivo original sem compressão sob demanda."
        ),
        "video_quality": (
            "<b>🎬 Qualidade do Vídeo</b>\n"
            "Selecione a resolução de download de vídeo de sua preferência:\n\n"
            "• <b>Melhor (1080p+)</b>: Resolução máxima possível.\n"
            "• <b>Equilibrada (720p)</b>: Ótimo equilíbrio entre qualidade e velocidade.\n"
            "• <b>Econômica (480p)</b>: Downloads mais rápidos com menor consumo de dados."
        ),
        "as_document": (
            "<b>📄 Enviar como Arquivo</b>\n"
            "Quando ativado, vídeos e fotos serão enviados como documentos (.mp4 / .jpg) sem compressão, preservando 100% da qualidade original."
        ),
        "audio_format": (
            "<b>🎵 Formato de Áudio</b>\n"
            "Escolha o formato de áudio padrão para downloads de música:\n\n"
            "• <b>MP3</b>: Formato de áudio universal padrão.\n"
            "• <b>M4A (AAC)</b>: Formato compacto de alta qualidade para iOS e Mac.\n"
            "• <b>FLAC / Original</b>: Áudio sem perdas (lossless) quando disponível."
        ),
    }

    texts_en = {
        "captions": (
            "<b>📝 Descriptions</b>\n"
            "Show or hide post captions in downloaded media. "
            "Some sources may not provide captions."
        ),
        "delete_message": (
            "<b>🗑️ Delete Messages</b>\n"
            "Automatically remove your link once the download is handled."
        ),
        "info_buttons": (
            "<b>ℹ️ Info Buttons</b>\n"
            "Toggle additional info buttons under downloaded media."
        ),
        "url_button": (
            "<b>🔗 URL Button</b>\n"
            "Show or hide a button with the original post link."
        ),
        "audio_button": (
            "<b>🎧 MP3 Button</b>\n"
            "Toggle the Download MP3 button when audio is available."
        ),
        "file_button": (
            "<b>📄 File Button</b>\n"
            "Show or hide the Download File button under videos to get original uncompressed files on demand."
        ),
        "video_quality": (
            "<b>🎬 Video Quality</b>\n"
            "Select your preferred video download resolution:\n\n"
            "• <b>Best (1080p+)</b>: Maximum possible resolution.\n"
            "• <b>Balanced (720p)</b>: Great balance of quality and speed.\n"
            "• <b>Data Saver (480p)</b>: Faster downloads with minimal data usage."
        ),
        "as_document": (
            "<b>📄 Send as File</b>\n"
            "When enabled, videos and photos will be sent as uncompressed documents (.mp4 / .jpg) preserving 100% original quality."
        ),
        "audio_format": (
            "<b>🎵 Audio Format</b>\n"
            "Choose default audio format for music downloads:\n\n"
            "• <b>MP3</b>: Standard universal audio format.\n"
            "• <b>M4A (AAC)</b>: High quality compact format for iOS & Mac.\n"
            "• <b>FLAC / Original</b>: Uncompressed lossless audio where available."
        ),
    }

    if lang_code == "pt":
        return texts_pt.get(field, "<b>Configurações</b>\nEsta opção ainda não possui uma descrição.")
    return texts_en.get(field, "<b>Settings</b>\nThis option doesn't have a description yet.")


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

    footer = '<tg-emoji emoji-id="5283080528818360566">🚀</tg-emoji> Powered by <a href="{bot_url}">Save Vídeo DL Bot</a>'.format(bot_url=bot_url)

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


def downloading_audio_status(lang: str = DEFAULT_LANG):
    return "🎧 Baixando áudio..." if _normalize_lang(lang) == "pt" else "🎧 Downloading audio..."


def downloading_video_status(lang: str = DEFAULT_LANG):
    return "<tg-emoji emoji-id='5375464961822695044'>🎬</tg-emoji> Baixando vídeo..." if _normalize_lang(lang) == "pt" else "<tg-emoji emoji-id='5375464961822695044'>🎬</tg-emoji> Downloading video..."


def uploading_status(lang: str = DEFAULT_LANG):
    return "☁️ Enviando arquivo para o Telegram..." if _normalize_lang(lang) == "pt" else "☁️ Uploading file to Telegram..."


def retrying_again_status(next_attempt: int, total_attempts: int, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return f"Erro, tentando novamente... ({next_attempt}/{total_attempts})"
    return f"Error, trying again... ({next_attempt}/{total_attempts})"


def dm_start_required(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "<tg-emoji emoji-id='5472308992514464048'>🔒</tg-emoji> Configuração inicial necessária: abra o chat privado, envie /start e reenvie o link."
    return "<tg-emoji emoji-id='5472308992514464048'>🔒</tg-emoji> First-time setup needed: open DM, press Start, and resend the link."


def duplicate_link_processing(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "Este link já está sendo processado. Aguarde alguns segundos."
    return "This link is already being processed. Wait a few seconds."


def duplicate_link_recently_processed(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "Este link acabou de ser processado. Se ainda precisar dele, tente novamente em alguns segundos."
    return "This link was just handled. If you still need it, try again in a few seconds."


def settings_admin_only(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "Apenas administradores podem usar /settings em grupos."
    return "Only group admins can open /settings in group chats."


def invalid_settings_option(lang: str = DEFAULT_LANG):
    return "Opção de configuração inválida." if _normalize_lang(lang) == "pt" else "Invalid settings option."


def join_group(chat_title: str, lang: str = DEFAULT_LANG) -> str:
    if _normalize_lang(lang) == "pt":
        return (
            "Obrigado por me adicionar ao <b>{chat_title}</b> <tg-emoji emoji-id='5280764381804650651'>🌸</tg-emoji>\n"
            "Por favor, conceda-me <b>permissões de administrador</b> para liberar todas as funções 🔓"
        ).format(chat_title=chat_title)
    return (
        "Thanks for adding me to <b>{chat_title}</b> <tg-emoji emoji-id='5280764381804650651'>🌸</tg-emoji>\n"
        "Please grant me <b>admin rights</b> to unlock full functionality 🔓"
    ).format(chat_title=chat_title)


def admin_rights_granted(chat_title: str, lang: str = DEFAULT_LANG) -> str:
    if _normalize_lang(lang) == "pt":
        return (
            "Obrigado por conceder permissões de administrador no <b>{chat_title}</b> <tg-emoji emoji-id='5280764381804650651'>🌸</tg-emoji>\n"
            "💻 Manterei os downloads funcionando com rapidez e estabilidade."
        ).format(chat_title=chat_title)
    return (
        "Thanks for granting admin rights in <b>{chat_title}</b> <tg-emoji emoji-id='5280764381804650651'>🌸</tg-emoji>\n"
        "💻 I'll keep downloads running smoothly."
    ).format(chat_title=chat_title)


def keyboard_removed(lang: str = DEFAULT_LANG):
    return "Teclado removido." if _normalize_lang(lang) == "pt" else "Reply keyboard removed."


def tiktok_live_not_supported(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "Transmissões ao vivo (LIVE) do TikTok ainda não são suportadas. Envie o link de um post regular."
    return "TikTok LIVE streams aren't supported yet. Send a regular TikTok post link."


def delete_permission_warning(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "Falha ao apagar mensagem: sem permissão para excluir mensagens neste chat. Conceda permissão de administrador ou desative a exclusão automática nas configurações."
    return "Auto-delete failed: missing permission to delete messages in this chat. Please grant delete permissions or turn off auto-delete in settings."


def stats_temporarily_unavailable(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "Não foi possível gerar as estatísticas agora. Tente novamente mais tarde."
    return "Couldn't generate stats right now. Please try again later."


def no_queue_metrics_yet(lang: str = DEFAULT_LANG):
    return "Nenhuma métrica de fila disponível no momento." if _normalize_lang(lang) == "pt" else "No queue metrics yet."


def open_bot_for_audio(lang: str = DEFAULT_LANG):
    return "Abra o bot no privado para baixar o áudio." if _normalize_lang(lang) == "pt" else "Open the bot in private chat to download audio."


def audio_fetch_failed(lang: str = DEFAULT_LANG):
    return "Falha ao obter informações do áudio. Tente novamente mais tarde." if _normalize_lang(lang) == "pt" else "Failed to get audio info. Please try again later."


def audio_download_failed(lang: str = DEFAULT_LANG):
    return "Falha no download do áudio. Tente novamente mais tarde." if _normalize_lang(lang) == "pt" else "Audio download failed. Please try again later."


def spotify_metadata_failed(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "Não foi possível ler esta faixa do Spotify. Verifique o link e tente novamente."
    return "Couldn't read this Spotify track. Please check the link and try again."


def spotify_source_not_found(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "Não foi possível encontrar uma fonte de áudio correspondente para esta música do Spotify."
    return "Couldn't find a matching audio source for this Spotify track."


def inline_album_link_invalid(lang: str = DEFAULT_LANG):
    return "Este link de álbum expirou ou é inválido." if _normalize_lang(lang) == "pt" else "This album link is expired or invalid."


def inline_photo_title(service_name: str, lang: str = DEFAULT_LANG):
    return f"Foto do {service_name}" if _normalize_lang(lang) == "pt" else f"{service_name} Photo"


def inline_photo_description(lang: str = DEFAULT_LANG):
    return "Foto individual" if _normalize_lang(lang) == "pt" else "Single photo"


def inline_album_title(service_name: str, lang: str = DEFAULT_LANG):
    return f"Álbum do {service_name}" if _normalize_lang(lang) == "pt" else f"{service_name} Album"


def inline_album_description(lang: str = DEFAULT_LANG):
    return "Abrir álbum completo no bot" if _normalize_lang(lang) == "pt" else "Open full album in bot"


def inline_open_full_album_button(lang: str = DEFAULT_LANG):
    return "Abrir Álbum Completo" if _normalize_lang(lang) == "pt" else "Open Full Album"


def inline_photos_title(service_name: str, lang: str = DEFAULT_LANG):
    return f"Fotos do {service_name}" if _normalize_lang(lang) == "pt" else f"{service_name} Photos"


def inline_photos_not_supported(service_name: str, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return f"Fotos do {service_name} não são suportadas no modo inline."
    return f"{service_name} photos are not supported inline."


def inline_send_video_button(lang: str = DEFAULT_LANG):
    return "Enviar vídeo inline" if _normalize_lang(lang) == "pt" else "Send video inline"


def inline_send_video_prompt(service_name: str, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return f"O vídeo do {service_name} está sendo preparado...\nSe o envio não iniciar automaticamente, toque no botão abaixo."
    return f"{service_name} video is being prepared...\nIf it does not start automatically, tap the button below."


def inline_send_audio_prompt(service_name: str, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return f"O áudio do {service_name} está sendo preparado...\nSe o envio não iniciar automaticamente, toque no botão abaixo."
    return f"{service_name} audio is being prepared...\nIf it does not start automatically, tap the button below."


def inline_video_already_processing(lang: str = DEFAULT_LANG):
    return "Este vídeo inline já está sendo preparado." if _normalize_lang(lang) == "pt" else "This inline video is already being prepared."


def inline_video_already_sent(lang: str = DEFAULT_LANG):
    return "Este vídeo inline já foi enviado." if _normalize_lang(lang) == "pt" else "This inline video was already sent."


def supported_sites_message(bot_username: str | None = None, lang: str = DEFAULT_LANG):
    return help_message(bot_username, lang=lang)


def category_settings_text(category: str, lang: str = DEFAULT_LANG) -> str:
    lang_code = _normalize_lang(lang)
    if category == "media":
        if lang_code == "pt":
            return (
                "<b>🎬 Mídia & Configurações de Qualidade</b>\n\n"
                "Configure a resolução de vídeo, formato de arquivo e opções de áudio:"
            )
        return (
            "<b>🎬 Media & Quality Settings</b>\n\n"
            "Configure video resolution, file format, and audio options:"
        )
    if category == "appearance":
        if lang_code == "pt":
            return (
                "<b>🎨 Aparência & Botões</b>\n\n"
                "Personalize descrições de posts, links originais e botões de ação:"
            )
        return (
            "<b>🎨 Appearance & Buttons</b>\n\n"
            "Customize post descriptions, original URL links, and action buttons:"
        )
    if category == "chat":
        if lang_code == "pt":
            return (
                "<b>💬 Chat & Limpeza</b>\n\n"
                "Gerencie o comportamento do bot em grupos e a exclusão automática de mensagens:"
            )
        return (
            "<b>💬 Chat & Clean-up</b>\n\n"
            "Manage group chat behavior and message cleanup settings:"
        )
    return settings(lang=lang)


def help_message(bot_username: str | None = None, lang: str = DEFAULT_LANG) -> str:
    username = bot_username or "MaxLoadBot"
    if _normalize_lang(lang) == "pt":
        return (
            "<b>📖 Guia & Ajuda do MaxLoad</b>\n\n"
            "Envie um link ou cole vários links na mesma mensagem. O bot extrairá e entregará a mídia automaticamente.\n\n"
            "<blockquote expandable><b>📷 Instagram & Threads</b>\n"
            "• Download de Posts, Reels, IGTV & Stories\n"
            "• Carrosséis de fotos e álbuns multimídia\n"
            "• Copie o link via Compartilhar → Copiar link</blockquote>\n\n"
            "<blockquote expandable><b>🎵 TikTok</b>\n"
            "• Downloads de vídeo sem marca d'água\n"
            "• Carrosséis de fotos e apresentações\n"
            "• Extração de áudio MP3 suportada</blockquote>\n\n"
            "<blockquote expandable><b>▶️ YouTube & YouTube Music</b>\n"
            "• YouTube Shorts e Vídeos convencionais\n"
            "• Áudio e vídeo em alta definição\n"
            "• Toque no botão MP3 para baixar o áudio</blockquote>\n\n"
            "<blockquote expandable><b>🐦 X / Twitter & 📌 Pinterest</b>\n"
            "• Vídeos, GIFs e imagens do X / Twitter\n"
            "• Pins de vídeo e imagem do Pinterest</blockquote>\n\n"
            "<blockquote expandable><b>🎧 SoundCloud & 🟢 Spotify</b>\n"
            "• Faixas de áudio do SoundCloud em alta qualidade\n"
            "• Identificação de faixas e download do Spotify</blockquote>\n\n"
            f"<blockquote expandable><b>⚡ Modo Inline</b>\n"
            f"• Digite <code>@{username} [link]</code> em qualquer chat\n"
            "• Prévia instantânea e envio direto de mídia</blockquote>\n\n"
            "<blockquote expandable><b>📦 Download em Lote (Batch)</b>\n"
            "• Envie até 6 links em uma única mensagem\n"
            "• Entregues um a um para manter o chat organizado</blockquote>"
        )
    return (
        "<b>📖 MaxLoad Help & Guide</b>\n\n"
        "Send one link or paste multiple links in one message. The bot will automatically extract and deliver the media.\n\n"
        "<blockquote expandable><b>📷 Instagram & Threads</b>\n"
        "• Download Posts, Reels, IGTV & Stories\n"
        "• Photo carousels & multi-media albums\n"
        "• Copy link via Share → Copy link</blockquote>\n\n"
        "<blockquote expandable><b>🎵 TikTok</b>\n"
        "• Watermark-free video downloads\n"
        "• Photo carousels & slideshows\n"
        "• MP3 audio extraction supported</blockquote>\n\n"
        "<blockquote expandable><b>▶️ YouTube & YouTube Music</b>\n"
        "• YouTube Shorts & regular Videos\n"
        "• High quality audio & video streams\n"
        "• Tap MP3 button to download audio</blockquote>\n\n"
        "<blockquote expandable><b>🐦 X / Twitter & 📌 Pinterest</b>\n"
        "• X / Twitter videos, GIFs & images\n"
        "• Pinterest video and image Pins</blockquote>\n\n"
        "<blockquote expandable><b>🎧 SoundCloud & 🟢 Spotify</b>\n"
        "• High quality SoundCloud audio tracks\n"
        "• Spotify track matching & audio download</blockquote>\n\n"
        f"<blockquote expandable><b>⚡ Inline Mode</b>\n"
        f"• Type <code>@{username} [link]</code> in any chat\n"
        "• Instant preview and direct media sharing</blockquote>\n\n"
        "<blockquote expandable><b>📦 Batch Downloading</b>\n"
        "• Paste up to 6 links in a single message\n"
        "• Delivered one by one to keep chat clean</blockquote>"
    )


def referral_message(bot_username: str, user_id: int, invited_count: int, lang: str = DEFAULT_LANG) -> str:
    username = bot_username or "MaxLoadBot"
    ref_link = f"https://t.me/{username}?start=ref_{user_id}"
    if _normalize_lang(lang) == "pt":
        return (
            "<b>👥 Programa de Indicação</b>\n\n"
            "Convide amigos para usar o MaxLoad! Compartilhe seu link pessoal:\n"
            f"<code>{ref_link}</code>\n\n"
            f"Usuários convidados: <b>{invited_count}</b>"
        )
    return (
        "<b>👥 Your Referral Program</b>\n\n"
        "Invite friends to use MaxLoad! Share your personal referral link:\n"
        f"<code>{ref_link}</code>\n\n"
        f"Users invited: <b>{invited_count}</b>"
    )


def batch_links_started(processed_total: int, detected_total: int | None = None, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        if detected_total is not None and detected_total > processed_total:
            return (
                f"Encontrados {detected_total} links suportados. "
                f"Processarei os primeiros {processed_total} um por um para manter o chat organizado."
            )
        return f"Encontrados {processed_total} links suportados. Processarei um por um para manter o chat organizado."

    if detected_total is not None and detected_total > processed_total:
        return (
            f"Found {detected_total} supported links. "
            f"I'll process the first {processed_total} one by one so the chat stays readable."
        )
    return f"Found {processed_total} supported links. I'll process them one by one so the chat stays readable."


def batch_link_progress(current: int, total: int, service_name: str, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return f"Processando link {current}/{total}: {service_name}..."
    return f"Processing link {current}/{total}: {service_name}..."


def batch_links_finished(total: int, lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return f"Processamento em lote concluído para {total} links."
    return f"Finished batch processing for {total} links."


def timeout_error(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "Tempo limite esgotado. O servidor de origem pode estar lento no momento. Tente novamente mais tarde."
    return "Request timed out. The source may be slow right now. Please try again later."


def something_went_wrong(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return (
            "Não foi possível processar este link no momento.\n"
            "O conteúdo pode ser privado, ter sido apagado, ter restrição de região ou estar temporariamente bloqueado pela plataforma de origem. "
            "Tente novamente mais tarde."
        )
    return (
        "Couldn't process this link right now.\n"
        "It may be private, deleted, region-limited, or temporarily blocked by the source. "
        "Please try again later."
    )


def video_too_large(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "O vídeo é muito grande para o Telegram. Tente um vídeo mais curto ou a opção de áudio/MP3 se disponível."
    return "The video is too large for Telegram. Try a shorter video or an MP3/audio option if available."


def audio_too_large(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "O áudio é muito grande para o Telegram. Tente uma faixa mais curta ou outro link de origem."
    return "The audio is too large for Telegram. Try a shorter track or another source link."


def nothing_found(lang: str = DEFAULT_LANG):
    if _normalize_lang(lang) == "pt":
        return "Nenhuma mídia encontrada. Verifique se o link é público, não expirou e aponta diretamente para um post ou vídeo."
    return "No media found. Check that the link is public, not expired, and points directly to a post or video."
