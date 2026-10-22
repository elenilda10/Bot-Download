"""Prevent tuple unpacking crashes in Threads and shared single-video delivery."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.media import delivery

ROOT = Path(__file__).resolve().parents[1]


def _load_threads_send(env: dict):
    path = ROOT / "handlers/threads.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    outer = next(
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "process_threads_single_media"
    )
    nested = next(
        node for node in outer.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_send_downloaded"
    )
    module = ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(
            module="__future__",
            names=[ast.alias(name="annotations")],
            level=0,
        ), nested],
        type_ignores=[],
    ))
    scope = dict(env)
    exec(compile(module, str(path), "exec"), scope)
    return scope["_send_downloaded"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_threads_video_sends_only_kwargs_mapping_and_cleans_thumbnail(fail):
    error = RuntimeError("telegram upload error")
    message = SimpleNamespace(
        answer_video=AsyncMock(
            side_effect=error if fail else None,
            return_value=SimpleNamespace(video=SimpleNamespace(file_id="ok")),
        ),
        reply_video=AsyncMock(side_effect=AssertionError("must not reply")),
    )
    metadata = AsyncMock(return_value=(
        {"width": 720, "height": 1280, "supports_streaming": True},
        "/tmp/threads_video_thumb.jpg",
    ))
    cleanup = AsyncMock()
    env = {
        "media_kind": "video",
        "message": message,
        "FSInputFile": lambda path: SimpleNamespace(path=path),
        "post": SimpleNamespace(description="Threads clip"),
        "bm": SimpleNamespace(captions=lambda *_args: "test caption"),
        "user_settings": {"captions": "on"},
        "bot_url": "https://t.me/SaveVidDLBot",
        "source_url": "https://www.threads.net/post/123",
        "_threads_reply_markup": lambda *_args: "keyboard",
        "build_video_send_kwargs": metadata,
        "remove_file": cleanup,
    }
    send = _load_threads_send(env)
    if fail:
        with pytest.raises(RuntimeError, match="upload error"):
            await send("/tmp/threads_video.mp4")
    else:
        sent = await send("/tmp/threads_video.mp4")
        assert sent.video.file_id == "ok"
    metadata.assert_awaited_once_with("/tmp/threads_video.mp4")
    message.answer_video.assert_awaited_once()
    kwargs = message.answer_video.await_args.kwargs
    assert kwargs["video"].path == "/tmp/threads_video.mp4"
    assert kwargs["width"] == 720
    assert kwargs["allow_sending_without_reply"] is True
    message.reply_video.assert_not_awaited()
    cleanup.assert_awaited_once_with("/tmp/threads_video_thumb.jpg")


@pytest.mark.asyncio
async def test_shared_single_video_sends_mapping_and_cleans_thumbnail(monkeypatch, tmp_path):
    thumbnail = tmp_path / "clip_thumb.jpg"
    thumbnail.write_bytes(b"JPEG")
    monkeypatch.setattr(
        delivery, "build_video_send_kwargs",
        AsyncMock(return_value=(
            {"supports_streaming": True, "width": 640, "height": 360},
            str(thumbnail),
        )),
    )
    message = SimpleNamespace(
        answer_video=AsyncMock(return_value=SimpleNamespace(
            video=SimpleNamespace(file_id="sent-file"),
        )),
    )
    db = SimpleNamespace(add_file=AsyncMock())
    sent = await delivery.send_cached_media_entries(
        message,
        [{"kind": "video", "path": "/tmp/clip.mp4", "cache_key": "single-video"}],
        db_service=db,
    )
    assert sent.video.file_id == "sent-file"
    kwargs = message.answer_video.await_args.kwargs
    assert kwargs["video"].path == "/tmp/clip.mp4"
    assert kwargs["width"] == 640
    assert kwargs["height"] == 360
    assert kwargs["supports_streaming"] is True
    assert not thumbnail.exists()
    db.add_file.assert_awaited_once_with("single-video", "sent-file", "video")


@pytest.mark.asyncio
async def test_shared_single_video_cleans_thumbnail_on_error(monkeypatch, tmp_path):
    thumbnail = tmp_path / "clip_thumb.jpg"
    thumbnail.write_bytes(b"JPEG")
    monkeypatch.setattr(
        delivery, "build_video_send_kwargs",
        AsyncMock(return_value=({"supports_streaming": True}, str(thumbnail))),
    )
    message = SimpleNamespace(answer_video=AsyncMock(side_effect=RuntimeError("send failed")))
    with pytest.raises(RuntimeError, match="send failed"):
        await delivery.send_cached_media_entries(
            message, [{"kind": "video", "path": "/tmp/clip.mp4"}],
            db_service=SimpleNamespace(add_file=AsyncMock()),
        )
    assert not thumbnail.exists()
