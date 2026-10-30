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
