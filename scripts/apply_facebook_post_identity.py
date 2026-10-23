#!/usr/bin/env python3
"""Apply only the scoped Facebook fix to a locally modified VPS checkout.

Requires an offline, known-photo fixture to pass before touching the target.
No git reset/pull, site-packages modification, service restart or secret output.
"""
import argparse
import ast
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile

SOURCE = Path(__file__).resolve().parents[1]
MODULE = 'services/platforms/facebook_post_identity.py'
MEDIA = 'services/platforms/facebook_media.py'
CACHE = 'services/platforms/facebook_cache.py'
FUNCTIONS = ('_facebook_profile_post_metadata', '_download_facebook_profile_post_photos')


def transplant(current, replacement):
    original_nodes = {n.name: n for n in ast.parse(current).body
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    new_nodes = {n.name: n for n in ast.parse(replacement).body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    lines = current.splitlines(keepends=True)
    source_lines = replacement.splitlines(keepends=True)
    for name in sorted(FUNCTIONS, key=lambda key: original_nodes[key].lineno, reverse=True):
        old, new = original_nodes[name], new_nodes[name]
        if ast.dump(old.args) != ast.dump(new.args):
            raise ValueError(f'Unexpected local signature: {name}')
        lines[old.lineno-1:old.end_lineno] = source_lines[new.lineno-1:new.end_lineno]
    result = ''.join(lines)
    anchor = 'from __future__ import annotations\n'
    if anchor not in result:
        raise ValueError('Missing import anchor')
    result = result.replace(anchor, anchor + 'import tempfile\nfrom pathlib import Path\n', 1)
    result = result.replace('target = resolved\n', 'target = _canonical_fb_media_url(resolved) or resolved\n')
    ast.parse(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, default=Path('/root/Bot-Download'))
    parser.add_argument('--html', type=Path, required=True)
    parser.add_argument('--post-id', required=True)
    parser.add_argument('--expect-photo', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('fb_identity_patch', SOURCE / MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = module.extract_post_identity(args.html.read_text(encoding='utf-8'), args.post_id)
    if not data or args.expect_photo not in data['ids']:
        raise SystemExit('ABORTADO: o HTML não comprovou a foto esperada. Nenhum arquivo alterado.')
    print('Validação offline:', json.dumps(data, ensure_ascii=False, indent=2))
    if not args.apply:
        print('Somente diagnóstico. Use --apply para aplicar após conferir o resultado.')
        return
    target = args.target.resolve()
    if target == SOURCE:
        raise SystemExit('Use um checkout separado para preparar a atualização.')
    old = {name: (target / name).read_text() if (target / name).exists() else None
           for name in (MODULE, MEDIA, CACHE)}
    if old[MODULE] is not None:
        raise SystemExit('Módulo já existe: não reaplicar automaticamente.')
    media = transplant(old[MEDIA], (SOURCE / MEDIA).read_text())
    cache = old[CACHE]
    if 'CACHE_VERSION = 1' not in cache or 'facebook:v1:' not in cache:
        raise SystemExit('Versão local do cache inesperada. Nenhum arquivo alterado.')
    cache = cache.replace('CACHE_VERSION = 1', 'CACHE_VERSION = 2').replace('facebook:v1:', 'facebook:v2:')
    new = {MODULE: (SOURCE / MODULE).read_text(), MEDIA: media, CACHE: cache}
    for name, content in new.items():
        compile(content, name, 'exec')
    for name, content in old.items():
        path = target / name
        if (path.read_text() if path.exists() else None) != content:
            raise SystemExit('Arquivo mudou durante a preparação; abortado.')
    backup = Path(tempfile.mkdtemp(prefix='facebook-identity-', dir=target / 'backups'
                                  if (target / 'backups').is_dir() else target))
    for name, content in old.items():
        if content is not None:
            dest = backup / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target / name, dest)
    (backup / 'manifest.json').write_text(json.dumps({'new_files': [MODULE], 'changed_files': [MEDIA, CACHE]}))
    written = []
    try:
        for name, content in new.items():
            path = target / name
            fd, staging = tempfile.mkstemp(prefix='.fb-fix-', dir=path.parent)
            try:
                with os.fdopen(fd, 'w') as stream:
                    stream.write(content)
                os.chmod(staging, path.stat().st_mode & 0o777 if path.exists() else 0o644)
                os.replace(staging, path)
                written.append(name)
            finally:
                if os.path.exists(staging):
                    os.unlink(staging)
    except BaseException:
        for name in reversed(written):
            if old[name] is None:
                (target / name).unlink()
            else:
                shutil.copy2(backup / name, target / name)
        raise
    print('Aplicado. Backup:', backup)
    print('O serviço NÃO foi reiniciado. Execute o teste direto do fluxo antes de reiniciar.')


if __name__ == '__main__':
    main()
