"""Inspect the public caption of one Kwai video without downloading media.

Prints caption metadata only. Never prints CDN URLs, page HTML, cookies or headers.
Usage:
    python scripts/diagnose_kwai_caption.py 'https://www.kwai.com/@user/video/123'
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
from html import unescape
from html.parser import HTMLParser
import sys
from pathlib import Path
from urllib.parse import urlparse

SOURCE = Path(__file__).resolve().parents[1] / "services/platforms/kwai_media.py"
SPEC = importlib.util.spec_from_file_location("kwai_media_caption_diagnostic", SOURCE)
assert SPEC and SPEC.loader
kwai = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(kwai)



def _safe_excerpt(text: str, limit: int = 220) -> str:
    """Redact URLs and long tokens before displaying a short HTML context."""
    text = re.sub(r"https?://[^\s\"'<>]+", "[URL]", text, flags=re.I)
    text = re.sub(r"(?<!\w)[A-Za-z0-9+/_=-]{60,}(?!\w)", "[LONG_VALUE]", text)
    text = re.sub(r"\s+", " ", unescape(text)).strip()
    return text[:limit]


class _CaptionTextInspector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.matches = []

    def handle_starttag(self, tag, attrs):
        if tag not in {"meta", "link", "img", "input", "br", "hr", "source"}:
            self.tags.append(tag)
        if tag == "meta":
            data = dict(attrs)
            field = data.get("property") or data.get("name") or ""
            content = data.get("content") or ""
            if "por que funciona" in content.casefold():
                self.matches.append(("meta:" + field, _safe_excerpt(content)))

    def handle_endtag(self, tag):
        if tag in self.tags:
            while self.tags:
                removed = self.tags.pop()
                if removed == tag:
                    break

    def handle_data(self, data):
        lower = data.casefold()
        needle = "por que funciona"
        pos = lower.find(needle)
        if pos >= 0:
            tag = self.tags[-1] if self.tags else "[sem tag]"
            self.matches.append(
                (tag, _safe_excerpt(data[max(0, pos - 100):pos + 180]))
            )


def _inspect_caption_context(page: str) -> None:
    inspector = _CaptionTextInspector()
    inspector.feed(page)
    print("CONTEXTOS HTML:", len(inspector.matches))
    for i, (where, excerpt) in enumerate(inspector.matches[:5], 1):
        print(f"  CONTEXTO {i} [{where}]: {excerpt}")
    if not inspector.matches:
        lower = page.casefold()
        pos = lower.find("por que funciona")
        if pos >= 0:
            print(
                "  CONTEXTO FONTE:",
                _safe_excerpt(page[max(0, pos - 100):pos + 160]),
            )


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
        video_id = (re.search(r"/video/(\d+)", final) or id_match)
        target_id = video_id.group(1) if video_id else ""
        print("ID NOS DADOS DO VÍDEO:", target_id in kwai._POST_ID_FIELD.findall(page))
        print("OBJETOS photoId:", len(kwai._POST_ID_FIELD.findall(page)))
        print("CAMPOS caption:", len(kwai._EMBEDDED_CAPTION_FIELD.findall(page)))
        print("TEXTO 'Por que funciona' NA PÁGINA:", "por que funciona" in page.casefold())
        _inspect_caption_context(page)
        print("OG TITLE GENÉRICO:", kwai._is_generic_kwai_text(
            kwai._extract_meta(page, "og:title")
        ))
        embedded = kwai._extract_embedded_post_caption(page, final)
        print("LEGENDA ASSOCIADA AO ID:", embedded[:400] if embedded else "[não encontrada]")
        hydrated = kwai._extract_visible_hydrated_caption(page)
        print("LEGENDA VISÍVEL + JAVASCRIPT:", hydrated[:400] if hydrated else "[não encontrada]")
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
