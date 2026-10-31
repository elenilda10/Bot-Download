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

