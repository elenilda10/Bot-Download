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
    "german_installer_base", ROOT / "scripts/install_i18n_vps.py"
)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.fixture
def baseline(tmp_path):
    manifest = json.loads((ROOT / "scripts/german_vps_manifest.json").read_text())
    for name in manifest["files"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, path)
    subprocess.run(
        ["git", "apply", "--reverse", str(ROOT / "scripts/german_vps.patch")],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / ".env").write_text("UNCHANGED=1\n")
    return tmp_path


def test_german_cli_installs_full_catalog_and_grid_idempotently(baseline):
    path = baseline / "keyboards/inline_keyboards.py"
    path.write_text(path.read_text() + "\n# VPS LOCAL CUSTOMIZATION\n")
    command = [
        sys.executable,
        str(ROOT / "scripts/install_german_vps.py"),
        "--target",
        str(baseline),
        "--apply",
        "--diagnose",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "# VPS LOCAL CUSTOMIZATION" in path.read_text()
    german = json.loads((baseline / "i18n/locales/de.json").read_text())
    assert len(german) == len(
        json.loads((baseline / "i18n/locales/en.json").read_text())
    )
    assert german["commands.settings"] == "Einstellungen"
    assert "'de'" in (baseline / "i18n/__init__.py").read_text()
    assert (baseline / ".env").read_text() == "UNCHANGED=1\n"
    again = subprocess.run(command, capture_output=True, text=True)
    assert again.returncode == 0 and "Already installed" in again.stdout


def test_invalid_german_catalog_fails_before_any_runtime_write(baseline, tmp_path):
    reference = tmp_path.parent / (tmp_path.name + "-reference")
    reference.mkdir()
    manifest = json.loads((ROOT / "scripts/german_vps_manifest.json").read_text())
    # Supply an internally consistent bundle whose German text violates placeholders.
    for name in manifest["files"]:
        path = reference / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, path)
    catalog = reference / "i18n/locales/de.json"
    values = json.loads(catalog.read_text())
    values["download_error.header"] = "{unexpected}"
    catalog.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n")
    before = {
        str(p.relative_to(baseline)): p.read_bytes()
        for p in baseline.rglob("*")
        if p.is_file()
    }
    # Keep patch integrity valid so the catalog validator rejects the bad placeholder.
    import hashlib

    patch = (
        (ROOT / "scripts/german_vps.patch")
        .read_bytes()
        .replace(
            json.dumps(
                json.loads((ROOT / "i18n/locales/de.json").read_text())[
                    "download_error.header"
                ],
                ensure_ascii=False,
            ).encode(),
            b'"{unexpected}"',
        )
    )
    scripts = reference / "scripts"
    scripts.mkdir()
    (scripts / "german_vps.patch").write_bytes(patch)
    manifest["patch_sha256"] = hashlib.sha256(patch).hexdigest()
    manifest["files"]["i18n/locales/de.json"]["result_sha256"] = hashlib.sha256(
        catalog.read_bytes()
    ).hexdigest()
    (scripts / "german_vps_manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Invalid translation"):
        installer.prepare(baseline, reference=scripts, bundle="german_vps")
    after = {
        str(p.relative_to(baseline)): p.read_bytes()
        for p in baseline.rglob("*")
        if p.is_file()
    }
    assert before == after
