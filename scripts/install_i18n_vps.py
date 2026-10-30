"""Stage and validate the complete PT/EN/ES migration; --apply saves backups.

Only listed runtime files are patched. .env, credentials, database and service
state are never modified. Unknown conflicting revisions fail before any write.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
from string import Formatter
import subprocess
import tempfile

REFERENCE = Path(__file__).resolve().parent


def digest(data):
    return hashlib.sha256(data).hexdigest()


def prepare(target, *, reference=REFERENCE):
    manifest = json.loads((reference / 'i18n_vps_manifest.json').read_text())
    patch = (reference / 'i18n_vps.patch').resolve()
    if digest(patch.read_bytes()) != manifest['patch_sha256']:
        raise ValueError('The migration patch failed its integrity check.')
    names = list(manifest['files'])
    if any(Path(name).is_absolute() or '..' in Path(name).parts for name in names):
        raise ValueError('Invalid runtime path in migration manifest.')
    if all((target / name).is_file() and digest((target / name).read_bytes()) == info['result_sha256']
           for name, info in manifest['files'].items()):
        return {}
    with tempfile.TemporaryDirectory(prefix='bot-i18n-stage-') as directory:
        staging = Path(directory)
        for name, info in manifest['files'].items():
            source = target / name
            if source.exists():
                if not source.is_file() or source.is_symlink():
                    raise ValueError('Expected a regular runtime file: ' + name)
                destination = staging / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            elif info['base_sha256'] is not None:
                raise ValueError('Missing required runtime file: ' + name)
        check = subprocess.run(['git', 'apply', '--check', '--whitespace=nowarn', str(patch)],
                               cwd=staging, capture_output=True, text=True)
        if check.returncode:
            raise ValueError('Runtime changes conflict with this migration; no files changed.\n' + check.stderr.strip())
        subprocess.run(['git', 'apply', '--whitespace=nowarn', str(patch)], cwd=staging, check=True, capture_output=True)
        values = {name: (staging / name).read_bytes() for name in names}
        for name, content in values.items():
            if name.endswith('.py'):
                compile(content, name, 'exec')
        catalogs = {lang: json.loads(values['i18n/locales/' + lang + '.json']) for lang in ('pt', 'en', 'es')}
        for lang, catalog in catalogs.items():
            if catalog.keys() != catalogs['en'].keys():
                raise ValueError('Translation keys differ: ' + lang)
            for key, text in catalog.items():
                fields = {field for _, field, _, _ in Formatter().parse(text) if field is not None}
                expected = {field for _, field, _, _ in Formatter().parse(catalogs['en'][key]) if field is not None}
                if not text.strip() or fields != expected:
                    raise ValueError('Invalid translation: ' + lang + '/' + key)
        return {name: content for name, content in values.items()
                if not (target / name).is_file() or (target / name).read_bytes() != content}


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.i18n-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
        os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install(target, changes):
    if not changes:
        return None
    backup = target / 'backups' / ('i18n-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f'))
    previous = {}
    for name in changes:
        path = target / name
        previous[name] = path.read_bytes() if path.exists() else None
        if path.exists():
            destination = backup / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
    backup.mkdir(parents=True, exist_ok=True)
    (backup / 'created-files.json').write_text(json.dumps([name for name, data in previous.items() if data is None], indent=2))
    written = []
    try:
        for name, content in changes.items():
            atomic_write(target / name, content)
            written.append(name)
    except Exception:
        for name in reversed(written):
            if previous[name] is None:
                (target / name).unlink(missing_ok=True)
            else:
                atomic_write(target / name, previous[name])
        raise
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    target = args.target.resolve()
    try:
        changes = prepare(target)
        for name in changes:
            print('UPDATE' if (target / name).exists() else 'CREATE', name)
        if not changes:
            print('PT/EN/ES migration is already installed.')
        elif not args.apply:
            print('DRY RUN: no files modified. Use --apply to install.')
        else:
            print('Backup:', install(target, changes))
            print('Installed. Environment, database and service state were not changed.')
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == '__main__':
    main()
