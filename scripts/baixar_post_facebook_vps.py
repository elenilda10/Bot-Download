#!/usr/bin/env python3
"""Teste manual de links Facebook na VPS. Não envia nada ao Telegram."""
import asyncio
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

# Funciona tanto na raiz do projeto quanto dentro de scripts/.
BASE = Path(__file__).resolve().parent
if not (BASE / "venv/bin/gallery-dl").is_file():
    BASE = BASE.parent
sys.path.insert(0, str(BASE))

from dotenv import load_dotenv
from services.platforms.facebook_media import (
    _is_facebook_media_link,
    _is_facebook_url,
    _resolve_facebook_short_link,
)

load_dotenv(BASE / ".env")
GALLERY = BASE / "venv/bin/gallery-dl"
PROFILE = os.getenv("FACEBOOK_FIREFOX_PROFILE", "").strip()
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".webm", ".mkv"}


def midias(pasta):
    return sorted(
        f for f in pasta.rglob("*")
        if f.is_file() and f.suffix.lower() in EXTENSIONS and f.stat().st_size > 0
    )


def baixar(link, pasta, *, autenticado=False):
    pasta.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(GALLERY), "--range", "1-10", "--write-metadata", "--no-mtime",
        "-d", str(pasta),
    ]
    if autenticado:
        cmd.extend(["--cookies-from-browser", f"firefox:{PROFILE}"])
    cmd.append(link)
    try:
        result = subprocess.run(cmd, text=True, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        print("⏳ gallery-dl excedeu 120 segundos.")
        return False
    except OSError as exc:
        print(f"❌ Falha ao iniciar gallery-dl: {type(exc).__name__}")
        return False

    files = midias(pasta)
    print(f"Código gallery-dl: {result.returncode} | Mídias: {len(files)}")
    if result.returncode and result.stderr:
        # Apenas uma linha de diagnóstico, sem imprimir conteúdo dos cookies.
        errors = [s.strip() for s in result.stderr.splitlines() if s.strip()]
        if errors:
            last = errors[-1]
            if "AuthRequired" in last:
                print("Motivo: o Facebook exigiu autenticação.")
            else:
                print("Motivo:", last[:240])

    for file in files:
        print(f"  • {file} ({file.stat().st_size:,} bytes)")
    return bool(files) and result.returncode == 0


async def main():
    original = input("Cole o link do post do Facebook: ").strip()
    if not original:
        print("❌ Nenhum link informado.")
        return
    if "://" not in original:
        original = "https://" + original
    if urlsplit(original).scheme != "https" or not _is_facebook_url(original) or not _is_facebook_media_link(original):
        print("❌ Informe um link HTTPS direto de publicação, foto ou vídeo do Facebook.")
        return

    resolvido = await _resolve_facebook_short_link(original)
    if not resolvido:
        print("❌ Não foi possível resolver um link de mídia do Facebook.")
        return
    print("\n🔗 Link original:", original)
    print("↪️  Link resolvido:", resolvido)

    destino = BASE / "facebook_testes" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    print("\n📁 Pasta de saída:", destino)
    print("\n1/2 Tentando download público...")
    if baixar(resolvido, destino / "publico"):
        print("\n✅ Download público concluído.")
    elif PROFILE:
        print("\n2/2 Tentando com a sessão Firefox da VPS...")
        if baixar(resolvido, destino / "firefox", autenticado=True):
            print("\n✅ Download autenticado concluído.")
        else:
            print("\n❌ Download não concluído nem com Firefox.")
    else:
        print("\n⚠️  Sem FACEBOOK_FIREFOX_PROFILE no .env para tentativa autenticada.")

    print("\n⚠️  A mídia ainda precisa ser conferida: o gallery-dl pode escolher outra foto da página.")
    print("Os arquivos .json de metadados ficaram ao lado das mídias.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrompido.")
