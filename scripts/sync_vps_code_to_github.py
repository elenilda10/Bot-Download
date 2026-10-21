#!/usr/bin/env python3
"""Publish the full VPS source-code snapshot to a NEW GitHub branch, without touching the running tree.

The VPS contains runtime state, media, Firefox cookies, backups and certificates.
This tool only stages reviewed source-code extensions, rejects obvious secrets,
and uses a 3-way merge against origin/main. No reset, checkout, or restart.
"""
from __future__ import annotations

import ast
import datetime as dt
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile

SOURCE = Path("/root/Bot-Download")
REPO = "elenilda10/Bot-Download"
BLOCKED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "cookies", "backups", "certs", "downloads",
    "temp", "tmp", "logs", "facebook_auditoria", "facebook_testes",
    "dist", "build", ".tox", "site-packages",
}
BLOCKED_NAMES = {
    ".env", ".runtime_state.json", "cookies.txt", "bot_backup.dump",
    ".netrc", "credentials.json", "secrets.json", "token.json", "id_rsa",
    "known_hosts", "authorized_keys",
}
ALLOWED_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".sh", ".bash",
    ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".md", ".rst",
    ".html", ".css", ".scss", ".sql", ".xml", ".service", ".lock",
    ".gitignore", ".dockerignore",
}
ALLOWED_BASENAMES = {
    "Dockerfile", "Makefile", "Procfile", ".gitignore", ".dockerignore",
    ".python-version", "Caddyfile", "LICENSE", "LICENCE",
}
LIMIT_PER_FILE = 2 * 1024 * 1024
LIMIT_TOTAL = 25 * 1024 * 1024
SUSPECT_SECRETS = {
    "private-key": re.compile(r"-----BEGIN (?:[A-Z ]* )?PRIVATE KEY-----"),
    "telegram-bot-token": re.compile(r"\b[0-9]{6,12}:[A-Za-z0-9_-]{30,}\b"),
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "credential-in-url": re.compile(r"https?://[^/@\s:]+:[^/@\s]+@", re.I),
    "hardcoded-credential": re.compile(
        r"(?im)\b(?:password|passwd|api[_-]?key|client[_-]?secret|"
        r"secret[_-]?key|access[_-]?token|sessionid)\s*[:=]\s*"
        r"['\"][^'\"\r\n]{8,}['\"]"
    ),
}


def run(*args: str, cwd: Path = SOURCE, check: bool = True, input_bytes: bytes | None = None):
    result = subprocess.run(
        args, cwd=cwd, input=input_bytes,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if check and result.returncode:
        msg = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"{args[0]} {args[1] if len(args)>1 else ''} falhou: {msg[:500]}")
    return result


def git(*args: str, cwd: Path = SOURCE, check: bool = True):
    return run("git", *args, cwd=cwd, check=check)


def candidates() -> list[str]:
    tracked = git("ls-files", "--cached", "-z").stdout
    new = git("ls-files", "--others", "--exclude-standard", "-z").stdout
    return sorted({
        s.decode("utf-8", "surrogateescape")
        for s in (tracked + new).split(b"\0") if s
    })


def allowed(name: str) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        return False
    parts = [p.lower() for p in path.parts]
    base = path.name
    lower = base.lower()
    if any(p in BLOCKED_DIRS for p in parts[:-1]):
        return False
    if (
        lower in BLOCKED_NAMES
        or lower.startswith(".env")
        or lower.endswith(("~", ".bak", ".backup", ".orig", ".swp", ".part"))
        or ".bak-" in lower
        or ".bak." in lower
    ):
        return False
    if base.startswith(".") and base not in ALLOWED_BASENAMES:
        return False
    if re.fullmatch(r"requirements(?:-[a-z0-9_-]+)?\.txt", lower):
        return True
    return base in ALLOWED_BASENAMES or path.suffix.lower() in ALLOWED_SUFFIXES


def read_local(name: str) -> bytes:
    path = SOURCE / name
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"Arquivo alterado não é um arquivo regular: {name}")
    if not path.resolve().is_relative_to(SOURCE.resolve()):
        raise RuntimeError(f"Caminho fora do projeto: {name}")
    if path.stat().st_size > LIMIT_PER_FILE:
        raise RuntimeError(f"Arquivo de código maior que 2 MiB: {name}")
    return path.read_bytes()


def ref_contents(ref: str, path: str) -> bytes | None:
    found = git("cat-file", "-e", f"{ref}:{path}", check=False)
    if found.returncode:
        return None
    return git("show", f"{ref}:{path}").stdout


def inspect_text(data: bytes, name: str) -> str:
    if b"\0" in data:
        raise RuntimeError(f"Arquivo binário não permitido: {name}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise RuntimeError(f"Arquivo sem UTF-8: {name}") from None
    for reason, pattern in SUSPECT_SECRETS.items():
        if pattern.search(text):
            raise RuntimeError(f"Possível segredo ({reason}) no arquivo {name}; nada enviado.")
    if name.endswith(".py"):
        try:
            ast.parse(text, filename=name)
        except SyntaxError as exc:
            raise RuntimeError(f"Erro de sintaxe em {name}:{exc.lineno}; nada enviado.") from None
    return text


def merge_text(name: str, base: bytes, upstream: bytes, local: bytes, tmp: Path) -> bytes:
    if upstream == base:
        return local
    if local == base:
        return upstream
    for filename, data in (("main", upstream), ("base", base), ("vps", local)):
        (tmp / filename).write_bytes(data)
    process = run(
        "git", "merge-file", "-p",
        str(tmp / "main"), str(tmp / "base"), str(tmp / "vps"),
        check=False,
    )
    if 0 < process.returncode <= 127:
        # VPS is authoritative for this one-time export. The branch is for
        # review and will NOT be auto-merged into main.
        print(f"REVISAR CONFLITO: {name} (mantida a versao da VPS na branch)")
        return local
    if process.returncode != 0:
        raise RuntimeError(f"Falha no merge de {name}; nada enviado.")
    return process.stdout


def main() -> None:
    if not (SOURCE / ".git").exists():
        raise RuntimeError(f"Repositório Git ausente em {SOURCE}")
    remote = git("remote", "get-url", "origin").stdout.decode().strip()
    if not re.search(r"(?:^|[/@])github\.com[:/]elenilda10/Bot-Download(?:\.git)?$", remote, re.I):
        raise RuntimeError("O remote origin não aponta para o repositório GitHub esperado.")
    selected = [name for name in candidates() if allowed(name)]
    if not selected:
        print("Nenhum arquivo de código encontrado.")
        return
    print(f"Arquivos de código na VPS: {len(selected)}")
    print("Dados privados, backups, cookies e mídias não serão enviados.")
    source_files = {}
    total = 0
    for name in selected:
        if not (SOURCE / name).exists():
            raise RuntimeError(f"Arquivo de código ausente/excluído localmente: {name}; revisão necessária.")
        raw = read_local(name)
        total += len(raw)
        if total > LIMIT_TOTAL:
            raise RuntimeError("Mais de 25 MiB de código alterado; revisão necessária.")
        inspect_text(raw, name)
        source_files[name] = raw

    print("Consultando origin/main...")
    git("fetch", "origin", "main")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch = f"sync/vps-code-{stamp}"
    with tempfile.TemporaryDirectory(prefix="vps-github-sync-") as tempname:
        temp = Path(tempname)
        worktree = temp / "tree"
        git("worktree", "add", "-b", branch, str(worktree), "origin/main")
        try:
            changed = []
            with tempfile.TemporaryDirectory(prefix="vps-merge-") as merge_dir:
                merge_tmp = Path(merge_dir)
                for name, local in source_files.items():
                    base = ref_contents("HEAD", name)
                    upstream = ref_contents("origin/main", name)
                    if base is None and upstream is not None and local != upstream:
                        print(f"REVISAR CONFLITO: {name} (arquivo novo na VPS e na main; priorizada VPS)")
                    if base is not None and upstream is None:
                        print(f"REVISAR CONFLITO: {name} (removido da main; restaurado da VPS)")
                    # Snapshot faithful to the code on VPS; no automatic merge
                    # into main is performed. Review any displaced upstream
                    # changes before merging the resulting branch.
                    if upstream is not None and local != upstream and base != upstream:
                        print(f"REVISAR DIVERGENCIA: {name} (branch usa VPS; main intacta)")
                    merged = local
                    inspect_text(merged, name)
                    if merged == upstream:
                        continue
                    dest = worktree / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(merged)
                    dest.chmod((SOURCE / name).stat().st_mode & 0o777)
                    changed.append(name)
            if not changed:
                print("Todos os arquivos selecionados já coincidem com a main. Nada para enviar.")
                return
            for name in changed:
                git("add", "-f", "--", name, cwd=worktree)
            git("diff", "--cached", "--check", cwd=worktree)
            print(f"Arquivos prontos para commit: {len(changed)}")
            for name in changed:
                print("  +", name)
            git("commit", "-m", "Sync VPS application code (exclude secrets and runtime data)", cwd=worktree)
            print("Enviando para branch isolada...")
            git("push", "-u", "origin", branch, cwd=worktree)
            print("SUCESSO: snapshot de codigo da VPS publicado em branch para revisao.")
            print(f"BRANCH={branch}")
            print(f"LINK=https://github.com/{REPO}/tree/{branch}")
            print("A main não foi alterada; o serviço não foi reiniciado.")
        finally:
            git("worktree", "remove", "--force", str(worktree), check=False)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"SYNC BLOQUEADA: {exc}", file=sys.stderr)
        sys.exit(1)
