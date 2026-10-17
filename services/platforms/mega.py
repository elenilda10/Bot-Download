"""Download public MEGA files using megadl."""
import asyncio
import os
import re
import signal
import time
from pathlib import Path
from urllib.parse import urlparse

MAX_BYTES = int(os.getenv(
    "CLOUD_FILE_MAX_BYTES", str(2000 * 1024 * 1024)
))
TIMEOUT = int(os.getenv("MEGA_DOWNLOAD_TIMEOUT", "900"))

MEGA_PATH = re.compile(r"^/file/[A-Za-z0-9_-]+$")
MEGA_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def is_mega_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname in {"mega.nz", "www.mega.nz"}
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and bool(MEGA_PATH.fullmatch(parsed.path))
            and bool(MEGA_KEY.fullmatch(parsed.fragment))
            and not parsed.query
        )
    except ValueError:
        return False


def folder_size(folder: Path) -> int:
    total = 0
    for path in folder.rglob("*"):
        if path.is_symlink():
            raise ValueError("Link simbólico inesperado")
        if path.is_file():
            total += path.stat().st_size
    return total


async def download_mega(url: str, folder: Path) -> Path:
    if not is_mega_url(url):
        raise ValueError("Link MEGA inválido")

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    if any(folder.iterdir()):
        raise ValueError("Diretório de download não está vazio")

    process = await asyncio.create_subprocess_exec(
        "/usr/bin/megadl",
        "--no-progress",
        "--no-ask-password",
        "--ignore-config-file",
        f"--path={folder}",
        url,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )

    async def stop_process():
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()

    started = time.monotonic()

    try:
        while process.returncode is None:
            if folder_size(folder) >= MAX_BYTES:
                raise ValueError("Arquivo acima do limite")

            remaining = TIMEOUT - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("Download MEGA expirou")

            try:
                await asyncio.wait_for(
                    process.wait(),
                    timeout=min(0.5, remaining),
                )
            except asyncio.TimeoutError:
                pass

        if process.returncode != 0:
            raise RuntimeError("Falha no download MEGA")

        files = [
            path for path in folder.rglob("*")
            if path.is_file() and not path.is_symlink()
        ]

        if len(files) != 1:
            raise ValueError("Esperado um único arquivo MEGA")

        if folder_size(folder) >= MAX_BYTES:
            raise ValueError("Arquivo acima do limite")

        if files[0].stat().st_size == 0:
            raise ValueError("Arquivo MEGA vazio")

        return files[0]

    except BaseException:
        await stop_process()
        raise
