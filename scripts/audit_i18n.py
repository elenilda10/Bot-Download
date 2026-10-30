"""Inventory tracked runtime Python strings and locale routing without importing the bot.

Run: python scripts/audit_i18n.py --json /tmp/bot-i18n.json
Counts describe source occurrences, not translation keys or proven reachable UI.
"""
import argparse
import ast
from collections import Counter
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
UI_METHODS = {'answer', 'reply', 'edit_text', 'edit_caption', 'send_message',
              'send_photo', 'send_video', 'send_audio', 'send_document',
              'answer_photo', 'answer_video', 'answer_audio', 'answer_document',
              'safe_edit_text', 'safe_edit_inline_text', 'edit_status', '_edit_status'}
UI_KEYS = {'text', 'caption', 'title', 'description', 'label', 'message_text'}


def scan(root):
    names = subprocess.check_output(['git', 'ls-files', '*.py'], cwd=root, text=True).splitlines()
    names = [n for n in names if not n.startswith(('tests/', 'scripts/')) and not Path(n).name.startswith('test_')]
    records, selectors, normalizers, duplicates, errors = [], [], [], [], []
    functions = Counter()
    localized = set()
    for catalog in ('messages/user_messages.py', 'messages/admin_messages.py'):
        for node in ast.parse((root / catalog).read_text()).body:
            if isinstance(node, ast.FunctionDef) and any(a.arg == 'lang' for a in node.args.args + node.args.kwonlyargs):
                localized.add(node.name)
    missing_locale = []
    for name in names:
        source = (root / name).read_text()
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            errors.append({'file': name, 'line': exc.lineno})
            continue
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        definitions = Counter(n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
        duplicates.extend({'file': name, 'function': n, 'count': c} for n, c in definitions.items() if c > 1)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == 'bm'
                    and node.func.attr in localized and not node.args
                    and not any(k.arg == 'lang' or k.arg is None for k in node.keywords)):
                missing_locale.append({'file': name, 'line': node.lineno, 'function': node.func.attr})
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if name.startswith('messages/') and node.name != '_normalize_lang':
                    functions[name] += 1
                if node.name in {'_normalize_lang', '_is_pt'}:
                    normalizers.append({'file': name, 'line': node.lineno, 'function': node.name})
            if isinstance(node, (ast.If, ast.IfExp, ast.Compare)):
                test = ast.unparse(node.test if isinstance(node, (ast.If, ast.IfExp)) else node)
                if re.search(r'\b(lang|language|user_lang|new_lang|chosen_lang|language_code|current_lang|saved_lang|telegram_lang)\b', test) and re.search(r"['\"](?:pt|en)['\"]", test):
                    if not isinstance(parents.get(node), (ast.If, ast.IfExp)):
                        selectors.append({'file': name, 'line': node.lineno, 'expression': test})
            if not isinstance(node, ast.JoinedStr) and not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            chain = []
            parent = parents.get(node)
            while parent is not None:
                chain.append(parent)
                parent = parents.get(parent)
            if any(isinstance(p, ast.JoinedStr) for p in chain):
                continue
            if isinstance(parents.get(node), ast.Expr) and any(
                    isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module))
                    and p.body and p.body[0] is parents.get(node) for p in chain):
                continue
            text = ast.unparse(node) if isinstance(node, ast.JoinedStr) else node.value
            if not text.strip():
                continue
            context = next((p.name for p in chain if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef))), '<module>')
            category = 'technical_or_data'
            calls = [p for p in chain if isinstance(p, ast.Call)]
            direct = next((c for c in calls if isinstance(c.func, ast.Attribute) and c.func.attr in UI_METHODS
                           or isinstance(c.func, ast.Name) and c.func.id in UI_METHODS), None)
            diagnostic = any(isinstance(c.func, ast.Attribute) and c.func.attr in {'debug', 'info', 'warning', 'error', 'exception', 'critical', 'bind'} for c in calls)
            prose = bool(re.search(r'[A-Za-zÀ-ÿ]{2,}\s+[A-Za-zÀ-ÿ]{2,}', text) or re.search(r'[À-ÿ]', text))
            if diagnostic or any(isinstance(p, ast.Raise) for p in chain):
                category = 'diagnostic'
            elif name.startswith('messages/') and (prose or re.search(r'[A-Za-zÀ-ÿ]{3,}', text)):
                category = 'central_messages'
            elif name.startswith('keyboards/') and (prose or re.search(r'[A-ZÀ-Ý][a-zà-ÿ]{2,}', text)):
                category = 'keyboard_candidates'
            elif direct is not None and (prose or any(isinstance(p, ast.keyword) and p.arg in UI_KEYS for p in chain)):
                category = 'direct_ui_candidates'
            elif prose:
                category = 'other_prose_review'
            records.append({'file': name, 'line': node.lineno, 'function': context,
                            'category': category, 'template': text})
    counts = Counter(r['category'] for r in records)
    review = [r for r in records if r['category'] in {'central_messages', 'keyboard_candidates', 'direct_ui_candidates', 'other_prose_review'}]
    return {'revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
            'runtime_files': len(names), 'parse_errors': errors, 'occurrences': len(records),
            'counts': dict(counts), 'review_files': len({r['file'] for r in review}),
            'message_functions': dict(functions), 'message_calls_without_locale': missing_locale, 'locale_helpers': normalizers,
            'locale_conditions': selectors, 'duplicate_top_level_functions': duplicates,
            'top_review_files': Counter(r['file'] for r in review).most_common(), 'records': records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    result = scan(ROOT)
    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in {'records', 'locale_conditions'}}, ensure_ascii=False, indent=2))
    print('Locale conditions:', len(result['locale_conditions']))
    if result['parse_errors']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
