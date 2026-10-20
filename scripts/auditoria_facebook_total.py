#!/usr/bin/env python3
"""Auditoria isolada de mídias Facebook: múltiplos formatos, modos e um relatório.

Não usa Telegram, não mexe em cache e não reinicia o bot. Mantém resultados
autenticados privados e nunca assume que download == prova de identidade.
"""
import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from dotenv import load_dotenv  # noqa: E402
from services.platforms.facebook_media import (  # noqa: E402
    _facebook_group_post_id,
    _facebook_login_prompt,
    _facebook_profile_post_id,
    _is_facebook_media_link,
    _is_facebook_url,
    _resolve_facebook_short_link,
    _verified_facebook_group_photos,
)
from scripts.baixar_post_facebook_vps import midias, validar_midias  # noqa: E402

load_dotenv(BASE / ".env")
GALLERY = BASE / "venv/bin/gallery-dl"
YTDLP = BASE / "venv/bin/yt-dlp"
PROFILE = os.getenv("FACEBOOK_FIREFOX_PROFILE", "").strip()

# Referências reais dos testes anteriores, não uma promessa de que continuam acessíveis.
CONHECIDOS = [
    ("Grupo / foto", "https://www.facebook.com/share/p/1GdzWY4FEU/", ("3885634374927658",)),
    ("Página LaLiga / foto", "https://www.facebook.com/share/1MGrq5g49B/", ()),
    ("Perfil / foto (referência visual)", "https://www.facebook.com/share/1MbW3uewmv/", ("1162826316324741",)),
]
TIPOS = (
    "grupo_post", "perfil_pagina_post", "foto_direta", "album",
    "reel", "video", "watch", "story_php", "outro_post"
)
IMAGENS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEOS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
EXTS = IMAGENS | VIDEOS


def json_privado(caminho, dados):
    with caminho.open("x", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
        f.write("\n")


def tipo_de(url):
    p = urlsplit(url)
    caminho = p.path.lower()
    if _facebook_group_post_id(url):
        return "grupo_post"
    if _facebook_profile_post_id(url):
        return "perfil_pagina_post"
    if caminho.rstrip("/") in {"/photo", "/photo.php"}:
        return "foto_direta"
    if caminho.startswith("/media/set") or "/photos/" in caminho:
        return "album"
    if re.search(r"/reels?/\d+/?$", caminho):
        return "reel"
    if re.search(r"/videos/\d+/?$", caminho):
        return "video"
    if caminho.rstrip("/") == "/watch":
        return "watch"
    if caminho.rstrip("/") == "/story.php":
        return "story_php"
    return "outro_post"


def normalizar_story_php(url):
    """Converte story.php para o mesmo post numérico, sem tracking.

    Só altera se id/story_fbid/post_id forem compatíveis. Outros formatos
    permanecem inalterados para não direcionar a extração a outra mídia.
    """
    partes = urlsplit(url)
    if partes.path.lower().rstrip("/") != "/story.php":
        return url, None
    query = parse_qs(partes.query)
    perfil = query.get("id", [])
    post = query.get("story_fbid", [])
    if len(perfil) != 1 or len(post) != 1:
        return url, "story.php não fornece um par único id/story_fbid"
    if not perfil[0].isdigit() or not post[0].isdigit():
        return url, "story.php contém IDs não numéricos"
    composto = query.get("post_id", [])
    if composto and composto != [f"{perfil[0]}_{post[0]}"]:
        return url, "story.php tem post_id divergente; sem conversão"
    return (
        f"https://www.facebook.com/{perfil[0]}/posts/{post[0]}/",
        "story.php convertido para post numérico sem parâmetros de rastreamento",
    )


def id_foto_em_post_photo(valor):
    if not isinstance(valor, str):
        return None
    url = unquote(unescape(valor.replace("\\/", "/")))
    p = urlsplit(url)
    if (p.scheme != "https" or not _is_facebook_url(url)
            or p.path.lower().rstrip("/") not in {"/photo", "/photo.php"}):
        return None
    ids = parse_qs(p.query).get("fbid", [])
    return ids[0] if len(ids) == 1 and ids[0].isdigit() else None


def sidecar(foto):
    try:
        dados = json.loads(Path(str(foto) + ".json").read_text(encoding="utf-8"))
        return dados if isinstance(dados, dict) else None
    except (OSError, ValueError):
        return None


def fotos_de_perfil(url, arquivos):
    """Não considera coincidência foto/JSON como prova do post."""
    post = _facebook_profile_post_id(url)
    if not post or not arquivos or len(arquivos) > 20:
        return "INCONCLUSIVO", "Não é postagem específica de perfil ou lista limitada"
    pasta_esperada = "pcb." + post
    ids = []
    conjuntos = []
    for f in arquivos:
        if f.suffix.lower() not in IMAGENS or not f.stem.isdigit():
            return "INCONCLUSIVO", "Há arquivo que não é foto"
        meta = sidecar(f)
        if meta is None:
            return "INCONCLUSIVO", "JSON lateral ausente"
        if _facebook_login_prompt(meta):
            return "REJEITADO", "Metadados parecem tela de login"
        if str(meta.get("id")) != f.stem:
            return "REJEITADO", "ID do arquivo difere do JSON"
        set_id = str(meta.get("set_id") or "")
        conjuntos.append(set_id)
        if set_id:
            if set_id != pasta_esperada or f"({pasta_esperada})" not in f.parent.name:
                return "REJEITADO", "Álbum não aponta para o post solicitado"
        elif id_foto_em_post_photo(meta.get("post_photo")) != f.stem:
            return "REJEITADO", "post_photo aponta outra foto"
        ids.append(f.stem)
    if len(ids) != len(set(ids)):
        return "REJEITADO", "IDs duplicados no download"
    if all(x == pasta_esperada for x in conjuntos):
        return "VERIFICADO", "Álbum pcb.<post_id> corresponde ao post no caminho e no JSON"
    if any(x for x in conjuntos):
        return "INCONCLUSIVO", "Post com imagens de álbuns misturados"
    return "INCONCLUSIVO", "Autor, foto e post_photo podem coincidir sem comprovar ID do post"


def validar_fotos(url, arquivos, tipo):
    if not arquivos:
        return "SEM_MIDIA", "Nenhuma mídia baixada"
    if len(arquivos) > 20:
        return "INCONCLUSIVO", "Mais de 20 mídias; revisão manual necessária"
    if tipo == "grupo_post":
        if _verified_facebook_group_photos(url, [str(x) for x in arquivos]):
            return "VERIFICADO", "pcb.<post_id>, pasta, arquivos e JSON coincidem"
        return "REJEITADO", "Post de grupo sem associação de álbum válida"
    if tipo == "perfil_pagina_post":
        return fotos_de_perfil(url, arquivos)
    if tipo == "foto_direta":
        aprovado, motivo = validar_midias(url, arquivos)
        return ("VERIFICADO" if aprovado else "REJEITADO"), motivo
    if tipo == "album":
        s = parse_qs(urlsplit(url).query).get("set", [])
        if len(s) == 1 and s[0]:
            esperado = s[0]
            if all(
                x.suffix.lower() in IMAGENS and
                (m := sidecar(x)) is not None and
                str(m.get("set_id")) == esperado and
                str(m.get("id")) == x.stem
                for x in arquivos
            ):
                return "VERIFICADO", "Fotos e metadados correspondem ao set solicitado"
        return "INCONCLUSIVO", "Não foi possível associar álbum e imagens"
    return "INCONCLUSIVO", "Verificação de fotos não implementada para este tipo"


def extrair_id_video_direto(url):
    p = urlsplit(url)
    matches = re.findall(r"/(?:reel|reels|videos)/(\d+)/?$", p.path.lower())
    if matches:
        return matches[0]
    if p.path.lower().rstrip("/") == "/watch":
        ids = parse_qs(p.query).get("v", [])
        return ids[0] if len(ids) == 1 and ids[0].isdigit() else None
    return None


def analisar_video(url, arquivos):
    if not arquivos:
        return "SEM_MIDIA", "Nenhum vídeo encontrado"
    esperado = extrair_id_video_direto(url)
    if not esperado:
        return "INCONCLUSIVO", "Post ou grupo não fornece ID direto do vídeo"
    for video in arquivos:
        if video.suffix.lower() not in VIDEOS:
            return "INCONCLUSIVO", "Extensão de vídeo desconhecida"
        metadados = video.with_name(video.stem + ".info.json")
        try:
            info = json.loads(metadados.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "INCONCLUSIVO", "Vídeo sem .info.json"
        if str(info.get("id")) != esperado:
            return "REJEITADO", "ID do vídeo baixado difere do ID solicitado"
    return "VERIFICADO", "ID direto da URL confere com .info.json do vídeo"


def executar(comando, limite=100):
    try:
        processo = subprocess.run(
            comando, capture_output=True, text=True, timeout=limite,
            check=False
        )
    except subprocess.TimeoutExpired:
        return 124, "Tempo limite atingido"
    except OSError:
        return 127, "Extrator não disponível"
    saida = (processo.stderr or "")[-3000:]
    low = saida.lower()
    if "authrequired" in low or "login required" in low or "log in to" in low:
        erro = "Facebook exige login ou acesso ao conteúdo"
    elif "unsupported url" in low or "no suitable extractor" in low:
        erro = "URL não reconhecida pelo extrator"
    elif "http error 403" in low or "403 forbidden" in low:
        erro = "Acesso negado (403)"
    elif "http error 404" in low or "404 not found" in low:
        erro = "Post indisponível (404)"
    elif "private" in low or "not available" in low:
        erro = "Post privado, removido ou não acessível"
    elif "no video formats" in low or "no formats found" in low:
        erro = "Nenhum formato de vídeo encontrado"
    elif "no media" in low or "no images" in low:
        erro = "Nenhuma imagem encontrada (pode ser texto puro)"
    elif "captcha" in low or "checkpoint" in low:
        erro = "Facebook exige verificação de segurança"
    elif "maximum number of downloads reached" in low:
        erro = "Extrator atingiu limite artificial de downloads"
    elif processo.returncode:
        erro = "Falha não classificada; não compartilhar logs brutos com cookies"
    else:
        erro = ""
    return processo.returncode, erro


def cmd_cookies(autenticado):
    return ["--cookies-from-browser", f"firefox:{PROFILE}"] if autenticado else []


def preflight(url, autenticado, maximo):
    """Uma segunda leitura para detectar divergência; não prova autoria do post."""
    comando = [
        str(GALLERY), "--dump-json", "--range", f"1-{maximo}",
        *cmd_cookies(autenticado), url
    ]
    try:
        p = subprocess.run(comando, capture_output=True, text=True, timeout=70)
        if p.returncode or len(p.stdout) > 2_000_000:
            return {"estado": "indisponivel", "ids": []}
        data = json.loads(p.stdout)
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return {"estado": "indisponivel", "ids": []}
    encontrados = []
    pilha = [data]
    visitados = 0
    while pilha and visitados < 4000:
        obj = pilha.pop()
        visitados += 1
        if isinstance(obj, list):
            pilha.extend(obj[:80])
        elif isinstance(obj, dict):
            if "id" in obj and ("post_photo" in obj or "set_id" in obj):
                ident = str(obj.get("id") or "")
                if ident.isdigit():
                    encontrados.append(ident)
            pilha.extend(list(obj.values())[:80])
    return {"estado": "ok", "ids": sorted(set(encontrados), key=int)}


def resumo_arquivos(arquivos):
    infos = []
    for f in arquivos:
        met = sidecar(f) if f.suffix.lower() in IMAGENS else None
        infos.append({
            "arquivo": f.name,
            "bytes": f.stat().st_size,
            "id": f.stem,
            "json_id_coincide": bool(met and str(met.get("id")) == f.stem),
            "post_photo_id": id_foto_em_post_photo(met.get("post_photo")) if met else None,
            "set_id": str(met.get("set_id") or "") if met else "",
            "login_suspeito": bool(met and _facebook_login_prompt(met)),
        })
    return infos


def rodada_gallery(url, tipo, pasta, autenticado, maximo, sem_preflight):
    pasta.mkdir(mode=0o700)
    previo = {"estado": "desativado", "ids": []} if sem_preflight else preflight(url, autenticado, maximo)
    comando = [
        str(GALLERY), "--range", f"1-{maximo}", "--write-metadata",
        "--no-mtime", "-d", str(pasta), *cmd_cookies(autenticado), url
    ]
    saida, erro = executar(comando, 150)
    arquivos = midias(pasta)
    estado, motivo = validar_fotos(url, arquivos, tipo)
    ids = sorted([x.stem for x in arquivos])
    if saida != 0:
        estado = "ERRO" if not arquivos else "INCONCLUSIVO"
        motivo = erro or "Execução incompleta"
    if len(arquivos) >= maximo:
        estado, motivo = "PARCIAL", "Limite de mídias atingido; álbum pode estar truncado"
    if previo["estado"] == "ok" and previo["ids"] and sorted(previo["ids"]) != ids:
        estado = "CONFLITO", "IDs no pré-teste diferem dos IDs efetivamente baixados"
    return {
        "extrator": "gallery-dl", "modo": "firefox" if autenticado else "publico",
        "codigo": saida, "estado": estado, "motivo": motivo,
        "preflight": previo, "midias": resumo_arquivos(arquivos),
        "pasta": str(pasta), "erro": erro
    }


def rodada_video(url, pasta, autenticado):
    pasta.mkdir(mode=0o700)
    if not YTDLP.is_file():
        return {
            "extrator": "yt-dlp", "modo": "firefox" if autenticado else "publico",
            "codigo": 127, "estado": "ERRO", "motivo": "yt-dlp ausente na venv",
            "midias": [], "pasta": str(pasta), "erro": "yt-dlp indisponível"
        }
    comando = [
        str(YTDLP), "--no-playlist",
        "--restrict-filenames", "--write-info-json", "--no-write-thumbnail",
        "--no-progress", "-S", "+codec:avc:m4a",
        "-o", str(pasta / "%(id)s.%(ext)s"),
        *cmd_cookies(autenticado), url
    ]
    codigo, erro = executar(comando, 150)
    arquivos = sorted(
        x for x in pasta.iterdir() if x.is_file()
        and x.suffix.lower() in VIDEOS and x.stat().st_size > 0
    )
    estado, motivo = analisar_video(url, arquivos)
    if codigo:
        estado, motivo = ("ERRO" if not arquivos else "INCONCLUSIVO"), (erro or "Vídeo incompleto")
    return {
        "extrator": "yt-dlp", "modo": "firefox" if autenticado else "publico",
        "codigo": codigo, "estado": estado, "motivo": motivo,
        "midias": [{"arquivo": x.name, "bytes": x.stat().st_size, "id": x.stem} for x in arquivos],
        "pasta": str(pasta), "erro": erro
    }


def relatorio_markdown(relatorio):
    ls = ["# Auditoria unificada do Facebook", "",
          f"Data UTC: {relatorio['data_utc']}",
          "Execução: somente testes; bot Telegram não alterado.", "",
          "VERIFICADO = conferência de identidade suportada pelo tipo de URL.",
          "INCONCLUSIVO = download não comprova a publicação solicitada.",
          "CONFLITO = pré-teste, referência ou sessões discordaram.",
          "PARCIAL = limite atingido, não garante álbum inteiro.", "",
          "| Caso | Tipo | Público | Firefox | Observação |",
          "|---|---|---|---|---|"]
    for caso in relatorio["casos"]:
        if "erro" in caso:
            ls.append(f"| {caso['numero']} | inválido | — | — | {caso['erro']} |")
            continue
        modos = {"publico": [], "firefox": []}
        for r in caso["tentativas"]:
            modos[r["modo"]].append(f"{r['extrator']}: {r['estado']}")
        a = ", ".join(modos["publico"]) or "não executado"
        b = ", ".join(modos["firefox"]) or "não executado"
        obs = ", ".join(caso.get("avisos", [])) or "—"
        ls.append(f"| {caso['numero']} | {caso['tipo']} | {a} | {b} | {obs} |")
    ls.extend(["", "## Detalhes", ""])
    for caso in relatorio["casos"]:
        ls.append(f"### Caso {caso['numero']}: {caso.get('rotulo','link')}")
        ls.append(f"- Original: {caso['original']}")
        ls.append(f"- Resolvido: {caso.get('resolvido') or 'não resolvido'}")
        if caso.get("url_extracao"):
            ls.append(f"- Extração: {caso['url_extracao']}")
        if "erro" in caso:
            ls.append(f"- Erro: {caso['erro']}")
        for r in caso.get("tentativas", []):
            ids = ", ".join(m["id"] for m in r["midias"]) or "nenhum"
            ls.append(
                f"- {r['modo']} / {r['extrator']}: **{r['estado']}**; "
                f"IDs: {ids}; {r['motivo']}"
            )
        for aviso in caso.get("avisos", []):
            ls.append(f"- Atenção: {aviso}")
        ls.append("")
    ls.extend(["## Cobertura dos formatos", ""])
    for t in TIPOS:
        cobertura = sum(c.get("tipo") == t for c in relatorio["casos"])
        ls.append(f"- {t}: {cobertura} link(s) testado(s)")
    ls.extend(["", "Observação: uma extração com êxito não confirma o conteúdo visual.",
               "Confira casos INCONCLUSIVO/CONFLITO antes de encaminhar mídias ao Telegram.", ""])
    return "\n".join(ls)


async def auditar(caso, numero, pasta, args):
    rotulo, original, referencia = caso
    result = {
        "numero": numero, "rotulo": rotulo, "original": original,
        "referencia_ids": list(referencia), "tentativas": [], "avisos": [],
    }
    if not original.startswith("https://") or not _is_facebook_url(original) or not _is_facebook_media_link(original):
        result["erro"] = "URL não é uma mídia HTTPS válida do Facebook"
        return result
    resolvido = await _resolve_facebook_short_link(original)
    if not resolvido or not _is_facebook_url(resolvido):
        result["erro"] = "Redirecionamento não resolvido com segurança"
        return result
    tipo = tipo_de(resolvido)
    extraido, aviso_story = normalizar_story_php(resolvido)
    if aviso_story:
        result["avisos"].append(aviso_story)
    if extraido != resolvido:
        result["url_extracao"] = extraido
        tipo = tipo_de(extraido)
    result.update({"resolvido": resolvido, "tipo": tipo})
    print(f"\n[{numero}] {rotulo}: {tipo}\n↪️  {resolvido}", flush=True)
    if extraido != resolvido:
        print(f"    URL de extração: {extraido}", flush=True)
    resolvido = extraido
    modos = [False] if args.somente_publico or not PROFILE else [False, True]
    for autenticado in modos:
        modo = "firefox" if autenticado else "publico"
        raiz = pasta / f"caso_{numero:02d}" / modo
        raiz.mkdir(parents=True, exist_ok=True, mode=0o700)
        if tipo not in {"reel", "video", "watch"}:
            r = rodada_gallery(resolvido, tipo, raiz / "fotos", autenticado,
                               args.max_midias, args.sem_preflight)
            result["tentativas"].append(r)
            print(f"  {modo} gallery-dl: {r['estado']} — {len(r['midias'])} arquivo(s)")
        if tipo in {"reel", "video", "watch"} or (
            tipo in {"perfil_pagina_post", "grupo_post", "outro_post"}
            and not args.sem_videos_em_posts
        ):
            r = rodada_video(resolvido, raiz / "videos", autenticado)
            result["tentativas"].append(r)
            print(f"  {modo} yt-dlp: {r['estado']} — {len(r['midias'])} arquivo(s)")
    # Baseline visual/observado: referência útil, não constitui prova por si.
    if referencia:
        for t in result["tentativas"]:
            ids = {m["id"] for m in t["midias"]}
            if not ids:
                continue
            if ids == set(referencia):
                t["referencia"] = "CONFERE"
            else:
                t["referencia"] = "DIVERGE"
                t["estado"] = "CONFLITO"
                t["motivo"] += "; não corresponde aos IDs de referência do teste"
                result["avisos"].append(f"{t['modo']} / {t['extrator']} diverge da referência")
    for extrator in ("gallery-dl", "yt-dlp"):
        por_modo = {
            t["modo"]: {m["id"] for m in t["midias"]}
            for t in result["tentativas"] if t["extrator"] == extrator and t["midias"]
        }
        if len(por_modo) == 2 and por_modo["publico"] != por_modo["firefox"]:
            result["avisos"].append(f"{extrator}: IDs públicos e autenticados diferentes")
            for t in result["tentativas"]:
                if t["extrator"] == extrator and t["midias"]:
                    t["estado"] = "CONFLITO"
                    t["motivo"] += "; mídia diferente entre as sessões"
    return result


def revisar_existente(pasta):
    """Inspeciona vídeos da auditoria anterior sem Facebook/re-download.

    A revisão preserva o relatório original e não aprova vídeos sem
    correspondência id URL -> id do JSON. Não exibe textos do conteúdo.
    """
    caminho = pasta / "relatorio.json"
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("❌ Relatório JSON não encontrado ou inválido:", caminho)
        return
    print("🔎 Revisão local (sem downloads):", pasta)
    total = 0
    revisoes = []
    for caso in dados.get("casos", []):
        url = caso.get("url_extracao") or caso.get("resolvido", "")
        for t in caso.get("tentativas", []):
            if t.get("extrator") != "yt-dlp":
                continue
            total += 1
            d = Path(t.get("pasta", ""))
            if not d.is_dir() or not d.is_relative_to(pasta):
                print("⚠️  Pasta de download inválida, revisão ignorada")
                continue
            arquivos = sorted(
                f for f in d.iterdir() if f.is_file()
                and f.suffix.lower() in VIDEOS and f.stat().st_size > 0
            )
            atual, motivo = analisar_video(url, arquivos)
            esperado = extrair_id_video_direto(url)
            metadados = []
            for f in arquivos:
                info_path = f.with_name(f.stem + ".info.json")
                try:
                    dados_video = json.loads(info_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    dados_video = {}
                metadados.append({
                    "arquivo": f.name,
                    "id_video": f.stem,
                    "id_info_json": str(dados_video.get("id") or ""),
                    "info_json_existe": info_path.is_file(),
                })
            anterior = t.get("estado", "desconhecido")
            codigo = t.get("codigo")
            nota = ("A auditoria anterior usava --max-downloads 1, que pode "
                    "causar saída não zero após baixar um vídeo."
                    if codigo and arquivos else "")
            print(
                f"  Caso {caso.get('numero')} {t.get('modo')}: "
                f"antes={anterior}, agora={atual}, código anterior={codigo}; {motivo}"
            )
            print("    Esperado:", esperado or "ID direto indisponível")
            for meta in metadados:
                print(
                    f"    Arquivo={meta['arquivo']}, "
                    f"ID JSON={meta['id_info_json'] or 'ausente'}"
                )
            if nota:
                print("    ⚠️ ", nota)
            revisoes.append({
                "caso": caso.get("numero"), "modo": t.get("modo"),
                "estado_anterior": anterior, "estado_id": atual,
                "motivo": motivo, "codigo_anterior": codigo,
                "id_esperado": esperado, "arquivos": metadados,
                "observacao": nota,
            })
    destino = pasta / "REVISAO_OFFLINE.json"
    destino.write_text(
        json.dumps({"revisoes": revisoes}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"✅ Revisadas {total} tentativas de vídeo. Resultado: {destino}")
    print("Falhas de extração sem arquivos exigem novo teste com diagnóstico aprimorado.")


def obter_casos(args):
    casos = []
    if args.conhecidos:
        casos.extend(CONHECIDOS)
    for url in args.url or []:
        casos.append(("Link informado", url.strip(), ()))
    if args.arquivo:
        for linha in Path(args.arquivo).read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#"):
                continue
            # URL | id1,id2 (referência opcional conferida pelo usuário).
            url, _, ids = linha.partition("|")
            ref = tuple(re.findall(r"\d+", ids)) if ids.strip() else ()
            casos.append(("Lista", url.strip(), ref))
    if not casos:
        print("Cole um link de cada vez. Linha vazia inicia a auditoria.")
        while True:
            try:
                linha = input("Link (ou Enter para iniciar): ").strip()
            except EOFError:
                break
            if not linha:
                break
            casos.append(("Digitado", linha, ()))
    # Não testa duas vezes o mesmo link exato.
    distintos = []
    vistos = set()
    for c in casos:
        if c[1] not in vistos:
            vistos.add(c[1])
            distintos.append(c)
    return distintos


async def main():
    parser = argparse.ArgumentParser(description="Auditoria unificada do Facebook na VPS.")
    parser.add_argument("--revisar", metavar="PASTA", help="Revê os downloads de uma auditoria anterior, sem baixar novamente")
    parser.add_argument("--conhecidos", action="store_true", help="Audita 3 links já fornecidos nesta investigação")
    parser.add_argument("--url", action="append", help="Adicionar um link de Facebook (pode repetir)")
    parser.add_argument("--arquivo", help="Arquivo com links, um por linha; opcional: URL | ids")
    parser.add_argument("--somente-publico", action="store_true", help="Não usar cookies do Firefox")
    parser.add_argument("--sem-preflight", action="store_true", help="Pular dump-json antes dos downloads")
    parser.add_argument("--sem-videos-em-posts", action="store_true", help="Não testar vídeo em posts genéricos")
    parser.add_argument("--max-midias", type=int, default=20, help="Máximo por download (1 a 30)")
    args = parser.parse_args()
    if args.revisar:
        revisar_existente(Path(args.revisar).expanduser().resolve())
        return
    if not 1 <= args.max_midias <= 30:
        parser.error("--max-midias precisa estar entre 1 e 30")
    if not GALLERY.is_file():
        parser.error("gallery-dl não existe na venv do projeto")
    os.umask(0o077)
    casos = obter_casos(args)
    if not casos:
        print("Nenhum link fornecido. Nada foi executado.")
        return
    if len(casos) > 20:
        parser.error("Máximo de 20 links por auditoria; divida em lotes")
    raiz = BASE / "facebook_auditoria"
    raiz.mkdir(mode=0o700, exist_ok=True)
    pasta = raiz / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    pasta.mkdir(mode=0o700)
    report = {
        "data_utc": datetime.now(timezone.utc).isoformat(),
        "max_midias": args.max_midias,
        "firefox_configurado": bool(PROFILE),
        "tipos_cobertos": list(TIPOS),
        "casos": []
    }
    print(f"\n🔎 Auditoria de {len(casos)} link(s), até {args.max_midias} mídias por tentativa.")
    print(f"📁 Relatório privado: {pasta}")
    print("⚠️  Acesso público e Firefox são testados separadamente; conflitos ficam sinalizados.")
    for n, c in enumerate(casos, 1):
        report["casos"].append(await auditar(c, n, pasta, args))
        # Atualiza o relatório entre casos; interrupções preservam progresso.
        rpath = pasta / "resultado_parcial.json"
        rpath.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["contagem"] = dict(Counter(
        t["estado"] for c in report["casos"] for t in c.get("tentativas", [])
    ))
    json_privado(pasta / "relatorio.json", report)
    (pasta / "RELATORIO.md").write_text(relatorio_markdown(report), encoding="utf-8")
    print("\n✅ AUDITORIA CONCLUÍDA")
    print("Resumo das tentativas:", report["contagem"])
    print("📄", pasta / "RELATORIO.md")
    print("📊", pasta / "relatorio.json")
    print("⚠️  VERIFICADO não significa que álbuns no limite estejam completos.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrompido. Resultado parcial preservado quando disponível.")
