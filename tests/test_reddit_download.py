"""Validate Reddit service boundaries with a simulated shared downloader."""
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[1]


def service(monkeypatch, result):
    spec = importlib.util.spec_from_file_location('reddit_links_isolated', ROOT / 'services/links/reddit.py')
    links = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(links)
    shared = ModuleType('services.platforms.universal_extractor')
    shared.download_universal_media = AsyncMock(return_value=result)
    monkeypatch.setitem(sys.modules, 'services.platforms.universal_extractor', shared)
    monkeypatch.setitem(sys.modules, 'services.links.reddit', links)
    spec = importlib.util.spec_from_file_location('reddit_download_isolated', ROOT / 'services/platforms/reddit_download.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, shared.download_universal_media


@pytest.mark.asyncio
@pytest.mark.parametrize('url', ['https://evil.test/post', 'https://www.reddit.com/r/test/', 'https://reddit.com.evil.test/comments/abc'])
async def test_adapter_rejects_unknown_hosts_and_feed_urls(monkeypatch, tmp_path, url):
    module, download = service(monkeypatch, None)
    assert await module.download_reddit_media(url, output_dir=str(tmp_path), max_file_size=100) is None
    download.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['oversized', 'outside', 'unsupported', 'missing'])
async def test_adapter_rejects_invalid_files(monkeypatch, tmp_path, invalid):
    directory = tmp_path / 'request'
    directory.mkdir()
    path = (tmp_path if invalid == 'outside' else directory) / 'media.mp4'
    if invalid != 'missing':
        path.write_bytes(b'x' * (101 if invalid == 'oversized' else 10))
    result = SimpleNamespace(media_list=[SimpleNamespace(url=str(path), type='audio' if invalid == 'unsupported' else 'video')])
    module, _ = service(monkeypatch, result)
    with pytest.raises(ValueError):
        await module.download_reddit_media('https://redd.it/abc123', output_dir=str(directory), max_file_size=100)


@pytest.mark.asyncio
async def test_adapter_preserves_mixed_media_and_normalizes_tracking(monkeypatch, tmp_path):
    photo = tmp_path / 'photo.jpg'
    photo.write_bytes(b'photo')
    video = tmp_path / 'video.mp4'
    video.write_bytes(b'video')
    result = SimpleNamespace(media_list=[SimpleNamespace(url=str(photo), type='photo'), SimpleNamespace(url=str(video), type='video')])
    module, download = service(monkeypatch, result)
    assert await module.download_reddit_media('https://redd.it/abc123?utm_source=test', output_dir=str(tmp_path), max_file_size=100) is result
    assert download.await_args.args[0] == 'https://redd.it/abc123'
