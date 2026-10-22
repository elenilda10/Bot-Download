"""Pinterest HLS/yt-dlp recovery tests without network, cookies or real ffmpeg."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.platforms import pinterest_media as pm
from utils.download_manager import DownloadMetrics, DownloadRateLimitError


URL_VIDEO = "https://pin.it/3Ae91Svbf"
URL_IMAGE = "https://pin.it/5Qs3W9mxR"


def _metadata(video=True):
    return {
        "extractor_key": "Pinterest",
        "id": "886998089120536411",
        "description": "Teste público de vídeo",
        "formats": [{
            "url": "https://v1.pinimg.com/videos/video.m3u8",
            "vcodec": "avc1.42E01E" if video else "none",
            "acodec": "none",
            "protocol": "m3u8_native",
        }],
    }


def _service(tmp_path):
    async def fetch(_base, _key, _payload, **kwargs):
        return None
    async def retry(action, **kwargs):
        return await action()
    return pm.PinterestMediaService(
        str(tmp_path),
        cobalt_api_url="https://cobalt.test",
        cobalt_api_key="fake",
        fetch_cobalt_data_func=fetch,
        retry_async_operation_func=retry,
    )


def test_video_metadata_maps_hls_video_to_original_pin_link():
    post = pm._parse_pinterest_video_metadata(
        json.dumps(_metadata()).encode(), URL_VIDEO
    )
    assert post is not None
    assert post.id == "886998089120536411"
    assert post.media_list[0].type == "video"
    assert post.media_list[0].url == URL_VIDEO
    assert "Teste público" in post.description


@pytest.mark.parametrize("bad", [
    {"extractor_key": "Generic", **{k: v for k, v in _metadata().items() if k != "extractor_key"}},
    {"extractor_key": "Pinterest", "id": "123", "_type": "playlist", "formats": _metadata()["formats"]},
    _metadata(video=False),
    {"extractor_key": "Pinterest", "id": "123", "formats": []},
])
def test_video_metadata_rejects_generic_playlist_or_audio_only(bad):
    assert pm._parse_pinterest_video_metadata(
        json.dumps(bad).encode(), URL_VIDEO
    ) is None


@pytest.mark.asyncio
async def test_video_probe_is_anonymous_and_restricted_to_pins(monkeypatch):
    probe = AsyncMock(return_value=(0, json.dumps(_metadata()).encode()))
    monkeypatch.setattr(pm, "_run_yt_dlp", probe)
    post = await pm._fetch_public_pin_yt_dlp(URL_VIDEO)
    assert post is not None
    argv = probe.await_args.args[0]
    assert "--ignore-config" in argv
    assert "--no-playlist" in argv
    assert "--skip-download" in argv
    assert "--cookies" not in argv
    assert argv[-1] == URL_VIDEO
    assert await pm._fetch_public_pin_yt_dlp(
        "https://www.pinterest.com/user/board/"
    ) is None
    probe.assert_awaited_once()


@pytest.mark.asyncio
async def test_video_yt_dlp_precedes_gallery_when_cobalt_has_no_media(monkeypatch, tmp_path):
    service = _service(tmp_path)
    video = pm.PinterestPost(
        "886998089120536411", "video",
        [pm.PinterestMedia(URL_VIDEO, "video")],
    )
    gallery = AsyncMock()
    yt = AsyncMock(return_value=video)
    monkeypatch.setattr(pm, "_fetch_public_pin_gallery", gallery)
    monkeypatch.setattr(pm, "_fetch_public_pin_yt_dlp", yt)
    result = await service.fetch_post(URL_VIDEO)
    assert result is video
    gallery.assert_not_awaited()
    yt.assert_awaited_once_with(URL_VIDEO)


@pytest.mark.asyncio
async def test_photo_uses_gallery_when_video_probe_has_no_video(monkeypatch, tmp_path):
    service = _service(tmp_path)
    photo = pm.PinterestPost(
        "abc", "foto", [pm.PinterestMedia("https://i.pinimg.com/a.jpg", "photo")]
    )
    gallery = AsyncMock(return_value=photo)
    yt = AsyncMock(return_value=None)
    monkeypatch.setattr(pm, "_fetch_public_pin_gallery", gallery)
    monkeypatch.setattr(pm, "_fetch_public_pin_yt_dlp", yt)
    assert await service.fetch_post(URL_IMAGE) is photo
    yt.assert_awaited_once_with(URL_IMAGE)
    gallery.assert_awaited_once_with(URL_IMAGE)


@pytest.mark.asyncio
async def test_hls_manifest_from_cobalt_is_not_downloaded_as_mp4(monkeypatch, tmp_path):
    service = _service(tmp_path)
    service._fetch_cobalt_json = AsyncMock(return_value={
        "status": "tunnel",
        "url": "https://v1.pinimg.com/playlist.m3u8",
        "filename": "video.m3u8",
    })
    monkeypatch.setattr(pm, "_fetch_public_pin_yt_dlp", AsyncMock(return_value=None))
    monkeypatch.setattr(pm, "_fetch_public_pin_gallery", AsyncMock(return_value=None))
    assert await service.fetch_post(URL_VIDEO) is None


@pytest.mark.asyncio
async def test_local_public_photo_does_not_call_cobalt_invalid_body(monkeypatch, tmp_path):
    service = _service(tmp_path)
    cobalt = AsyncMock(side_effect=RuntimeError("error.api.invalid_body"))
    service._fetch_cobalt_json = cobalt
    monkeypatch.setattr(pm, "_fetch_public_pin_yt_dlp", AsyncMock(return_value=None))
    gallery = AsyncMock(return_value=pm.PinterestPost(
        "photo", "", [pm.PinterestMedia("https://i.pinimg.com/working.jpg", "photo")]
    ))
    monkeypatch.setattr(pm, "_fetch_public_pin_gallery", gallery)
    result = await service.fetch_post(URL_IMAGE)
    assert result is not None
    assert result.media_list[0].type == "photo"
    cobalt.assert_not_awaited()
    gallery.assert_awaited_once_with(URL_IMAGE)


@pytest.mark.asyncio
async def test_local_public_video_does_not_call_cobalt_invalid_body(monkeypatch, tmp_path):
    service = _service(tmp_path)
    cobalt = AsyncMock(side_effect=RuntimeError("error.api.invalid_body"))
    service._fetch_cobalt_json = cobalt
    video = AsyncMock(return_value=pm.PinterestPost(
        "video", "", [pm.PinterestMedia(URL_VIDEO, "video")]
    ))
    gallery = AsyncMock()
    monkeypatch.setattr(pm, "_fetch_public_pin_yt_dlp", video)
    monkeypatch.setattr(pm, "_fetch_public_pin_gallery", gallery)
    result = await service.fetch_post(URL_VIDEO)
    assert result is not None
    assert result.media_list[0].type == "video"
    cobalt.assert_not_awaited()
    gallery.assert_not_awaited()


@pytest.mark.asyncio
async def test_ytdlp_video_download_produces_real_mp4_metrics_in_isolated_folder(monkeypatch, tmp_path):
    async def fake_download(argv, *, timeout):
        assert "--merge-output-format" in argv
        assert "--remux-video" in argv
        assert "--max-filesize" in argv
        assert "--ignore-config" in argv
        assert argv[-1] == URL_VIDEO
        output_path = Path(argv[argv.index("-o") + 1].replace("%(ext)s", "mp4"))
        output_path.write_bytes(b"\0\0\0\x18ftypmp42TESTVIDEO")
        return (0, b"")

    monkeypatch.setattr(pm, "_run_yt_dlp", fake_download)
    metrics = await pm._download_pinterest_yt_dlp(
        URL_VIDEO, "pin_video.mp4", str(tmp_path)
    )
    assert metrics is not None
    assert metrics.path == str(tmp_path / "pin_video.mp4")
    assert metrics.size > 0
    assert Path(metrics.path).read_bytes().startswith(b"\0\0\0\x18ftypmp42")
    assert not list(tmp_path.glob("pinterest-ytdlp-*"))


@pytest.mark.asyncio
async def test_yt_dlp_video_download_does_not_accept_failed_or_absent_file(monkeypatch, tmp_path):
    runner = AsyncMock(return_value=(1, b""))
    monkeypatch.setattr(pm, "_run_yt_dlp", runner)
    assert await pm._download_pinterest_yt_dlp(
        URL_VIDEO, "pin_video.mp4", str(tmp_path)
    ) is None
    runner.return_value = (0, b"")
    assert await pm._download_pinterest_yt_dlp(
        URL_VIDEO, "pin_video.mp4", str(tmp_path)
    ) is None


@pytest.mark.asyncio
async def test_yt_dlp_download_rejects_path_escape_and_untrusted_source(monkeypatch, tmp_path):
    runner = AsyncMock()
    monkeypatch.setattr(pm, "_run_yt_dlp", runner)
    assert await pm._download_pinterest_yt_dlp(
        URL_VIDEO, "../escape.mp4", str(tmp_path)
    ) is None
    assert await pm._download_pinterest_yt_dlp(
        "https://example.com/video.mp4", "media.mp4", str(tmp_path)
    ) is None
    runner.assert_not_awaited()


@pytest.mark.asyncio
async def test_yt_dlp_video_download_uses_download_queue(monkeypatch, tmp_path):
    metrics = DownloadMetrics(
        url=URL_VIDEO, path=str(tmp_path / "clip.mp4"),
        size=100, elapsed=0.1, used_multipart=False, resumed=False,
    )
    runner = AsyncMock(return_value=metrics)

    class StubQueue:
        async def submit(self, work, **kwargs):
            assert kwargs["user_id"] == 7
            assert kwargs["chat_id"] == -100
            assert kwargs["source"] == "pinterest_ytdlp"
            return await work()

    monkeypatch.setattr(pm, "get_download_queue", lambda: StubQueue())
    monkeypatch.setattr(pm, "_download_pinterest_yt_dlp", runner)
    count = Mock()
    monkeypatch.setattr(pm, "record_download", count)
    service = _service(tmp_path)
    assert await service.download_media(
        URL_VIDEO, "clip.mp4", user_id=7, chat_id=-100
    ) == metrics
    runner.assert_awaited_once_with(URL_VIDEO, "clip.mp4", str(tmp_path))
    count.assert_called_once_with("pinterest", metrics)


@pytest.mark.asyncio
async def test_yt_dlp_queue_throttle_is_translated_to_downloader_errors(monkeypatch, tmp_path):
    class BlockedQueue:
        async def submit(self, work, **kwargs):
            raise pm.QueueRateLimitError(5)

    monkeypatch.setattr(pm, "get_download_queue", lambda: BlockedQueue())
    with pytest.raises(DownloadRateLimitError):
        await _service(tmp_path).download_media(
            URL_VIDEO, "clip.mp4", user_id=7
        )


@pytest.mark.asyncio
async def test_direct_pinimg_download_still_uses_existing_cobalt_downloader(monkeypatch, tmp_path):
    service = _service(tmp_path)
    expected = DownloadMetrics(
        url="https://i.pinimg.com/originals/photo.jpg",
        path=str(tmp_path / "photo.jpg"),
        size=12, elapsed=0.1, used_multipart=False, resumed=False,
    )
    bridge = AsyncMock(return_value=expected)
    monkeypatch.setattr(service._downloader, "download", bridge)
    assert await service.download_media(
        expected.url, "photo.jpg", user_id=2
    ) == expected
