"""Selective installer must preserve unrelated local routes and compile first."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("kwai_installer", ROOT / "scripts/install_kwai_vps.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


def test_route_patches_preserve_unrelated_local_customizations():
    source = (
        "# VPS custom routes\n"
        "from . import facebook, local_custom, universal\n"
        "routers = [\n    facebook.router,\n    local_custom.router,\n    universal.router,\n]\n"
    )
    patched = installer.patch_handler_init(source)
    assert "# VPS custom routes" in patched
    assert "    local_custom.router," in patched
    assert patched.index("    kwai.router,") < patched.index("    universal.router,")
    assert installer.patch_handler_init(patched) == patched


def test_calculate_changes_includes_firefox_and_is_read_only(tmp_path):
    for name in ("handlers/__init__.py", "handlers/media_download.py", "services/links/detection.py"):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*.py")}
    changes = installer.calculate_changes(ROOT, tmp_path)
    assert "services/platforms/kwai_firefox.py" in changes
    assert "services/platforms/kwai_service.py" in changes
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*.py")} == before
    assert "handlers/facebook.py" not in changes


def test_unknown_local_router_layout_aborts_instead_of_overwriting():
    with pytest.raises(ValueError):
        installer.patch_handler_init("# unrecognized customized layout\n")
