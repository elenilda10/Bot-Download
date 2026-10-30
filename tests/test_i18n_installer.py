"""Exercise the installer on the previous runtime, without a live bot or secrets."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('i18n_installer', ROOT / 'scripts/install_i18n_vps.py')
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


@pytest.fixture
def baseline(tmp_path):
    manifest = json.loads((ROOT / 'scripts/i18n_vps_manifest.json').read_text())
    for name in manifest['files']:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, path)
    subprocess.run(['git', 'apply', '--reverse', str(ROOT / 'scripts/i18n_vps.patch')], cwd=tmp_path, check=True, capture_output=True)
    for name, info in manifest['files'].items():
        if info['base_sha256'] is None:
            assert not (tmp_path / name).exists()
        else:
            assert installer.digest((tmp_path / name).read_bytes()) == info['base_sha256']
    (tmp_path / '.env').write_text('UNCHANGED=1\n')
    return tmp_path


def test_patch_matches_current_sources_and_installs_idempotently_with_backups(baseline):
    before = (baseline / 'messages/user_messages.py').read_bytes()
    changes = installer.prepare(baseline)
    assert changes['i18n/locales/es.json'] == (ROOT / 'i18n/locales/es.json').read_bytes()
    backup = installer.install(baseline, changes)
    assert (backup / 'messages/user_messages.py').read_bytes() == before
    assert (baseline / '.env').read_text() == 'UNCHANGED=1\n'
    assert installer.prepare(baseline) == {}


def test_unrelated_local_handler_customizations_are_preserved(baseline):
    path = baseline / 'handlers/music.py'
    path.write_text(path.read_text() + '\n# LOCAL CUSTOMIZATION\n')
    changes = installer.prepare(baseline)
    assert b'# LOCAL CUSTOMIZATION' in changes['handlers/music.py']
    installer.install(baseline, changes)
    assert (baseline / '.env').read_text() == 'UNCHANGED=1\n'


def test_conflicting_runtime_fails_before_modifying_target(baseline):
    path = baseline / 'messages/user_messages.py'
    path.write_text('different runtime\n')
    with pytest.raises(ValueError, match='conflict'):
        installer.prepare(baseline)
    assert path.read_text() == 'different runtime\n'
    assert not (baseline / 'i18n/locales/es.json').exists()
    assert not (baseline / 'backups').exists()


def test_failed_write_restores_original_files(baseline, monkeypatch):
    before = {str(path.relative_to(baseline)): path.read_bytes() for path in baseline.rglob('*') if path.is_file()}
    changes = installer.prepare(baseline)
    write = installer.atomic_write
    count = 0
    def fail_once(path, content):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError('disk error')
        write(path, content)
    monkeypatch.setattr(installer, 'atomic_write', fail_once)
    with pytest.raises(OSError, match='disk error'):
        installer.install(baseline, changes)
    for name, data in before.items():
        assert (baseline / name).read_bytes() == data


def test_three_way_merge_preserves_local_edits_that_only_break_patch_context(baseline):
    path = baseline / 'handlers/music.py'
    source = path.read_text()
    anchor = '                titulo, artista, musica.get("url"), is_pt\n            )'
    assert anchor in source
    path.write_text(source.replace(anchor, anchor + '  # VPS CUSTOMIZATION', 1))
    check = subprocess.run(['git', 'apply', '--check', str(ROOT / 'scripts/i18n_vps.patch')], cwd=baseline, capture_output=True)
    assert check.returncode != 0  # Exercise the fallback rather than an already-applicable patch.
    changes = installer.prepare(baseline)
    merged = changes['handlers/music.py'].decode()
    assert '# VPS CUSTOMIZATION' in merged
    assert "_t('handlers.music.callback_identify_music.ce0b1fc3'" in merged
    assert path.read_text().count('# VPS CUSTOMIZATION') == 1
    assert not (baseline / 'i18n/locales/es.json').exists()


def test_three_way_merge_refuses_competing_message_changes(baseline):
    path = baseline / 'handlers/music.py'
    source = path.read_text()
    assert 'Identified Music:' in source
    path.write_text(source.replace('Identified Music:', 'MY LOCAL TITLE:', 1))
    with pytest.raises(ValueError, match='CONFLICT handlers/music.py'):
        installer.prepare(baseline)
    assert 'MY LOCAL TITLE:' in path.read_text()
    assert not (baseline / 'i18n/locales/es.json').exists()


def test_diagnostics_contain_only_conflicting_blocks_without_writing_files(baseline):
    path = baseline / 'handlers/music.py'
    source = path.read_text()
    path.write_text(source.replace('Identified Music:', 'MY LOCAL TITLE:', 1) + '\n# UNRELATED LOCAL SENTINEL\n')
    with pytest.raises(installer.RuntimeConflict) as result:
        installer.prepare(baseline)
    details = result.value.details
    assert 'MY LOCAL TITLE:' in details and '<<<<<<<' in details and '|||||||' in details
    assert 'UNRELATED LOCAL SENTINEL' not in details
    assert not (baseline / 'i18n/locales/es.json').exists()


def test_observed_vps_reply_variants_keep_reply_behavior_and_receive_translations(baseline):
    import ast
    names = ['handlers/eporner.py', 'handlers/pornhub.py', 'handlers/universal.py']
    original_calls = {}
    for name in names:
        path = baseline / name
        source = path.read_text()
        source = source.replace('.answer(', '.reply(').replace('.answer_audio(', '.reply_audio(')
        path.write_text(source)
        original_calls[name] = [n.func.attr for n in ast.walk(ast.parse(source))
                                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                                and n.func.attr in {'answer', 'reply', 'answer_audio', 'reply_audio'}]
    changes = installer.prepare(baseline)
    for name in names:
        source = changes[name].decode()
        calls = [n.func.attr for n in ast.walk(ast.parse(source))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr in {'answer', 'reply', 'answer_audio', 'reply_audio'}]
        assert calls == original_calls[name]
        assert '_t(' in source
    installer.install(baseline, changes)
    assert installer.prepare(baseline) == {}


def test_reply_adapter_does_not_resolve_competing_arguments_or_extra_calls():
    old = b'async def send():\n    await message.answer("Old text")\n'
    incoming = b'async def send():\n    await message.answer(_t("key"))\n'
    for local in (b'async def send():\n    await message.reply("LOCAL TEXT")\n',
                  b'async def send():\n    await message.reply("Old text")\n    await message.reply("Added")\n'):
        assert installer.preserve_reply_methods(old, incoming, local) == (old, incoming)


def test_existing_i18n_runtime_receives_error_upgrade_with_local_replies(tmp_path):
    """Reconstruct the published i18n release without requiring git history on VPS."""
    full = json.loads((ROOT / 'scripts/i18n_vps_manifest.json').read_text())
    upgrade = json.loads((ROOT / 'scripts/download_errors_vps_manifest.json').read_text())
    for name in full['files']:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, path)
    subprocess.run(['git', 'apply', '--reverse', str(ROOT / 'scripts/download_errors_vps.patch')],
                   cwd=tmp_path, check=True, capture_output=True)
    for name, info in upgrade['files'].items():
        path = tmp_path / name
        if info['base_sha256'] is None:
            assert not path.exists()
        else:
            assert installer.digest(path.read_bytes()) == info['base_sha256']
    reply = tmp_path / 'handlers/universal.py'
    reply.write_text(reply.read_text().replace('.answer(', '.reply('))
    changes = installer.prepare(tmp_path)
    assert b'download_failure_message' in changes['handlers/telegram_ui_utils.py']
    assert b'.reply(' in changes['handlers/universal.py']
    installer.install(tmp_path, changes)
    assert installer.prepare(tmp_path) == {}


@pytest.mark.parametrize('migrated', [False, True])
def test_notice_payload_preserves_observed_local_reply_and_adds_html(baseline, migrated):
    if migrated:
        full = json.loads((ROOT / 'scripts/i18n_vps_manifest.json').read_text())
        for name in full['files']:
            path = baseline / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, path)
        subprocess.run(['git', 'apply', '--reverse', str(ROOT / 'scripts/download_errors_vps.patch')],
                       cwd=baseline, check=True, capture_output=True)
    path = baseline / 'handlers/telegram_ui_utils.py'
    source = path.read_text().replace(
        '                    "receiver_user_id": user_id,\n',
        '                    "receiver_user_id": user_id,\n'
        '                    "reply_parameters": {\n'
        '                        "message_id": message.message_id\n'
        '                    }\n', 1)
    path.write_text(source)
    changes = installer.prepare(baseline)
    result = changes['handlers/telegram_ui_utils.py'].decode()
    assert '"parse_mode": kwargs.get("parse_mode", "HTML")' in result
    assert '"reply_parameters": {' in result
    assert '"message_id": message.message_id' in result
    compile(result, str(path), 'exec')
    installer.install(baseline, changes)
    assert installer.prepare(baseline) == {}


def test_notice_adapter_refuses_competing_local_payload():
    old = b'async def _send_with_reaction():\n    payload = {\n        "chat_id": message.chat.id,\n        "text": text,\n        "receiver_user_id": user_id,\n    }\n'
    incoming = old.replace(b'"receiver_user_id": user_id,', b'"receiver_user_id": user_id,\n        "parse_mode": kwargs.get("parse_mode", "HTML"),')
    for reply in (b'{"message_id": other.message_id}', b'{"message_id": message.message_id, "allow_sending_without_reply": True}'):
        local = old.replace(b'"receiver_user_id": user_id,', b'"receiver_user_id": user_id,\n        "reply_parameters": ' + reply + b',')
        assert installer.preserve_notice_reply_parameters(old, incoming, local) == (old, incoming)


def test_existing_diagnostics_runtime_receives_json_and_keeps_local_reply(tmp_path):
    full = json.loads((ROOT / 'scripts/i18n_vps_manifest.json').read_text())
    for name in full['files']:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, path)
    subprocess.run(['git', 'apply', '--reverse', str(ROOT / 'scripts/download_errors_json_vps.patch')],
                   cwd=tmp_path, check=True, capture_output=True)
    path = tmp_path / 'handlers/telegram_ui_utils.py'
    path.write_text(path.read_text().replace('                    "receiver_user_id": user_id,\n',
        '                    "receiver_user_id": user_id,\n'
        '                    "reply_parameters": {"message_id": message.message_id},\n', 1))
    changes = installer.prepare(tmp_path)
    assert b'language-json' in changes['messages/user_messages.py']
    assert b'"reply_parameters": {"message_id": message.message_id}' in changes['handlers/telegram_ui_utils.py']
    installer.install(tmp_path, changes)
    assert installer.prepare(tmp_path) == {}


@pytest.mark.parametrize('migrated', [False, True])
def test_youtube_reply_not_found_replaced_by_json_without_losing_other_replies(baseline, migrated):
    if migrated:
        full = json.loads((ROOT / 'scripts/i18n_vps_manifest.json').read_text())
        for name in full['files']:
            path = baseline / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, path)
        subprocess.run(['git', 'apply', '--reverse', str(ROOT / 'scripts/download_errors_json_vps.patch')],
                       cwd=baseline, check=True, capture_output=True)
    path = baseline / 'handlers/youtube.py'
    before = path.read_text()
    source = before.replace('.answer(', '.reply(').replace('.answer_video(', '.reply_video(').replace('.answer_audio(', '.reply_audio(')
    path.write_text(source)
    assert source.count('message.reply(bm.nothing_found(lang=user_lang)') == 2
    changes = installer.prepare(baseline)
    result = changes['handlers/youtube.py'].decode()
    assert 'message.reply(bm.nothing_found(lang=user_lang)' not in result
    assert result.count("await handle_download_error(message, business_id=business_id, lang=user_lang, platform='YouTube')") == 2
    assert result.count('.reply(') == source.count('.reply(') - 2
    assert result.count('.reply_video(') == source.count('.reply_video(')
    assert result.count('.reply_audio(') == source.count('.reply_audio(')
    installer.install(baseline, changes)
    assert installer.prepare(baseline) == {}


def test_replaced_notice_adapter_refuses_competing_local_text():
    before = b'async def download_video():\n    if not yt:\n        await message.answer(bm.nothing_found(lang=user_lang), allow_sending_without_reply=True)\n        return\n'
    incoming = before.replace(b'message.answer(bm.nothing_found(lang=user_lang), allow_sending_without_reply=True)',
        b"handle_download_error(message, business_id=business_id, lang=user_lang, platform='YouTube')")
    local = before.replace(b'message.answer(bm.nothing_found(lang=user_lang)', b'message.reply("LOCAL TEXT"')
    assert installer.preserve_replaced_youtube_notice(before, incoming, local) == (before, incoming)
