"""Upgrade the published collection runtime without replacing VPS customizations."""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "rate_limit_installer_base", ROOT / "scripts/install_i18n_vps.py"
)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.fixture
def baseline(tmp_path):
    manifest = json.loads((ROOT / "scripts/telegram_rate_limit_vps_manifest.json").read_text())
    for name in manifest["files"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, path)
    subprocess.run(
        ["git", "apply", "--reverse", str(ROOT / "scripts/telegram_rate_limit_vps.patch")],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    # Unchanged catalogs are required for validation of this incremental upgrade.
    for name in ["i18n/__init__.py", *[f"i18n/locales/{lang}.json" for lang in ("pt", "en", "es", "de")]]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, path)
    (tmp_path / ".env").write_text("UNCHANGED=1\n")
    return tmp_path


def test_rate_limit_upgrade_preserves_replies_and_is_idempotent(baseline):
    path = baseline / "handlers/youtube.py"
    path.write_text(path.read_text().replace("message.answer(", "message.reply(") + "\n# VPS LOCAL CUSTOMIZATION\n")
    command = [
        sys.executable,
        str(ROOT / "scripts/install_telegram_rate_limit_vps.py"),
        "--target",
        str(baseline),
        "--apply",
        "--diagnose",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "# VPS LOCAL CUSTOMIZATION" in path.read_text()
    german = json.loads((baseline / "i18n/locales/de.json").read_text())
    assert len(german) == len(
        json.loads((baseline / "i18n/locales/en.json").read_text())
    )
    assert german["commands.lang"] == "Sprache ändern"
    assert (baseline / "services/runtime/telegram_rate_limit.py").is_file()
    assert "message.reply(" in path.read_text()
    assert "session.middleware(TelegramRateLimitMiddleware())" in (baseline / "main.py").read_text()
    assert "'de'" in (baseline / "i18n/__init__.py").read_text()
    assert (baseline / ".env").read_text() == "UNCHANGED=1\n"
    again = subprocess.run(command, capture_output=True, text=True, check=False)
    assert again.returncode == 0 and "Already installed" in again.stdout



def add_vps_deleted_reply_fallback(target, *, arguments=''):
    path = target / 'main.py'
    text = path.read_text()
    anchor = 'from services.runtime.analytics_status import record_drop as record_analytics_drop\n'
    text = text.replace(anchor, anchor + 'from services.runtime.telegram_deleted_reply import DeletedReplyFallbackMiddleware\n')
    anchor = '        timeout=session_timeout,\n    )\n'
    text = text.replace(anchor, anchor + f'    session.middleware(DeletedReplyFallbackMiddleware({arguments}))\n')
    path.write_text(text)
    fallback = target / 'services/runtime/telegram_deleted_reply.py'
    fallback.parent.mkdir(parents=True, exist_ok=True)
    fallback.write_text('# EXISTING VPS FALLBACK: keep unchanged\n')
    return path, fallback


def test_rate_limit_upgrade_preserves_deleted_reply_fallback_and_wrap_order(baseline):
    path, fallback = add_vps_deleted_reply_fallback(baseline)
    original = path.read_bytes()
    unchanged = fallback.read_bytes()
    changes = installer.prepare(baseline, reference=ROOT / 'scripts', bundle='telegram_rate_limit_vps')
    source = changes['main.py'].decode()
    assert source.count('import DeletedReplyFallbackMiddleware') == 1
    assert source.count('session.middleware(DeletedReplyFallbackMiddleware())') == 1
    assert source.count('session.middleware(TelegramRateLimitMiddleware())') == 1
    assert source.index('session.middleware(DeletedReplyFallbackMiddleware())') < source.index('session.middleware(TelegramRateLimitMiddleware())')
    assert path.read_bytes() == original  # preparation must not modify production
    backup = installer.install(baseline, changes)
    assert (backup / 'main.py').read_bytes() == original
    assert fallback.read_bytes() == unchanged
    assert installer.prepare(baseline, reference=ROOT / 'scripts', bundle='telegram_rate_limit_vps') == {}


def test_unknown_deleted_reply_fallback_arguments_still_require_conflict_resolution(baseline):
    path, fallback = add_vps_deleted_reply_fallback(baseline, arguments='enabled=False')
    original = path.read_bytes()
    with pytest.raises(installer.RuntimeConflict, match='CONFLICT main.py'):
        installer.prepare(baseline, reference=ROOT / 'scripts', bundle='telegram_rate_limit_vps')
    assert path.read_bytes() == original
    assert fallback.read_text() == '# EXISTING VPS FALLBACK: keep unchanged\n'
