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
    "audible_installer_base", ROOT / "scripts/install_i18n_vps.py"
)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.fixture
def baseline(tmp_path):
    manifest = json.loads((ROOT / "scripts/audible_vps_manifest.json").read_text())
    for name in manifest["files"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, path)
    subprocess.run(
        ["git", "apply", "--reverse", str(ROOT / "scripts/audible_vps.patch")],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    path = tmp_path / "i18n/__init__.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "i18n/__init__.py", path)
    # Existing private sessions are outside the source bundle and must survive.
    private = tmp_path / "private-audible-session"
    private.mkdir()
    (private / "AccountsSettings.json").write_text("private test data")
    (tmp_path / ".env").write_text("UNCHANGED=1\n")
    return tmp_path


def test_audible_install_preserves_changes_and_is_idempotent(baseline):
    path = baseline / "handlers/media_download.py"
    path.write_text(path.read_text() + "\n# VPS LOCAL CUSTOMIZATION\n")
    command = [
        sys.executable,
        str(ROOT / "scripts/install_audible_vps.py"),
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
    assert "audible.explain" in german
    assert (baseline / "handlers/audible.py").is_file()
    assert (baseline / "services/platforms/audible_service.py").is_file()
    assert "'de'" in (baseline / "i18n/__init__.py").read_text()
    assert (baseline / ".env").read_text() == "UNCHANGED=1\n"
    assert (
        baseline / "private-audible-session/AccountsSettings.json"
    ).read_text() == "private test data"
    again = subprocess.run(command, capture_output=True, text=True, check=False)
    assert again.returncode == 0 and "Already installed" in again.stdout
