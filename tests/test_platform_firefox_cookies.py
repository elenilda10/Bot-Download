"""Exercise profile precedence, yt-dlp builders and selective VPS install."""
import ast
import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def access(monkeypatch, tmp_path):
    for platform in ('TWITTER', 'TIKTOK', 'REDDIT', 'YOUTUBE'):
        monkeypatch.delenv(platform + '_FIREFOX_PROFILE', raising=False)
    monkeypatch.delenv('MEDIA_FIREFOX_PROFILE', raising=False)
    profile = tmp_path / 'Firefox profile'
    profile.mkdir()
    (profile / 'cookies.sqlite').write_bytes(b'fake database')
    return load('services/platforms/firefox_cookies.py', 'profile_test'), profile


@pytest.mark.parametrize('url', ['https://x.com/user/status/123', 'https://twitter.com/user/status/123', 'https://vm.tiktok.com/abc', 'https://www.reddit.com/r/test/comments/abc', 'https://youtu.be/test'])
def test_shared_profile_cli_and_no_cookie_file_mixing(access, monkeypatch, url):
    module, profile = access
    monkeypatch.setenv('MEDIA_FIREFOX_PROFILE', str(profile))
    assert module.firefox_cookie_args(url, 'yt-dlp', '/legacy/cookies') == ['--cookies-from-browser', 'firefox:' + str(profile)]
    assert module.firefox_cookie_args(url, 'gallery-dl', '/legacy/cookies') == ['--cookies-from-browser', 'firefox:' + str(profile)]


@pytest.mark.parametrize('url', ['https://facebook.com/watch/123', 'https://kwai.com/@u/video/123', 'https://x.com.evil.test/123', 'https://example.com/', 'https://x.com@evil.test/a'])
def test_shared_profile_does_not_add_browser_to_unlisted_hosts(access, monkeypatch, url):
    module, profile = access
    monkeypatch.setenv('MEDIA_FIREFOX_PROFILE', str(profile))
    assert module.firefox_cookie_args(url, 'yt-dlp') == []


def test_per_platform_override_disable_and_missing_profile_fallback(access, monkeypatch):
    module, profile = access
    monkeypatch.setenv('MEDIA_FIREFOX_PROFILE', str(profile))
    monkeypatch.setenv('TWITTER_FIREFOX_PROFILE', '')
    assert module.firefox_cookie_args('https://x.com/a', 'yt-dlp', 'legacy') == ['--cookies', 'legacy']
    monkeypatch.setenv('TWITTER_FIREFOX_PROFILE', '/missing')
    assert module.firefox_profile('twitter') is None
    monkeypatch.setenv('TWITTER_FIREFOX_PROFILE', str(profile))
    assert module.firefox_profile('twitter') == str(profile)
    assert module.firefox_profile('facebook') is None


def extract_functions(file, names, ns):
    nodes = [n for n in ast.parse((ROOT / file).read_text()).body if isinstance(n, ast.FunctionDef) and n.name in names]
    tree = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(tree, file, 'exec', flags=__import__('__future__').annotations.compiler_flag), ns)
    return ns


@pytest.mark.parametrize('file,method', [('tiktok_metadata_mixin.py', '_build_ytdlp_options'), ('tiktok_download_mixin.py', '_build_ytdlp_download_options')])
def test_actual_tiktok_builders_use_profile_for_metadata_and_download(access, monkeypatch, file, method):
    module, profile = access
    monkeypatch.setenv('MEDIA_FIREFOX_PROFILE', str(profile))
    tree = ast.parse((ROOT / 'services/platforms' / file).read_text())
    function = next(n for cls in tree.body if isinstance(cls, ast.ClassDef) for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == method)
    ns = {'_get_tiktok_cookie_file': lambda: 'legacy', 'firefox_ytdlp_options': module.firefox_ytdlp_options}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), file, 'exec', flags=__import__('__future__').annotations.compiler_flag), ns)
    result = ns[method](None)
    assert result['cookiesfrombrowser'] == ('firefox', str(profile), None, None)
    assert 'cookiefile' not in result


def test_youtube_shared_profile_preserves_explicit_browser_configuration(access, monkeypatch):
    module, profile = access
    monkeypatch.setenv('MEDIA_FIREFOX_PROFILE', str(profile))
    monkeypatch.delenv('YTDLP_YOUTUBE_COOKIES_FROM_BROWSER', raising=False)
    ns = dict(os=os, re=re, logging=Mock(), YTDLP_SPEED_OPTS={}, DEFAULT_YOUTUBE_COOKIES_FILE='/missing', firefox_ytdlp_options=module.firefox_ytdlp_options)
    extract_functions('services/platforms/youtube_media.py', {'_read_float_env','_split_env_list','_parse_js_runtimes','_parse_cookies_from_browser','build_ytdlp_youtube_options'}, ns)
    result = ns['build_ytdlp_youtube_options']()
    assert result['cookiesfrombrowser'] == ('firefox', str(profile), None, None)
    monkeypatch.setenv('YTDLP_YOUTUBE_COOKIES_FROM_BROWSER', 'firefox:/explicit')
    assert ns['build_ytdlp_youtube_options']()['cookiesfrombrowser'][1] == '/explicit'


def test_patch_installer_preserves_local_changes_and_can_repeat(tmp_path):
    installer = load('scripts/install_platform_cookies_vps.py', 'platform_installer')
    target = tmp_path / 'target'
    for name in installer.FILES:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
    patch = ROOT / 'scripts/patches/platform-firefox-cookies.patch'
    subprocess.run(['git', 'apply', '--reverse', str(patch)], cwd=target, check=True)
    sentinel = target / installer.FILES[0]
    with sentinel.open('a') as output:
        output.write('\nLOCAL_VPS_SETTING = 42\n')
    stage = tmp_path / 'stage'
    stage.mkdir()
    changed = installer.prepare(target, ROOT, stage)
    assert len(changed) == len(installer.FILES) + 1
    assert 'LOCAL_VPS_SETTING = 42' in (stage / installer.FILES[0]).read_text()
    for name in changed:
        shutil.copy2(stage / name, target / name)
    second = tmp_path / 'second'
    second.mkdir()
    assert installer.prepare(target, ROOT, second) == []
    profile = tmp_path / 'profile'
    profile.mkdir()
    (profile / 'cookies.sqlite').write_bytes(b'fake database')
    env = target / '.env'
    env.write_text('OTHER_SETTING=preserved\n')
    args = ['python', str(ROOT / 'scripts/install_platform_cookies_vps.py'),
            '--target', str(target), '--firefox-profile', str(profile)]
    subprocess.run(args, check=True, capture_output=True)
    assert env.read_text() == 'OTHER_SETTING=preserved\n'
    subprocess.run(args + ['--apply'], check=True, capture_output=True)
    assert 'OTHER_SETTING=preserved' in env.read_text()
    assert 'MEDIA_FIREFOX_PROFILE=' in env.read_text()
    assert len(list(target.glob('.env.firefox-backup-*'))) == 1
    subprocess.run(args + ['--apply'], check=True, capture_output=True)
    assert len(list(target.glob('.env.firefox-backup-*'))) == 1
