#!/usr/bin/env python3
"""Deploy only corrected gallery-dl record parsing to the Bot-Download VPS.

Verifies old/new Git blob hashes, makes a backup, swaps only the Pinterest
extractor atomically, and does not restart the running bot.
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
ACCEPTED_OLD = {
    "760cad6ac4f76a665a9c201ccf9ff48d77c67e24",  # PR #32 deployed
}
EXPECTED_NEW = "01bde200ed3a63098a11a620d9c647dac0fd7521"


def git_sha(content: bytes) -> str:
    return hashlib.sha1(
        b"blob " + str(len(content)).encode("ascii") + b"\0" + content
    ).hexdigest()


def main() -> None:
    if len(sys.argv) != 2:
        raise ValueError(
            "Uso: python3 /tmp/apply_pin_json.py /tmp/pinterest_media_fixed.py"
        )
    source = Path(sys.argv[1])
    if (
        not TARGET.is_file() or TARGET.is_symlink()
        or not source.is_file() or source.is_symlink()
    ):
        raise ValueError("Arquivo ausente ou irregular; nenhuma alteração será feita.")

    old = TARGET.read_bytes()
    current_sha = git_sha(old)
    if current_sha == EXPECTED_NEW:
        print("Pinterest já contém a correção. Nenhuma alteração necessária.")
        return
    if current_sha not in ACCEPTED_OLD:
        raise ValueError(
            "A versão local difere da versão conhecida; não sobrescrever "
            "modificações feitas na VPS."
        )

    updated = source.read_bytes()
    if git_sha(updated) != EXPECTED_NEW:
        raise ValueError("Arquivo recebido difere do código validado no GitHub.")
    ast.parse(updated.decode("utf-8"), filename=str(source))

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup = ROOT / "backups" / f"pinterest-real-json-{timestamp}" / TARGET.name
    backup.parent.mkdir(parents=True, exist_ok=False)
    with backup.open("xb") as out:
        out.write(old)
        out.flush()
        os.fsync(out.fileno())

    fd, temporary = tempfile.mkstemp(
        prefix=".pinterest-real-json-", suffix=".py", dir=TARGET.parent
    )
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(updated)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temporary, stat.S_IMODE(TARGET.stat().st_mode))
        os.replace(temporary, TARGET)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

    print("SUCESSO: parser JSON real do Pinterest instalado.")
    print("BACKUP:", backup)
    print("Outros handlers intactos; serviço não reiniciado.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"INSTALACAO BLOQUEADA: {exc}", file=sys.stderr)
        raise SystemExit(1)
