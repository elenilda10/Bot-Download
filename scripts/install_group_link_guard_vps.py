#!/usr/bin/env python3
"""Install the no-spam group link guard without replacing VPS customizations.

Run from a clean checkout of the PR with:
  python3 scripts/install_group_link_guard_vps.py --target /root/Bot-Download
  python3 scripts/install_group_link_guard_vps.py --target /root/Bot-Download --apply

The default is a dry run. --apply saves backups and never restarts the bot.
"""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import sys
import tempfile


_UNIVERSAL_OLD = '''    request_url_match = re.search(_UNIVERSAL_LINK_REGEX, text or "")
    if not request_url_match:
        return

    url = request_url_match.group(0).strip()'''
_UNIVERSAL_NEW = '''    # Ordinary group posts must not trigger downloads of unrelated links
    # (stores, channels, promotions, etc.). Private chats and delegated
    # direct_url requests preserve the existing universal fallback.
    if direct_url is None and message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
        url = select_group_download_url(text)
        if url is None:
            return
    else:
        request_url_match = re.search(_UNIVERSAL_LINK_REGEX, text or "")
        if not request_url_match:
            return
        url = request_url_match.group(0).strip()'''

_CHANNEL_OLD = '''        # Ignora completamente mensagens/publicacoes de canais
        if event.chat.type == ChatType.CHANNEL:
            return None'''
_CHANNEL_NEW = '''        # Channel posts mirrored into their linked discussion groups arrive
        # as automatic forwards (chat.type == SUPERGROUP). Never download
        # links from these broadcasts or reply in their comment threads.
        if event.chat.type == ChatType.CHANNEL or getattr(event, "is_automatic_forward", False) is True:
            return None'''


def _replace_or_verify(source: str, before: str, after: str, filename: str) -> str:
    old_count = source.count(before)
    new_count = source.count(after)
    if old_count == 1 and new_count == 0:
        return source.replace(before, after, 1)
    if old_count == 0 and new_count == 1:
        return source
    raise RuntimeError(f"Conteúdo inesperado em {filename}; arquivo preservado.")


def _patch_universal(source: str) -> str:
    for before, after in (
        ("from aiogram.enums import ChatAction, ParseMode",
         "from aiogram.enums import ChatAction, ChatType, ParseMode"),
        ("from services.links.linkedin import LINKEDIN_URL, LinkedInRedirectError",
         "from services.links.linkedin import LINKEDIN_URL, LinkedInRedirectError\n"
         "from services.links.universal_guard import select_group_download_url"),
        (_UNIVERSAL_OLD, _UNIVERSAL_NEW),
    ):
        source = _replace_or_verify(source, before, after, "handlers/universal.py")
    return source


def _patch_channel_guard(source: str) -> str:
    return _replace_or_verify(
        source, _CHANNEL_OLD, _CHANNEL_NEW, "middlewares/private_chat_guard.py"
    )


def _check_path(path: Path, *, required: bool) -> None:
    if path.is_symlink():
        raise RuntimeError(f"Symlink recusado: {path}")
    if required and not path.is_file():
        raise RuntimeError(f"Arquivo não encontrado: {path}")
    if not required and path.exists() and not path.is_file():
        raise RuntimeError(f"Destino não é arquivo regular: {path}")


def _stage(path: Path, content: bytes) -> Path:
    fd, name = tempfile.mkstemp(prefix=".group-link-", suffix=".py", dir=path.parent)
    staging = Path(name)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(staging, (path.stat().st_mode & 0o777) if path.exists() else 0o644)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    return staging


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Salvar backups e aplicar.")
    args = parser.parse_args()

    source_root = Path(__file__).resolve().parents[1]
    target = args.target.resolve(strict=True)
    new_module = source_root / "services/links/universal_guard.py"
    universal = target / "handlers/universal.py"
    channel = target / "middlewares/private_chat_guard.py"
    module = target / "services/links/universal_guard.py"

    for path in (new_module, universal, channel):
        _check_path(path, required=True)
    _check_path(module, required=False)

    updates: dict[Path, tuple[bytes | None, bytes]] = {}
    for path, patcher in ((universal, _patch_universal), (channel, _patch_channel_guard)):
        old = path.read_bytes()
        new = patcher(old.decode("utf-8")).encode("utf-8")
        if old != new:
            updates[path] = (old, new)

    new_content = new_module.read_bytes()
    if module.exists():
        if module.read_bytes() != new_content:
            raise RuntimeError("O arquivo universal_guard.py já existe com conteúdo diferente.")
    else:
        updates[module] = (None, new_content)

    for path, (_, content) in updates.items():
        ast.parse(content.decode("utf-8"), filename=str(path))

    if not updates:
        print("Proteção já instalada. Nenhum arquivo alterado.")
        return

    for path in updates:
        print("ALTERAÇÃO:", path)
    if not args.apply:
        print("SIMULAÇÃO: nenhuma alteração realizada. Use --apply para instalar.")
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backups: dict[Path, Path] = {}
    staging: dict[Path, Path] = {}
    try:
        for path, (old, new) in updates.items():
            if old is not None:
                backup = path.with_name(path.name + f".bak-group-links-{timestamp}")
                with backup.open("xb") as out:
                    out.write(old)
                    out.flush()
                    os.fsync(out.fileno())
                shutil.copymode(path, backup)
                backups[path] = backup
            staging[path] = _stage(path, new)

        installed: list[Path] = []
        try:
            for path, temporary in staging.items():
                os.replace(temporary, path)
                installed.append(path)
        except BaseException:
            for path in reversed(installed):
                backup = backups.get(path)
                if backup:
                    shutil.copy2(backup, path)
                elif path.exists():
                    path.unlink()
            raise
    finally:
        for temporary in staging.values():
            temporary.unlink(missing_ok=True)

    print("SUCESSO: filtro de links instalado com backups.")
    for backup in backups.values():
        print("BACKUP:", backup)
    print("O bot NÃO foi reiniciado. Reinicie bot-download.service após revisar os testes.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"INSTALAÇÃO BLOQUEADA: {exc}", file=sys.stderr)
        sys.exit(1)
