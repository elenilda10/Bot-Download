"""Install optional Firefox cookie integrations, preserving local VPS changes."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

FILES = tuple('services/platforms/' + name + '.py' for name in (
    'tiktok_download_mixin', 'tiktok_metadata_mixin', 'twitter_media',
    'reddit_media', 'universal_extractor', 'youtube_media',
))
HELPER = 'services/platforms/firefox_cookies.py'


def prepare(target: Path, reference: Path, staging: Path):
    patch = reference / 'scripts/patches/platform-firefox-cookies.patch'
    for name in FILES:
        source = target / name
        destination = staging / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    apply = subprocess.run(['git', 'apply', str(patch)], cwd=staging, capture_output=True)
    if apply.returncode:
        reverse = subprocess.run(['git', 'apply', '--reverse', '--check', str(patch)], cwd=staging, capture_output=True)
        if reverse.returncode:
            raise ValueError('Local platform code differs from patch context. No files changed; inspect before installing.')
        # Existing integration can still receive a helper update.
    (staging / HELPER).write_text((reference / HELPER).read_text())
    for name in (*FILES, HELPER):
        compile((staging / name).read_text(), name, 'exec')
    return [name for name in (*FILES, HELPER)
            if not (target / name).exists()
            or (target / name).read_bytes() != (staging / name).read_bytes()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--firefox-profile', type=Path)
    args = parser.parse_args()
    reference = Path(__file__).resolve().parents[1]
    target = args.target.resolve()
    with tempfile.TemporaryDirectory(prefix='platform-cookies-stage-') as tmp:
        staging = Path(tmp)
        changed = prepare(target, reference, staging)
        if args.firefox_profile is not None:
            profile = args.firefox_profile.resolve()
            if not (profile / 'cookies.sqlite').is_file() or not os.access(profile / 'cookies.sqlite', os.R_OK):
                raise SystemExit('Firefox cookies.sqlite is missing or unreadable; no target files changed.')
            if any(char in str(profile) for char in ('\r', '\n', '\0')):
                raise SystemExit('Invalid profile path')
            env_file = target / '.env'
            original = env_file.read_text() if env_file.exists() else ''
            lines = [line for line in original.splitlines()
                     if not re.match(r'^\s*(?:export\s+)?MEDIA_FIREFOX_PROFILE\s*=', line)]
            updated = '\n'.join(lines).rstrip() + '\nMEDIA_FIREFOX_PROFILE=' + repr(str(profile)) + '\n'
            if updated != original:
                (staging / '.env').write_text(updated)
                os.chmod(staging / '.env', 0o600)
                changed.append('.env')
        for name in changed:
            print('UPDATE' if (target / name).exists() else 'CREATE', name)
        if not changed:
            print('Already installed; no files changed.')
            return
        if not args.apply:
            print('DRY RUN: no files changed. Use --apply to install.')
            return
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        for name in changed:
            destination = target / name
            if destination.exists():
                shutil.copy2(destination, destination.with_name(destination.name + '.firefox-backup-' + stamp))
        for name in changed:
            destination = target / name
            fd, temporary = tempfile.mkstemp(prefix='.firefox-install-', dir=destination.parent)
            try:
                with os.fdopen(fd, 'wb') as output:
                    output.write((staging / name).read_bytes())
                if destination.exists():
                    shutil.copystat(destination, temporary)
                else:
                    os.chmod(temporary, 0o600 if name == '.env' else 0o644)
                os.replace(temporary, destination)
            finally:
                Path(temporary).unlink(missing_ok=True)
    print('Installed with backups. Service was not restarted.')


if __name__ == '__main__':
    main()
