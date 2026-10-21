#!/usr/bin/env python3
"""Apply an opt-in, minimal Instagram Story patch to the LOCAL VPS file.

No git checkout/reset/pull and no other production file is modified.
The Firefox-backed path stays disabled until the service is configured.
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path
import shutil
import tempfile

TARGET = Path("/root/Bot-Download/services/platforms/instagram_media.py")
MARKER = "# INSTAGRAM_FIREFOX_STORY_PATCH_V1"


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: esperado exatamente 1 trecho, encontrado {count}; arquivo preservado")
    return source.replace(old, new, 1)


def main() -> None:
    source = TARGET.read_text(encoding="utf-8")
    if MARKER in source:
        print("Patch de Stories ja aplicado: nenhuma alteracao.")
        return
    if "import os" not in source:
        raise RuntimeError("Nao encontrei import os; arquivo preservado")

    marker_loc = "logger = logging.getLogger(__name__)\n"
    additions = (
        "\n" + MARKER + "\n"
        "INSTAGRAM_STORY_GDL_BIN = os.getenv('INSTAGRAM_STORY_GDL_BIN', '').strip()\n"
        "INSTAGRAM_FIREFOX_PROFILE = os.getenv('INSTAGRAM_FIREFOX_PROFILE', '').strip()\n"
    )
    result = replace_once(source, marker_loc, marker_loc + additions, "constantes")

    start = result.find("def _run_gallery_dl(")
    end = result.find("\ndef _run_ytdlp(", start)
    if start < 0 or end < 0:
        raise RuntimeError("Estrutura do extrator diferente; arquivo preservado")
    section = result[start:end]
    binary_line = '        binary = GDL_BIN if os.path.exists(GDL_BIN) else "gallery-dl"\n'
    binary_update = (
        binary_line +
        "        if story_id and INSTAGRAM_STORY_GDL_BIN:\n"
        "            if os.path.isfile(INSTAGRAM_STORY_GDL_BIN) and os.access(INSTAGRAM_STORY_GDL_BIN, os.X_OK):\n"
        "                binary = INSTAGRAM_STORY_GDL_BIN\n"
        "            else:\n"
        "                logger.warning('Instagram Story: gallery-dl isolado nao encontrado')\n"
    )
    section = replace_once(section, binary_line, binary_update, "seletor gallery-dl")

    anchor = "        cmd.append(url)\n"
    extra = (
        "        if story_id:\n"
        "            # Remover filtros/templates de posts: o ID da URL ja seleciona o Story.\n"
        "            for flag in ('--filter', '--filename', '--user-agent'):\n"
        "                if flag in cmd:\n"
        "                    pos = cmd.index(flag)\n"
        "                    del cmd[pos:pos + 2]\n"
        "            if INSTAGRAM_FIREFOX_PROFILE:\n"
        "                cookies_db = os.path.join(INSTAGRAM_FIREFOX_PROFILE, 'cookies.sqlite')\n"
        "                if os.path.isfile(cookies_db):\n"
        "                    if '--cookies' in cmd:\n"
        "                        pos = cmd.index('--cookies')\n"
        "                        del cmd[pos:pos + 2]\n"
        "                    cmd.extend(('--cookies-from-browser',\n"
        "                                'firefox/instagram.com:' + INSTAGRAM_FIREFOX_PROFILE))\n"
        "                else:\n"
        "                    logger.warning('Instagram Story: Firefox cookies.sqlite ausente')\n"
        "\n"
    )
    section = replace_once(section, anchor, extra + anchor, "autenticacao Story")

    # Never return a different Story if its ID was not found among downloaded files.
    old_fallback = "            else:\n                saved_files = [saved_files[0]]\n"
    if old_fallback in section:
        section = replace_once(
            section, old_fallback,
            "            else:\n"
            "                logger.warning('Instagram Story: ID solicitado nao corresponde a midia baixada')\n"
            "                return None\n",
            "validacao de ID",
        )

    result = result[:start] + section + result[end:]
    compile(result, str(TARGET), "exec")  # Abort before touching disk on syntax errors.

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = TARGET.with_name(TARGET.name + ".bak-story-" + stamp)
    shutil.copy2(TARGET, backup)

    fd, tmpname = tempfile.mkstemp(prefix=".instagram_media.", suffix=".py", dir=TARGET.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(result)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmpname, TARGET.stat().st_mode & 0o777)
        os.replace(tmpname, TARGET)
    finally:
        if os.path.exists(tmpname):
            os.unlink(tmpname)
    print("OK: atualizado apenas", TARGET)
    print("BACKUP:", backup)
    print("INATIVO ate configurar as variaveis do servico (nao reiniciado).")


if __name__ == "__main__":
    main()
