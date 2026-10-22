#!/usr/bin/env python3
"""Safe one-file deployment for Pinterest pin.it image URL recovery.

Usage: python3 /tmp/apply_pinterest_pin_it_photos.py /tmp/pinterest_media_new.py
Atomically updates one module with verified Git blob hashes and a backup.
No git checkout/pull and no bot restart.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import stat
import sys
import tempfile

ROOT = Path("/root/Bot-Download")
TARGET = ROOT / "services/platforms/pinterest_media.py"
PREVIOUS_SHAS = {
    "e4939dfe8dbe59a7bd3ee34a761b5c071c741ef5",  # HLS + JPG first attempt
}
NEW_SHA = "eda4823763043df4aa91127cda915eaa6efb7adb"


def git_blob(data: bytes) -> str:
    return hashlib.sha1(
        b"blob " + str(len(data)).encode() + b"\0" + data
    ).hexdigest()


def main() -> None:
    if len(sys.argv) != 2:
        raise RuntimeError("Informe o arquivo novo baixado em /tmp.")
    incoming = Path(sys.argv[1])
    if (
        not TARGET.is_file()
        or TARGET.is_symlink()
        or not incoming.is_file()
        or incoming.is_symlink()
    ):
        raise RuntimeError("Arquivo inexistente ou symlink: instalação cancelada.")
    old = TARGET.read_bytes()
    old_sha = git_blob(old)
    if old_sha == NEW_SHA:
        print("Pinterest já atualizado: não é necessário repetir a instalação.")
        return
    if old_sha not in PREVIOUS_SHAS:
        raise RuntimeError(
            "Código na VPS difere da versão anterior conhecida. "
            "Nenhuma alteração: evitar sobrescrever modificações locais."
        )
    new = incoming.read_bytes()
    if git_blob(new) != NEW_SHA:
        raise RuntimeError("Integridade do arquivo novo inválida: instalação cancelada.")
    ast.parse(new.decode("utf-8"), filename=str(incoming))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup = ROOT / "backups" / f"pinterest-pin-it-{stamp}" / TARGET.name
    backup.parent.mkdir(parents=True, exist_ok=False)
    with backup.open("xb") as handle:
        handle.write(old)
        handle.flush()
        os.fsync(handle.fileno())

    fd, staged = tempfile.mkstemp(
        prefix=".pinterest-pin-it-", suffix=".py", dir=TARGET.parent
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(new)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(staged, stat.S_IMODE(TARGET.stat().st_mode))
        os.replace(staged, TARGET)
    finally:
        if os.path.exists(staged):
            os.unlink(staged)

    print("SUCESSO: Pinterest photo fallback atualizado.")
    print("BACKUP:", backup)
    print("Serviço não reiniciado.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("INSTALAÇÃO BLOQUEADA:", exc, file=sys.stderr)
        sys.exit(1)
