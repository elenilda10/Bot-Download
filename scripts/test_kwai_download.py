"""Isolated Kwai smoke download; no bot token, Telegram send or DB needed.

Run from a temporary source checkout before installing/restarting the bot.
Firefox credentials remain local and are never printed or sent to the CDN.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import types
import uuid
from pathlib import Path


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--firefox-profile")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    # Avoid services/__init__.py: it imports the running bot's DB/config.
    for name, directory in (
        ("services", root / "services"),
        ("services.platforms", root / "services/platforms"),
    ):
        package = types.ModuleType(name)
        package.__path__ = [str(directory)]
        sys.modules[name] = package

    from services.platforms.kwai_service import download_kwai_media

    output = args.output_dir or Path(tempfile.mkdtemp(prefix="kwai-smoke-"))
    print("Testando extração e download do Kwai...", flush=True)
    result = await download_kwai_media(
        args.url, output_dir=str(output),
        post_id="kwai_" + uuid.uuid4().hex[:16],
        max_file_size=64 * 1024 * 1024,
        firefox_profile=args.firefox_profile,
    )
    if not result:
        raise SystemExit("FALHOU: nenhum vídeo com identidade verificada foi baixado.")
    item = result.media_list[0]
    print("SUCESSO: vídeo com identidade verificada")
    print("Autor:", result.author)
    print("Legenda:", result.description)
    print("Duração (s):", item.duration)
    print("Resolução:", item.width, "x", item.height)
    print("Bytes:", Path(item.url).stat().st_size)
    print("Arquivo:", item.url)
    print("Miniatura:", item.thumb or "[não gerada]")


if __name__ == "__main__":
    asyncio.run(main())
