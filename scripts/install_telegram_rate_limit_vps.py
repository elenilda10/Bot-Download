"""Install asynchronous Telegram flood control with staged validation/backups."""

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
    try:
        changes = prepare(
            target, reference=Path(__file__).resolve().parent, bundle="telegram_rate_limit_vps"
        )
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
