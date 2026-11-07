"""Remove the progress keyboard from existing VPS handlers, keeping local edits."""

import argparse
import ast
from pathlib import Path
import re
import shutil
import tempfile

def remove_button(source):
    tree = ast.parse(source)
    helpers = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_make_progress_keyboard"]
    if len(helpers) != 1:
        raise ValueError("Construtor de teclado inesperado")
    lines = source.splitlines(keepends=True)
    helper = helpers[0]
    end = helper.end_lineno
    while end < len(lines) and not lines[end].strip():
        end += 1
    del lines[helper.lineno - 1:end]
    source = "".join(lines)
    source, assignments = re.subn(r"(?m)^ +progress_kb = _make_progress_keyboard\([^\n]+\)\n", "", source)
    source, buttons = re.subn(r"(?m)^ +reply_markup=progress_kb,\n", "", source)
    source = source.replace(", reply_markup=progress_kb)", ")")
    if assignments != 1 or buttons != 1 or "progress_kb" in source or "_make_progress_keyboard" in source:
        raise ValueError("Uso do teclado diferente do esperado")
    if source.count("InlineKeyboardMarkup") == source.count("InlineKeyboardButton") == 1:
        source = source.replace(", InlineKeyboardMarkup, InlineKeyboardButton", "")
    compile(source, "<handler>", "exec")
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    changes = {}
    for name in ("handlers/youtube.py", "handlers/universal.py"):
        source = (args.target / name).read_text()
        if "_make_progress_keyboard" not in source and "progress_kb" not in source:
            continue
        changes[name] = remove_button(source).encode()
        print("UPDATE", name)
    if not changes:
        print("Já aplicado.")
    elif args.apply:
        backups = args.target / "backups"
        backups.mkdir(exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix="progress-buttons-", dir=backups))
        with tempfile.TemporaryDirectory(dir=args.target) as stage_dir:
            stage = Path(stage_dir)
            for name, data in changes.items():
                for root in (stage, backup):
                    (root / name).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(args.target / name, root / name)
                (stage / name).write_bytes(data)
            replaced = []
            try:
                for name in changes:
                    (stage / name).replace(args.target / name)
                    replaced.append(name)
            except OSError:
                for name in replaced:
                    shutil.copy2(backup / name, args.target / name)
                raise
        print("Backup:", backup)
    else:
        print("Simulação concluída.")


if __name__ == "__main__":
    main()
