"""Patch only Instagram cookie selection; dry run by default, with backup."""
from __future__ import annotations

import argparse
import ast
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

MODULE = Path('services/platforms/instagram_media.py')


def _function(source: str, name: str):
    return next((n for n in ast.parse(source).body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name), None)


def patch_source(target: str, reference: str) -> str:
    helper = _function(reference, '_instagram_cookie_args')
    if helper is None:
        raise ValueError('Source has no Instagram cookie helper')
    helper_text = ast.get_source_segment(reference, helper)
    edits = []
    lines = target.splitlines(keepends=True)
    for name in ('_run_gallery_dl', '_run_ytdlp'):
        function = _function(target, name)
        if function is None:
            raise ValueError(f'Target missing {name}; no changes made')
        for node in ast.walk(function):
            if not isinstance(node, ast.If):
                continue
            test = ast.get_source_segment(target, node.test) or ''
            if test == 'INSTAGRAM_FIREFOX_PROFILE' and name == '_run_gallery_dl':
                edits.append((node.lineno - 1, node.end_lineno, ''))
            elif 'EXTENSION_COOKIES_PATH' in test:
                commands = [n.value.func.value.id for n in node.body
                            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                            and isinstance(n.value.func, ast.Attribute)
                            and isinstance(n.value.func.value, ast.Name)
                            and n.value.func.attr == 'extend']
                if len(commands) != 1 or commands[0] not in ('cmd', 'metadata_cmd', 'download_cmd'):
                    raise ValueError('Unrecognized Instagram cookie block')
                command = commands[0]
                extractor = 'gallery-dl' if command == 'cmd' else 'yt-dlp'
                replacement = ' ' * node.col_offset + f'{command}.extend(_instagram_cookie_args(url, {extractor!r}))\n'
                edits.append((node.lineno - 1, node.end_lineno, replacement))
    for start, end, replacement in sorted(edits, reverse=True):
        lines[start:end] = [replacement] if replacement else []
    result = ''.join(lines)
    old_helper = _function(result, '_instagram_cookie_args')
    if old_helper:
        lines = result.splitlines(keepends=True)
        lines[old_helper.lineno - 1:old_helper.end_lineno] = [helper_text + '\n']
        result = ''.join(lines)
    else:
        first = _function(result, '_run_gallery_dl')
        lines = result.splitlines(keepends=True)
        lines.insert(first.lineno - 1, helper_text + '\n\n\n')
        result = ''.join(lines)
    tree = ast.parse(result)
    defined = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    if 'INSTAGRAM_FIREFOX_PROFILE' not in defined:
        result = "\n".join(result.splitlines()) + '\n'
        marker = result.index('def _instagram_cookie_args')
        result = result[:marker] + "INSTAGRAM_FIREFOX_PROFILE = os.getenv('INSTAGRAM_FIREFOX_PROFILE', '').strip()\n\n\n" + result[marker:]
    result = result.replace('logger.warning("gallery-dl output/warn: %s", proc.stderr)',
                            'logger.warning("Instagram gallery-dl terminou com código %s", proc.returncode)')
    integrated = {
        node.func.value.id for node in ast.walk(ast.parse(result))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name) and node.func.attr == 'extend'
        and node.args and isinstance(node.args[0], ast.Call)
        and isinstance(node.args[0].func, ast.Name)
        and node.args[0].func.id == '_instagram_cookie_args'
    }
    if integrated != {'cmd', 'metadata_cmd', 'download_cmd'}:
        raise ValueError('Cookie integration missing for one or more commands')
    compile(result, str(MODULE), 'exec')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / MODULE
    target = args.target.resolve() / MODULE
    original = target.read_text()
    updated = patch_source(original, source.read_text())
    if updated == original:
        print('Instagram cookies already integrated; no changes.')
        return
    print('UPDATE', MODULE)
    if not args.apply:
        print('DRY RUN: no files modified. Use --apply to install.')
        return
    if target.read_text() != original:
        raise SystemExit('Target changed during inspection; retry.')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = target.with_name(target.name + '.cookies-backup-' + stamp)
    shutil.copy2(target, backup)
    fd, temporary = tempfile.mkstemp(prefix='.instagram-cookies-', dir=target.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(updated)
        shutil.copystat(target, temporary)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print('Backup:', backup)
    print('Installed. Environment and service state were not modified.')


if __name__ == '__main__':
    main()
