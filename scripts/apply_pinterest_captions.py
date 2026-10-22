#!/usr/bin/env python3
"""Install improved Pinterest photo/album captions without changing other code."""
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
EXPECTED_PREVIOUS = {"5306b7da6a4bce7670f891dde854186333814198"}
EXPECTED_NEW = "5c3c930fd33370a6b035b89726d62e032cef31cf"


def _git_blob_sha(content: bytes) -> str:
    return hashlib.sha1(
        b"blob " + str(len(content)).encode("ascii") + b"\0" + content
    ).hexdigest()


def main() -> None:
    if len(sys.argv) != 2:
        raise RuntimeError("Uso: python3 /tmp/apply_pinterest_captions.py /tmp/pinterest_media_caption.py")
    supplied = Path(sys.argv[1])
    if (
        not TARGET.is_file() or TARGET.is_symlink()
        or not supplied.is_file() or supplied.is_symlink()
    ):
        raise RuntimeError("Arquivo Pinterest ausente ou irregular; instalação não iniciada.")

    existing = TARGET.read_bytes()
    sha = _git_blob_sha(existing)
    if sha == EXPECTED_NEW:
        print("Pinterest já está com a correção de captions. Nenhuma alteração.")
        return
    if sha not in EXPECTED_PREVIOUS:
        raise RuntimeError(
            "O arquivo do Pinterest na VPS difere da versão validada. "
            "Instalação bloqueada para preservar alterações locais."
        )

    updated = supplied.read_bytes()
    if _git_blob_sha(updated) != EXPECTED_NEW:
        raise RuntimeError("Integridade do novo arquivo Pinterest inválida.")
    ast.parse(updated.decode("utf-8"), filename=str(supplied))

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup = ROOT / "backups" / f"pinterest-captions-{timestamp}" / TARGET.name
    backup.parent.mkdir(parents=True, exist_ok=False)
    with backup.open("xb") as stream:
        stream.write(existing)
        stream.flush()
        os.fsync(stream.fileno())

    fd, staging = tempfile.mkstemp(
        prefix=".pinterest-captions-", suffix=".py", dir=TARGET.parent
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(staging, stat.S_IMODE(TARGET.stat().st_mode))
        os.replace(staging, TARGET)
    finally:
        if os.path.exists(staging):
            os.unlink(staging)

    print("SUCESSO: captions de imagens e álbuns do Pinterest atualizados.")
    print("BACKUP:", backup)
    print("O bot não foi reiniciado.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"INSTALACAO BLOQUEADA: {exc}", file=sys.stderr)
        sys.exit(1)
