"""Tests for the standalone Kwai downloader, isolated from bot runtime."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from urllib.parse import urlparse

import pytest

SERVICE = Path(__file__).resolve().parents[1] / "services/platforms/kwai_service.py"


def _load_service(monkeypatch, *, cdn_url=None):
    page = types.ModuleType("services.platforms.kwai_media")
    source = cdn_url or "https://aws-br-cdn.kwai.net/upic/test.mp4?signature=secret"
    page._DEFAULT_UA = "Mozilla/5.0"
    page.is_kwai_url = lambda url: (urlparse(url).hostname or "").endswith(".kwai.com")
    page._is_allowed_media_url = lambda url: (
        urlparse(url).scheme == "https"
        and (urlparse(url).hostname or "").endswith(".kwai.net")
    )

    async def extract(_url):
        return {"media_url": source, "title": "Vídeo de teste", "author": "autor"}

    page.extract_kwai_video_page = extract
    parent = types.ModuleType("services")
    parent.__path__ = []
    platforms = types.ModuleType("services.platforms")
    platforms.__path__ = []
    monkeypatch.setitem(sys.modules, "services", parent)
    monkeypatch.setitem(sys.modules, "services.platforms", platforms)
    monkeypatch.setitem(sys.modules, "services.platforms.kwai_media", page)

    spec = importlib.util.spec_from_file_location("kwai_service_isolated", SERVICE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def _mock_http(monkeypatch, module, *, chunks, status=200, final_url=None):
    target = "https://aws-br-cdn.kwai.net/upic/test.mp4?signature=secret"

    class Content:
        async def iter_chunked(self, _size):
            for chunk in chunks:
                yield chunk

    class Response:
        headers = {"Content-Length": str(sum(map(len, chunks)))}
        content = Content()

        def __init__(self):
            self.status = status
            self.url = final_url or target

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class Session:
        def __init__(self, **_kwargs):
            assert "Cookie" not in _kwargs.get("headers", {})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def get(self, _url, **_kwargs):
            return Response()

    monkeypatch.setattr(module.aiohttp, "ClientSession", Session)


@pytest.mark.asyncio
async def test_profile_is_forwarded_only_when_explicit(monkeypatch, tmp_path):
    module = _load_service(monkeypatch)
    calls = []

    async def extract(url, **kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(module, "extract_kwai_video_page", extract)
    url = "https://www.kwai.com/@user/video/5200118832330956026"
    await module.download_kwai_media(url, post_id="anonymous", output_dir=str(tmp_path))
    await module.download_kwai_media(
        url, post_id="owner", output_dir=str(tmp_path), firefox_profile="/test/profile"
    )
    assert calls == [{}, {"firefox_profile": "/test/profile"}]


@pytest.mark.asyncio
async def test_standalone_download_returns_media_metadata_and_thumbnail(monkeypatch, tmp_path):
    module = _load_service(monkeypatch)
    _mock_http(monkeypatch, module, chunks=[b"ftyp", b"FAKE_MP4_VIDEO"])

    async def probe(path):
        assert Path(path).is_file()
        return {"width": 640, "height": 480, "duration": 9}

    async def thumbnail(_path, output):
        Path(output).write_bytes(b"JPEG_PLACEHOLDER")
        return output

    monkeypatch.setattr(module, "_run_probe", probe)
    monkeypatch.setattr(module, "_make_thumbnail", thumbnail)

    result = await module.download_kwai_media(
        "https://www.kwai.com/@user/video/123",
        output_dir=str(tmp_path), post_id="kwai_test",
        max_file_size=1024,
    )

    assert result is not None
    assert result.author == "autor"
    assert result.description == "Vídeo de teste"
    assert result.media_list[0].duration == 9
    assert result.media_list[0].width == 640
    assert Path(result.media_list[0].url).read_bytes() == b"ftypFAKE_MP4_VIDEO"
    assert Path(result.media_list[0].thumb).exists()


@pytest.mark.asyncio
async def test_standalone_stream_enforces_max_size_and_cleans_up(monkeypatch, tmp_path):
    module = _load_service(monkeypatch)
    _mock_http(monkeypatch, module, chunks=[b"x" * 12, b"y" * 12])
    result = await module.download_kwai_media(
        "https://www.kwai.com/@user/video/123",
        output_dir=str(tmp_path), post_id="size_limited",
        max_file_size=15,
    )
    assert result is None
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_standalone_rejects_cdn_redirect_off_allowlist(monkeypatch, tmp_path):
    module = _load_service(monkeypatch)
    _mock_http(monkeypatch, module, chunks=[b"VIDEO"], final_url="https://127.0.0.1/internal")
    result = await module.download_kwai_media(
        "https://www.kwai.com/@user/video/123",
        output_dir=str(tmp_path), post_id="blocked",
    )
    assert result is None
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_standalone_rejects_unsafe_temp_id(monkeypatch, tmp_path):
    module = _load_service(monkeypatch)
    with pytest.raises(ValueError, match="temporary"):
        await module.download_kwai_media(
            "https://www.kwai.com/@user/video/123",
            output_dir=str(tmp_path), post_id="../../escape",
        )
