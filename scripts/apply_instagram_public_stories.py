"""Install the public Story route while preserving VPS-specific extractor code."""

import ast
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile

CLIENT = "services/platforms/instagram_saveinsta.py"
STORIES = "services/platforms/instagram_stories.py"
SERVICE = "services/platforms/instagram_media.py"
KNOWN_CLIENT_HASHES = {
    "b10fdb8c15c7738f21ba9da1f930b96fc757151b2f28243ddca2c9c6c8de93f0",
    "b49a308c63a27e02bb1b84ba2aa872327d1c7b585d443c73529bfb09d955eb56",
}


def _patch_story_route(source: str) -> str:
    tree = ast.parse(source)
    functions = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                 and node.name == "fetch_instagram_media"]
    if len(functions) != 1:
        raise RuntimeError("Função fetch_instagram_media não encontrada de forma única.")
    function = functions[0]
    if "INSTAGRAM_PUBLIC_STORIES_V1" in ast.get_source_segment(source, function):
        return source
    if not function.args.args or function.args.args[0].arg != "url":
        raise RuntimeError("Assinatura do extrator diferente da esperada.")
    if "output_dir" not in {arg.arg for arg in function.args.args}:
        raise RuntimeError("O extrator não possui output_dir.")
    body = function.body
    # Preserve the function docstring if the VPS has one.
    first = body[1] if (len(body) > 1 and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)) else body[0]
    indentation = " " * first.col_offset
    hook = [
        "# INSTAGRAM_PUBLIC_STORIES_V1: no blocked Firefox account for Stories.",
        "from urllib.parse import urlparse as _story_urlparse",
        'if _story_urlparse(url).path.startswith("/stories/"):',
        "    from services.platforms.instagram_stories import fetch_public_instagram_story",
        "    return await fetch_public_instagram_story(url, output_dir)",
    ]
    lines = source.splitlines(keepends=True)
    lines.insert(first.lineno - 1, "".join(indentation + line + "\n" for line in hook))
    result = "".join(lines)
    compile(result, SERVICE, "exec")
    return result


def _download(revision: str, path: str) -> bytes:
    url = f"https://raw.githubusercontent.com/elenilda10/Bot-Download/{revision}/{path}"
    return subprocess.check_output(["curl", "-fsSL", "--max-time", "60", url])


def install(root: Path, revision: str, *, restart: bool = True):
    root = Path(root)
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise RuntimeError("É necessário um commit fixo de 40 caracteres.")
    for command in ("curl", "ffprobe") + (("systemctl",) if restart else ()):
        if not shutil.which(command):
            raise RuntimeError(f"Comando ausente: {command}")
    if not (root / SERVICE).is_file() or not (root / CLIENT).is_file():
        raise RuntimeError("Instale primeiro a integração Save Insta de posts.")
    print("📥 Obtendo suporte aos Stories públicos...", flush=True)
    plan = {CLIENT: _download(revision, CLIENT), STORIES: _download(revision, STORIES)}
    old_client = (root / CLIENT).read_bytes()
    if (old_client != plan[CLIENT]
            and hashlib.sha256(old_client).hexdigest() not in KNOWN_CLIENT_HASHES):
        raise RuntimeError("Cliente Save Insta possui mudanças locais desconhecidas. Nada foi alterado.")
    if (root / STORIES).exists() and (root / STORIES).read_bytes() != plan[STORIES]:
        raise RuntimeError("instagram_stories.py já existe com outro conteúdo. Nada foi alterado.")
    plan[SERVICE] = _patch_story_route((root / SERVICE).read_text()).encode()
    for path, content in plan.items():
        compile(content, path, "exec")
    if all((root / path).exists() and (root / path).read_bytes() == content
           for path, content in plan.items()):
        print("✅ Suporte aos Stories públicos já instalado.")
        return
    backup_root = root / "backups"
    backup_root.mkdir(exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="stories_publicos_", dir=backup_root))
    originals = {}
    modes = {}
    for path in plan:
        original = root / path
        originals[path] = original.read_bytes() if original.exists() else None
        modes[path] = stat.S_IMODE(original.stat().st_mode) if original.exists() else 0o644
        if original.exists():
            destination = backup / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, destination)
    print(f"💾 Backup: {backup}", flush=True)
    try:
        for path, content in plan.items():
            destination = root / path
            fd, temporary = tempfile.mkstemp(dir=destination.parent, prefix=".stories_update_")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                os.chmod(temporary, modes[path])
                os.replace(temporary, destination)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        if restart:
            subprocess.run(["systemctl", "restart", "bot-download.service"], check=True)
            subprocess.run(["systemctl", "is-active", "bot-download.service"], check=True)
        print("✅ Stories públicos ativados pelo Save Insta, sem Firefox.")
        print("Teste com um link /stories/usuario/ID de um Story ainda disponível.")
    except BaseException:
        for path, content in originals.items():
            destination = root / path
            if content is None:
                destination.unlink(missing_ok=True)
            else:
                destination.write_bytes(content)
                destination.chmod(modes[path])
        print("↩️ Arquivos anteriores restaurados.")
        if restart:
            subprocess.run(["systemctl", "restart", "bot-download.service"], check=False)
        raise
