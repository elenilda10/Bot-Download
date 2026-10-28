"""Selective Reddit handler installation, dry run by default and with backups."""
from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

NEW_FILES = ('handlers/reddit.py', 'services/links/reddit.py', 'services/platforms/reddit_download.py',
             'services/platforms/reddit_json.py')


def patch_init(source):
    lines = source.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith('from . import '):
            names = line[len('from . import '):].strip().split(', ')
            if 'reddit' not in names:
                names.insert(names.index('universal'), 'reddit')
                lines[i] = 'from . import ' + ', '.join(names) + '\n'
            break
    else:
        raise ValueError('Cannot find handler imports')
    source = ''.join(lines)
    if '    reddit.router,' not in source:
        if '    universal.router,' not in source:
            raise ValueError('Cannot find universal router')
        source = source.replace('    universal.router,', '    reddit.router,\n    universal.router,', 1)
    if source.index('reddit.router') > source.index('universal.router'):
        raise ValueError('Reddit must be registered before Universal')
    return source


def patch_dispatch(source):
    if '    if service == "reddit":' in source:
        if 'await reddit.process_reddit(message, direct_url=url)' not in source:
            raise ValueError('Unknown existing Reddit dispatch; inspect first')
        return source
    anchor = '    if service == "instagram":'
    if anchor not in source:
        raise ValueError('Cannot find batch dispatch anchor')
    return source.replace(anchor, '    if service == "reddit":\n        from handlers import reddit\n        await reddit.process_reddit(message, direct_url=url)\n        return\n\n' + anchor, 1)


def patch_detection(source, reference):
    if '\n        "reddit",' not in source:
        start = reference.index('    (\n        "reddit",')
        end = reference.index('    (\n        "instagram",', start)
        block = reference[start:end]
        anchor = '    (\n        "instagram",'
        if anchor not in source:
            raise ValueError('Cannot find link detector anchor')
        source = source.replace(anchor, block + anchor, 1)
    if 'service == "reddit" and parsed.hostname' not in source:
        anchor = '    elif service in {"soundcloud", "spotify"}:'
        if anchor not in source:
            raise ValueError('Cannot find URL normalization anchor')
        source = source.replace(anchor, '    elif service == "reddit" and parsed.hostname in {"i.redd.it", "v.redd.it", "preview.redd.it"}:\n        query = parsed.query\n' + anchor, 1)
    return source


def prepare(target, reference):
    result = {name: (reference / name).read_text() for name in NEW_FILES}
    name = 'handlers/__init__.py'
    result[name] = patch_init((target / name).read_text())
    name = 'handlers/media_download.py'
    result[name] = patch_dispatch((target / name).read_text())
    name = 'services/links/detection.py'
    result[name] = patch_detection((target / name).read_text(), (reference / name).read_text())
    name = 'services/stats/constants.py'
    source = (target / name).read_text()
    if '"reddit": "Reddit"' not in source:
        anchor = '    "twitter": "X / Twitter",'
        if anchor not in source:
            raise ValueError('Cannot find service display names anchor')
        source = source.replace(anchor, anchor + '\n    "reddit": "Reddit",', 1)
    result[name] = source
    for name, content in result.items():
        compile(content, name, 'exec')
    return {name: text for name, text in result.items()
            if not (target / name).exists() or (target / name).read_text() != text}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    target = args.target.resolve()
    reference = Path(__file__).resolve().parents[1]
    if not (target / 'services/platforms/firefox_cookies.py').is_file():
        raise SystemExit('Install the platform Firefox cookie integration first.')
    changed = prepare(target, reference)
    for name in changed:
        print('UPDATE' if (target / name).exists() else 'CREATE', name)
    if not changed:
        print('Already installed; no changes.')
        return
    if not args.apply:
        print('DRY RUN: no files changed. Use --apply to install.')
        return
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    for name in changed:
        path = target / name
        if path.exists():
            shutil.copy2(path, path.with_name(path.name + '.reddit-backup-' + stamp))
    for name, text in changed.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.reddit-install-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as output:
                output.write(text)
            if path.exists():
                shutil.copystat(path, temporary)
            else:
                os.chmod(temporary, 0o644)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    print('Installed with backups. Environment and service state were not changed.')


if __name__ == '__main__':
    main()
