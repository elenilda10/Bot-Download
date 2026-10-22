#!/usr/bin/env python3
"""Probe a candidate Pinterest caption extractor without modifying the VPS bot.

Usage:
  python3 /tmp/probe_pinterest_public_caption.py \
    /tmp/pinterest_media_candidate.py https://pin.it/gLqzFSUZO

Only metadata is fetched: gallery-dl -J/-G and anonymous Pinterest public SEO.
No files are downloaded, no cookies are read, no systemd services touched.
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys


async def main() -> None:
    if len(sys.argv) != 3:
        raise ValueError("Uso: probe.py /tmp/pinterest_media_candidate.py https://pin.it/CODIGO")
    source = Path(sys.argv[1]).resolve(strict=True)
    source_bytes = source.read_bytes()
    if len(source_bytes) > 120_000 or not source.name.endswith(".py"):
        raise ValueError("Arquivo Python candidato inválido")
    url = sys.argv[2]
    if not url.startswith("https://pin.it/") and not url.startswith("https://www.pinterest.com/pin/"):
        raise ValueError("Teste somente Pin público do Pinterest")

    module_name = "pinterest_caption_candidate_probe"
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise RuntimeError("Não foi possível carregar o extrator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    print("URL:", url)
    print("Consultando metadados, sem baixar mídias nem reiniciar o bot...")
    dumped = await module._run_public_pin_gallery(
        module._gallery_cli_executable(), url, "-J"
    )
    canonical = None
    if dumped and dumped[1]:
        raw = dumped[1].decode("utf-8", "replace")
        canonical = module._canonical_pin_from_gallery_output(raw)
        print("JSON -J forneceu texto:", bool(module._gallery_caption_hint(raw)))
    else:
        print("JSON -J disponível: não")
    print("Pin canônico localizado:", bool(canonical))

    if canonical:
        page_caption = await module._fetch_pinterest_public_caption(canonical)
        print("Legenda por página/SEO:", repr(page_caption))
    post = await module._fetch_public_pin_gallery(url)
    if not post:
        print("Resultado: nenhuma mídia identificada")
    else:
        print("Mídias identificadas:", len(post.media_list))
        print("Tipos:", ", ".join(item.type for item in post.media_list))
        print("Legenda final:", repr(post.description))


if __name__ == "__main__":
    asyncio.run(main())
