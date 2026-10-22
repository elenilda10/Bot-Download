#!/usr/bin/env python3
"""Atomic, one-file Pinterest caption metadata patch for the bot VPS.

Updates only services/platforms/pinterest_media.py, accepting only known
previous Git blobs. Existing source is backed up before replacement.
The running service is not restarted.
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
KNOWN_PREVIOUS = {
    "5c3c930fd33370a6b035b89726d62e032cef31cf",  # PR #31
    "5306b7da6a4bce7670f891dde854186333814198",  # prior caption parser
}
EXPECTED_NEW = "760cad6ac4f76a665a9c201ccf9ff48d77c67e24"


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(
        b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    ).hexdigest()


def main() -> None:
    if len(sys.argv) != 2:
        raise ValueError("Uso: python3 /tmp/apply_pinterest_caption_records.py /tmp/pinterest_media.py")
    new_source = Path(sys.argv[1])
    if (
        not TARGET.is_file() or TARGET.is_symlink()
        or not new_source.is_file() or new_source.is_symlink()
    ):
        raise ValueError("Arquivo ausente ou symlink; implantação bloqueada.")

    old_data = TARGET.read_bytes()
    old_sha = git_blob_sha(old_data)
    if old_sha == EXPECTED_NEW:
        print("Legenda Pinterest já atualizada; nada a fazer.")
        return
    if old_sha not in KNOWN_PREVIOUS:
        raise ValueError(
            "Arquivo Pinterest local difere das versões testadas; "
            "nenhuma alteração será sobrescrita."
        )

    data = new_source.read_bytes()
    if git_blob_sha(data) != EXPECTED_NEW:
        raise ValueError("Integridade do código recebido falhou; não aplicar.")
    ast.parse(data.decode("utf-8"), filename=str(new_source))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup = ROOT / "backups" / f"pinterest-caption-records-{stamp}" / TARGET.name
    backup.parent.mkdir(parents=True, exist_ok=False)
    with backup.open("xb") as fd:
        fd.write(old_data)
        fd.flush()
        os.fsync(fd.fileno())

    descriptor, staging = tempfile.mkstemp(
        prefix=".pinterest-captions-", suffix=".py", dir=TARGET.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as fd:
            fd.write(data)
            fd.flush()
            os.fsync(fd.fileno())
        os.chmod(staging, stat.S_IMODE(TARGET.stat().st_mode))
        os.replace(staging, TARGET)
    finally:
        if os.path.exists(staging):
            os.unlink(staging)

    print("SUCESSO: parser de legendas Pinterest atualizado.")
    print("BACKUP:", backup)
    print("Serviço não reiniciado.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"INSTALACAO BLOQUEADA: {exc}", file=sys.stderr)
        raise SystemExit(1)
