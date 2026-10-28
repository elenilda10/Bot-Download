"""Reddit-native manifest download and audio validation without network."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def module(monkeypatch):
    links_spec = importlib.util.spec_from_file_location('native_links_test', ROOT / 'services/links/reddit.py')
    links = importlib.util.module_from_spec(links_spec)
    links_spec.loader.exec_module(links)
    monkeypatch.setitem(sys.modules, 'services.links.reddit', links)
    spec = importlib.util.spec_from_file_location('native_json_test', ROOT / 'services/platforms/reddit_json.py')
    json_module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, json_module)
    spec.loader.exec_module(json_module)
    monkeypatch.setitem(sys.modules, 'services.platforms.reddit_json', json_module)
    spec = importlib.util.spec_from_file_location('native_video_test', ROOT / 'services/platforms/reddit_native.py')
    native = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, native)
    spec.loader.exec_module(native)
    return native


@pytest.mark.asyncio
@pytest.mark.parametrize('audio', [False, True])
async def test_native_manifest_is_downloaded_directly_without_cookies_and_audio_is_probed(module, monkeypatch, tmp_path, audio):
    calls = []
    async def run(command, timeout):
        calls.append(command)
        if command[0] != 'ffprobe':
            (tmp_path / 'reddit_native.mp4').write_bytes(b'video')
            return 0, b'', b''
        streams = [{'codec_type': 'video'}] + ([{'codec_type': 'audio'}] if audio else [])
        return 0, json.dumps({'streams': streams}).encode(), b''
    monkeypatch.setattr(module, '_run', run)
    source = {'url': 'https://v.redd.it/abc/DASHPlaylist.mpd?signature=preserved', 'expects_audio': audio}
    result = await module.download_native_video({'id': 'abc', 'title': 'Original'}, source, str(tmp_path), 1000)
    assert result.media_list[0].has_audio is audio
    assert result.media_list[0].type == 'video'
    assert calls[0][-1] == source['url']
    assert '--ignore-config' in calls[0]
    assert not any('cookie' in arg for arg in calls[0])
    assert calls[0][calls[0].index('-f') + 1] == 'bestvideo*+bestaudio/best'
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('failure,code', [('http', 'native_video_http_403'), ('audio', 'native_video_missing_audio'),
                                        ('size', 'native_video_missing_or_oversized'), ('probe', 'native_video_probe_failed')])
async def test_failed_video_cleans_partial_downloads_and_does_not_expose_response_text(module, monkeypatch, tmp_path, failure, code):
    async def run(command, timeout):
        if command[0] != 'ffprobe':
            (tmp_path / 'reddit_native.mp4').write_bytes(b'x' * (101 if failure == 'size' else 10))
            (tmp_path / 'reddit_native.f1.mp4.part').write_bytes(b'partial')
            return (1, b'', b'403 SECRET_URL_TOKEN') if failure == 'http' else (0, b'', b'')
        return (1, b'', b'') if failure == 'probe' else (0, b'{"streams":[{"codec_type":"video"}]}', b'')
    monkeypatch.setattr(module, '_run', run)
    (tmp_path / 'unrelated.txt').write_text('preserve')
    with pytest.raises(module.RedditVideoError, match=code) as error:
        await module.download_native_video({'id': 'abc'}, {'url': 'https://v.redd.it/abc/DASHPlaylist.mpd', 'expects_audio': True}, str(tmp_path), 100)
    assert 'SECRET' not in str(error.value)
    assert sorted(p.name for p in tmp_path.iterdir()) == ['unrelated.txt']


@pytest.mark.asyncio
async def test_native_rejects_external_manifest_before_starting_process(module, monkeypatch, tmp_path):
    run = AsyncMock()
    monkeypatch.setattr(module, '_run', run)
    with pytest.raises(module.RedditVideoError, match='native_video_invalid_source'):
        await module.download_native_video({'id': 'abc'}, {'url': 'https://evil.test/video.mpd'}, str(tmp_path), 100)
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_timeout_kills_process_group_and_reaps_child(module, monkeypatch):
    process = SimpleNamespace(returncode=None, pid=123456789, kill=Mock())
    calls = []
    async def communicate():
        calls.append(1)
        if len(calls) == 1:
            await asyncio.Event().wait()
        return b'', b''
    process.communicate = communicate
    monkeypatch.setattr(module.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    kill_group = Mock()
    monkeypatch.setattr(module.os, 'killpg', kill_group)
    with pytest.raises(TimeoutError):
        await module._run(['fake-program'], 0.01)
    assert len(calls) == 2
    if module.os.name == 'posix':
        kill_group.assert_called_once_with(process.pid, module.signal.SIGKILL)
    else:
        process.kill.assert_called_once()
