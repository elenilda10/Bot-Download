"""Inspect the public caption of one Kwai video without downloading media.

Prints caption metadata only. Never prints CDN URLs, page HTML, cookies or headers.
Usage:
    python scripts/diagnose_kwai_caption.py 'https://www.kwai.com/@user/video/123'
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

SOURCE = Path(__file__).resolve().parents[1] / "services/platforms/kwai_media.py"
SPEC = importlib.util.spec_from_file_location("kwai_media_caption_diagnostic", SOURCE)
assert SPEC and SPEC.loader
kwai = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(kwai)


async def main(url: str):
    if not kwai.is_kwai_url(url):
        print("Somente URLs Kwai são aceitas.")
        return

    print("DOMÍNIO:", urlparse(url).hostname)
    id_match = re.search(r"/video/(\d+)", url)
    print("ID SOLICITADO:", id_match.group(1) if id_match else "[não informado]")
    chrome = await asyncio.to_thread(kwai._fetch_kwai_chrome_page, url)
    if chrome:
        final, page = chrome
        print("CHROME HTML:", len(page))
        print("ID PRESENTE:", bool(re.search(r"/video/(\d+)", final)))
        print("OBJETOS photoId:", len(kwai._POST_ID_FIELD.findall(page)))
        print("CAMPOS caption:", len(kwai._EMBEDDED_CAPTION_FIELD.findall(page)))
        print("TEXTO 'Por que funciona' NA PÁGINA:", "por que funciona" in page.casefold())
        print("OG TITLE GENÉRICO:", kwai._is_generic_kwai_text(
            kwai._extract_meta(page, "og:title")
        ))
        embedded = kwai._extract_embedded_post_caption(page, final)
        print("LEGENDA ASSOCIADA AO ID:", embedded[:400] if embedded else "[não encontrada]")
    else:
        print("CHROME: falhou")

    result = await kwai.extract_kwai_video_page(url)
    if result:
        print("AUTOR EXTRAÍDO:", result.get("author") or "[ausente]")
        title = result.get("title") or ""
        print("LEGENDA FINAL:", (
            title[:400] if not kwai._is_generic_kwai_text(title)
            else "[não disponível]"
        ))
        print("VÍDEO DETECTADO:", bool(result.get("media_url")))
    else:
        print("EXTRATOR: não encontrou um vídeo público")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Uso: python scripts/diagnose_kwai_caption.py 'URL_DO_KWAI'")
    asyncio.run(main(sys.argv[1]))
