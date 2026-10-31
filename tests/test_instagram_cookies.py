"""Isolated Instagram public access tests; no network or real sessions."""
import importlib.util
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(monkeypatch, tmp_path):
    config = types.ModuleType('config')
    config.COBALT_API_KEY = config.COBALT_API_URL = None
    cobalt = types.ModuleType('utils.cobalt_client')
    async def fetch(*args, **kwargs): return None
    cobalt.fetch_cobalt_data = fetch
    monkeypatch.setitem(sys.modules, 'config', config)
    monkeypatch.setitem(sys.modules, 'utils.cobalt_client', cobalt)
    spec = importlib.util.spec_from_file_location('instagram_cookie_test', ROOT / 'services/platforms/instagram_media.py')
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    profile = tmp_path / 'Firefox profile'
    profile.mkdir()
    (profile / 'cookies.sqlite').write_bytes(b'fake database; never opened by tests')
    cookie_file = tmp_path / 'cookies.txt'
    cookie_file.write_text('# Netscape HTTP Cookie File\n')
    monkeypatch.setenv('INSTAGRAM_FIREFOX_PROFILE', str(profile))
    monkeypatch.setenv('INSTAGRAM_COOKIES_FILE', str(cookie_file))
    return module, profile


@pytest.mark.parametrize('story', [False, True])
def test_gallery_ignores_accounts_and_preserves_story_flags(monkeypatch, tmp_path, story, caplog):
    module, profile = load(monkeypatch, tmp_path)
    commands = []
    def run(cmd, **kwargs):
        commands.append(cmd)
        target = Path(cmd[cmd.index('-d') + 1])
        name = '123456789_1.jpg' if story else 'ABC_1.jpg'
        (target / name).write_bytes(b'photo')
        return SimpleNamespace(returncode=1, stderr='SECRET_COOKIE_VALUE', stdout='')
    monkeypatch.setattr(module.subprocess, 'run', run)
    result = module._run_gallery_dl(
        'https://www.instagram.com/stories/user/123456789/' if story else 'https://www.instagram.com/p/ABC/',
        str(tmp_path), '123456789' if story else 'ABC', '123456789' if story else None,
    )
    cmd = commands[0]
    assert '--config-ignore' in cmd
    assert '--cookies-from-browser' not in cmd
    assert '--cookies' not in cmd
    assert all(str(profile) not in arg for arg in cmd)
    assert ('--filename' in cmd) is (not story)
    assert ('--user-agent' in cmd) is (not story)
    assert '--filter' not in cmd
    assert result.media_list
    assert 'SECRET_COOKIE_VALUE' not in caplog.text


def test_ytdlp_metadata_and_video_download_ignore_accounts(monkeypatch, tmp_path):
    module, profile = load(monkeypatch, tmp_path)
    commands = []
    def run(cmd, **kwargs):
        commands.append(cmd)
        if '--dump-single-json' in cmd:
            return SimpleNamespace(stdout=json.dumps({'formats': [{'url': 'https://cdn.test/video'}]}), stderr='', returncode=0)
        (tmp_path / 'ABC_ytdlp_1.mp4').write_bytes(b'video')
        (tmp_path / 'ABC_ytdlp_1.info.json').write_text(json.dumps({'width': 100, 'height': 200}))
        return SimpleNamespace(stdout='', stderr='', returncode=0)
    monkeypatch.setattr(module.subprocess, 'run', run)
    result = module._run_ytdlp('https://www.instagram.com/reel/ABC/', str(tmp_path), 'ABC')
    assert result.media_list[0].type == 'video'
    assert len(commands) == 2
    for command in commands:
        assert '--ignore-config' in command
        assert '--no-cookies' in command
        assert '--no-cookies-from-browser' in command
        assert '--cookies-from-browser' not in command
        assert all(str(profile) not in arg for arg in command)
        assert '--cookies' not in command


def test_installer_preserves_vps_changes_and_is_idempotent(tmp_path):
    spec = importlib.util.spec_from_file_location('ig_installer', ROOT / 'scripts/install_instagram_public_vps.py')
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    reference = (ROOT / 'services/platforms/instagram_media.py').read_text()
    original = '''import os
from urllib.parse import urlparse
EXTENSION_COOKIES_PATH = "cookies.txt"
SENTINEL_LOCAL_SETTING = 42

def _run_gallery_dl(url):
    cmd = []
    if os.path.exists(EXTENSION_COOKIES_PATH) and os.path.getsize(EXTENSION_COOKIES_PATH) > 0:
        cmd.extend(["--cookies", EXTENSION_COOKIES_PATH])
    if True:
        if INSTAGRAM_FIREFOX_PROFILE:
            cmd.extend(["--cookies-from-browser", "OLD"])
        cmd.append("STORY_SETTING_PRESERVED")
    cmd.append(url)
    return cmd

def _run_ytdlp(url):
    metadata_cmd, download_cmd = [], []
    if os.path.exists(EXTENSION_COOKIES_PATH):
        metadata_cmd.extend(["--cookies", EXTENSION_COOKIES_PATH])
    if os.path.exists(EXTENSION_COOKIES_PATH):
        download_cmd.extend(["--cookies", EXTENSION_COOKIES_PATH])
    metadata_cmd.append(url)
    download_cmd.append(url)
    return metadata_cmd, download_cmd
'''
    updated = installer.patch_source(original, reference)
    assert 'SENTINEL_LOCAL_SETTING = 42' in updated
    assert 'STORY_SETTING_PRESERVED' in updated
    assert '"OLD"' not in updated
    namespace = {}
    exec(updated, namespace)  # noqa: S102 - execute only the literal test fixture
    assert '--config-ignore' in namespace['_run_gallery_dl']('https://instagram.com/p/ABC/')
    for command in namespace['_run_ytdlp']('https://instagram.com/p/ABC/'):
        assert '--ignore-config' in command and '--cookies' not in command
    assert installer.patch_source(updated, reference) == updated
    assert installer.patch_source(reference, reference) == reference
    with pytest.raises(ValueError):
        installer.patch_source('pass\n', reference)


def test_failure_sink_preserves_redirect_cause_without_cookie_values(monkeypatch, tmp_path):
    from utils.download_errors import Failure
    module, _ = load(monkeypatch, tmp_path)
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        returncode=1, stdout='', stderr='HttpError: TooManyRedirects cookie=SECRET'))
    sink = []
    assert module._run_gallery_dl('https://instagram.com/p/ABC/', str(tmp_path), 'ABC', failure_sink=sink) is None
    assert sink == [Failure('redirect_loop')]
    assert 'SECRET' not in repr(sink)


@pytest.mark.asyncio
async def test_executor_failure_reaches_async_request_and_success_clears_it(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    from utils.download_errors import (
        best_failure,
        close_failure_context,
        failure_context,
        note_failure,
    )
    module, _ = load(monkeypatch, tmp_path)
    def gallery(url, output, post, story, sink):
        note_failure('redirect_loop', sink=sink)
    monkeypatch.setattr(module, '_run_gallery_dl', gallery)
    monkeypatch.setattr(module, '_run_ytdlp', lambda *args: None)
    monkeypatch.setattr(module, 'fetch_cobalt_data', AsyncMock(return_value=None))
    monkeypatch.setattr(module, 'PUBLIC_COBALT_INSTANCES', [])
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kwargs: AsyncMock())
    token = failure_context()
    try:
        assert await module.fetch_instagram_media('https://instagram.com/p/ABC/', str(tmp_path)) is None
        assert best_failure().reason == 'redirect_loop'
        result = SimpleNamespace(media_list=['downloaded'])
        monkeypatch.setattr(module, '_run_ytdlp', lambda *args: result)
        assert await module.fetch_instagram_media('https://instagram.com/p/ABC/', str(tmp_path)) is result
        assert best_failure().reason == 'unknown'
    finally:
        close_failure_context(token)


def test_installer_upgrades_connected_layout_with_backup(tmp_path):
    import subprocess

    reference = (ROOT / 'services/platforms/instagram_media.py').read_text()
    source = reference
    flags = {
        'cmd': ['--config-ignore'],
        'metadata_cmd': ['--ignore-config', '--no-cookies', '--no-cookies-from-browser'],
        'download_cmd': ['--ignore-config', '--no-cookies', '--no-cookies-from-browser'],
    }
    for command, options in flags.items():
        source = source.replace(f'{command}.extend({options!r})',
                                f'{command}.extend(_instagram_cookie_args(url, "ignored"))')
    source += '\ndef _instagram_cookie_args(url, extractor):\n    return ["--cookies", "private-cookie-file"]\n'
    source += '\n# VPS CUSTOMIZATION\n'
    target = tmp_path / 'services/platforms/instagram_media.py'
    target.parent.mkdir(parents=True)
    target.write_text(source)
    (tmp_path / '.env').write_text('INSTAGRAM_FIREFOX_PROFILE=keep-for-other-platforms\n')
    command = [sys.executable, str(ROOT / 'scripts/install_instagram_public_vps.py'),
               '--target', str(tmp_path)]
    dry = subprocess.run(command, capture_output=True, text=True, check=False)
    assert dry.returncode == 0 and target.read_text() == source
    applied = subprocess.run([*command, '--apply'], capture_output=True, text=True, check=False)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert '# VPS CUSTOMIZATION' in target.read_text()
    assert '_instagram_cookie_args' not in target.read_text()
    assert next(target.parent.glob('*.public-backup-*')).read_text() == source
    assert 'keep-for-other-platforms' in (tmp_path / '.env').read_text()
    again = subprocess.run([*command, '--apply'], capture_output=True, text=True, check=False)
    assert again.returncode == 0 and 'already installed' in again.stdout
