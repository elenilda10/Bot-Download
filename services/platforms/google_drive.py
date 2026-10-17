"""Download public Google Drive files without authentication."""
from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp

MAX_BYTES = int(os.getenv("CLOUD_FILE_MAX_BYTES", str(2000 * 1024 * 1024)))
TIMEOUT = int(os.getenv("CLOUD_FILE_TIMEOUT", "600"))
FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")
HOSTS = {"drive.google.com", "drive.usercontent.google.com"}


def extract_drive_id(url: str) -> str | None:
    try:
        p = urlparse(url)
        if (
            p.scheme != "https"
            or p.hostname not in HOSTS
            or p.username is not None
            or p.password is not None
            or p.port not in (None, 443)
        ):
            return None

        if p.hostname == "drive.google.com":
            match = re.fullmatch(r"/file/d/([A-Za-z0-9_-]+)/?(?:view|preview|edit)?/?", p.path)
            if match:
                file_id = match.group(1)
            elif p.path in {"/open", "/uc"}:
                file_id = parse_qs(p.query).get("id", [""])[0]
            else:
                return None
        elif p.path == "/download":
            file_id = parse_qs(p.query).get("id", [""])[0]
        else:
            return None

        return file_id if FILE_ID_RE.fullmatch(file_id) else None
    except ValueError:
        return None


async def _check_host(host: str) -> None:
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, 443, type=socket.SOCK_STREAM
    )
    if not infos or any(
        not ipaddress.ip_address(info[4][0]).is_global
        for info in infos
    ):
        raise ValueError("Endereço de rede não permitido")


class ConfirmParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_form = False
        self.action = None
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "form" and values.get("id") == "download-form":
            self.in_form = True
            self.action = values.get("action")
        elif self.in_form and tag == "input":
            if values.get("type") == "hidden" and values.get("name"):
                self.fields[values["name"]] = values.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.in_form = False


def _safe_name(header: str, fallback: str) -> str:
    match = re.search(r'filename="([^"]+)"', header, re.I)
    if not match:
        match = re.search(r"filename=([^;]+)", header, re.I)
    name = match.group(1).strip().strip('"') if match else fallback
    name = name.replace("\\", "/").split("/")[-1].strip()
    if name in {"", ".", ".."}:
        name = fallback
    return name[:200]


async def download_google_drive(file_id: str, folder: Path) -> Path:
    if not FILE_ID_RE.fullmatch(file_id):
        raise ValueError("ID Google Drive inválido")

    folder.mkdir(parents=True, exist_ok=True)

    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    async with aiohttp.ClientSession(
        timeout=timeout,
        raise_for_status=False,
        trust_env=False,
    ) as session:
        url = "https://drive.google.com/uc?" + urlencode({
            "export": "download",
            "id": file_id,
        })

        for attempt in range(5):
            parsed = urlparse(url)
            if (
                parsed.scheme != "https"
                or parsed.hostname not in HOSTS
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port not in (None, 443)
            ):
                raise ValueError("Redirecionamento não permitido")

            await _check_host(parsed.hostname)

            async with session.get(url, allow_redirects=False) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location", "")
                    from urllib.parse import urljoin
                    url = urljoin(url, location)
                    continue

                if response.status != 200:
                    raise ValueError(
                        f"Google Drive respondeu HTTP {response.status}"
                    )

                content_type = response.headers.get(
                    "Content-Type", ""
                ).lower()

                if "text/html" in content_type:
                    html = await response.content.read(1024 * 1024 + 1)
                    if len(html) > 1024 * 1024:
                        raise ValueError("Página de confirmação muito grande")

                    parser = ConfirmParser()
                    parser.feed(html.decode("utf-8", errors="replace"))

                    if not parser.action:
                        raise ValueError(
                            "Arquivo indisponível ou confirmação desconhecida"
                        )

                    if parser.fields.get("id") != file_id:
                        raise ValueError("ID inesperado na confirmação")

                    if parser.fields.get("confirm") != "t":
                        raise ValueError("Confirmação inesperada")

                    url = parser.action + "?" + urlencode(parser.fields)
                    continue

                length = int(response.headers.get("Content-Length", "0"))
                if length > MAX_BYTES:
                    raise ValueError("Arquivo acima do limite")

                filename = _safe_name(
                    response.headers.get("Content-Disposition", ""),
                    f"{file_id}.bin",
                )
                destination = folder / filename
                total = 0

                with destination.open("wb") as output:
                    async for chunk in response.content.iter_chunked(
                        1024 * 1024
                    ):
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise ValueError("Arquivo acima do limite")
                        output.write(chunk)

                if total == 0:
                    raise ValueError("Arquivo vazio")

                return destination

        raise ValueError("Redirecionamentos excessivos")
