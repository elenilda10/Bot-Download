import asyncio
import os
import uuid
from urllib.parse import quote

import httpx
from aiogram import F, Router, types
from aiogram.enums import ChatAction, ChatType, ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app_context import bot, db
from config import BOT_TOKEN, OUTPUT_DIR
from handlers.utils import load_user_settings
from services.logger import logger as logging
from services.music_recognition import reconhecer_musica_do_video

logger = logging.bind(service="music_handler")
router = Router()

TELEGRAM_API_DIR = "/var/lib/telegram-bot-api"


def _get_lang(settings: dict) -> str:
    lang = settings.get("language") or settings.get("lang") or "pt"
    return "pt" if str(lang).lower().startswith("pt") else "en"


def _criar_botoes_plataformas(titulo: str, artista: str, shazam_url: str | None, is_pt: bool) -> InlineKeyboardMarkup:
    """Monta teclado com links de streaming para a música encontrada."""
    query = quote(f"{titulo} {artista}")
    spotify_url = f"https://open.spotify.com/search/{query}"
    youtube_url = f"https://www.youtube.com/results?search_query={query}"

    botoes_linha_1 = [
        InlineKeyboardButton(text="🎧 Spotify", url=spotify_url),
        InlineKeyboardButton(text="▶️ YouTube", url=youtube_url),
    ]
    if shazam_url:
        botoes_linha_1.append(InlineKeyboardButton(text="⚡ Shazam", url=shazam_url))

    return InlineKeyboardMarkup(inline_keyboard=[botoes_linha_1])


async def _delete_message_after_delay(msg: types.Message, delay: int = 8):
    """Apaga uma mensagem após N segundos em segundo plano."""
    try:
        await asyncio.sleep(delay)
        await msg.delete()
    except Exception:
        pass


async def _send_ephemeral_group_error(
    chat_id: int,
    text: str,
    user_id: int,
    message_id: int,
    callback_query_id: str | None = None,
) -> bool:
    """Envia mensagem efêmera no grupo com a tag visível só para o usuário."""
    try:
        bot_token = getattr(bot, "token", None) or BOT_TOKEN
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "receiver_user_id": user_id,
            "reply_parameters": {"message_id": message_id},
        }
        if callback_query_id:
            payload["callback_query_id"] = callback_query_id

        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json=payload,
            )
            data = res.json()
            if data.get("ok"):
                return True
            else:
                logger.warning("[EFÊMERO API RESPOSTA]: %s", data)
    except Exception as exc:
        logger.warning("[EFÊMERO EXCEÇÃO]: %s", exc)
    return False


async def _resolve_local_or_download_file(video: types.Video, temp_dest: str) -> tuple[str, bool]:
    """
    Localiza o arquivo baixado pelo servidor local do telegram-bot-api em disco
    ou faz o download via Bot API local.
    Retorna (caminho_no_disco, deve_apagar_depois).
    """
    token_str = getattr(bot, "token", None) or BOT_TOKEN

    # 1. Tenta encontrar em downloads recentes no OUTPUT_DIR
    if os.path.isdir(OUTPUT_DIR):
        try:
            arquivos = [
                os.path.join(OUTPUT_DIR, f)
                for f in os.listdir(OUTPUT_DIR)
                if f.endswith((".mp4", ".mov", ".mkv", ".webm"))
            ]
            arquivos.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            for arq in arquivos[:15]:
                if video.file_size and abs(os.path.getsize(arq) - video.file_size) < 4096:
                    return arq, False
        except Exception as err:
            logger.debug("Falha na busca em OUTPUT_DIR: %s", err)

    # 2. Obtém o file_info através do bot (apontado para o telegram-bot-api local)
    file_info = await bot.get_file(video.file_id)
    if not file_info.file_path:
        raise RuntimeError("file_path não disponível no Telegram File")

    # 3. Verifica se o caminho já é absoluto retornado pela flag --local
    if os.path.isabs(file_info.file_path) and os.path.exists(file_info.file_path):
        return file_info.file_path, False

    # 4. Verifica na pasta do token do telegram-bot-api montada no host
    caminho_api_local = os.path.join(TELEGRAM_API_DIR, token_str, file_info.file_path)
    if os.path.exists(caminho_api_local) and os.path.getsize(caminho_api_local) > 0:
        return caminho_api_local, False

    nome_arquivo = os.path.basename(file_info.file_path)
    candidato_direto = os.path.join(TELEGRAM_API_DIR, token_str, "videos", nome_arquivo)
    if os.path.exists(candidato_direto) and os.path.getsize(candidato_direto) > 0:
        return candidato_direto, False

    # 5. Se não encontrou direto em disco, baixa pelo Bot API local (até 2GB)
    await bot.download_file(file_info.file_path, destination=temp_dest)
    return temp_dest, True


@router.callback_query(F.data == "identify_music")
async def callback_identify_music(callback: types.CallbackQuery):
    message = callback.message
    if not message:
        await callback.answer()
        return

    user_settings = await load_user_settings(db, message)
    is_pt = _get_lang(user_settings) == "pt"
    is_group = message.chat and message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
    user_id = callback.from_user.id

    video = getattr(message, "video", None)
    if not video:
        alert_no_video = (
            "Este item não é um vídeo com áudio reconhecível."
            if is_pt
            else "This media is not a video with recognizable audio."
        )
        await callback.answer(alert_no_video, show_alert=True)
        return

    alert_loading = (
        "🔍 Identificando música... aguarde alguns segundos."
        if is_pt
        else "🔍 Identifying music... please wait a few seconds."
    )
    await callback.answer(alert_loading, show_alert=False)

    temp_video_dest = os.path.join(OUTPUT_DIR, f"tg_vid_{uuid.uuid4().hex[:8]}.mp4")
    resolved_path = None
    should_cleanup = False

    try:
        resolved_path, should_cleanup = await _resolve_local_or_download_file(
            video, temp_video_dest
        )

        musica = await reconhecer_musica_do_video(resolved_path)
        if musica:
            titulo = musica["titulo"]
            artista = musica["artista"]
            kb_plataformas = _criar_botoes_plataformas(
                titulo, artista, musica.get("url"), is_pt
            )
            if is_pt:
                texto = (
                    f"🎵 <b>Música Identificada:</b>\n\n"
                    f"<b>Título:</b> {titulo}\n"
                    f"<b>Artista:</b> {artista}"
                )
            else:
                texto = (
                    f"🎵 <b>Identified Music:</b>\n\n"
                    f"<b>Title:</b> {titulo}\n"
                    f"<b>Artist:</b> {artista}"
                )
            await message.reply(texto, parse_mode=ParseMode.HTML, reply_markup=kb_plataformas)
        else:
            msg_not_found = (
                "❌ Não consegui identificar a música deste vídeo (o áudio pode estar alterado, baixo ou não listado no Shazam)."
                if is_pt
                else "❌ Could not identify the song in this video (audio might be low, altered or unlisted on Shazam)."
            )
            sent_ephemeral = False
            if is_group:
                sent_ephemeral = await _send_ephemeral_group_error(
                    chat_id=message.chat.id,
                    text=msg_not_found,
                    user_id=user_id,
                    message_id=message.message_id,
                    callback_query_id=callback.id,
                )
            if not sent_ephemeral:
                err_msg = await message.reply(msg_not_found, parse_mode=ParseMode.HTML)
                asyncio.create_task(_delete_message_after_delay(err_msg, delay=8))

    except Exception as exc:
        logger.warning("Erro no callback identify_music: %s", exc)
        err_generic = (
            "❌ Ocorreu um erro ao tentar identificar a música."
            if is_pt
            else "❌ An error occurred while trying to identify music."
        )
        sent_ephemeral = False
        if is_group:
            sent_ephemeral = await _send_ephemeral_group_error(
                chat_id=message.chat.id,
                text=err_generic,
                user_id=user_id,
                message_id=message.message_id,
                callback_query_id=callback.id,
            )
        if not sent_ephemeral:
            err_msg = await message.reply(err_generic)
            asyncio.create_task(_delete_message_after_delay(err_msg, delay=8))
    finally:
        if should_cleanup and resolved_path and os.path.exists(resolved_path):
            try:
                os.remove(resolved_path)
            except Exception:
                pass


@router.message(F.chat.type == ChatType.PRIVATE, F.video)
async def handle_direct_private_video(message: types.Message):
    video = message.video
    if not video:
        return

    user_settings = await load_user_settings(db, message)
    is_pt = _get_lang(user_settings) == "pt"

    txt_searching = (
        "🔍 Analisando áudio do vídeo para identificar a música..."
        if is_pt
        else "🔍 Analyzing video audio to identify the song..."
    )
    status_msg = await message.reply(txt_searching)
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    temp_video_dest = os.path.join(OUTPUT_DIR, f"direct_vid_{uuid.uuid4().hex[:8]}.mp4")
    resolved_path = None
    should_cleanup = False

    try:
        resolved_path, should_cleanup = await _resolve_local_or_download_file(
            video, temp_video_dest
        )

        musica = await reconhecer_musica_do_video(resolved_path)
        if musica:
            titulo = musica["titulo"]
            artista = musica["artista"]
            kb_plataformas = _criar_botoes_plataformas(
                titulo, artista, musica.get("url"), is_pt
            )

            if is_pt:
                texto = (
                    f"🎵 <b>Música Encontrada:</b>\n\n"
                    f"<b>Faixa:</b> {titulo}\n"
                    f"<b>Artista:</b> {artista}"
                )
            else:
                texto = (
                    f"🎵 <b>Song Found:</b>\n\n"
                    f"<b>Track:</b> {titulo}\n"
                    f"<b>Artist:</b> {artista}"
                )
            await status_msg.edit_text(texto, parse_mode=ParseMode.HTML, reply_markup=kb_plataformas)
        else:
            txt_nf = (
                "❌ Nenhuma música comercial foi reconhecida no áudio deste vídeo."
                if is_pt
                else "❌ No commercial song was recognized in this video's audio."
            )
            await status_msg.edit_text(txt_nf)
            asyncio.create_task(_delete_message_after_delay(status_msg, delay=8))
    except Exception as exc:
        logger.warning("Erro ao reconhecer vídeo direto no PV: %s", exc)
        txt_err = (
            "❌ Ocorreu um erro ao processar o vídeo."
            if is_pt
            else "❌ An error occurred while processing the video."
        )
        await status_msg.edit_text(txt_err)
        asyncio.create_task(_delete_message_after_delay(status_msg, delay=8))
    finally:
        if should_cleanup and resolved_path and os.path.exists(resolved_path):
            try:
                os.remove(resolved_path)
            except Exception:
                pass
