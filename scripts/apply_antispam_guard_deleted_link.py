#!/usr/bin/env python3
"""Safely apply the deleted-group-link onboarding fix to the running VPS tree.

No git checkout/reset, no downloads of source dependencies, and no restart.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import tempfile

TARGET = Path("/root/Bot-Download/middlewares/private_chat_guard.py")
OLD = '''        notice = await event.reply(
            bm.dm_start_required(lang=user_lang),
            reply_markup=kb.start_private_chat_keyboard(_bot_username, lang=user_lang),
        )'''
NEW = '''        # Antispam managers may delete the user's link before we finish
        # checking whether the private chat is available. A reply would then
        # fail with "message to be replied not found" and lose the pending
        # request entirely. Send the notice independently of that message.
        notice = await event.answer(
            bm.dm_start_required(lang=user_lang),
            reply_markup=kb.start_private_chat_keyboard(_bot_username, lang=user_lang),
            allow_sending_without_reply=True,
        )'''


def main() -> None:
    if not TARGET.is_file() or TARGET.is_symlink():
        raise RuntimeError(f"Arquivo indisponível ou symlink: {TARGET}")
    original = TARGET.read_bytes()
    source = original.decode("utf-8")
    if NEW in source:
        print("A correção do guard antispam já está aplicada. Nenhuma alteração necessária.")
        return
    if source.count(OLD) != 1:
        raise RuntimeError("Conteúdo local divergente; nenhum arquivo foi alterado.")
    updated = source.replace(OLD, NEW)
    ast.parse(updated, filename=str(TARGET))
    backup = TARGET.with_name(
        TARGET.name + ".bak-antispam-" +
        datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    )
    with open(backup, "xb") as dest:
        dest.write(original)
        dest.flush()
        os.fsync(dest.fileno())
    fd, staging = tempfile.mkstemp(prefix=".guard-antispam-", suffix=".py", dir=TARGET.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(updated.encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
        os.chmod(staging, TARGET.stat().st_mode & 0o777)
        os.replace(staging, TARGET)
    finally:
        if os.path.exists(staging):
            os.unlink(staging)
    print("SUCESSO: guard antispam corrigido na VPS.")
    print("BACKUP:", backup)
    print("O serviço não foi reiniciado; faça um teste após reiniciar.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"CORRECAO BLOQUEADA: {exc}", file=sys.stderr)
        sys.exit(1)
