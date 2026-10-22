#!/usr/bin/env python3
"""Install deleted-link Telegram reply fallback into the live VPS checkout.

Run from /root/Bot-Download after downloading the companion middleware to
/tmp/telegram_deleted_reply.py. Never git pull/reset, overwrite unrelated code,
expose cookies, touch services or restart the running bot automatically.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path("/root/Bot-Download")
MAIN = ROOT / "main.py"
MODULE = ROOT / "services/runtime/telegram_deleted_reply.py"
DOWNLOADED_MODULE = Path("/tmp/telegram_deleted_reply.py")
IMPORT = "from services.runtime.telegram_deleted_reply import DeletedReplyFallbackMiddleware\n"
STARTUP = "    session.middleware(DeletedReplyFallbackMiddleware())\n"
IMPORT_ANCHOR = "from services.runtime.analytics_status import record_drop as record_analytics_drop\n"
STARTUP_ANCHOR = '    default = DefaultBotProperties(parse_mode=ParseMode.HTML)\n'


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".bot-safe-reply-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, (path.stat().st_mode & 0o777) if path.exists() else 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main() -> None:
    if not MAIN.is_file() or not DOWNLOADED_MODULE.is_file():
        raise RuntimeError("main.py ou módulo baixado não encontrado; nada alterado.")
    if MODULE.is_symlink() or MAIN.is_symlink():
        raise RuntimeError("Arquivos simbólicos não são suportados; nada alterado.")

    original = MAIN.read_text(encoding="utf-8")
    module_text = DOWNLOADED_MODULE.read_text(encoding="utf-8")
    if "class DeletedReplyFallbackMiddleware" not in module_text:
        raise RuntimeError("Módulo baixado inesperado; nada alterado.")

    patched = original
    if IMPORT not in patched:
        if patched.count(IMPORT_ANCHOR) != 1:
            raise RuntimeError("Ponto de importação não localizado de forma única; nada alterado.")
        patched = patched.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + IMPORT, 1)
    if STARTUP not in patched:
        if patched.count(STARTUP_ANCHOR) != 1:
            raise RuntimeError("Ponto de configuração do bot não localizado de forma única; nada alterado.")
        patched = patched.replace(STARTUP_ANCHOR, STARTUP + STARTUP_ANCHOR, 1)

    for label, code in (("main.py", patched), ("telegram_deleted_reply.py", module_text)):
        ast.parse(code, filename=label)
        compile(code, label, "exec")

    if patched == original and MODULE.is_file() and MODULE.read_bytes() == module_text.encode("utf-8"):
        print("Já instalado. Nenhuma alteração necessária.")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    saved_main = MAIN.with_name(f"main.py.bak-antispam-{stamp}")
    saved_module = MODULE.with_name(f"telegram_deleted_reply.py.bak-antispam-{stamp}") if MODULE.exists() else None
    shutil.copy2(MAIN, saved_main)
    if saved_module is not None:
        shutil.copy2(MODULE, saved_module)

    try:
        atomic_write(MODULE, module_text.encode("utf-8"))
        atomic_write(MAIN, patched.encode("utf-8"))
    except Exception:
        shutil.copy2(saved_main, MAIN)
        if saved_module is not None:
            shutil.copy2(saved_module, MODULE)
        else:
            MODULE.unlink(missing_ok=True)
        raise

    print("OK: middleware de envio resiliente instalado.")
    print("BACKUP main:", saved_main)
    print("ARQUIVO NOVO/ATUALIZADO:", MODULE)
    print("Bot não reiniciado. Verifique testes antes da ativação.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("INSTALAÇÃO BLOQUEADA:", str(exc), file=sys.stderr)
        raise SystemExit(1)
