"""Inspect public Kwai page structure without dumping HTML, cookies or CDN URLs.

Run on a developer machine:
    python scripts/diagnose_kwai_page.py https://k.kwai.com/p/...
This is a diagnostic only. It never writes files or downloads a media stream.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

_MODULE_FILE = Path(__file__).resolve().parents[1] / "services/platforms/kwai_media.py"
_SPEC = importlib.util.spec_from_file_location("kwai_media_diagnostic", _MODULE_FILE)
assert _SPEC is not None and _SPEC.loader is not None
kwai = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(kwai)

_MARKERS = (
    ".mp4", ".m3u8", "videoUrl", "playUrl", "photoUrl", "contentUrl",
    "mainMvUrls", "manifest", "VideoObject", "og:video", "<video",
    "kwai.net", "kwimgs.com", "challenge",
)


def report(label: str, status: int, page: str) -> None:
    normalized = kwai._normalize_page(page)
    print(f"\n=== {label} ===")
    print("HTTP:", status, "| HTML:", len(page))
    print("MIDIA EXTRAIDA:", bool(kwai._extract_media_url(page)))
    print("MARCADORES:", ", ".join(
        f"{key}={normalized.lower().count(key.lower())}" for key in _MARKERS
    ))

    blocks = re.findall(
        r'<script\b[^>]*\btype\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script\s*>',
        page, flags=re.I | re.S,
    )
    print("JSON-LD blocos:", len(blocks))
    types: set[str] = set()
    video_keys: set[str] = set()
    for block in blocks:
        try:
            data = json.loads(block.strip())
        except (ValueError, TypeError):
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            kind = node.get("@type")
            if isinstance(kind, str):
                types.add(kind[:50])
            elif isinstance(kind, list):
                types.update(str(item)[:50] for item in kind)
            if kind == "VideoObject" or (isinstance(kind, list) and "VideoObject" in kind):
                video_keys.update(str(key)[:50] for key in node.keys())
    print("JSON-LD tipos:", ", ".join(sorted(types)) or "nenhum")
    print("JSON-LD campos de video:", ", ".join(sorted(video_keys)) or "nenhum")


async def main(url: str) -> None:
    if not kwai.is_kwai_url(url):
        print("Erro: somente links Kwai são aceitos.")
        return
    headers = {
        "User-Agent": kwai._DEFAULT_UA,
        "Referer": "https://www.kwai.com/",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    }
    timeout = aiohttp.ClientTimeout(total=25)
    canonical = None

    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        try:
            async with session.get(url, allow_redirects=True) as response:
                final = str(response.url)
                print("DOMINIO FINAL:", urlparse(final).hostname)
                if not kwai.is_kwai_url(final):
                    print("Redirecionamento fora do Kwai. Diagnostico interrompido.")
                    return
                canonical = final.split("?", 1)[0].split("#", 1)[0]
                report("aiohttp link compartilhado", response.status, await response.text(errors="replace"))
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            print("aiohttp erro:", type(exc).__name__)

        if canonical and canonical != url:
            try:
                async with session.get(canonical, allow_redirects=True) as response:
                    if kwai.is_kwai_url(str(response.url)):
                        report("aiohttp URL canonica", response.status, await response.text(errors="replace"))
            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                print("URL canonica erro:", type(exc).__name__)

    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        print("\ncurl_cffi: nao instalado; verificacao adicional ignorada.")
        return

    def fetch_chrome() -> None:
        try:
            response = curl_requests.get(
                canonical or url,
                impersonate="chrome",
                timeout=25,
                allow_redirects=True,
                headers={"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"},
            )
            if not kwai.is_kwai_url(str(response.url)):
                print("curl_cffi redirecionou para dominio nao esperado.")
                return
            report("curl_cffi Chrome", response.status_code, response.text)
        except Exception as exc:
            print("curl_cffi erro:", type(exc).__name__)

    await asyncio.to_thread(fetch_chrome)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python scripts/diagnose_kwai_page.py https://k.kwai.com/p/...")
        sys.exit(2)
    asyncio.run(main(sys.argv[1]))
