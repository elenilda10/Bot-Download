#!/usr/bin/env python3
"""Teste manual de links Facebook na VPS. Não envia nada ao Telegram."""
import asyncio
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from html import unescape
from urllib.parse import parse_qs, unquote, urlsplit

# Funciona tanto na raiz do projeto quanto dentro de scripts/.
BASE = Path(__file__).resolve().parent
if not (BASE / "venv/bin/gallery-dl").is_file():
    BASE = BASE.parent
sys.path.insert(0, str(BASE))

from dotenv import load_dotenv
from services.platforms.facebook_media import (
    _facebook_group_post_id,
    _facebook_profile_post_id,
    _is_facebook_media_link,
    _is_facebook_url,
    _verified_facebook_group_photos,
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


def diagnosticar_post_perfil(link, arquivos):
    """Mostra indícios sem fingir comprovação do ID da publicação.

    O Facebook/gallery-dl pode extrair uma foto diferente da solicitada e
    ainda gerar metadados internamente consistentes. Só o autor e o fbid
    coincidirem NÃO demonstra o vínculo ao post.
    """
    post_id = _facebook_profile_post_id(link)
    if not post_id:
        return
    perfil = urlsplit(link).path.strip("/").split("/")[0]
    print(f"🔎 Publicação de perfil/página: {post_id} (perfil {perfil})")
    for arquivo in arquivos:
        if arquivo.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            print(f"  • {arquivo.name}: não é foto; vínculo não verificado")
            continue
        try:
            meta = json.loads(
                arquivo.with_name(arquivo.name + ".json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            print(f"  • {arquivo.name}: metadados ausentes; NÃO VERIFICADO")
            continue
        if not isinstance(meta, dict):
            print(f"  • {arquivo.name}: metadados inválidos; NÃO VERIFICADO")
            continue
        foto_id = arquivo.stem
        origem = unquote(unescape(str(meta.get("post_photo") or "").replace("\\/", "/")))
        parsed = urlsplit(origem)
        fbids = parse_qs(parsed.query).get("fbid", [])
        url_foto_valida = (
            parsed.scheme == "https"
            and _is_facebook_url(origem)
            and parsed.path.lower().rstrip("/") in {"/photo", "/photo.php"}
        )
        meta_bate = str(meta.get("id")) == foto_id
        post_photo_bate = url_foto_valida and fbids == [foto_id]
        autor_bate = str(meta.get("user_id")) == perfil
        print(f"  • Foto ID: {foto_id}")
        print(f"    Arquivo = ID do JSON: {meta_bate}")
        print(f"    Arquivo = fbid do post_photo: {post_photo_bate}")
        print(f"    Autor = perfil do post: {autor_bate}")
        print(f"    Texto post_text disponível: {bool(meta.get('post_text'))}")
        print("    ⚠️ Nenhum desses campos vincula diretamente a foto ao post", post_id)


def validar_midias(link, arquivos):
    """Aprova só fotos de grupo/post ou foto com ID direto comprovados.

    Um arquivo existir, ou o gallery-dl sair com código 0, não prova que ele
    pertence à publicação solicitada. Publicações de perfis, vídeos e outros
    formatos permanecem como downloads NÃO VERIFICADOS neste script.
    """
    post_grupo = _facebook_group_post_id(link)
    if post_grupo:
        aprovou = _verified_facebook_group_photos(
            link, [str(arquivo) for arquivo in arquivos]
        )
        motivo = (
            f"álbum, arquivos e metadados coincidem com o post {post_grupo}"
            if aprovou else "foto sem associação comprovada ao post do grupo"
        )
        return aprovou, motivo

    partes = urlsplit(link)
    if partes.path.lower().rstrip("/") in {"/photo", "/photo.php"}:
        ids = parse_qs(partes.query).get("fbid", [])
        if len(ids) != 1 or not ids[0].isdigit() or len(arquivos) != 1:
            return False, "link de foto sem ID numérico único ou mídia múltipla"
        foto = arquivos[0]
        if foto.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            return False, "arquivo recebido não é uma imagem permitida"
        if foto.stem != ids[0]:
            return False, "ID no nome do arquivo diferente do ID solicitado"
        try:
            dados = json.loads(
                foto.with_name(foto.name + ".json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return False, "metadados da foto ausentes ou inválidos"
        if not isinstance(dados, dict) or str(dados.get("id")) != ids[0]:
            return False, "ID do arquivo difere do ID dos metadados"
        return True, f"foto direta com fbid {ids[0]} comprovado"

    return False, "formato sem validador de identidade disponível neste script"


def baixar(link, pasta, *, autenticado=False):
    pasta.mkdir(parents=True, exist_ok=True)
    pasta.chmod(0o700)
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
    if not files or result.returncode != 0:
        return False

    aprovado, motivo = validar_midias(link, files)
    if _facebook_profile_post_id(link):
        diagnosticar_post_perfil(link, files)
    if aprovado:
        print("✅ IDENTIDADE VERIFICADA:", motivo)
    else:
        print("⚠️  DOWNLOAD NÃO VERIFICADO:", motivo)
        print("⚠️  Arquivos preservados para inspeção, sem aprovação automática.")
    return aprovado


async def main():
    os.umask(0o077)  # Impede leitura acidental de downloads autenticados por outros usuários.
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
        print("\n✅ Download público VERIFICADO.")
    elif PROFILE:
        print("\n2/2 Tentando com a sessão Firefox da VPS...")
        if baixar(resolvido, destino / "firefox", autenticado=True):
            print("\n✅ Download autenticado VERIFICADO.")
        else:
            print("\n⚠️  Nenhum download passou na validação de identidade.")
    else:
        print("\n⚠️  Nenhuma mídia aprovada. Sem Firefox configurado para outra tentativa.")

    print("\nOs arquivos e metadados .json ficaram na pasta acima.")
    print("Somente mídias com identidade VERIFICADA devem ser consideradas aprovadas.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrompido.")
