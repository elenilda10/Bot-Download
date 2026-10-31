"""Upgrade the published VPS baseline with compatible local reply changes."""

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "collections_installer_base", ROOT / "scripts/install_i18n_vps.py"
)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.fixture
def baseline(tmp_path):
    manifest = json.loads((ROOT / "scripts/collections_vps_manifest.json").read_text())
    for name in manifest["files"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, path)
    subprocess.run(
        ["git", "apply", "--reverse", str(ROOT / "scripts/collections_vps.patch")],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / ".env").write_text("UNCHANGED=1\n")
    return tmp_path


def test_collection_upgrade_preserves_local_replies_and_is_idempotent(baseline):
    for name in ["handlers/youtube.py", "handlers/spotify.py"]:
        path = baseline / name
        path.write_text(
            path.read_text()
            .replace(".answer(", ".reply(")
            .replace(".answer_audio(", ".reply_audio(")
        )
    changes = installer.prepare(baseline, bundle="collections_vps")
    assert b"collection_item" in changes["handlers/youtube.py"]
    assert b".reply_audio(" in changes["handlers/spotify.py"]
    assert b"collection:cancel:" in changes["handlers/collections.py"]
    backup = installer.install(baseline, changes)
    assert (backup / "handlers/youtube.py").is_file()
    assert (baseline / ".env").read_text() == "UNCHANGED=1\n"
    assert installer.prepare(baseline, bundle="collections_vps") == {}


def test_collection_conflict_leaves_every_runtime_file_untouched(baseline):
    path = baseline / "handlers/spotify.py"
    path.write_text("incompatible runtime\n")
    before = {
        str(p.relative_to(baseline)): p.read_bytes()
        for p in baseline.rglob("*")
        if p.is_file()
    }
    with pytest.raises(ValueError, match="conflict"):
        installer.prepare(baseline, bundle="collections_vps")
    after = {
        str(p.relative_to(baseline)): p.read_bytes()
        for p in baseline.rglob("*")
        if p.is_file()
    }
    assert before == after


def test_cli_upgrades_published_collections_to_album_audio(tmp_path):
    import sys
    manifest=json.loads((ROOT/'scripts/collections_vps_manifest.json').read_text())
    for name in manifest['files']:
        path=tmp_path/name
        path.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT/name,path)
    subprocess.run(['git','apply','--reverse',str(ROOT/'scripts/collection_album_audio_vps.patch')],
                   cwd=tmp_path,check=True,capture_output=True)
    youtube=tmp_path/'handlers/youtube.py'
    youtube.write_text(youtube.read_text().replace('.answer(','.reply('))
    result=subprocess.run([sys.executable,str(ROOT/'scripts/install_collections_vps.py'),
                           '--target',str(tmp_path),'--apply','--diagnose'],capture_output=True,text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'audio=youtube_collection_prefers_audio(collection_url)' in youtube.read_text()
    assert '.reply(' in youtube.read_text()
    again=subprocess.run([sys.executable,str(ROOT/'scripts/install_collections_vps.py'),
                           '--target',str(tmp_path),'--apply'],capture_output=True,text=True)
    assert again.returncode == 0 and 'Already installed' in again.stdout
