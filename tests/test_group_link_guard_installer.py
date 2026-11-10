"""Isolated tests for the incremental VPS patcher (no bot credentials needed)."""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install_group_link_guard_vps.py"

OLD_UNIVERSAL = """
import re
from aiogram.enums import ChatAction, ParseMode
from services.links.linkedin import LINKEDIN_URL, LinkedInRedirectError

_UNIVERSAL_LINK_REGEX = r"https?://\\S+"

def handle(message, text, direct_url=None):
    request_url_match = re.search(_UNIVERSAL_LINK_REGEX, text or "")
    if not request_url_match:
        return

    url = request_url_match.group(0).strip()
    return url

# Keep this local customization.
"""
OLD_MIDDLEWARE = """
from aiogram.enums import ChatType

def handle(event):
    # Ignora completamente mensagens/publicacoes de canais
    if event.chat.type == ChatType.CHANNEL:
        return None
    return True
"""


def _make_target(tmp_path):
    target = tmp_path / "bot"
    universal = target / "handlers/universal.py"
    channel = target / "middlewares/private_chat_guard.py"
    for path in (universal, channel):
        path.parent.mkdir(parents=True, exist_ok=True)
    (target / "services/links").mkdir(parents=True, exist_ok=True)
    universal.write_text(OLD_UNIVERSAL)
    channel.write_text(OLD_MIDDLEWARE)
    return target, universal, channel


def _run(target, *options):
    return subprocess.run(
        [sys.executable, str(INSTALLER), "--target", str(target), *options],
        check=False, capture_output=True, text=True,
    )


def test_installer_dry_run_apply_backups_and_repeat(tmp_path):
    target, universal, channel = _make_target(tmp_path)
    module = target / "services/links/universal_guard.py"

    diagnosis = _run(target)
    assert diagnosis.returncode == 0, diagnosis.stderr
    assert "SIMULAÇÃO" in diagnosis.stdout
    assert universal.read_text() == OLD_UNIVERSAL
    assert channel.read_text() == OLD_MIDDLEWARE
    assert not module.exists()

    applied = _run(target, "--apply")
    assert applied.returncode == 0, applied.stderr
    assert "select_group_download_url" in universal.read_text()
    assert "# Keep this local customization." in universal.read_text()
    assert "is_automatic_forward" in channel.read_text()
    assert module.read_text() == (
        ROOT / "services/links/universal_guard.py"
    ).read_text()
    assert list(universal.parent.glob("universal.py.bak-group-links-*"))
    assert list(channel.parent.glob("private_chat_guard.py.bak-group-links-*"))

    again = _run(target, "--apply")
    assert again.returncode == 0, again.stderr
    assert "já instalada" in again.stdout


def test_installer_aborts_on_conflict_before_touching_files(tmp_path):
    target, universal, channel = _make_target(tmp_path)
    universal.write_text(OLD_UNIVERSAL.replace(
        "    request_url_match = re.search(_UNIVERSAL_LINK_REGEX, text or \"\")",
        "    request_url_match = completely_different_parser(text)",
    ))
    before = universal.read_bytes()
    result = _run(target, "--apply")
    assert result.returncode != 0
    assert universal.read_bytes() == before
    assert channel.read_text() == OLD_MIDDLEWARE
    assert not (target / "services/links/universal_guard.py").exists()
