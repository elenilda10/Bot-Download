import asyncio
import glob
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from typing import Optional
from mutagen import File as MutagenFile
from mutagen.id3 import APIC

from services.logger import logger as logging

logger = logging.bind(service="deezer")

DEEZER_ARL = "b0cd1039d707c6f1ead964aba72992a4f662072cc6300433ae20ffe7591c980a96b16c998bcebeef31f2d72c4fd37a8aa12383328bfe159395188bf0011406ba157e2ef923298a788c498f85bdd79c94bb4a95096458f96d7cddc66a18911393"


@dataclass
class DeezerTrack:
    file_path: str
    title: str
    artist: str
    duration: int
    thumb_path: Optional[str] = None


def _extract_track_metadata(file_path: str) -> DeezerTrack:
    title = os.path.splitext(os.path.basename(file_path))[0]
    artist = "Deezer"
    duration = 0
    thumb_path = None

    try:
        audio = MutagenFile(file_path)
        if audio is not None:
            if hasattr(audio, "info") and hasattr(audio.info, "length"):
                duration = int(audio.info.length)

            if audio.tags:
                title = str(audio.tags.get("TIT2") or audio.tags.get("title", [title])[0] or title)
                artist = str(audio.tags.get("TPE1") or audio.tags.get("artist", [artist])[0] or artist)

                for tag in audio.tags.values():
                    if isinstance(tag, APIC):
                        thumb_path = file_path.rsplit(".", 1)[0] + "_thumb.jpg"
                        with open(thumb_path, "wb") as f:
                            f.write(tag.data)
                        break

                if not thumb_path and hasattr(audio, "pictures") and audio.pictures:
                    thumb_path = file_path.rsplit(".", 1)[0] + "_thumb.jpg"
                    with open(thumb_path, "wb") as f:
                        f.write(audio.pictures[0].data)

    except Exception as exc:
        logger.warning(f"Erro ao extrair metadados: {exc}")

    return DeezerTrack(
        file_path=file_path,
        title=title,
        artist=artist,
        duration=duration,
        thumb_path=thumb_path,
    )


async def download_deezer_track(url: str, output_dir: str) -> Optional[DeezerTrack]:
    os.makedirs(output_dir, exist_ok=True)

    home_dir = os.path.expanduser("~")
    deemix_cfg_dir = os.path.join(home_dir, ".config", "deemix")
    os.makedirs(deemix_cfg_dir, exist_ok=True)
    with open(os.path.join(deemix_cfg_dir, ".arl"), "w") as f:
        f.write(DEEZER_ARL)

    start_time = time.time() - 2
    task_dir = tempfile.mkdtemp(prefix="dz_dl_")

    cmd = [
        "deemix",
        "-b", "1",
        "-p", task_dir,
        url,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=task_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        shutil.rmtree(task_dir, ignore_errors=True)
        logger.error(f"Timeout ao baixar Deezer: {url}")
        return None

    stdout_str = stdout.decode(errors="ignore") if stdout else ""
    stderr_str = stderr.decode(errors="ignore") if stderr else ""

    if proc.returncode != 0:
        logger.error(f"Falha deemix (code={proc.returncode}): {stderr_str or stdout_str}")
        shutil.rmtree(task_dir, ignore_errors=True)
        return None

    possible_roots = [
        task_dir,
        os.path.join(task_dir, "deemix Music"),
        os.path.abspath(output_dir),
        os.path.join(home_dir, "deemix Music"),
        os.path.join(home_dir, "Music"),
    ]

    audio_extensions = {".mp3", ".flac", ".m4a", ".ogg"}
    found_files = []

    for root_path in possible_roots:
        if not os.path.exists(root_path):
            continue
        for root, _, files in os.walk(root_path):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in audio_extensions:
                    full_p = os.path.join(root, file)
                    try:
                        if os.path.getmtime(full_p) >= start_time:
                            found_files.append(full_p)
                    except OSError:
                        pass

    if not found_files:
        logger.error(f"Nenhum arquivo de áudio localizado após download. Log deemix: {stdout_str}")
        shutil.rmtree(task_dir, ignore_errors=True)
        return None

    downloaded_file = max(found_files, key=os.path.getmtime)
    final_path = os.path.join(output_dir, os.path.basename(downloaded_file))

    shutil.move(downloaded_file, final_path)
    shutil.rmtree(task_dir, ignore_errors=True)

    return _extract_track_metadata(final_path)
