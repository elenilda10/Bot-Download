#!/usr/bin/env python3
"""Atomically install Pinterest photo URL extraction without touching other code."""
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
PREVIOUS = {
    "e4939dfe8dbe59a7bd3ee34a761b5c071c741ef5",  # Cobalt + HLS + -J
    "b86b7303d9f71ee16e6102ea23de420d98ef7803",  # Cobalt + gallery -J
    "eab748adc02389b641681ea96dc56a2908775f59",  # original
}
EXPECTED = "6b4ea539a88813f791c04fb9b6c25fe217a35960"


def blob_hash(data: bytes) -> str:
    return hashlib.sha1(
        b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    ).hexdigest()


def main() -> None:
    if len(sys.argv) != 2:
        raise RuntimeError(
            "Uso: python3 /tmp/apply_pinterest_gallery_urls.py "
            "/tmp/pinterest_gallery_urls.py"
        )
    incoming = Path(sys.argv[1])
    if not TARGET.is_file() or TARGET.is_symlink():
        raise RuntimeError("Arquivo existente do Pinterest ausente ou irregular.")
    if not incoming.is_file() or incoming.is_symlink():
        raise RuntimeError("Arquivo de correção ausente ou irregular.")

    original = TARGET.read_bytes()
    existing_sha = blob_hash(original)
    if existing_sha == EXPECTED:
        print("Pinterest já atualizado. Nenhuma alteração necessária.")
        return
    if existing_sha not in PREVIOUS:
        raise RuntimeError(
            "Arquivo Pinterest da VPS foi modificado localmente. "
            "Instalação cancelada para não sobrescrever alterações."
        )

    updated = incoming.read_bytes()
    if blob_hash(updated) != EXPECTED:
        raise RuntimeError("Hash do novo arquivo não corresponde à versão revisada.")
    ast.parse(updated.decode("utf-8"), filename=str(incoming))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup = ROOT / "backups" / f"pinterest-gallery-urls-{stamp}" / TARGET.name
    backup.parent.mkdir(parents=True, exist_ok=False)
    with backup.open("xb") as stream:
        stream.write(original)
        stream.flush()
        os.fsync(stream.fileno())

    fd, staging = tempfile.mkstemp(
        prefix=".pinterest-gallery-urls-", suffix=".py", dir=TARGET.parent
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

    print("SUCESSO: Pinterest atualizado com URLs diretas das fotos.")
    print("BACKUP:", backup)
    print("Nenhum serviço foi reiniciado.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"INSTALACAO BLOQUEADA: {exc}", file=sys.stderr)
        raise SystemExit(1)
