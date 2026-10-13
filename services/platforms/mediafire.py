"""Download public MediaFire files with size and host checks."""
from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

MAX_BYTES = int(os.getenv("CLOUD_FILE_MAX_BYTES", str(500 * 1024 * 1024)))
TIMEOUT = int(os.getenv("CLOUD_FILE_TIMEOUT", "600"))
HOSTS = {
    "mediafire": {"mediafire.com", "www.mediafire.com", "m.mediafire.com"},
}

def is_mediafire_url(url: str) -> bool:
    try:
        p = urlparse(url)
        if p.scheme != "https" or p.username or p.password or p.port not in (None, 443):
            return False
        return p.hostname in HOSTS["mediafire"]
    except ValueError:
        return False

async def _public_host(host: str) -> None:
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not infos or any(not ipaddress.ip_address(item[4][0]).is_global for item in infos):
        raise ValueError("Destino de rede não permitido")

class _MediaFireParser(HTMLParser):
    """Extract the download button regardless of HTML attribute order."""
    def __init__(self):
        super().__init__()
        self.link = None

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        values = dict(attrs)
        if values.get("id") == "downloadButton":
            self.link = values.get("href")


async def download_mediafire(url: str, folder: Path) -> Path:
    if not is_mediafire_url(url):
        raise ValueError("URL não permitida")
    from html import unescape
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=TIMEOUT),
        raise_for_status=True,
        trust_env=False,
    ) as session:
        await _public_host(urlparse(url).hostname or "")
        async with session.get(url, allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError("Link MediaFire indisponível")
            if int(response.headers.get("Content-Length", "0")) > 2 * 1024 * 1024:
                raise ValueError("Página MediaFire muito grande")
            html_bytes = await response.content.read(2 * 1024 * 1024 + 1)
            if len(html_bytes) > 2 * 1024 * 1024:
                raise ValueError("Página MediaFire muito grande")
            html = html_bytes.decode(response.charset or "utf-8", errors="replace")
        parser = _MediaFireParser()
        parser.feed(html)
        if not parser.link:
            raise ValueError("Link público de download não encontrado")
        direct = unescape(parser.link)
        parsed = urlparse(direct)
        if (
            parsed.scheme != "https"
            or parsed.username or parsed.password
            or parsed.port not in (None, 443)
            or not parsed.hostname
            or not re.fullmatch(r"download[0-9]+\\.mediafire\\.com", parsed.hostname)
        ):
            raise ValueError("Servidor MediaFire inesperado")
        await _public_host(parsed.hostname)
        async with session.get(direct, allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError("Download MediaFire indisponível")
            length = int(response.headers.get("Content-Length", "0"))
            if length > MAX_BYTES:
                raise ValueError("Arquivo acima do limite")
            filename = response.headers.get("Content-Disposition", "")
            match = re.search(r'filename="?([^";]+)', filename)
            raw_name = match.group(1) if match else parsed.path.rsplit("/", 1)[-1]
            name = raw_name.replace("\\\\", "/").rsplit("/", 1)[-1].strip() or "arquivo.bin"
            if name in (".", ".."):
                raise ValueError("Nome de arquivo inválido")
            dest = folder / name
            total = 0
            with dest.open("wb") as output:
                async for chunk in response.content.iter_chunked(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError("Arquivo acima do limite")
                    output.write(chunk)
            return dest

