import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.media import video_metadata


@pytest.mark.asyncio
async def test_probe_telegram_video_attrs_adjusts_dimensions_from_display_aspect_ratio(monkeypatch):
    process = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(
            return_value=(
                b'{"streams":[{"width":720,"height":1280,"sample_aspect_ratio":"1216:405","display_aspect_ratio":"171:100"}]}',
                b"",
            )
        ),
    )
    create_subprocess_exec = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)

    attrs = await video_metadata.probe_telegram_video_attrs("downloads/test.mp4")

    assert attrs.width == 2189
    assert attrs.height == 1280
    assert attrs.supports_streaming is True


@pytest.mark.asyncio
async def test_build_video_send_kwargs_falls_back_to_supports_streaming_when_probe_fails(monkeypatch):
    monkeypatch.setattr(
        video_metadata,
        "probe_telegram_video_attrs",
        AsyncMock(return_value=video_metadata.TelegramVideoAttrs()),
    )

    kwargs, thumbnail_path = await video_metadata.build_video_send_kwargs("downloads/test.mp4")

    assert kwargs == {"supports_streaming": True}
    assert thumbnail_path is None


@pytest.mark.asyncio
async def test_build_video_send_kwargs_returns_mapping_and_thumbnail_path(monkeypatch, tmp_path):
    thumbnail = tmp_path / "video_thumb.jpg"
    thumbnail.write_bytes(b"JPEG thumbnail")
    monkeypatch.setattr(
        video_metadata,
        "probe_telegram_video_attrs",
        AsyncMock(return_value=video_metadata.TelegramVideoAttrs(
            width=1080,
            height=1920,
            duration=15,
            thumbnail_path=str(thumbnail),
            supports_streaming=True,
        )),
    )
    kwargs, thumbnail_path = await video_metadata.build_video_send_kwargs("video.mp4")

    assert isinstance(kwargs, dict)
    assert kwargs["supports_streaming"] is True
    assert kwargs["width"] == 1080
    assert kwargs["height"] == 1920
    assert kwargs["duration"] == 15
    assert kwargs["thumbnail"].path == str(thumbnail)
    assert thumbnail_path == str(thumbnail)
