import asyncio
import json
import math
import os
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Optional

from aiogram.types import FSInputFile
from services.logger import logger as logging

logging = logging.bind(service="video_metadata")

_FFPROBE_COMMAND = (
    "ffprobe",
    "-v",
    "error",
    "-select_streams",
    "v:0",
    "-show_entries",
    "stream=width,height,duration,sample_aspect_ratio,display_aspect_ratio:format=duration",
    "-of",
    "json",
)


@dataclass(slots=True)
class TelegramVideoAttrs:
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[int] = None
    thumbnail_path: Optional[str] = None
    supports_streaming: bool = True


def _parse_ratio(value: object) -> Optional[float]:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw or raw in {"N/A", "0:1"}:
        return None
    try:
        if ":" in raw:
            numerator, denominator = raw.split(":", 1)
            numerator_value = int(numerator)
            denominator_value = int(denominator)
            if denominator_value <= 0:
                return None
            return numerator_value / denominator_value
        fraction = Fraction(raw)
        if fraction.denominator == 0:
            return None
        return float(fraction)
    except (ArithmeticError, ValueError, ZeroDivisionError):
        return None


def _coerce_dimension(value: object) -> Optional[int]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _coerce_duration(value: object) -> Optional[int]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return int(round(parsed)) if parsed > 0 else None


def _normalize_display_dimensions(
    width: Optional[int],
    height: Optional[int],
    *,
    sample_aspect_ratio: Optional[float],
    display_aspect_ratio: Optional[float],
) -> tuple[Optional[int], Optional[int]]:
    if not width or not height:
        return width, height

    target_ratio = display_aspect_ratio
    if target_ratio is None and sample_aspect_ratio is not None:
        target_ratio = (width / height) * sample_aspect_ratio

    if (
        target_ratio is None
        or not math.isfinite(target_ratio)
        or target_ratio <= 0
        or target_ratio < 0.2
        or target_ratio > 5.0
    ):
        return width, height

    encoded_ratio = width / height
    if abs(target_ratio - encoded_ratio) / max(target_ratio, encoded_ratio) < 0.015:
        return width, height

    adjusted_width = max(1, round(height * target_ratio))
    return adjusted_width, height


async def extract_video_thumbnail(path: str) -> Optional[str]:
    """Gera um frame JPEG nitido aos 1.0s do video para exibicao de alta qualidade."""
    if not path or not Path(path).exists():
        return None
    
    thumb_path = f"{path}_thumb.jpg"
    if Path(thumb_path).exists():
        return thumb_path

    # Pega o frame nos 3 segundos ou no meio do inicio para evitar tela preta de introducao
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", "00:00:04",
        "-i", path,
        "-vframes", "1",
        "-q:v", "2",
        thumb_path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        if Path(thumb_path).exists() and os.path.getsize(thumb_path) > 0:
            return thumb_path
    except Exception as exc:
        logging.debug("Falha ao gerar thumbnail do video %s: %s", path, exc)
    return None


async def probe_telegram_video_attrs(path: Optional[str]) -> TelegramVideoAttrs:
    if not path:
        return TelegramVideoAttrs()

    thumb_path = await extract_video_thumbnail(path)

    try:
        process = await asyncio.create_subprocess_exec(
            *_FFPROBE_COMMAND,
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception:
        return TelegramVideoAttrs(thumbnail_path=thumb_path)

    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        return TelegramVideoAttrs(thumbnail_path=thumb_path)

    try:
        payload = json.loads(stdout.decode("utf-8"))
    except Exception:
        return TelegramVideoAttrs(thumbnail_path=thumb_path)

    streams = payload.get("streams")
    format_info = payload.get("format", {}) if isinstance(payload.get("format"), dict) else {}

    if not isinstance(streams, list) or not streams:
        return TelegramVideoAttrs(thumbnail_path=thumb_path)

    stream = streams[0] if isinstance(streams[0], dict) else {}
    width = _coerce_dimension(stream.get("width"))
    height = _coerce_dimension(stream.get("height"))
    duration = _coerce_duration(stream.get("duration")) or _coerce_duration(format_info.get("duration"))

    normalized_width, normalized_height = _normalize_display_dimensions(
        width,
        height,
        sample_aspect_ratio=_parse_ratio(stream.get("sample_aspect_ratio")),
        display_aspect_ratio=_parse_ratio(stream.get("display_aspect_ratio")),
    )

    return TelegramVideoAttrs(
        width=normalized_width,
        height=normalized_height,
        duration=duration,
        thumbnail_path=thumb_path,
        supports_streaming=True,
    )


async def build_video_send_kwargs(path: Optional[str] = None) -> tuple[dict[str, object], Optional[str]]:
    attrs = await probe_telegram_video_attrs(path)
    kwargs: dict[str, object] = {"supports_streaming": attrs.supports_streaming}
    if attrs.width:
        kwargs["width"] = attrs.width
    if attrs.height:
        kwargs["height"] = attrs.height
    if attrs.duration:
        kwargs["duration"] = attrs.duration
    if attrs.thumbnail_path and Path(attrs.thumbnail_path).exists():
        kwargs["thumbnail"] = FSInputFile(attrs.thumbnail_path)
    return kwargs, attrs.thumbnail_path
