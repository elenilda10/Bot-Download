"""Regression tests for the Pinterest send-video crash after successful download.

Load only the actual async send closures from the repository AST. This avoids
importing every handler through handlers/__init__.py and the optional Shazam
dependency while executing the same Telegram send statements.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_source_coroutine(relative_path: str, function_name: str, *, parent: str | None, env: dict):
    path = ROOT / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    candidates = tree.body
    if parent:
        outer = next(
            node for node in candidates
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == parent
        )
        candidates = outer.body

    target = next(
        node for node in candidates
        if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name
    )
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0
    )
    module = ast.fix_missing_locations(ast.Module(body=[future, target], type_ignores=[]))
    scope = dict(env)
    exec(compile(module, str(path), "exec"), scope)
    return scope[function_name]


def _pinterest_sender_env(*, kind="video", fail=False, thumbnail="/tmp/pin_thumb.jpg"):
    exception = RuntimeError("Telegram upload failed") if fail else None
    message = SimpleNamespace(
        answer_video=AsyncMock(
            side_effect=exception if fail else None,
            return_value=SimpleNamespace(video=SimpleNamespace(file_id="sent-video")),
        ),
        answer_photo=AsyncMock(return_value="photo-sent"),
        reply_video=AsyncMock(side_effect=AssertionError("Reply to deleted original")),
    )
    thumbnail_cleanup = AsyncMock()
    metadata = AsyncMock(return_value=(
        {"supports_streaming": True, "width": 720, "height": 1280, "duration": 19},
        thumbnail,
    ))
    scope = dict(
        media_kind=kind,
        message=message,
        post=SimpleNamespace(description="Pinterest video"),
        bot_url="https://t.me/SaveVidDLBot",
        user_settings={"captions": "on"},
        _reply_markup=lambda: "keyboard",
        bm=SimpleNamespace(captions=lambda *_args: "video caption"),
        FSInputFile=lambda path: SimpleNamespace(path=path),
        build_video_send_kwargs=metadata,
        remove_file=thumbnail_cleanup,
    )
    return scope, message, metadata, thumbnail_cleanup


@pytest.mark.asyncio
async def test_pinterest_downloaded_video_expands_only_mapping_and_cleans_thumbnail():
    env, message, metadata, cleanup = _pinterest_sender_env()
    send = _load_source_coroutine(
        "handlers/pinterest.py", "_send_downloaded",
        parent="process_pinterest_single_media", env=env,
    )
    sent = await send("/tmp/pinterest_video.mp4")
    assert sent.video.file_id == "sent-video"
    metadata.assert_awaited_once_with("/tmp/pinterest_video.mp4")
    message.answer_video.assert_awaited_once()
    kwargs = message.answer_video.await_args.kwargs
    assert kwargs["video"].path == "/tmp/pinterest_video.mp4"
    assert kwargs["supports_streaming"] is True
    assert kwargs["width"] == 720
    assert kwargs["height"] == 1280
    assert kwargs["duration"] == 19
    assert kwargs["allow_sending_without_reply"] is True
    message.reply_video.assert_not_awaited()
    cleanup.assert_awaited_once_with("/tmp/pin_thumb.jpg")


@pytest.mark.asyncio
async def test_pinterest_video_cleans_thumbnail_when_telegram_send_fails():
    env, message, _, cleanup = _pinterest_sender_env(fail=True)
    send = _load_source_coroutine(
        "handlers/pinterest.py", "_send_downloaded",
        parent="process_pinterest_single_media", env=env,
    )
    with pytest.raises(RuntimeError, match="upload failed"):
        await send("/tmp/pinterest_video.mp4")
    message.answer_video.assert_awaited_once()
    cleanup.assert_awaited_once_with("/tmp/pin_thumb.jpg")


@pytest.mark.asyncio
async def test_pinterest_photo_uses_normal_photo_send_without_video_metadata():
    env, message, metadata, cleanup = _pinterest_sender_env(kind="photo")
    send = _load_source_coroutine(
        "handlers/pinterest.py", "_send_downloaded",
        parent="process_pinterest_single_media", env=env,
    )
    await send("/tmp/pinterest_photo.jpg")
    message.answer_photo.assert_awaited_once()
    assert message.answer_photo.await_args.kwargs["photo"].path.endswith(".jpg")
    message.answer_video.assert_not_awaited()
    metadata.assert_not_awaited()
    cleanup.assert_not_awaited()


@pytest.mark.asyncio
async def test_inline_video_unpacks_metadata_tuple_and_registers_thumbnail_cleanup():
    metadata = AsyncMock(return_value=(
        {
            "supports_streaming": True,
            "width": 640,
            "height": 1136,
            "duration": 13,
            "thumbnail": "thumb_input",
        },
        "/tmp/inline_thumb.jpg",
    ))
    send_video = AsyncMock(return_value=SimpleNamespace(
        video=SimpleNamespace(file_id="uploaded-file-id")
    ))
    db = SimpleNamespace(
        get_file_id=AsyncMock(return_value=None),
        add_file=AsyncMock(),
    )
    cleanup_paths = []
    state = SimpleNamespace(
        register_download_path=lambda path: cleanup_paths.append(path),
    )
    completed = Mock()
    raw = dict(
        _handler_utils=lambda: SimpleNamespace(),
        logging=SimpleNamespace(info=Mock(), error=Mock()),
        VIDEO_TOO_LARGE=object(),
        reset_inline_video_request=Mock(),
        complete_inline_video_request=completed,
        bm=SimpleNamespace(
            downloading_video_status=lambda **kw: "downloading",
            uploading_status=lambda **kw: "uploading",
            something_went_wrong=lambda **kw: "failed",
            video_too_large=lambda **kw: "too large",
        ),
        FSInputFile=lambda path: SimpleNamespace(path=path),
        build_video_send_kwargs=metadata,
        _optimize_video_for_streaming=Mock(),
        summarize_url_for_log=lambda url: url,
        log_download_metrics=Mock(),
        types=SimpleNamespace(InputMediaVideo=lambda **kwargs: kwargs),
    )
    deliver = _load_source_coroutine(
        "services/inline/send_flow.py",
        "deliver_inline_video", parent=None, env=raw,
    )
    deps = SimpleNamespace(
        bot=SimpleNamespace(send_video=send_video), db=db
    )
    edit_status = AsyncMock()
    edit_inline_media = AsyncMock(return_value=True)
    caption_builder = AsyncMock(return_value="Pinterest clip")
    download = AsyncMock(return_value=SimpleNamespace(
        path="/tmp/pin.mp4", size=100,
    ))
    await deliver(
        deps=deps,
        token="abc",
        inline_message_id="inline-1",
        channel_id=-1001234,
        max_file_size=1000000,
        service_name="Pinterest",
        cache_key="pinterest-inline-video",
        channel_caption="Cache video",
        download_fn=download,
        progress_label="Pinterest",
        build_caption=caption_builder,
        reply_markup=None,
        edit_status=edit_status,
        state=state,
        safe_edit_inline_media_fn=edit_inline_media,
        log=SimpleNamespace(info=Mock(), error=Mock()),
    )
    metadata.assert_awaited_once_with("/tmp/pin.mp4")
    send_video.assert_awaited_once()
    kwargs = send_video.await_args.kwargs
    assert kwargs["width"] == 640
    assert kwargs["height"] == 1136
    assert kwargs["duration"] == 13
    assert kwargs["supports_streaming"] is True
    assert kwargs["thumbnail"] == "thumb_input"
    assert cleanup_paths == ["/tmp/pin.mp4", "/tmp/inline_thumb.jpg"]
    db.add_file.assert_awaited_once_with(
        "pinterest-inline-video", "uploaded-file-id", "video"
    )
    edit_inline_media.assert_awaited_once()
    completed.assert_called_once_with("abc")
