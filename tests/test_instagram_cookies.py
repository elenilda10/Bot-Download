"""Isolated Instagram cookie integration tests; no network or real sessions."""
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
    monkeypatch.setattr(module, 'INSTAGRAM_FIREFOX_PROFILE', str(profile))
    monkeypatch.setattr(module, 'EXTENSION_COOKIES_PATH', str(cookie_file))
    monkeypatch.delenv('INSTAGRAM_COOKIES_FILE', raising=False)
    return module, profile


@pytest.mark.parametrize('path', ['p/ABC/', 'reel/ABC/', 'reels/ABC/', 'stories/user/123456789/'])
@pytest.mark.parametrize('extractor,prefix', [('gallery-dl', 'firefox/instagram.com:'), ('yt-dlp', 'firefox:')])
def test_profile_selected_for_all_post_types(monkeypatch, tmp_path, path, extractor, prefix):
    module, profile = load(monkeypatch, tmp_path)
    assert module._instagram_cookie_args('https://www.instagram.com/' + path, extractor) == ['--cookies-from-browser', prefix + str(profile)]


@pytest.mark.parametrize('url', ['https://instagram.com.evil.test/p/X/', 'https://evil.test/', 'https://instagram.com@evil.test/p/X/', 'file:///instagram.com', 'https://user:password@instagram.com/p/X/'])
def test_non_instagram_urls_never_receive_explicit_cookie_flags(monkeypatch, tmp_path, url):
    module, _ = load(monkeypatch, tmp_path)
    assert module._instagram_cookie_args(url, 'gallery-dl') == []


def test_missing_profile_uses_file_and_empty_sources_use_anonymous(monkeypatch, tmp_path):
    module, profile = load(monkeypatch, tmp_path)
    (profile / 'cookies.sqlite').unlink()
    assert module._instagram_cookie_args('https://instagram.com/p/X/', 'yt-dlp') == ['--cookies', module.EXTENSION_COOKIES_PATH]
    monkeypatch.setenv('INSTAGRAM_COOKIES_FILE', '')
    assert module._instagram_cookie_args('https://instagram.com/p/X/', 'yt-dlp') == []


@pytest.mark.parametrize('story', [False, True])
def test_gallery_command_reads_profile_once_and_preserves_story_flags(monkeypatch, tmp_path, story, caplog):
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
    assert cmd.count('--cookies-from-browser') == 1
    assert '--cookies' not in cmd
    assert 'firefox/instagram.com:' + str(profile) in cmd
    assert ('--filename' in cmd) is (not story)
    assert ('--user-agent' in cmd) is (not story)
    assert '--filter' not in cmd
    assert result.media_list
    assert 'SECRET_COOKIE_VALUE' not in caplog.text


def test_ytdlp_metadata_and_video_download_use_same_profile(monkeypatch, tmp_path):
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
        assert command.count('--cookies-from-browser') == 1
        assert 'firefox:' + str(profile) in command
        assert '--cookies' not in command


def test_installer_preserves_vps_changes_and_is_idempotent(tmp_path):
    spec = importlib.util.spec_from_file_location('ig_installer', ROOT / 'scripts/install_instagram_cookies_vps.py')
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
    return cmd

def _run_ytdlp(url):
    metadata_cmd, download_cmd = [], []
    if os.path.exists(EXTENSION_COOKIES_PATH):
        metadata_cmd.extend(["--cookies", EXTENSION_COOKIES_PATH])
    if os.path.exists(EXTENSION_COOKIES_PATH):
        download_cmd.extend(["--cookies", EXTENSION_COOKIES_PATH])
    return metadata_cmd, download_cmd
'''
    updated = installer.patch_source(original, reference)
    assert 'SENTINEL_LOCAL_SETTING = 42' in updated
    assert 'STORY_SETTING_PRESERVED' in updated
    assert '"OLD"' not in updated
    assert installer.patch_source(updated, reference) == updated
    assert installer.patch_source(reference, reference) == reference
    with pytest.raises(ValueError):
        installer.patch_source('pass\n', reference)
