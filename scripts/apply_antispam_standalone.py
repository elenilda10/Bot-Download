#!/usr/bin/env python3
"""Make Telegram media delivery independent from deleted link messages.

One-shot, idempotent patch for a VPS that may contain local modifications.
Only targeted method references and reply IDs are changed. It never restarts
the running service or contacts GitHub itself.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

ROOT = Path("/root/Bot-Download")
FILES = (
    "handlers/instagram.py",
    "handlers/facebook.py",
    "handlers/universal.py",
    "handlers/youtube.py",
    "handlers/tiktok.py",
    "handlers/twitter.py",
    "handlers/threads.py",
    "handlers/pinterest.py",
    "handlers/spotify.py",
    "handlers/deezer.py",
    "handlers/soundcloud.py",
    "handlers/mediafire.py",
    "handlers/mega.py",
    "handlers/google_drive.py",
    "handlers/xvideos.py",
    "handlers/xvideos_actions.py",
    "handlers/pornhub.py",
    "handlers/eporner.py",
    "handlers/music.py",
    "handlers/media_download.py",
    "handlers/telegram_ui_utils.py",
    "services/media/delivery.py",
    "services/media/orchestration.py",
)
MESSAGE_REPLY = re.compile(r"\bmessage\.reply(?!_to_message)(_[a-z_]+)?\b")


def patch_file(name: str, contents: str) -> str:
    modified = MESSAGE_REPLY.sub(
        lambda match: "message.answer" + (match.group(1) or ""), contents
    )
    if name == "services/media/delivery.py":
        modified = modified.replace(
            '            if offset == 0:\n'
            '                send_kwargs["reply_to_message_id"] = message.message_id\n',
            "",
        )
        modified = modified.replace(
            '        "reply_to_message_id": message.message_id,\n',
            "",
        )
        if '"reply_to_message_id"' in modified:
            raise RuntimeError("reply_to_message_id ainda presente no envio central")
    if name == "handlers/telegram_ui_utils.py":
        modified = modified.replace('method: str = "reply"', 'method: str = "answer"')
        modified = modified.replace(
            '                    "reply_parameters": {\n'
            '                        "message_id": message.message_id\n'
            '                    }\n',
            "",
        )
        modified = modified.replace(
            '    responder = getattr(message, method, None)',
            '    safe_method = method.replace("reply", "answer", 1) '
            'if method.startswith("reply") else method\n'
            '    responder = getattr(message, safe_method, None)',
        ).replace(
            'raise AttributeError(f"Message object has no method \'{method}\'")',
            'raise AttributeError(f"Message object has no method \'{safe_method}\'")',
        )
        if '"reply_parameters"' in modified:
            raise RuntimeError("reply_parameters ainda presente em notificações")
    if name == "handlers/music.py":
        modified = modified.replace(
            '            "reply_parameters": {"message_id": message_id},\n', ""
        )
    ast.parse(modified, filename=name)
    return modified


def main() -> None:
    planned: dict[str, tuple[bytes, bytes]] = {}
    for name in FILES:
        filename = ROOT / name
        if not filename.is_file() or filename.is_symlink():
            raise RuntimeError(f"Arquivo não encontrado/irregular: {name}")
        original = filename.read_bytes()
        original_text = original.decode("utf-8")
        updated = patch_file(name, original_text).encode("utf-8")
        if updated != original:
            planned[name] = (original, updated)
    if not planned:
        print("A correção já está aplicada. Nenhuma alteração necessária.")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup_dir = ROOT / "backups" / ("antispam-" + stamp)
    backup_dir.mkdir(parents=True, exist_ok=False)
    applied: list[str] = []
    try:
        for name, (original, updated) in planned.items():
            target = ROOT / name
            backup = backup_dir / name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)

            fd, tempname = tempfile.mkstemp(
                prefix=".antispam-", suffix=".py", dir=str(target.parent)
            )
            try:
                with os.fdopen(fd, "wb") as out:
                    out.write(updated)
                    out.flush()
                    os.fsync(out.fileno())
                os.chmod(tempname, target.stat().st_mode & 0o777)
                os.replace(tempname, target)
            finally:
                if os.path.exists(tempname):
                    os.unlink(tempname)
            applied.append(name)
    except Exception:
        for name in reversed(applied):
            shutil.copy2(backup_dir / name, ROOT / name)
        raise

    print(f"SUCESSO: {len(planned)} arquivo(s) atualizados.")
    print("BACKUP:", backup_dir)
    for name in planned:
        print("  +", name)
    print("A VPS não foi reiniciada; valide antes de reiniciar.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"PATCH BLOQUEADA: {exc}", file=sys.stderr)
        sys.exit(1)
