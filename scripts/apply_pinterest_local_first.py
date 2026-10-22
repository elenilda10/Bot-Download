#!/usr/bin/env python3
"""Safely deploy local-first Pinterest extraction to a VPS with existing code.

Only changes the Pinterest media module, verifies blob hashes, creates a
byte-for-byte backup and does not restart the running bot.
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
KNOWN_OLD_SHAS = {
    "e4939dfe8dbe59a7bd3ee34a761b5c071c741ef5",  # photo -J + yt-dlp HLS
    "eda4823763043df4aa91127cda915eaa6efb7adb",  # photo -J/-G + yt-dlp HLS
}
EXPECTED_NEW_SHA = "5306b7da6a4bce7670f891dde854186333814198"


def git_blob_sha(payload: bytes) -> str:
    return hashlib.sha1(
        b"blob " + str(len(payload)).encode("ascii") + b"\0" + payload
    ).hexdigest()


def main() -> None:
    if len(sys.argv) != 2:
        raise RuntimeError(
            "Uso: python3 /tmp/apply_pinterest_local_first.py /tmp/pinterest_media_new.py"
        )
    incoming = Path(sys.argv[1])
    if TARGET.is_symlink() or not TARGET.is_file():
        raise RuntimeError("Arquivo Pinterest original ausente ou irregular.")
    if incoming.is_symlink() or not incoming.is_file():
        raise RuntimeError("Novo arquivo Pinterest ausente ou irregular.")

    old = TARGET.read_bytes()
    old_sha = git_blob_sha(old)
    if old_sha == EXPECTED_NEW_SHA:
        print("Pinterest já está nesta versão. Nenhuma alteração necessária.")
        return
    if old_sha not in KNOWN_OLD_SHAS:
        raise RuntimeError(
            "Arquivo local divergente. Instalação interrompida para não "
            "substituir alterações feitas diretamente na VPS."
        )

    new = incoming.read_bytes()
    if git_blob_sha(new) != EXPECTED_NEW_SHA:
        raise RuntimeError("SHA do arquivo novo não corresponde à versão revisada.")
    ast.parse(new.decode("utf-8"), filename=str(incoming))

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup = ROOT / "backups" / f"pinterest-local-first-{timestamp}" / TARGET.name
    backup.parent.mkdir(parents=True, exist_ok=False)
    with backup.open("xb") as handle:
        handle.write(old)
        handle.flush()
        os.fsync(handle.fileno())

    fd, temp_path = tempfile.mkstemp(
        prefix=".pinterest-local-first-", suffix=".py", dir=TARGET.parent
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(new)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, stat.S_IMODE(TARGET.stat().st_mode))
        os.replace(temp_path, TARGET)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)

    print("SUCESSO: Pinterest extraindo fotos e vídeos diretamente na VPS.")
    print("BACKUP:", backup)
    print("Serviço preservado: não foi reiniciado.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"INSTALACAO BLOQUEADA: {exc}", file=sys.stderr)
        raise SystemExit(1)
