"""Stage and validate the complete PT/EN/ES migration; --apply saves backups.

Only listed runtime files are patched. .env, credentials, database and service
state are never modified. Unknown conflicting revisions fail before any write.
"""
import argparse
import ast
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


class RuntimeConflict(ValueError):
    def __init__(self, message, details):
        super().__init__(message)
        self.details = details


def conflict_blocks(content):
    """Return only conflicting code blocks, not the complete live handlers."""
    result = []
    inside = False
    for line in content.decode('utf-8', errors='replace').splitlines():
        if line.startswith('<<<<<<<'):
            inside = True
        if inside:
            result.append(line)
        if line.startswith('>>>>>>>'):
            inside = False
    return '\n'.join(result)


def preserve_reply_methods(base_source, incoming_source, local_source):
    """Carry proven answer->reply choices into both merge sides without changing text.

    Scope and send-call order must agree, and the complete local argument AST
    must equal either the old or translated call. Extra/reordered calls or
    competing message/argument changes do not get an automatic resolution.
    """
    methods = {'answer' + suffix: 'reply' + suffix for suffix in
               ('', '_audio', '_video', '_photo', '_document', '_media_group')}
    family = {value: key for key, value in methods.items()}
    family.update({key: key for key in methods})

    class Calls(ast.NodeVisitor):
        def __init__(self, source):
            self.scopes = {}
            self.scope = ()
            self.visit(ast.parse(source))

        def visit_FunctionDef(self, node):
            previous = self.scope
            self.scope = previous + (node.name,)
            self.generic_visit(node)
            self.scope = previous

        visit_AsyncFunctionDef = visit_FunctionDef
        visit_ClassDef = visit_FunctionDef

        def visit_Call(self, node):
            if isinstance(node.func, ast.Attribute) and node.func.attr in family:
                self.scopes.setdefault(self.scope, []).append(node)
            self.generic_visit(node)

    try:
        sources = [base_source.decode(), incoming_source.decode(), local_source.decode()]
        maps = [Calls(source).scopes for source in sources]
    except (SyntaxError, UnicodeDecodeError):
        return base_source, incoming_source
    edits = [[], []]
    def signature(call):
        return ast.dump(call.func.value), family[call.func.attr]

    def arguments(call):
        return ast.dump(ast.Tuple(elts=[*call.args, *call.keywords], ctx=ast.Load()))

    for scope, old_calls in maps[0].items():
        new_calls = maps[1].get(scope, [])
        local_calls = maps[2].get(scope, [])
        expected = [signature(call) for call in old_calls]
        if expected != [signature(call) for call in new_calls] or expected != [signature(call) for call in local_calls]:
            continue
        for old, new, local in zip(old_calls, new_calls, local_calls):
            if (old.func.attr not in methods or new.func.attr != old.func.attr
                    or local.func.attr != methods[old.func.attr]
                    or arguments(local) not in {arguments(old), arguments(new)}):
                continue
            for index, call in enumerate((old, new)):
                lines = sources[index].splitlines(keepends=True)
                line = lines[call.func.end_lineno - 1]
                end = sum(map(len, lines[:call.func.end_lineno - 1])) + len(line.encode()[:call.func.end_col_offset].decode())
                edits[index].append((end - len(call.func.attr), end, local.func.attr))
    for index in (0, 1):
        for start, end, value in sorted(edits[index], reverse=True):
            sources[index] = sources[index][:start] + value + sources[index][end:]
    return sources[0].encode(), sources[1].encode()


def merge_runtime(target, staging, manifest, patch, reference):
    """Three-way merge only in temporary directories; never choose a conflict side."""
    with tempfile.TemporaryDirectory(prefix='bot-i18n-base-') as directory:
        base = Path(directory)
        incoming = reference.parent
        for name, info in manifest['files'].items():
            content = (incoming / name).read_bytes()
            if digest(content) != info['result_sha256']:
                raise ValueError('Reference runtime failed its integrity check: ' + name)
            destination = base / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        subprocess.run(['git', 'apply', '--reverse', '--whitespace=nowarn', str(patch)],
                       cwd=base, check=True, capture_output=True)
        conflicts = []
        details = []
        for name, info in manifest['files'].items():
            source = target / name
            expected = (incoming / name).read_bytes()
            original = base / name
            if info['base_sha256'] is None:
                if source.exists() and source.read_bytes() != expected:
                    conflicts.append(name + ' (new path already has local contents)')
                    continue
                content = expected
            else:
                if digest(original.read_bytes()) != info['base_sha256']:
                    raise ValueError('Reconstructed base failed its integrity check: ' + name)
                local = source.read_bytes()
                if local == expected:
                    content = local
                elif local == original.read_bytes():
                    content = expected
                else:
                    compatible_base, compatible_incoming = preserve_reply_methods(original.read_bytes(), expected, local)
                    with tempfile.TemporaryDirectory(prefix='bot-i18n-replies-') as reply_directory:
                        merge_base = Path(reply_directory) / 'base.py'
                        merge_incoming = Path(reply_directory) / 'incoming.py'
                        merge_base.write_bytes(compatible_base)
                        merge_incoming.write_bytes(compatible_incoming)
                        merged = subprocess.run(
                            ['git', 'merge-file', '--stdout', '--diff3',
                             '-L', 'VPS', '-L', 'BASE', '-L', 'TRANSLATED',
                             str(source.resolve()), str(merge_base), str(merge_incoming)],
                            capture_output=True,
                        )
                    if merged.returncode:
                        conflicts.append(name)
                        blocks = conflict_blocks(merged.stdout)
                        if blocks:
                            details.append('=== ' + name + ' ===\n' + blocks)
                        continue
                    content = merged.stdout
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        if conflicts:
            raise RuntimeConflict('Local changes conflict with the migration; no files changed.\n'
                                  + '\n'.join('CONFLICT ' + name for name in conflicts), '\n\n'.join(details))


def prepare(target, *, reference=REFERENCE):
    # Already migrated runtimes need a base that includes their existing catalogs.
    # Older runtimes still use the complete migration and its reply-preserving merge.
    marker = target / 'i18n/__init__.py'
    upgrade = reference / 'download_errors_vps_manifest.json'
    use_upgrade = marker.is_file() and upgrade.is_file()
    bundle = 'download_errors_vps' if use_upgrade else 'i18n_vps'
    manifest = json.loads((reference / (bundle + '_manifest.json')).read_text())
    patch = (reference / (bundle + '.patch')).resolve()
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
            merge_runtime(target, staging, manifest, patch, reference)
        else:
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
    parser.add_argument('--diagnose', action='store_true', help='Show conflicting code blocks if staging fails.')
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
        if args.diagnose and isinstance(exc, RuntimeConflict) and exc.details:
            print(exc.details, flush=True)
        raise SystemExit(str(exc)) from exc


if __name__ == '__main__':
    main()
