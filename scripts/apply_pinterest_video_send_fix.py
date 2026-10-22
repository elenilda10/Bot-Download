#!/usr/bin/env python3
"""Fix Pinterest's Telegram video-send tuple error without replacing VPS code.

Edits only the known _send_downloaded() video call in handlers/pinterest.py.
Supports locally modified versions using reply_video or answer_video, and
converts to answer_video so deleted antispam messages don't prevent delivery.
Creates a byte-for-byte backup and never restarts the bot.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import os
from pathlib import Path
import stat
import sys
import tempfile

TARGET = Path("/root/Bot-Download/handlers/pinterest.py")


def patch_source(source: str) -> str:
    start_marker = "    async def _send_downloaded(path: str):"
    end_marker = "\n    async def _inspect_metrics"
    if source.count(start_marker) != 1:
        raise ValueError("Handler Pinterest diferente do esperado: _send_downloaded")
    start = source.index(start_marker)
    end = source.find(end_marker, start)
    if end < 0:
        raise ValueError("Limite de _send_downloaded não encontrado; operação bloqueada")
    block = source[start:end]

    new_signature = "video_kwargs, thumbnail_path = await build_video_send_kwargs(path)"
    if new_signature in block:
        if (
            "**video_kwargs" in block
            and "await remove_file(thumbnail_path)" in block
            and "return await message.answer_video(" in block
        ):
            return source
        raise ValueError("Correção parcial ou divergente; não substituir automaticamente")

    bad_unpack = "**(await build_video_send_kwargs(path)),"
    if block.count(bad_unpack) != 1:
        raise ValueError("Padrão exato do erro não encontrado no handler Pinterest")
    if block.count("if media_kind == \"video\":") != 1:
        raise ValueError("Fluxo de envio Pinterest não corresponde à versão conhecida")

    call_start = block.find("            return await message.")
    if call_start < 0:
        raise ValueError("Chamada de envio de vídeo não localizada")

    original_call = block[call_start:]
    allowed = (
        "            return await message.answer_video(",
        "            return await message.reply_video(",
    )
    if not original_call.startswith(allowed):
        raise ValueError("O método de envio de vídeo difere do previsto")

    ending = "\n            )"
    close = block.find(ending, call_start)
    if close < 0:
        raise ValueError("Fim da chamada de envio não localizado")
    close += len(ending)
    original_call = block[call_start:close]
    if original_call.count(bad_unpack) != 1:
        raise ValueError("Parâmetros do vídeo não correspondem ao erro reportado")

    corrected_call = original_call.replace(
        "return await message.reply_video(",
        "return await message.answer_video(",
        1,
    ).replace(bad_unpack, "**video_kwargs,")
    # Nest the original call one indentation level deeper inside try/finally.
    indented_call = "\n".join(
        "    " + line for line in corrected_call.split("\n")
    )
    replacement = (
        "            video_kwargs, thumbnail_path = await build_video_send_kwargs(path)\n"
        "            try:\n"
        + indented_call
        + "\n            finally:\n"
        "                if thumbnail_path:\n"
        "                    await remove_file(thumbnail_path)"
    )
    updated_block = block[:call_start] + replacement + block[close:]
    updated_source = source[:start] + updated_block + source[end:]
    ast.parse(updated_source)
    return updated_source


def main() -> None:
    if TARGET.is_symlink() or not TARGET.is_file():
        raise RuntimeError("Arquivo Pinterest da VPS não encontrado ou irregular")

    old_bytes = TARGET.read_bytes()
    original = old_bytes.decode("utf-8")
    updated = patch_source(original)
    if original == updated:
        print("A correção de envio do Pinterest já está instalada.")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup = TARGET.parents[1] / "backups" / f"pinterest-video-send-{stamp}" / TARGET.name
    backup.parent.mkdir(parents=True, exist_ok=False)
    with backup.open("xb") as stream:
        stream.write(old_bytes)
        stream.flush()
        os.fsync(stream.fileno())

    fd, staging = tempfile.mkstemp(
        prefix=".pinterest-video-send-", suffix=".py", dir=TARGET.parent
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(updated.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(staging, stat.S_IMODE(TARGET.stat().st_mode))
        os.replace(staging, TARGET)
    finally:
        if os.path.exists(staging):
            os.unlink(staging)

    print("SUCESSO: envio de vídeo do Pinterest corrigido.")
    print("BACKUP:", backup)
    print("Nenhum outro handler alterado. Serviço ainda não reiniciado.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"CORRECAO BLOQUEADA: {exc}", file=sys.stderr)
        sys.exit(1)
