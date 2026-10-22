#!/usr/bin/env python3
"""Deploy only the Pinterest extractor with atomic replacement and backup.

Accepts either of the two verified versions from previous conversations.
Never performs git pull/reset, changes settings, or restarts the running bot.
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
ACCEPTED_PREVIOUS_SHAS = {
    "eab748adc02389b641681ea96dc56a2908775f59",  # original Cobalt-only
    "b86b7303d9f71ee16e6102ea23de420d98ef7803",  # public gallery fallback
}
EXPECTED_NEW_SHA = "e4939dfe8dbe59a7bd3ee34a761b5c071c741ef5"


def _git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(
        b"blob " + str(len(data)).encode() + b"\x00" + data
    ).hexdigest()


def main() -> None:
    if len(sys.argv) != 2:
        raise RuntimeError("Uso: python3 /tmp/apply_pinterest_photo_hls.py /tmp/pinterest_media.py")
    incoming = Path(sys.argv[1])
    if (
        not TARGET.is_file() or TARGET.is_symlink()
        or not incoming.is_file() or incoming.is_symlink()
    ):
        raise RuntimeError("Arquivo ausente, irregular ou symlink. Nenhuma alteração feita.")
    original = TARGET.read_bytes()
    current = _git_blob_sha(original)
    if current == EXPECTED_NEW_SHA:
        print("Pinterest atualizado anteriormente; nenhuma alteração necessária.")
        return
    if current not in ACCEPTED_PREVIOUS_SHAS:
        raise RuntimeError(
            "Versão do Pinterest na VPS diferente das versões conhecidas. "
            "Bloqueado para não substituir modificações locais."
        )

    content = incoming.read_bytes()
    if _git_blob_sha(content) != EXPECTED_NEW_SHA:
        raise RuntimeError("O código baixado não corresponde ao commit revisado.")
    ast.parse(content.decode("utf-8"), filename=str(incoming))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup = ROOT / "backups" / f"pinterest-photo-hls-{stamp}" / TARGET.name
    backup.parent.mkdir(parents=True, exist_ok=False)
    with backup.open("xb") as out:
        out.write(original)
        out.flush()
        os.fsync(out.fileno())

    fd, staging = tempfile.mkstemp(
        prefix=".pinterest-hls-", suffix=".py", dir=TARGET.parent
    )
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(staging, stat.S_IMODE(TARGET.stat().st_mode))
        os.replace(staging, TARGET)
    finally:
        if os.path.exists(staging):
            os.unlink(staging)
    print("SUCESSO: Pinterest com imagens e vídeos HLS atualizado.")
    print("BACKUP:", backup)
    print("O bot continua em execução; reinicie somente após sua validação.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"INSTALACAO BLOQUEADA: {exc}", file=sys.stderr)
        raise SystemExit(1)
