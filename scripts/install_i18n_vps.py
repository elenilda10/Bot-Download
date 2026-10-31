"""Stage and validate the complete interface language migration; --apply saves backups.

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


def preserve_notice_reply_parameters(base_source, incoming_source, local_source):
    """Carry the observed source-message reply field into the HTML notice payload.

    Only the exact reply-to-current-message addition is accepted. Other payload
    changes remain ordinary three-way conflicts instead of being overwritten.
    """
    def payload(source):
        tree = ast.parse(source)
        function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                        and node.name == '_send_with_reaction')
        matches = [node.value for node in ast.walk(function) if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == 'payload' for target in node.targets)
                   and isinstance(node.value, ast.Dict)]
        if len(matches) != 1:
            raise ValueError('Ambiguous notice payload')
        node = matches[0]
        if any(not isinstance(key, ast.Constant) or not isinstance(key.value, str) for key in node.keys):
            raise ValueError('Dynamic notice payload')
        fields = {key.value: value for key, value in zip(node.keys, node.values)}
        if len(fields) != len(node.keys):
            raise ValueError('Duplicate notice payload field')
        return node, fields

    try:
        old, new, local = [payload(source) for source in (base_source, incoming_source, local_source)]
        required = {'chat_id', 'text', 'receiver_user_id'}
        if set(old[1]) != required or set(new[1]) != required | {'parse_mode'}:
            return base_source, incoming_source
        if set(local[1]) not in (required | {'reply_parameters'}, required | {'parse_mode', 'reply_parameters'}):
            return base_source, incoming_source
        for key in required:
            if not ast.dump(old[1][key]) == ast.dump(new[1][key]) == ast.dump(local[1][key]):
                return base_source, incoming_source
        reply = ast.parse('{"message_id": message.message_id}', mode='eval').body
        parse_mode = ast.parse('kwargs.get("parse_mode", "HTML")', mode='eval').body
        if ast.dump(local[1]['reply_parameters']) != ast.dump(reply):
            return base_source, incoming_source
        if ast.dump(new[1]['parse_mode']) != ast.dump(parse_mode):
            return base_source, incoming_source
        if 'parse_mode' in local[1] and ast.dump(local[1]['parse_mode']) != ast.dump(parse_mode):
            return base_source, incoming_source
        index = [key.value for key in local[0].keys].index('reply_parameters')
        key = local[0].keys[index]
        value = local[1]['reply_parameters']
        lines = local_source.splitlines(keepends=True)
        field = b''.join(lines[key.lineno - 1:value.end_lineno])
        # Require a standalone field, avoiding adjacent same-line dictionary data.
        if lines[key.lineno - 1][:key.col_offset].strip():
            return base_source, incoming_source
        ending = lines[value.end_lineno - 1][value.end_col_offset:].strip()
        if ending not in (b'', b','):
            return base_source, incoming_source
        field = field.rstrip().rstrip(b',') + b',\n'
        results = []
        for source, item in ((base_source, old), (incoming_source, new)):
            parts = source.splitlines(keepends=True)
            if parts[item[0].end_lineno - 1][:item[0].end_col_offset - 1].strip():
                return base_source, incoming_source
            parts.insert(item[0].end_lineno - 1, field)
            results.append(b''.join(parts))
        return tuple(results)
    except (SyntaxError, UnicodeDecodeError, StopIteration, ValueError):
        return base_source, incoming_source


def preserve_replaced_youtube_notice(base_source, incoming_source, local_source):
    """Accept only the observed reply variant of the notice replaced by diagnostics."""
    import copy

    old_call = ast.parse('message.answer(bm.nothing_found(lang=user_lang), allow_sending_without_reply=True)', mode='eval').body
    reply_call = ast.parse('message.reply(bm.nothing_found(lang=user_lang), allow_sending_without_reply=True)', mode='eval').body
    new_call = ast.parse("handle_download_error(message, business_id=business_id, lang=user_lang, platform='YouTube')", mode='eval').body
    condition = ast.dump(ast.parse('not yt', mode='eval').body)

    def blocks(source):
        tree = ast.parse(source)
        found = {}
        for function in tree.body:
            if isinstance(function, ast.AsyncFunctionDef) and function.name in {'download_video', 'download_music'}:
                matches = [node for node in ast.walk(function)
                           if isinstance(node, ast.If) and ast.dump(node.test) == condition]
                if len(matches) == 1:
                    found[function.name] = matches[0]
        return found

    class Replace(ast.NodeTransformer):
        def __init__(self, expected, replacement):
            self.expected, self.replacement = ast.dump(expected), replacement
            self.matches = 0

        def visit_Call(self, node):
            if ast.dump(node) == self.expected:
                self.matches += 1
                return copy.deepcopy(self.replacement)
            return self.generic_visit(node)

    try:
        old, new, local = [blocks(source) for source in (base_source, incoming_source, local_source)]
        edits = []
        for name, block in old.items():
            if name not in new or name not in local:
                continue
            normalize_local = Replace(reply_call, old_call)
            local_block = normalize_local.visit(copy.deepcopy(local[name]))
            if normalize_local.matches != 1 or ast.dump(local_block) != ast.dump(block):
                continue
            normalize_incoming = Replace(old_call, new_call)
            expected = normalize_incoming.visit(copy.deepcopy(block))
            if normalize_incoming.matches != 1 or ast.dump(expected) != ast.dump(new[name]):
                continue
            call = next(node for node in ast.walk(block)
                        if isinstance(node, ast.Call) and ast.dump(node) == ast.dump(old_call))
            lines = base_source.splitlines(keepends=True)
            end = sum(map(len, lines[:call.func.end_lineno - 1])) + call.func.end_col_offset
            edits.append((end - len('answer'), end))
        for start, end in sorted(edits, reverse=True):
            base_source = base_source[:start] + b'reply' + base_source[end:]
        return base_source, incoming_source
    except (SyntaxError, UnicodeDecodeError, StopIteration):
        return base_source, incoming_source


def preserve_deleted_reply_middleware(base_source, incoming_source, local_source):
    """Carry only the observed, argument-free VPS fallback registration forward.

    It must wrap the rate limiter: a fallback resend must pass through flood
    control too. Unknown aliases, arguments, duplicate or moved registrations
    are left to the normal conflict diagnostic rather than guessed.
    """
    import_text = (
        'from services.runtime.telegram_deleted_reply import DeletedReplyFallbackMiddleware\n'
    )
    registration = '    session.middleware(DeletedReplyFallbackMiddleware())\n'
    try:
        sources = [base_source, incoming_source, local_source]
        trees = [ast.parse(source) for source in sources]
        wanted_import = ast.parse(import_text).body[0]
        wanted_call = ast.parse(registration.strip()).body[0]
        matches = [node for node in trees[2].body
                   if ast.dump(node) == ast.dump(wanted_import)]
        if len(matches) != 1:
            return base_source, incoming_source
        # No competing alias, constructor use or additional fallback import.
        for index, tree in enumerate(trees):
            names = [node for node in ast.walk(tree)
                     if isinstance(node, ast.Name) and node.id == 'DeletedReplyFallbackMiddleware']
            imports = [alias for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                       for alias in node.names if alias.name == 'DeletedReplyFallbackMiddleware']
            if len(names) != (1 if index == 2 else 0) or len(imports) != (1 if index == 2 else 0):
                return base_source, incoming_source
        functions = [next(node for node in tree.body
                          if isinstance(node, ast.FunctionDef) and node.name == 'create_app')
                     for tree in trees]
        sessions = []
        for function in functions:
            candidates = [node for node in function.body if isinstance(node, ast.Assign)
                          and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                          and node.targets[0].id == 'session'
                          and isinstance(node.value, ast.Call)
                          and isinstance(node.value.func, ast.Name)
                          and node.value.func.id == 'AiohttpSession']
            if len(candidates) != 1:
                return base_source, incoming_source
            sessions.append(candidates[0])
        if len({ast.dump(node) for node in sessions}) != 1:
            return base_source, incoming_source
        local_body = functions[2].body
        following = local_body[local_body.index(sessions[2]) + 1]
        if ast.dump(following) != ast.dump(wanted_call):
            return base_source, incoming_source
        local_top = trees[2].body
        preceding = local_top[:local_top.index(matches[0])]
        # Insert the same import after a verified common import anchor.
        anchor = next(node for node in reversed(preceding)
                      if isinstance(node, (ast.Import, ast.ImportFrom)) and all(
                          any(ast.dump(candidate) == ast.dump(node) for candidate in tree.body)
                          for tree in trees[:2]))
        result = []
        for source, tree, session in zip(sources[:2], trees[:2], sessions[:2]):
            common = next(node for node in tree.body if ast.dump(node) == ast.dump(anchor))
            lines = source.splitlines(keepends=True)
            edits = [(session.end_lineno, registration.encode()),
                     (common.end_lineno, import_text.encode())]
            for line, text in sorted(edits, reverse=True):
                lines.insert(line, text)
            result.append(b''.join(lines))
        return tuple(result)
    except (SyntaxError, UnicodeDecodeError, StopIteration, IndexError):
        return base_source, incoming_source


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
                    if name == 'main.py':
                        compatible_base, compatible_incoming = preserve_deleted_reply_middleware(
                            compatible_base, compatible_incoming, local)
                    if name == 'handlers/youtube.py':
                        compatible_base, compatible_incoming = preserve_replaced_youtube_notice(
                            compatible_base, compatible_incoming, local)
                    if name == 'handlers/telegram_ui_utils.py':
                        compatible_base, compatible_incoming = preserve_notice_reply_parameters(
                            compatible_base, compatible_incoming, local)
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


def prepare(target, *, reference=REFERENCE, bundle=None):
    if bundle is None:
        # Already migrated runtimes need a base that includes their existing catalogs.
        # Older runtimes still use the complete migration and its reply-preserving merge.
        marker = target / 'i18n/__init__.py'
        upgrade = reference / 'download_errors_vps_manifest.json'
        use_upgrade = marker.is_file() and upgrade.is_file()
        bundle = 'download_errors_vps' if use_upgrade else 'i18n_vps'
        if (target / 'utils/download_errors.py').is_file() and (reference / 'download_errors_json_vps_manifest.json').is_file():
            bundle = 'download_errors_json_vps'
        diagnostic_module = target / 'utils/download_errors.py'
        # The published JSON runtime and this display-only upgrade share the same
        # diagnostics module. Older runtimes still receive the complete upgrade.
        if (diagnostic_module.is_file()
                and (reference / 'download_errors_raw_vps_manifest.json').is_file()
                and diagnostic_module.read_bytes() == (reference.parent / 'utils/download_errors.py').read_bytes()):
            bundle = 'download_errors_raw_vps'
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
        locale_source = values.get('i18n/__init__.py')
        if locale_source is None:
            locale_source = (target / 'i18n/__init__.py').read_bytes()
        declaration = next((node for node in ast.parse(locale_source).body
                            if isinstance(node, ast.Assign) and any(
                                isinstance(item, ast.Name) and item.id == 'SUPPORTED_LOCALES'
                                for item in node.targets)), None)
        locales = ast.literal_eval(declaration.value) if declaration is not None else ()
        if (not isinstance(locales, (tuple, list)) or 'en' not in locales
                or any(not isinstance(code, str) or not code.isascii() or not code.isalpha()
                       or not code.islower() or len(code) not in (2, 3) for code in locales)
                or len(locales) != len(set(locales))):
            raise ValueError('Invalid supported locale list.')
        catalogs = {}
        for lang in locales:
            name = 'i18n/locales/' + lang + '.json'
            catalogs[lang] = json.loads(values[name] if name in values else (target / name).read_bytes())
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
            print('Interface language migration is already installed.')
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
