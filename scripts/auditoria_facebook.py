#!/usr/bin/env python3
"""Auditoria unificada de posts do Facebook: links, downloads e identidade.

Uso:
  venv/bin/python scripts/auditoria_facebook.py
  venv/bin/python scripts/auditoria_facebook.py --arquivo links.txt
  venv/bin/python scripts/auditoria_facebook.py --url 'https://www.facebook.com/...'

Nao faz deploy, nao envia midias ao Telegram e nao modifica banco/cache do bot.
Falhas de identidade ficam classificadas como NAO VERIFICADAS.
"""
import argparse
import asyncio
import csv
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from dotenv import load_dotenv
from services.platforms.facebook_media import (
    _canonical_fb_media_url,
    _facebook_failure_reason,
    _facebook_group_post_id,
    _facebook_profile_post_id,
    _is_facebook_media_link,
    _is_facebook_url,
    _needs_redirect,
    _resolve_facebook_short_link,
    _verified_facebook_group_photos,
)

load_dotenv(BASE / ".env")
GALLERY = BASE / "venv/bin/gallery-dl"
YTDLP = BASE / "venv/bin/yt-dlp"
PROFILE = os.getenv("FACEBOOK_FIREFOX_PROFILE", "").strip()
IMAGES = {".jpg", ".jpeg", ".png", ".webp"}
VIDEOS = {".mp4", ".mkv", ".mov", ".webm", ".m4v"}
MEDIA = IMAGES | VIDEOS
MAX_LINKS = 20


def tipo_post(url):
    path = urlsplit(url).path.lower()
    if _facebook_group_post_id(url):
        return "grupo"
    if _facebook_profile_post_id(url):
        return "perfil_pagina"
    if path.rstrip("/") in {"/photo", "/photo.php"}:
        return "foto_direta"
    if "/media/set" in path or path.startswith("/photos/"):
        return "album"
    if re.search(r"/reels?/\d+/?$", path):
        return "reel"
    if re.search(r"/(?:videos|watch)(?:/|$)", path) or path.endswith("/video.php"):
        return "video"
    if "story.php" in path or "permalink.php" in path:
        return "post_generico"
    if path.startswith("/share/"):
        return "compartilhamento"
    return "outro"


def arquivos_midia(pasta):
    if not pasta.is_dir():
        return []
    return sorted(
        p for p in pasta.rglob("*")
        if p.is_file() and p.suffix.lower() in MEDIA and p.stat().st_size
    )


def metadados_imagem(arquivo):
    sidecar = Path(str(arquivo) + ".json")
    try:
        dado = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return dado if isinstance(dado, dict) else None


def id_video_esperado(url):
    parsed = urlsplit(url)
    path = parsed.path.lower()
    m = re.search(r"/(?:videos|reel|reels)/(\d+)/?$", path)
    if m:
        return m.group(1)
    if path.rstrip("/") in {"/watch", "/video.php"}:
        vals = parse_qs(parsed.query).get("v", [])
        if len(vals) == 1 and vals[0].isdigit():
            return vals[0]
    return None


def validar_imagens(url, fotos):
    """Status de vinculo com a URL solicitada, nunca apenas sucesso do comando."""
    if not fotos:
        return "sem_midia", "nenhuma imagem"
    if len(fotos) > 10:
        return "nao_verificado", "mais de 10 imagens; escopo excedido"
    if any(p.suffix.lower() not in IMAGES for p in fotos):
        return "nao_verificado", "resultado misto de imagens e videos"

    post = _facebook_group_post_id(url)
    if post:
        valido = _verified_facebook_group_photos(url, [str(p) for p in fotos])
        return ("verificado" if valido else "nao_verificado",
                f"set_id + diretorio + IDs associados a pcb.{post}"
                if valido else "grupo sem identidade consistente com o post")

    parsed = urlsplit(url)
    path = parsed.path.lower().rstrip("/")
    if path in {"/photo", "/photo.php"}:
        ids = parse_qs(parsed.query).get("fbid", [])
        if len(fotos) != 1 or len(ids) != 1 or not ids[0].isdigit():
            return "nao_verificado", "fbid unico nao encontrado"
        foto = fotos[0]
        info = metadados_imagem(foto)
        valido = (foto.stem == ids[0] and info is not None
                  and str(info.get("id")) == ids[0])
        return ("verificado" if valido else "nao_verificado",
                "ID direto do link coincide com arquivo e JSON" if valido
                else "ID da foto diverge do link ou metadados")

    if path in {"/media/set", "/media/set/"}:
        vals = parse_qs(parsed.query).get("set", [])
        if len(vals) == 1 and vals[0]:
            album = vals[0]
            valido = all(
                ((meta := metadados_imagem(p)) is not None
                 and str(meta.get("set_id", "")) == album
                 and str(meta.get("id", "")) == p.stem
                 and f"({album})" in p.parent.name)
                for p in fotos
            )
            return ("verificado" if valido else "nao_verificado",
                    "set e IDs coincidem com album solicitado"
                    if valido else "set/IDs do album nao coincidem")

    perfil_post = _facebook_profile_post_id(url)
    if perfil_post:
        perfil = parsed.path.strip("/").split("/")[0]
        coerentes = []
        for foto in fotos:
            meta = metadados_imagem(foto)
            if meta is None:
                coerentes.append(False)
                continue
            post_photo = str(meta.get("post_photo") or "").replace("\\/", "/")
            from html import unescape
            from urllib.parse import unquote
            foto_link = urlsplit(unquote(unescape(post_photo)))
            foto_ids = parse_qs(foto_link.query).get("fbid", [])
            coerentes.append(
                str(meta.get("id")) == foto.stem
                and str(meta.get("user_id")) == perfil
                and foto_link.scheme == "https"
                and _is_facebook_url(post_photo)
                and foto_ids == [foto.stem]
            )
        if coerentes and all(coerentes):
            return "nao_verificado", (
                f"foto/autor/post_photo coerentes, MAS sem vinculo provado "
                f"ao ID do post {perfil_post}"
            )
        return "nao_verificado", "post de perfil: metadados incompletos ou divergentes"

    return "nao_verificado", "nao existe validador do ID da publicacao para este formato"


def executar(comando, pasta, limite_tempo):
    pasta.mkdir(parents=True, exist_ok=True)
    pasta.chmod(0o700)
    try:
        proc = subprocess.run(
            comando, capture_output=True, text=True, errors="replace",
            timeout=limite_tempo, cwd=BASE
        )
        detalhe = _facebook_failure_reason(proc.stderr) if proc.returncode else None
        return proc.returncode, detalhe
    except subprocess.TimeoutExpired:
        return -1, "tempo_esgotado"
    except OSError:
        return -2, "ferramenta_indisponivel"


def auditar_gallery(url, pasta, autenticado):
    comando = [
        str(GALLERY), "--range", "1-10", "--write-metadata", "--no-mtime",
        "-o", "extractor.facebook.videos=ytdl", "-d", str(pasta),
    ]
    if autenticado:
        comando.extend(["--cookies-from-browser", f"firefox:{PROFILE}"])
    comando.append(url)
    codigo, erro = executar(comando, pasta, 140)
    arquivos = arquivos_midia(pasta)
    fotos = [p for p in arquivos if p.suffix.lower() in IMAGES]
    videos = [p for p in arquivos if p.suffix.lower() in VIDEOS]
    if not arquivos:
        status, motivo = "sem_midia", "nenhuma midia extraida"
    elif videos:
        status, motivo = "nao_verificado", (
            "gallery-dl retornou video; sem identidade comprovada"
        )
    else:
        status, motivo = validar_imagens(url, fotos)
    if codigo != 0:
        # Mesmo havendo arquivos parciais, nao considerar sucesso.
        status = "falha"
        motivo = f"gallery-dl saiu com codigo {codigo}; arquivos nao aprovados"
    return resultado("gallery-dl", autenticado, codigo, erro, status, motivo, arquivos, pasta)


def auditar_ytdlp(url, pasta, autenticado, limite_mb):
    comando = [
        str(YTDLP), "--no-playlist", "--no-progress",
        "--retries", "1", "--fragment-retries", "1",
        "--socket-timeout", "15", "--max-filesize", f"{limite_mb}M",
        "--max-downloads", "1", "--write-info-json",
        "--merge-output-format", "mp4", "--restrict-filenames",
        "-o", str(pasta / "%(id)s.%(ext)s"),
    ]
    if autenticado:
        comando.extend(["--cookies-from-browser", f"firefox:{PROFILE}"])
    comando.append(url)
    codigo, erro = executar(comando, pasta, 160)
    arquivos = arquivos_midia(pasta)
    videos = [p for p in arquivos if p.suffix.lower() in VIDEOS]
    if codigo != 0:
        status, motivo = "falha", f"yt-dlp saiu com codigo {codigo}"
    elif not videos:
        status, motivo = "sem_midia", "nenhum video baixado"
    else:
        esperado = id_video_esperado(url)
        iguais = []
        for video in videos:
            try:
                sidecar = video.with_suffix(".info.json")
                meta = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = None
            iguais.append(
                bool(esperado and isinstance(meta, dict)
                     and str(meta.get("id")) == esperado
                     and str(meta.get("extractor_key", "")).lower().startswith("facebook"))
            )
        if esperado and iguais and all(iguais):
            status, motivo = "verificado", f"video e JSON coincidem com ID {esperado}"
        else:
            status, motivo = "nao_verificado", (
                "video extraido, mas ID canônico da publicacao nao foi comprovado"
            )
    return resultado("yt-dlp", autenticado, codigo, erro, status, motivo, arquivos, pasta)


def resultado(ferramenta, autenticado, codigo, erro, status, motivo, arquivos, pasta):
    return {
        "ferramenta": ferramenta,
        "acesso": "firefox" if autenticado else "publico",
        "codigo": codigo,
        "erro_categoria": erro,
        "status": status,
        "motivo": motivo,
        "midias": [
            {"arquivo": str(p.relative_to(pasta)), "bytes": p.stat().st_size,
             "tipo": "imagem" if p.suffix.lower() in IMAGES else "video",
             "id_arquivo": p.stem}
            for p in arquivos
        ],
        "pasta": str(pasta),
    }


async def auditar_link(original, numero, raiz, testar_firefox=True, testar_video=True, max_video_mb=150):
    registro = {
        "numero": numero, "original": original,
        "resolvida": None, "tipo": None, "status": "falha",
        "tentativas": [], "observacoes": [],
    }
    if "://" not in original:
        original = "https://" + original
    try:
        scheme = urlsplit(original).scheme
    except ValueError:
        scheme = ""
    if scheme != "https" or not _is_facebook_url(original) or not _is_facebook_media_link(original):
        registro["observacoes"].append("URL rejeitada: nao e um post especifico HTTPS do Facebook")
        return registro
    destino = await _resolve_facebook_short_link(original) if _needs_redirect(original) else _canonical_fb_media_url(original)
    if not destino or not _is_facebook_media_link(destino):
        registro["observacoes"].append("redirecionamento falhou ou destino nao permitido")
        return registro
    registro["resolvida"] = destino
    categoria = tipo_post(destino)
    registro["tipo"] = categoria
    raiz_item = raiz / f"{numero:02d}_{categoria}"
    raiz_item.mkdir(parents=True, exist_ok=True)
    raiz_item.chmod(0o700)
    print(f"\n[{numero}] {categoria}: {destino}")
    print("    Testando extracao de imagens/album...")
    modos = [False, True] if testar_firefox and PROFILE else [False]
    for auth in modos:
        pasta = raiz_item / ("gallery_firefox" if auth else "gallery_publico")
        teste = auditar_gallery(destino, pasta, auth)
        registro["tentativas"].append(teste)
        print(f"    gallery-dl/{teste['acesso']}: {teste['status']} | {len(teste['midias'])} midias")
    if testar_video and categoria not in {"foto_direta", "album"}:
        print("    Testando extracao de video...")
        for auth in modos:
            pasta = raiz_item / ("video_firefox" if auth else "video_publico")
            teste = auditar_ytdlp(destino, pasta, auth, max_video_mb)
            registro["tentativas"].append(teste)
            print(f"    yt-dlp/{teste['acesso']}: {teste['status']} | {len(teste['midias'])} midias")

    tentativas = registro["tentativas"]
    # Comparar somente a MESMA ferramenta entre acessos.
    # Uma imagem e um video do mesmo post podem ter IDs diferentes normalmente.
    divergencias = []
    for ferramenta in ("gallery-dl", "yt-dlp"):
        conjuntos = {
            tuple(sorted(m["id_arquivo"] for m in t["midias"]))
            for t in tentativas
            if t["ferramenta"] == ferramenta and t["midias"]
        }
        if len(conjuntos) > 1:
            divergencias.append(ferramenta)
    if divergencias:
        registro["observacoes"].append(
            "CONFLITO entre IDs no acesso publico e Firefox em "
            + ", ".join(divergencias) + "; nenhuma aprovacao automatica"
        )
        registro["status"] = "conflito"
    elif any(t["status"] == "verificado" for t in tentativas):
        registro["status"] = "verificado"
    elif any(t["midias"] for t in tentativas):
        registro["status"] = "nao_verificado"
    else:
        registro["status"] = "falha"
    if categoria == "perfil_pagina":
        registro["observacoes"].append(
            "ID da foto/post_photo e autor nao comprovam vinculo ao ID da publicacao"
        )
    if not testar_firefox or not PROFILE:
        registro["observacoes"].append("Firefox nao testado nesta execucao")
    return registro


def autoteste():
    """Checagens sem rede, sem cookies e sem fazer download."""
    assert tipo_post("https://www.facebook.com/groups/9/permalink/123/") == "grupo"
    assert tipo_post("https://www.facebook.com/77/posts/123/") == "perfil_pagina"
    assert tipo_post("https://www.facebook.com/reel/123/") == "reel"
    assert tipo_post("https://www.facebook.com/photo/?fbid=987") == "foto_direta"
    assert id_video_esperado("https://www.facebook.com/reel/123/") == "123"
    assert id_video_esperado("https://www.facebook.com/watch/?v=987") == "987"
    with tempfile.TemporaryDirectory() as tmp:
        pasta = Path(tmp) / "Photos from Example (pcb.123)"
        pasta.mkdir()
        foto = pasta / "987.jpg"
        foto.write_bytes(b"imagem de teste")
        meta = {
            "id": "987", "set_id": "pcb.123", "user_id": "77",
            "post_photo": "https://www.facebook.com/photo/?fbid=987",
            "post_text": "Texto de teste",
        }
        Path(str(foto) + ".json").write_text(json.dumps(meta), encoding="utf-8")
        status, _ = validar_imagens(
            "https://www.facebook.com/groups/9/permalink/123/", [foto]
        )
        assert status == "verificado", "grupo de fotos com identidade completa"
        status, _ = validar_imagens(
            "https://www.facebook.com/77/posts/123/", [foto]
        )
        assert status == "nao_verificado", "post de perfil exige vínculo próprio"
        status, _ = validar_imagens(
            "https://www.facebook.com/photo/?fbid=987", [foto]
        )
        assert status == "verificado", "foto direta com ID consistente"
        status, _ = validar_imagens(
            "https://www.facebook.com/photo/?fbid=888", [foto]
        )
        assert status == "nao_verificado", "ID errado não pode ser aprovado"
    print("✅ AUTOTESTE LOCAL APROVADO: grupo, perfil, foto direta, Reel e video.")


def ler_entradas(args):
    links = list(args.url or [])
    if args.arquivo:
        arquivo = Path(args.arquivo).expanduser()
        links.extend(
            linha.strip() for linha in arquivo.read_text(encoding="utf-8").splitlines()
            if linha.strip() and not linha.lstrip().startswith("#")
        )
    if not links:
        print("Cole links do Facebook, UM POR LINHA. Enter vazio inicia a auditoria.")
        while len(links) < MAX_LINKS:
            try:
                linha = input(f"Link {len(links) + 1}: ").strip()
            except EOFError:
                break
            if not linha:
                break
            links.append(linha)
    # Deduplicar preservando ordem.
    unicos = list(dict.fromkeys(links))
    if len(unicos) > MAX_LINKS:
        raise ValueError(f"Limite de {MAX_LINKS} links por auditoria")
    return unicos


def escrever_relatorios(raiz, registros, config):
    resumo = {
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(),
        "configuracao": config,
        "total_links": len(registros),
        "totais": {
            status: sum(r["status"] == status for r in registros)
            for status in ("verificado", "nao_verificado", "conflito", "falha")
        },
        "resultados": registros,
    }
    (raiz / "auditoria.json").write_text(
        json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (raiz / "auditoria.csv").open("w", newline="", encoding="utf-8") as arq:
        campos = ["numero", "tipo", "status", "ferramenta", "acesso",
                  "status_tentativa", "midias", "codigo", "erro_categoria", "motivo"]
        writer = csv.DictWriter(arq, fieldnames=campos)
        writer.writeheader()
        for r in registros:
            for t in r["tentativas"]:
                writer.writerow({
                    "numero": r["numero"], "tipo": r["tipo"], "status": r["status"],
                    "ferramenta": t["ferramenta"], "acesso": t["acesso"],
                    "status_tentativa": t["status"], "midias": len(t["midias"]),
                    "codigo": t["codigo"], "erro_categoria": t["erro_categoria"],
                    "motivo": t["motivo"],
                })
    linhas = [
        "AUDITORIA UNIFICADA DO FACEBOOK",
        f"Links: {len(registros)} | {resumo['totais']}",
        "VERIFICADO = ID da midia ligado a um identificador pedido pela URL.",
        "NAO VERIFICADO = download existe, mas associacao ao post nao foi demonstrada.",
        "CONFLITO = IDs diferentes por modo de acesso, exige revisao manual.",
        "FALHA = sem midia ou erro.",
        "",
    ]
    for r in registros:
        linhas.append(f"[{r['numero']}] {r['tipo']} -> {r['status']}")
        linhas.append(f"URL resolvida: {r['resolvida'] or '(nao resolvida)'}")
        for t in r["tentativas"]:
            ids = ", ".join(m["id_arquivo"] for m in t["midias"]) or "-"
            linhas.append(
                f"  {t['ferramenta']}/{t['acesso']}: {t['status']} "
                f"({len(t['midias'])} midias; IDs {ids}) - {t['motivo']}"
            )
        for obs in r["observacoes"]:
            linhas.append(f"  AVISO: {obs}")
        linhas.append("")
    (raiz / "resumo.txt").write_text("\n".join(linhas), encoding="utf-8")
    return resumo


async def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Superauditoria de posts Facebook em lote")
    parser.add_argument("--autoteste", action="store_true",
                        help="Verificar classificadores/validadores sem rede")
    parser.add_argument("--arquivo", help="TXT com um link por linha")
    parser.add_argument("--url", action="append", help="URL para auditoria; pode repetir")
    parser.add_argument("--sem-firefox", action="store_true", help="Nao utilizar sessao autenticada")
    parser.add_argument("--sem-videos", action="store_true", help="Nao executar yt-dlp")
    parser.add_argument("--max-video-mb", type=int, default=150,
                        help="Tamanho maximo por video (padrao 150 MB)")
    args = parser.parse_args()
    if args.autoteste:
        autoteste()
        return
    if not 1 <= args.max_video_mb <= 300:
        parser.error("--max-video-mb deve estar entre 1 e 300")
    if not GALLERY.exists():
        parser.error(f"gallery-dl nao encontrado em {GALLERY}")
    if not args.sem_videos and not YTDLP.exists():
        parser.error(f"yt-dlp nao encontrado em {YTDLP}")
    try:
        links = ler_entradas(args)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    if not links:
        parser.error("Nenhum link para auditar")
    raiz = BASE / "facebook_auditorias" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    raiz.mkdir(parents=True, exist_ok=False)
    raiz.chmod(0o700)
    config = {
        "usar_firefox": bool(PROFILE and not args.sem_firefox),
        "testar_ytdlp": not args.sem_videos,
        "limite_video_mb": args.max_video_mb,
        "max_links": MAX_LINKS,
        "max_fotos_por_tentativa": 10,
    }
    print("\n🔎 Auditoria iniciada:", raiz)
    print("   Modos: publico", "+ Firefox" if config["usar_firefox"] else "")
    registros = []
    for i, url in enumerate(links, 1):
        try:
            registros.append(await auditar_link(
                url, i, raiz,
                testar_firefox=config["usar_firefox"],
                testar_video=config["testar_ytdlp"],
                max_video_mb=args.max_video_mb,
            ))
        except Exception as exc:
            # Isolar erro de um link; prosseguir para os demais.
            registros.append({
                "numero": i, "original": url, "resolvida": None, "tipo": None,
                "status": "falha", "tentativas": [],
                "observacoes": [f"erro inesperado: {type(exc).__name__}"],
            })
            print(f"[{i}] Falha inesperada: {type(exc).__name__}")
    relatorio = escrever_relatorios(raiz, registros, config)
    print("\n📋 AUDITORIA CONCLUIDA")
    print("Totais:", relatorio["totais"])
    print("📝 Relatorio legivel:", raiz / "resumo.txt")
    print("📊 Planilha:", raiz / "auditoria.csv")
    print("🧾 Dados completos:", raiz / "auditoria.json")
    print("⚠️  NAO VERIFICADO nunca significa foto certa; fotos autenticadas permanecem locais.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAuditoria interrompida.")
