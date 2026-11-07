#!/usr/bin/env python3
"""Patch only Instagram extraction order in an existing VPS checkout.

Dry-run unless --apply is set. Never resets git, changes .env, or restarts bot.
Run from the PR #60 worktree, with parth-dl installed in the target venv.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

SERVICE = Path("services/platforms/instagram_media.py")
CLIENT = Path("services/platforms/instagram_parth.py")

PARTH = """    # 2. parth-dl: posts, Reels and carousels (no account login).
    if (not story_id and "/stories/" not in clean_url
            and os.getenv("INSTAGRAM_PARTH_ENABLED", "1").strip().lower() not in {"0", "false", "off"}):
        _t = time.monotonic()
        try:
            items = await loop.run_in_executor(None, download_parth, clean_url, output_dir)
            if items:
                logger.info("Instagram parth-dl success: items=%s", len(items))
                return await _with_instagram_metadata(InstagramVideo(
                    id=post_id, description="", author="instagram_user",
                    media_list=[InstagramMedia(**item) for item in items],
                ), clean_url)
        except Exception as exc:
            logger.warning("Instagram parth-dl failed: %s", type(exc).__name__)
        finally:
            logger.warning(
                "Instagram fallback timing: stage=parth_dl duration=%.2fs",
                time.monotonic() - _t,
            )
"""

def function(source, name, async_=False):
    cls = ast.AsyncFunctionDef if async_ else ast.FunctionDef
    matches = [x for x in ast.parse(source).body
               if isinstance(x, cls) and x.name == name]
    if len(matches) != 1:
        raise RuntimeError(f"Unrecognized {name}; target left unchanged")
    return matches[0]

def cookie_free(source):
    edits = []
    for name, commands in (("_run_gallery_dl", ["cmd"]),
                           ("_run_ytdlp", ["metadata_cmd", "download_cmd"])):
        fn = function(source, name)
        removed = []
        for node in ast.walk(fn):
            if not isinstance(node, ast.If):
                continue
            used = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
            if not used.intersection({"EXTENSION_COOKIES_PATH", "INSTAGRAM_FIREFOX_PROFILE"}):
                continue
            body = ast.get_source_segment(source, node) or ""
            if "--cookies" not in body:
                raise RuntimeError("Unknown authentication condition; no changes")
            removed.append(node)
        for node in removed:
            if any(n is not node and n.lineno <= node.lineno
                   and n.end_lineno >= node.end_lineno for n in removed):
                continue
            edits.append((node.lineno - 1, node.end_lineno,
                          " " * node.col_offset + "pass  # Instagram cookies disabled\n"))
        for command in commands:
            anchors = [n for n in ast.walk(fn)
                       if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                       and isinstance(n.value.func, ast.Attribute)
                       and isinstance(n.value.func.value, ast.Name)
                       and n.value.func.value.id == command
                       and n.value.func.attr == "append"
                       and len(n.value.args) == 1
                       and isinstance(n.value.args[0], ast.Name)
                       and n.value.args[0].id == "url"]
            if len(anchors) != 1:
                raise RuntimeError(f"Unknown {command} URL handoff; no changes")
            flags = (["--config-ignore"] if command == "cmd" else
                     ["--ignore-config", "--no-cookies", "--no-cookies-from-browser"])
            known = set()
            for n in ast.walk(fn):
                if not isinstance(n, ast.Expr) or not isinstance(n.value, ast.Call):
                    continue
                call = n.value
                if (isinstance(call.func, ast.Attribute)
                        and isinstance(call.func.value, ast.Name)
                        and call.func.value.id == command
                        and call.func.attr == "extend"
                        and call.args and isinstance(call.args[0], (ast.List, ast.Tuple))):
                    known.update(x.value for x in call.args[0].elts
                                 if isinstance(x, ast.Constant) and isinstance(x.value, str))
            missing = [f for f in flags if f not in known]
            if missing:
                a = anchors[0]
                edits.append((a.lineno - 1, a.lineno - 1,
                              " " * a.col_offset + f"{command}.extend({missing!r})\n"))
    lines = source.splitlines(keepends=True)
    for a, b, value in sorted(edits, key=lambda x: (x[0], x[1]), reverse=True):
        lines[a:b] = [value]
    result = "".join(lines).replace(
        'logger.warning("gallery-dl output/warn: %s", proc.stderr)',
        'logger.warning("Instagram gallery-dl failed (exit=%s)", proc.returncode)'
    )
    compile(result, str(SERVICE), "exec")
    for name in ("_run_gallery_dl", "_run_ytdlp"):
        fn = function(result, name)
        if any(isinstance(n, ast.Constant) and n.value in
               {"--cookies", "--cookies-from-browser", "--netrc", "--username", "--password"}
               for n in ast.walk(fn)):
            raise RuntimeError("Unknown Instagram authentication flag; no changes")
        if any(isinstance(n, ast.Name) and n.id == "_instagram_cookie_args"
               for n in ast.walk(fn)):
            raise RuntimeError("Unknown Instagram cookie helper; no changes")
    return result

def reorder(source):
    fn = function(source, "fetch_instagram_media", async_=True)
    if "download_parth" in (ast.get_source_segment(source, fn) or ""):
        return source  # Already installed: keep local customizations.
    body = fn.body
    def assigned(name):
        hits = [i for i, n in enumerate(body)
                if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
        if len(hits) != 1:
            raise RuntimeError(f"Unrecognized stage {name}; no changes")
        return hits[0]
    save = [i for i, n in enumerate(body)
            if isinstance(n, ast.If)
            and any(isinstance(x, ast.Name) and x.id == "download_saveinsta"
                    for x in ast.walk(n))]
    if len(save) != 1:
        raise RuntimeError("SaveInsta stage missing or ambiguous; no changes")
    g, y, l, p = [assigned(n) for n in
                   ("res_gdl", "res_ytdlp", "payload", "headers")]
    if g < 1 or y < 1:
        raise RuntimeError("Missing extractor timing blocks; no changes")
    if any(not isinstance(body[i-1], ast.Assign)
           or not any(isinstance(t, ast.Name) and t.id == "_t"
                      for t in body[i-1].targets) for i in (g, y)):
        raise RuntimeError("Unexpected extractor layout; no changes")
    g -= 1
    y -= 1
    s = save[0]
    r = len(body) - 1
    if (not (g < s < y < l < p < r) or not isinstance(body[r], ast.Return)
            or not isinstance(body[r].value, ast.Constant)
            or body[r].value.value is not None):
        raise RuntimeError("Unexpected fallback order; no changes")
    lines = source.splitlines(keepends=True)
    def part(i, j):
        return "".join(lines[body[i].lineno-1:body[j-1].end_lineno]).rstrip() + "\n"
    start = body[g-1].end_lineno if g else fn.lineno
    end = body[r].lineno - 1
    new_flow = (
        "\n    # 1. SaveInsta first.\n" + part(s, y)
        + "\n" + PARTH
        + "\n    # 3. Cobalt local/public.\n" + part(l, p) + part(p, r)
        + "\n    # 4. yt-dlp without cookies.\n" + part(y, l)
        + "\n    # 5. gallery-dl last, without cookies.\n" + part(g, s)
        + "\n"
    )
    result = "".join(lines[:start]) + new_flow + "".join(lines[end:])
    compile(result, str(SERVICE), "exec")
    return result

def patch_service(source):
    marker = "from services.platforms.instagram_saveinsta import"
    if marker not in source:
        raise RuntimeError("SaveInsta import missing; no changes")
    source = reorder(cookie_free(source))
    parth_import = "from services.platforms.instagram_parth import download_parth\n"
    if parth_import not in source:
        lines = source.splitlines(keepends=True)
        matches = [i for i, l in enumerate(lines) if l.startswith(marker)]
        if len(matches) != 1:
            raise RuntimeError("Unexpected SaveInsta imports; no changes")
        lines.insert(matches[0] + 1, parth_import)
        source = "".join(lines)
    compile(source, str(SERVICE), "exec")
    return source

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=Path("/root/Bot-Download"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    source_client = Path(__file__).resolve().parents[1] / CLIENT
    target_client = args.target / CLIENT
    target_service = args.target / SERVICE
    if not source_client.is_file() or not target_service.is_file():
        raise SystemExit("Required source/target missing; nothing changed")
    client_bytes = source_client.read_bytes()
    compile(client_bytes, str(CLIENT), "exec")
    if target_client.exists() and target_client.read_bytes() != client_bytes:
        raise SystemExit("Different existing parth module; refusing to overwrite")
    old = target_service.read_text(encoding="utf-8")
    new = patch_service(old)
    changes = old != new
    create = not target_client.exists()
    if not changes and not create:
        print("Already installed; no changes.")
        return
    print("PLAN:", SERVICE, "update" if changes else "unchanged")
    print("PLAN:", CLIENT, "create" if create else "unchanged")
    if not args.apply:
        print("DRY RUN: no files modified. Re-run with --apply when reviewed.")
        return
    python = args.target / "venv/bin/python"
    if not python.is_file():
        raise RuntimeError("Bot venv missing")
    if subprocess.run([str(python), "-c", "import parth_dl"],
                      capture_output=True).returncode:
        raise RuntimeError("Install into bot venv first: venv/bin/pip install parth-dl==1.2.1")
    backups = args.target / "backups"
    backups.mkdir(exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="instagram_parth_", dir=backups))
    (backup / "instagram_media.py").write_text(old, encoding="utf-8")
    originals = [(target_service, target_service.read_bytes()),
                 (target_client, target_client.read_bytes() if target_client.exists() else None)]
    try:
        for path, data in ((target_service, new.encode()), (target_client, client_bytes)):
            if (path == target_service and not changes) or (path == target_client and not create):
                continue
            fd, temp = tempfile.mkstemp(prefix=".instagram_parth_", dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                if path.exists():
                    shutil.copymode(path, temp)
                os.replace(temp, path)
            finally:
                Path(temp).unlink(missing_ok=True)
        print("INSTALLED. Backup:", backup)
        print("Now manually restart: systemctl restart bot-download.service")
    except BaseException:
        for path, data in originals:
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(data)
        raise

if __name__ == "__main__":
    main()
