import asyncio
import os
import uuid
from shazamio import Shazam
from config import OUTPUT_DIR
from services.logger import logger as logging

logger = logging.bind(service="music_recognition")


async def extrair_audio_amostra(video_path: str, start_sec: int = 0, duracao: int = 20) -> str:
    """
    Extrai trecho de áudio em formato WAV PCM 44.1kHz (ideal para a assinatura acústica do Shazam).
    """
    audio_temp = os.path.join(OUTPUT_DIR, f"shazam_sample_{uuid.uuid4().hex[:8]}.wav")
    
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(start_sec),
        "-i", video_path,
        "-t", str(duracao),
        "-vn",
        "-ac", "1",
        "-ar", "44100",
        "-f", "wav",
        audio_temp,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except Exception as exc:
        logger.warning("Erro ao rodar ffmpeg para extrair áudio: %s", exc)

    return audio_temp


async def reconhecer_musica_do_video(video_path: str) -> dict | None:
    """
    Tenta identificar a música do vídeo consultando o Shazam.
    Faz tentativa primária (0-20s) e, se não encontrar, tenta uma fatia intermediária (10-30s).
    """
    if not video_path or not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
        return None

    shazam = Shazam()
    tentativas = [
        {"start": 0, "duracao": 20},
        {"start": 10, "duracao": 20},
    ]

    for tentativa in tentativas:
        audio_path = None
        try:
            audio_path = await extrair_audio_amostra(
                video_path,
                start_sec=tentativa["start"],
                duracao=tentativa["duracao"]
            )

            if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 1000:
                continue

            out = await shazam.recognize(audio_path)
            track = out.get("track")

            if track:
                return {
                    "titulo": track.get("title", "Desconhecido"),
                    "artista": track.get("subtitle", "Desconhecido"),
                    "url": track.get("url"),
                    "cover": track.get("images", {}).get("coverart"),
                }
        except Exception as exc:
            logger.warning("Falha na tentativa de reconhecimento do Shazam (%s): %s", tentativa, exc)
        finally:
            if audio_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                except Exception:
                    pass

    return None
