"""Disable Instagram account cookies without replacing local extractor customizations."""
from __future__ import annotations

import argparse
import ast
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

MODULE = Path('services/platforms/instagram_media.py')
SOURCES = {'INSTAGRAM_FIREFOX_PROFILE', 'EXTENSION_COOKIES_PATH', 'INSTAGRAM_COOKIES_FILE'}
FLAGS = {
    'cmd': ['--config-ignore'],
    'metadata_cmd': ['--ignore-config', '--no-cookies', '--no-cookies-from-browser'],
    'download_cmd': ['--ignore-config', '--no-cookies', '--no-cookies-from-browser'],
}


def patch_source(source: str, reference=None) -> str:
    tree = ast.parse(source)
    edits = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == '_instagram_cookie_args' or isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in SOURCES for t in node.targets
        ):
            edits.append((node.lineno - 1, node.end_lineno, ''))

    for name, commands in [('_run_gallery_dl', ['cmd']), ('_run_ytdlp', ['metadata_cmd', 'download_cmd'])]:
        function = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None)
        if function is None:
            raise ValueError(f'Target missing {name}; no changes made')
        removed = []
        for node in sorted(ast.walk(function), key=lambda n: getattr(n, 'lineno', 0)):
            if any(start <= getattr(node, 'lineno', 0) <= end for start, end in removed):
                continue
            auth = False
            if isinstance(node, ast.If):
                auth = any(isinstance(n, ast.Name) and n.id in SOURCES for n in ast.walk(node.test))
            elif isinstance(node, ast.Expr):
                auth = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                           and n.func.id == '_instagram_cookie_args' for n in ast.walk(node))
            if auth:
                removed.append((node.lineno, node.end_lineno))
                edits.append((node.lineno - 1, node.end_lineno, ' ' * node.col_offset + 'pass  # Account cookies disabled for Instagram.\n'))
        for command in commands:
            appends = [n for n in ast.walk(function) if isinstance(n, ast.Expr)
                       and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute)
                       and isinstance(n.value.func.value, ast.Name) and n.value.func.value.id == command
                       and n.value.func.attr == 'append' and len(n.value.args) == 1
                       and isinstance(n.value.args[0], ast.Name) and n.value.args[0].id == 'url']
            if len(appends) != 1:
                raise ValueError(f'Unrecognized {command} URL handoff; no changes made')
            # Only accept flags attached to this command, not unrelated literals.
            configured = set()
            for node in ast.walk(function):
                values = None
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == command for t in node.targets):
                    values = node.value
                elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                    call = node.value
                    if (isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name)
                            and call.func.value.id == command and call.func.attr == 'extend' and call.args):
                        values = call.args[0]
                if isinstance(values, (ast.List, ast.Tuple)):
                    configured.update(n.value for n in values.elts if isinstance(n, ast.Constant) and isinstance(n.value, str))
            missing = [flag for flag in FLAGS[command] if flag not in configured]
            if missing:
                anchor = appends[0]
                edits.append((anchor.lineno - 1, anchor.lineno - 1,
                              ' ' * anchor.col_offset + f'{command}.extend({missing!r})\n'))
    lines = source.splitlines(keepends=True)
    for start, end, replacement in sorted(edits, reverse=True):
        lines[start:end] = [replacement] if replacement else []
    result = ''.join(lines)
    compile(result, str(MODULE), 'exec')
    # Refuse unknown authentication layouts rather than partially installing.
    for node in ast.walk(ast.parse(result)):
        if isinstance(node, ast.Name) and (node.id in SOURCES or node.id == '_instagram_cookie_args'):
            raise ValueError('Unrecognized remaining Instagram cookie source; no changes made')
        if isinstance(node, ast.Constant) and node.value in ('--cookies', '--cookies-from-browser', '--username', '--password', '--netrc'):
            raise ValueError('Unrecognized remaining authentication option; no changes made')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    target = args.target.resolve() / MODULE
    original = target.read_text()
    updated = patch_source(original)
    if updated == original:
        print('Instagram public access already installed; no changes.')
        return
    print('UPDATE', MODULE)
    if not args.apply:
        print('DRY RUN: no files modified. Use --apply to install.')
        return
    if target.read_text() != original:
        raise SystemExit('Target changed during inspection; retry.')
    backup = target.with_name(target.name + '.public-backup-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ'))
    shutil.copy2(target, backup)
    fd, temporary = tempfile.mkstemp(prefix='.instagram-public-', dir=target.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(updated)
        shutil.copystat(target, temporary)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print('Backup:', backup)
    print('Installed. Restart bot-download.service to activate.')


if __name__ == '__main__':
    main()
