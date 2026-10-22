#!/usr/bin/env python3
"""Safely apply the reviewed Pinterest fallback file to the VPS.

Usage:
    python3 scripts/apply_pinterest_gallery_fallback.py /tmp/pinterest_media_new.py

Validates exact old/new Git blob IDs, backs up the original and atomically
replaces only services/platforms/pinterest_media.py. Does not restart systemd.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path("/root/Bot-Download")
TARGET = ROOT / "services/platforms/pinterest_media.py"
EXPECTED_OLD = "eab748adc02389b641681ea96dc56a2908775f59"
EXPECTED_NEW = "b86b7303d9f71ee16e6102ea23de420d98ef7803"


def git_blob_sha(contents: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(contents)).encode() + b"\0" + contents).hexdigest()


def main() -> None:
    if len(sys.argv) != 2:
        raise RuntimeError("Informe o caminho local do arquivo Pinterest revisado.")
    incoming = Path(sys.argv[1])
    if not TARGET.is_file() or TARGET.is_symlink():
        raise RuntimeError("Arquivo original ausente ou irregular na VPS.")
    if not incoming.is_file() or incoming.is_symlink():
        raise RuntimeError("Novo arquivo ausente ou irregular.")
    original = TARGET.read_bytes()
    current = git_blob_sha(original)
    if current == EXPECTED_NEW:
        print("O Pinterest já está atualizado. Nenhuma alteração necessária.")
        return
    if current != EXPECTED_OLD:
        raise RuntimeError(
            "Código Pinterest da VPS divergiu do GitHub; alteração bloqueada "
            "para não perder modificações locais."
        )

    updated = incoming.read_bytes()
    if git_blob_sha(updated) != EXPECTED_NEW:
        raise RuntimeError("SHA do novo arquivo não confere; não aplicar.")
    ast.parse(updated.decode("utf-8"), filename=str(incoming))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup = ROOT / "backups" / f"pinterest-{stamp}" / "pinterest_media.py"
    backup.parent.mkdir(parents=True, exist_ok=False)
    with backup.open("xb") as stream:
        stream.write(original)
        stream.flush()
        os.fsync(stream.fileno())

    fd, staging = tempfile.mkstemp(
        prefix=".pinterest-", suffix=".py", dir=TARGET.parent
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(staging, TARGET.stat().st_mode & 0o777)
        os.replace(staging, TARGET)
    finally:
        if os.path.exists(staging):
            os.unlink(staging)

    print("SUCESSO: Pinterest atualizado na VPS com backup.")
    print("BACKUP:", backup)
    print("Serviço não reiniciado.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"IMPLANTACAO BLOQUEADA: {exc}", file=sys.stderr)
        sys.exit(1)
