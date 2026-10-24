"""Install only the dedicated Kwai feature into a modified VPS checkout.

Dry run by default. Never runs git reset, checkout, pull or a service restart.

    python scripts/install_kwai_vps.py --target /root/Bot-Download
    python scripts/install_kwai_vps.py --target /root/Bot-Download --apply

Every overwritten file is copied into a timestamped backup folder, along
with a manifest for restoring the checkout manually.
"""
from __future__ import annotations

import argparse
import difflib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


NEW_MODULES = (
    "handlers/kwai.py",
    "services/platforms/kwai_service.py",
    "services/platforms/kwai_media.py",
)


def patch_handler_init(source: str) -> str:
    lines = source.splitlines(keepends=True)
    import_found = False
    for i, line in enumerate(lines):
        if line.startswith("from . import "):
            import_found = True
            names = line[len("from . import "):].strip().split(", ")
            if "universal" not in names:
                raise ValueError("handlers/__init__.py import list has no universal")
            if "kwai" not in names:
                names.insert(names.index("universal"), "kwai")
                lines[i] = "from . import " + ", ".join(names) + "\n"
            break
    if not import_found:
        raise ValueError("Cannot locate handlers/__init__.py import list")

    if not any("kwai.router" in line for line in lines):
        for i, line in enumerate(lines):
            if line.strip() == "universal.router,":
                lines.insert(i, "    kwai.router,\n")
                break
        else:
            raise ValueError("Cannot locate universal.router registration")
    return "".join(lines)


def patch_batch_router(source: str) -> str:
    anchor = '    if service == "kwai":'
    snippet = (
        '    if service == "kwai":\n'
        "        from handlers import kwai\n"
        "        await kwai.process_kwai_link(message, direct_url=url)\n"
        "        return\n\n"
    )
    if anchor in source:
        start = source.index(anchor)
        end = source.find('    if service == "', start + len(anchor))
        if end == -1:
            raise ValueError("Cannot find next platform after Kwai route")
        return source[:start] + snippet + source[end:]

    before = '    if service == "instagram":'
    if before not in source:
        raise ValueError("Cannot locate routing dispatch insertion point")
    return source.replace(before, snippet + before, 1)


def patch_link_detection(source: str) -> str:
    block = (
        '    (\n'
        '        "kwai",\n'
        '        re.compile(\n'
        '            r"(https?://(?:[\\w-]+\\.)?kwai\\.com/\\S+|"\n'
        '            r"https?://(?:[\\w-]+\\.)?kwai-video\\.com/\\S+|"\n'
        '            r"https?://(?:[\\w-]+\\.)?kw\\.ai/\\S+)",\n'
        '            re.IGNORECASE,\n'
        '        ),\n'
        '    ),\n'
    )
    start = source.find('    (\n        "kwai",')
    next_service = '    (\n        "instagram",'
    end = source.find(next_service, start + 1 if start != -1 else 0)
    if end == -1:
        raise ValueError("Cannot locate next service after Kwai in detection table")
    if start != -1:
        return source[:start] + block + source[end:]
    return source[:end] + block + source[end:]


def calculate_changes(source_dir: Path, target: Path) -> dict[str, bytes]:
    changes = {}
    for relative in NEW_MODULES:
        src_file = source_dir / relative
        if not src_file.is_file():
            raise FileNotFoundError(src_file)
        changes[relative] = src_file.read_bytes()

    transformations = (
        ("handlers/__init__.py", patch_handler_init),
        ("handlers/media_download.py", patch_batch_router),
        ("services/links/detection.py", patch_link_detection),
    )
    for relative, patcher in transformations:
        file = target / relative
        if not file.is_file():
            raise FileNotFoundError(file)
        changes[relative] = patcher(file.read_text("utf-8")).encode("utf-8")

    # Validate all modified Python files before writing anything.
    for relative, data in changes.items():
        compile(data.decode("utf-8"), relative, "exec")

    return {
        path: data
        for path, data in changes.items()
        if not (target / path).exists() or (target / path).read_bytes() != data
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, default=Path("/root/Bot-Download"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    target = args.target.resolve()
    source_dir = Path(__file__).resolve().parents[1]
    if source_dir == target:
        sys.exit("Error: the source worktree and the target checkout are identical.")

    changes = calculate_changes(source_dir, target)
    if not changes:
        print("Kwai integration already matches this worktree. No changes required.")
        return

    print(f"Source: {source_dir}\nTarget: {target}")
    print(f"Files changing: {len(changes)}")
    for relative, new_bytes in changes.items():
        old = target / relative
        print(f"  {'UPDATE' if old.exists() else 'CREATE'} {relative}")
        if old.exists() and relative not in NEW_MODULES:
            before = old.read_text("utf-8").splitlines(keepends=True)
            after = new_bytes.decode("utf-8").splitlines(keepends=True)
            for line in difflib.unified_diff(
                before, after, fromfile=relative + " (current)",
                tofile=relative + " (Kwai)", n=2,
            ):
                print(line, end="")

    if not args.apply:
        print("\nDRY RUN: no files modified. Use --apply only after reviewing the changes.")
        return

    backup = target / (
        ".kwai-install-backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    backup.mkdir(mode=0o700)
    manifest = {"source": str(source_dir), "target": str(target), "files": {}}
    for relative, data in changes.items():
        current = target / relative
        existed = current.exists()
        manifest["files"][relative] = {"existed": existed}
        if existed:
            old_file = backup / relative
            old_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(current, old_file)

    (backup / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    try:
        for relative, data in changes.items():
            file = target / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
    except Exception:
        for relative, info in manifest["files"].items():
            file = target / relative
            if info["existed"]:
                shutil.copy2(backup / relative, file)
            else:
                file.unlink(missing_ok=True)
        raise

    print(f"\nKwai installed without restarting the bot.")
    print(f"Backup: {backup}")
    print("Before restarting: import handlers and run the Kwai test in the target venv.")


if __name__ == "__main__":
    main()
