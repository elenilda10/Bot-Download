#!/usr/bin/env python3
"""Safely patch the Pinterest Telegram upload, including locally modified VPS code.

The running VPS may have message.reply_video() while GitHub uses
message.answer_video(). Both are handled without replacing other changes.

Also repairs the shared inline sender only when its exact known old block is
present. Backs up every modified file and does not restart systemd or git pull.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import difflib
import os
from pathlib import Path
import stat
import sys
import tempfile

ROOT = Path("/root/Bot-Download")
HANDLER = ROOT / "handlers/pinterest.py"
INLINE = ROOT / "services/inline/send_flow.py"

OLD_INLINE = """        raw_kwargs = build_video_send_kwargs(metrics.path)
        if asyncio.iscoroutine(raw_kwargs):
            video_kwargs = await raw_kwargs
        else:
            video_kwargs = raw_kwargs

        if not isinstance(video_kwargs, dict):
            video_kwargs = {}

        video_kwargs["supports_streaming"] = True"""
NEW_INLINE = """        # The helper returns (kwargs, thumbnail_path), not a single dict.
        # Register the thumbnail for the existing inline-flow cleanup.
        video_kwargs, thumbnail_path = await build_video_send_kwargs(metrics.path)
        if thumbnail_path:
            state.register_download_path(thumbnail_path)

        video_kwargs["supports_streaming"] = True"""


def patch_pinterest_source(source: str) -> str:
    """Patch exactly the _send_downloaded video branch, preserving other edits."""
    anchor = "    async def _send_downloaded(path: str):"
    if source.count(anchor) != 1:
        raise ValueError("Handler Pinterest não contém a função de envio esperada.")
    start = source.index(anchor)
    # _send_downloaded is a closure; only inspect its body before the photo send.
    end = source.find("        return await message.", start + len(anchor))
    if end < 0:
        raise ValueError("Handler Pinterest sem envio de vídeo/foto reconhecido.")
    # Preserve the entire function, even if the VPS uses reply_video().
    lines = source.splitlines(keepends=True)
    anchor_line = next(i for i, line in enumerate(lines) if line.rstrip("\r\n") == anchor)
    next_func = next(
        (i for i in range(anchor_line + 1, len(lines))
         if lines[i].startswith("    async def ") or lines[i].startswith("    def ")),
        len(lines),
    )
    function_text = "".join(lines[anchor_line:next_func])
    bad = "                **(await build_video_send_kwargs(path)),"
    # The old code has 16 spaces; accept the underlying expression regardless
    # of slight local formatting changes to the media upload parameters.
    if "**(await build_video_send_kwargs(path))," not in function_text:
        if "video_kwargs, thumbnail_path = await build_video_send_kwargs(path)" in function_text:
            return source
        raise ValueError("Não encontrei o trecho de kwargs incorreto; nada alterado.")

    indices = [
        i for i in range(anchor_line, next_func)
        if "**(await build_video_send_kwargs(path))," in lines[i]
    ]
    if len(indices) != 1:
        raise ValueError("Encontrados múltiplos trechos ambíguos de envio.")
    kw_i = indices[0]
    call_i = next(
        (j for j in range(kw_i - 1, max(anchor_line, kw_i - 35), -1)
         if lines[j].startswith("            return await message.")
         and (".reply_video(" in lines[j] or ".answer_video(" in lines[j])),
        None,
    )
    if call_i is None:
        raise ValueError("Envio de vídeo não encontrado perto dos kwargs.")
    close_i = next(
        (i for i in range(kw_i + 1, next_func)
         if lines[i].strip() == ")" and lines[i].startswith("            )")),
        None,
    )
    if close_i is None or close_i - kw_i > 4:
        raise ValueError("Limites da chamada de envio de vídeo não reconhecidos.")
    if lines[kw_i].strip() != "**(await build_video_send_kwargs(path)),":
        raise ValueError("Expressão kwargs não corresponde exatamente ao erro observado.")

    original_call = lines[call_i:close_i + 1]
    rewritten = [
        "            video_kwargs, thumbnail_path = await build_video_send_kwargs(path)\n",
        "            try:\n",
    ]
    for index, line in enumerate(original_call):
        if index == 0:
            rewritten.append("                return await message.answer_video(\n")
        elif call_i + index == kw_i:
            rewritten.append("                    **video_kwargs,\n")
        else:
            rewritten.append("    " + line)
    rewritten += [
        "            finally:\n",
        "                if thumbnail_path:\n",
        "                    await remove_file(thumbnail_path)\n",
    ]
    candidate = "".join(lines[:call_i] + rewritten + lines[close_i + 1:])
    ast.parse(candidate, filename="handlers/pinterest.py")
    return candidate


def patch_inline_source(source: str) -> str:
    """Fix the shared inline sender only when the known old version matches."""
    if OLD_INLINE in source:
        candidate = source.replace(OLD_INLINE, NEW_INLINE)
        ast.parse(candidate, filename="services/inline/send_flow.py")
        return candidate
    if (
        "video_kwargs, thumbnail_path = await build_video_send_kwargs(metrics.path)"
        in source
        and "state.register_download_path(thumbnail_path)" in source
    ):
        return source
    raise ValueError(
        "Fluxo inline tem alterações não reconhecidas; implantação bloqueada "
        "para evitar sobrescrever código personalizado."
    )


def _atomic_write(target: Path, content: bytes, mode: int) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".video-send-fix-", suffix=".py", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main() -> None:
    for target in (HANDLER, INLINE):
        if not target.is_file() or target.is_symlink():
            raise RuntimeError(f"Arquivo não encontrado ou symlink: {target}")

    originals = {p: p.read_bytes() for p in (HANDLER, INLINE)}
    changes: dict[Path, bytes] = {}
    for target, patch_fn in (
        (HANDLER, patch_pinterest_source),
        (INLINE, patch_inline_source),
    ):
        source = originals[target].decode("utf-8")
        updated = patch_fn(source)
        if updated != source:
            encoded = updated.encode("utf-8")
            ast.parse(updated, filename=str(target))
            changes[target] = encoded

    if not changes:
        print("O envio de vídeos Pinterest e inline já está corrigido.")
        return

    print("Arquivos que serão corrigidos:", len(changes))
    for target, updated in changes.items():
        print("PATCH:", target.relative_to(ROOT))
        diff = difflib.unified_diff(
            originals[target].decode("utf-8").splitlines(),
            updated.decode("utf-8").splitlines(),
            fromfile="anterior", tofile="corrigido", lineterm="",
        )
        # Limit diff output to 80 lines.
        for i, line in enumerate(diff):
            if i >= 80:
                print("... diferença truncada")
                break
            print(line)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup_root = ROOT / "backups" / f"pinterest-video-send-{stamp}"
    backup_root.mkdir(parents=True, exist_ok=False)

    for target in changes:
        backup_path = backup_root / target.relative_to(ROOT)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        with backup_path.open("xb") as handle:
            handle.write(originals[target])
            handle.flush()
            os.fsync(handle.fileno())

    replaced = []
    try:
        for target, updated in changes.items():
            _atomic_write(target, updated, stat.S_IMODE(target.stat().st_mode))
            replaced.append(target)
    except Exception:
        for target in reversed(replaced):
            _atomic_write(target, originals[target], stat.S_IMODE(target.stat().st_mode))
        raise

    print("SUCESSO: chamada de vídeo e metadados inline corrigidos.")
    print("BACKUP:", backup_root)
    print("O serviço não foi reiniciado.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"INSTALACAO BLOQUEADA: {exc}", file=sys.stderr)
        raise SystemExit(1)
