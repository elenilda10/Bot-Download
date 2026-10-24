"""Compare Kwai short/canonical public pages without downloading videos.

The diagnostic prints public post IDs and limited caption previews but never
prints CDN/media URLs, query signatures, cookies, or full HTML.
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

SOURCE = Path(__file__).resolve().parents[1] / "services/platforms/kwai_media.py"
SPEC = importlib.util.spec_from_file_location("kwai_identity_probe", SOURCE)
assert SPEC and SPEC.loader
kwai = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(kwai)


def summary(label: str, requested_id: str, result: tuple[str, str] | None) -> None:
    print("\n===", label, "===")
    if not result:
        print("CHROME: sem resposta válida do Kwai")
        return

    url, page = result
    final_id = kwai._video_id_from_url(url)
    print("HOST FINAL:", urlparse(url).hostname)
    print("ID URL FINAL:", final_id or "[ausente]")
    print("CORRESPONDE AO LINK:", final_id == requested_id)
    print("HTML:", len(page))
    print("ID APARECE NO HTML:", requested_id in page)
    print("OG URL CONFERE:", kwai._video_id_from_url(
        kwai._extract_meta(page, "og:url")
    ) == requested_id)
    print("MP4s TOTAL:", len(kwai._scoped_mp4_urls(page)))
    state = kwai._KwaiPageText()
    state.feed(page)
    primary = []
    for script in state.scripts:
        for entry in kwai._HYDRATION_ENTRY.finditer(script):
            if entry.group(1) == "0":
                block = kwai._javascript_object_at(script, entry.end() - 1)
                if block:
                    ids = re.findall(
                        r"""(?:photoId|photo_id)\s*:\s*["']?(\d{8,})""", block
                    )
                    primary.append({
                        "ids": ids[:3],
                        "mp4s": len(kwai._scoped_mp4_urls(block)),
                    })
    print("REGISTROS E[0]:", len(primary))
    print("E[0] IDENTIFICA PUBLICAÇÃO:", any(requested_id in x["ids"] for x in primary))
    print("E[0] CONTAGENS MP4:", [x["mp4s"] for x in primary[:4]])
    caption = kwai._extract_visible_hydrated_caption(page)
    print("LEGENDA VISÍVEL:", caption[:200] if caption else "[não reconhecida]")
    print("MP4 COM IDENTIDADE VERIFICADA:", bool(
        kwai._extract_verified_media_url(page, url)
    ))


async def main(short_url: str, canonical_url: str) -> None:
    if not all(kwai.is_kwai_url(url) for url in (short_url, canonical_url)):
        sys.exit("URLs devem pertencer ao Kwai")
    requested_id = kwai._video_id_from_url(canonical_url)
    if not requested_id:
        sys.exit("A URL canônica deve conter /video/<id numérico>")
    print("ID SOLICITADO:", requested_id)
    variants = [
        ("LINK CURTO ORIGINAL", short_url),
        ("URL CANÔNICA /video/", canonical_url),
        ("ALTERNATIVA /photo/", canonical_url.replace("/video/", "/photo/", 1)),
    ]
    for label, url in variants:
        result = await asyncio.to_thread(kwai._fetch_kwai_chrome_page, url)
        summary(label, requested_id, result)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(
            "Uso: python scripts/diagnose_kwai_post_identity.py "
            "'https://k.kwai.com/p/CODIGO' "
            "'https://www.kwai.com/@usuario/video/12345678'"
        )
    asyncio.run(main(sys.argv[1], sys.argv[2]))
