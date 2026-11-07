"""Install the public LinkedIn post handler with staged validation/backups."""

import argparse
from pathlib import Path

from install_i18n_vps import RuntimeConflict, install, prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--diagnose", action="store_true")
    args = parser.parse_args()
    target = args.target.resolve()
    reference = Path(__file__).resolve().parent
    registry = target / "services/platforms/universal_extractor.py"
    previous_handler = (target / "handlers/linkedin.py").is_file() and (target / "services/links/linkedin.py").is_file()
    incremental = previous_handler and registry.is_file() and "await resolve_linkedin_url(url)" in registry.read_text()
    bundle = "linkedin_vps"
    if incremental:
        bundle = "linkedin_browser_vps" if (target / "services/platforms/linkedin_photo.py").is_file() else "linkedin_photo_vps"
    try:
        changes = prepare(
            target, reference=reference, bundle=bundle
        )
        changes.update(prepare(target, reference=reference, bundle="linkedin_cli_vps"))
    except RuntimeConflict as exc:
        if args.diagnose and exc.details:
            print(exc.details)
        parser.exit(1, str(exc) + "\n")
    for name in changes:
        print(("UPDATE " if (target / name).exists() else "CREATE ") + name)
    if not args.apply:
        print("DRY RUN: no files modified. Use --apply to install with backups.")
    elif changes:
        backup = install(target, changes)
        print(f"Installed with backups: {backup}. Service was not restarted.")
    else:
        print("Already installed; no changes.")


if __name__ == "__main__":
    main()
