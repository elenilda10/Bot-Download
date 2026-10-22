"""Regression: video metadata returns (kwargs, thumbnail), never one mapping."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts.apply_pinterest_video_send_fix import patch_source
from services.media import delivery, video_metadata


LEGACY_HANDLERS = """async def outer():
    async def _send_downloaded(path: str):
        if media_kind == "video":
            return await message.METHOD(
                video=FSInputFile(path),
                allow_sending_without_reply=True,
                **(await build_video_send_kwargs(path)),
            )
        return await message.answer_photo(photo=FSInputFile(path))

    async def _inspect_metrics(metrics):
        return True
"""


@pytest.mark.parametrize("method", ["reply_video", "answer_video"])
def test_vps_patch_handles_reply_or_answer_video_and_is_idempotent(method):
    original = LEGACY_HANDLERS.replace("METHOD", method)
    fixed = patch_source(original)
    ast.parse(fixed)
    assert "return await message.answer_video(" in fixed
    assert "reply_video(" not in fixed
    assert "video_kwargs, thumbnail_path = await build_video_send_kwargs(path)" in fixed
    assert "**video_kwargs" in fixed
    assert "**(await build_video_send_kwargs(path))" not in fixed
    assert "await remove_file(thumbnail_path)" in fixed
    assert "return await message.answer_photo(" in fixed
    assert patch_source(fixed) == fixed


def test_vps_patch_rejects_unknown_source_without_changing_anything():
    with pytest.raises(ValueError):
        patch_source("async def something_else():\n    pass\n")
    with pytest.raises(ValueError):
        patch_source(LEGACY_HANDLERS.replace(
            "**(await build_video_send_kwargs(path)),",
            "**dict(something_else),",
        ))


def test_pinterest_and_threads_handlers_unpack_video_kwargs():
    for module in ["handlers/pinterest.py", "handlers/threads.py"]:
        text = Path(module).read_text(encoding="utf-8")
        ast.parse(text)
        assert "**(await build_video_send_kwargs(path))" not in text
        assert "video_kwargs, thumbnail_path = await build_video_send_kwargs(path)" in text
        assert "await remove_file(thumbnail_path)" in text
        assert "return await message.answer_video(" in text


@pytest.mark.asyncio
async def test_metadata_helper_returns_mapping_and_thumbnail_as_two_values(monkeypatch):
    monkeypatch.setattr(
        video_metadata,
        "probe_telegram_video_attrs",
        AsyncMock(return_value=video_metadata.TelegramVideoAttrs(
            width=1280, height=720, duration=7, supports_streaming=True,
        )),
    )
    kwargs, thumbnail = await video_metadata.build_video_send_kwargs("clip.mp4")
    assert kwargs == {
        "width": 1280, "height": 720,
        "duration": 7, "supports_streaming": True,
    }
    assert thumbnail is None


@pytest.mark.asyncio
async def test_single_media_delivery_unpacks_kwargs_and_cleans_generated_thumbnail(monkeypatch, tmp_path):
    thumb_path = tmp_path / "test-thumb.jpg"
    thumb_path.write_bytes(b"thumbnail")
    async def build_metadata(_path):
        return {
            "width": 640,
            "height": 360,
            "supports_streaming": True,
        }, str(thumb_path)

    monkeypatch.setattr(delivery, "build_video_send_kwargs", build_metadata)
    message = SimpleNamespace(
        answer_video=AsyncMock(return_value=SimpleNamespace(
            video=SimpleNamespace(file_id="sent-video"),
        ))
    )
    db = SimpleNamespace(add_file=AsyncMock())
    entries = [{
        "kind": "video",
        "path": "/tmp/video.mp4",
        "cache_key": "pinterest-photo-video-single",
    }]

    sent = await delivery.send_cached_media_entries(
        message, entries, db_service=db,
    )
    assert sent.video.file_id == "sent-video"
    kwargs = message.answer_video.await_args.kwargs
    assert kwargs["video"].path == "/tmp/video.mp4"
    assert kwargs["width"] == 640
    assert kwargs["height"] == 360
    assert kwargs["supports_streaming"] is True
    assert not thumb_path.exists()
    db.add_file.assert_awaited_once_with(
        "pinterest-photo-video-single", "sent-video", "video"
    )


@pytest.mark.asyncio
async def test_generated_thumbnail_is_cleaned_even_when_telegram_send_fails(monkeypatch, tmp_path):
    thumbnail = tmp_path / "video_thumb.jpg"
    thumbnail.write_bytes(b"x")
    monkeypatch.setattr(
        delivery,
        "build_video_send_kwargs",
        AsyncMock(return_value=({"supports_streaming": True}, str(thumbnail))),
    )
    message = SimpleNamespace(
        answer_video=AsyncMock(side_effect=RuntimeError("Telegram rejected upload")),
    )
    with pytest.raises(RuntimeError):
        await delivery.send_cached_media_entries(
            message, [{"kind": "video", "path": "/tmp/file.mp4"}],
            db_service=SimpleNamespace(add_file=AsyncMock()),
        )
    assert not thumbnail.exists()


def test_inline_video_path_unpacks_metadata_and_tracks_thumbnail():
    source = Path("services/inline/send_flow.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert "video_kwargs, thumbnail_path = await build_video_send_kwargs(metrics.path)" in source
    assert "state.register_download_path(thumbnail_path)" in source
