"""Adapt platform transfer statuses selectively; dry run unless --apply is set."""
import argparse
import ast
import shutil
from datetime import datetime, timezone
from pathlib import Path

PLATFORMS = {
    'instagram': 'Instagram', 'facebook': 'Facebook', 'tiktok': 'TikTok',
    'kwai': 'Kwai', 'youtube': 'YouTube', 'twitter': 'X/Twitter',
    'threads': 'Threads', 'reddit': 'Reddit', 'pinterest': 'Pinterest',
    'spotify': 'Spotify', 'deezer': 'Deezer', 'soundcloud': 'SoundCloud',
    'mediafire': 'MediaFire', 'google_drive': 'Google Drive', 'mega': 'MEGA',
    'pornhub': 'Pornhub', 'xvideos': 'Xvideos', 'eporner': 'Eporner',
    'universal': 'Link',
}
STATUS_NAMES = {'downloading_video_status', 'downloading_audio_status',
                'downloading_file_status', 'uploading_status', 'uploading_file_status'}
HELPER = '''def media_transfer_status(platform: str, *, uploading: bool = False, lang: str = DEFAULT_LANG):
    emoji = (
        '<tg-emoji emoji-id="5866355487255039002">🚀</tg-emoji>' if uploading
        else '<tg-emoji emoji-id="5435977112538324435">⬇️</tg-emoji>'
    )
    platform = "X/Twitter" if platform in {"Twitter", "X / Twitter", "X"} else platform
    label = html.escape(platform)
    if _normalize_lang(lang) == "pt":
        action = "Enviando para o Telegram…" if uploading else "Baixando conteúdo…"
    else:
        action = "Sending to Telegram…" if uploading else "Downloading content…"
    return f"{emoji} {label}: {action}"
'''


def patch_messages(source):
    edits = []
    for node in ast.parse(source).body:
        if not isinstance(node, ast.FunctionDef) or node.name not in STATUS_NAMES:
            continue
        if any(arg.arg == 'platform' for arg in node.args.kwonlyargs):
            continue
        lines = source.splitlines(keepends=True)
        signature = ''.join(lines[node.lineno - 1:node.body[0].lineno - 1])
        marker = signature.rfind('):')
        if marker < 0:
            raise ValueError('Unrecognized status signature: ' + node.name)
        addition = ', platform: str | None = None' if node.args.kwonlyargs else ', *, platform: str | None = None'
        signature = signature[:marker] + addition + signature[marker:]
        body = (f'    if platform is not None:\n'
                f'        return media_transfer_status(platform, uploading={node.name.startswith("uploading")}, lang=lang)\n')
        edits.append((node.lineno - 1, node.body[0].lineno - 1, signature + body))
    lines = source.splitlines(keepends=True)
    for start, end, replacement in sorted(edits, reverse=True):
        lines[start:end] = [replacement]
    source = ''.join(lines)
    helper = next((n for n in ast.parse(source).body
                   if isinstance(n, ast.FunctionDef) and n.name == 'media_transfer_status'), None)
    if helper is None:
        source += '\n\n' + HELPER
    else:
        lines = source.splitlines(keepends=True)
        lines[helper.lineno - 1:helper.end_lineno] = [HELPER]
        source = ''.join(lines)
    return source


def patch_calls(source, platform_expression):
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    edits = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == 'bm'
                and node.func.attr in STATUS_NAMES
                and not any(k.arg == 'platform' for k in node.keywords)):
            # AST columns count UTF-8 bytes, whereas string offsets count characters.
            column = len(lines[node.end_lineno - 1].encode()[:node.end_col_offset].decode())
            end = offsets[node.end_lineno - 1] + column - 1
            prefix = source[offsets[node.lineno - 1]:end].rstrip()
            separator = ' ' if prefix.endswith(',') else ', ' if node.args or node.keywords else ''
            edits.append((end, separator + 'platform=' + platform_expression))
    for position, addition in sorted(edits, reverse=True):
        source = source[:position] + addition + source[position:]
    return source


def prepare(target):
    changes = {}
    name = 'messages/user_messages.py'
    original = (target / name).read_text()
    revised = patch_messages(original)
    if revised != original:
        changes[name] = revised
    for path in sorted((target / 'handlers').glob('*.py')):
        key = path.stem.removesuffix('_inline').removesuffix('_actions')
        if key not in PLATFORMS:
            continue
        original = path.read_text()
        revised = patch_calls(original, repr(PLATFORMS[key]))
        if key == 'twitter' and path.stem == 'twitter' and 'bm.uploading_status(' not in revised:
            anchor = '        caption = bm.captions(\n'
            if revised.count(anchor) != 1:
                raise ValueError('Cannot locate Twitter upload transition safely')
            revised = revised.replace(anchor,
                '        await safe_edit_text(status_message, bm.uploading_status(lang=user_lang, platform="X/Twitter"))\n\n' + anchor, 1)
        if revised != original:
            changes[path.relative_to(target).as_posix()] = revised
    path = target / 'services/inline/send_flow.py'
    if path.exists():
        original = path.read_text()
        revised = patch_calls(original, 'service_name')
        if revised != original:
            changes[path.relative_to(target).as_posix()] = revised
    name = 'handlers/telegram_ui_utils.py'
    path = target / name
    if path.exists():
        original = path.read_text()
        revised = original.replace(
            '    speed = _format_bytes(int(progress.speed_bps)) + "/s"',
            '    platform = label.rsplit(" ", 1)[0] if label.rsplit(" ", 1)[-1] in {"video", "audio", "photo"} else label\n'
            '    header = bm.media_transfer_status(platform)\n'
            '    speed = _format_bytes(int(progress.speed_bps)) + "/s"', 1
        ) if 'header = bm.media_transfer_status(platform)' not in original else original
        revised = revised.replace('f"⬇️ {label}\\n"', 'f"{header}\\n"')
        revised = revised.replace('f"⬇️ {label}\\n{downloaded}', 'f"{header}\\n{downloaded}')
        if revised != original:
            changes[name] = revised
    name = 'handlers/tiktok.py'
    if (target / name).exists():
        original = changes.get(name, (target / name).read_text())
        revised = original.replace(
            'text = f"⏳ <b>{label}:</b> {percent}% ({current_mb:.1f}MB / {total_mb:.1f}MB)"',
            'text = bm.media_transfer_status("TikTok") + f" {percent}% ({current_mb:.1f}MB / {total_mb:.1f}MB)"'
        )
        if revised != original:
            changes[name] = revised
    for name, source in changes.items():
        compile(source, name, 'exec')
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    target = args.target.resolve()
    changes = prepare(target)
    for name in changes:
        print('UPDATE', name)
    if not args.apply:
        print('DRY RUN: no files modified. Use --apply to install.')
        return
    backup = target / 'backups' / ('platform-status-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f'))
    for name in changes:
        destination = backup / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target / name, destination)
    for name, source in changes.items():
        (target / name).write_text(source)
    print('Installed with backups. Service was not restarted.')


if __name__ == '__main__':
    main()
