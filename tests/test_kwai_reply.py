"""Exercise actual Kwai send flows without Telegram, bot secrets or a DB."""
import ast
import asyncio
import hashlib
import os
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

HANDLER = Path(__file__).resolve().parents[1] / "handlers/kwai.py"


class Message:
    def __init__(self, *, chat_id=-123, user_id=2, message_id=91):
        self.message_id = message_id
        self.chat = SimpleNamespace(id=chat_id, type="supergroup")
        self.from_user = SimpleNamespace(id=user_id)
        self.business_connection_id = None
        self.text = "https://www.kwai.com/@user/video/5200118832330956026"
        self.reply_markup = None
        self.reply_to_message = None
        self.sends = []

    async def answer(self, text, **kwargs):
        return SimpleNamespace(message_id=100)

    async def _send(self, kind, **kwargs):
        self.sends.append((kind, kwargs))
        return SimpleNamespace(**{kind: SimpleNamespace(file_id="sent-" + kind)})

    async def answer_video(self, **kwargs):
        return await self._send("video", **kwargs)

    async def answer_document(self, **kwargs):
        return await self._send("document", **kwargs)

    async def answer_audio(self, **kwargs):
        return await self._send("audio", **kwargs)


def runtime(tmp_path, monkeypatch, *, cached, as_document=False):
    monkeypatch.setenv("KWAI_FIREFOX_PROFILE", "/synthetic/profile")
    tree = ast.parse(HANDLER.read_text())
    nodes = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.decorator_list = []
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in {"_KWAI_LINK_REGEX", "_KWAI_CALLBACK_HASH"}
            for t in node.targets
        ):
            nodes.append(node)

    source = tmp_path / "source.mp4"
    source.write_bytes(b"test-video")
    result = SimpleNamespace(
        description="Original caption", author="Original author",
        media_list=[SimpleNamespace(url=str(source), type="video", thumb=None,
                                    width=576, height=1024, duration=9)],
    )
    db = SimpleNamespace(
        get_file_id=AsyncMock(return_value="cached-file" if cached else None),
        get_language=AsyncMock(return_value="pt"), add_file=AsyncMock(),
    )
    lease = SimpleNamespace(mark_success=Mock(), finish=Mock())
    settings = {"as_document": "on" if as_document else "off", "captions": "on"}

    async def audio(send, **kwargs):
        for helper in ("bot_avatar", "bot_url", "audio_path"):
            kwargs.pop(helper, None)
        return await send(**kwargs)

    async def ffmpeg(*args, **kwargs):
        Path(args[-1]).write_bytes(b"test-mp3")
        return SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"", b"")), kill=Mock())

    namespace = dict(
        asyncio=SimpleNamespace(create_subprocess_exec=ffmpeg, wait_for=asyncio.wait_for,
                                subprocess=asyncio.subprocess, TimeoutError=TimeoutError),
        os=os, re=re, uuid=uuid, hashlib=hashlib, Path=Path,
        types=SimpleNamespace(Message=Message),
        FSInputFile=lambda path, **kwargs: str(path),
        TelegramBadRequest=type("TelegramBadRequest", (Exception,), {}),
        ParseMode=SimpleNamespace(HTML="HTML"),
        ChatAction=SimpleNamespace(UPLOAD_VIDEO="upload_video"),
        build_media_cache_key=lambda base, *, variant: base + ":" + variant,
        db=db, bot=object(), logging=Mock(), OUTPUT_DIR=str(tmp_path), MAX_FILE_SIZE=1024,
        cmd_mod=SimpleNamespace(is_banned=AsyncMock(return_value=False)),
        bm=SimpleNamespace(captions=lambda *a: "caption",
                           downloading_video_status=lambda **k: "downloading",
                           downloading_audio_status=lambda **k: "downloading",
                           uploading_status=lambda **k: "uploading"),
        get_message_text=lambda m: m.text,
        summarize_url_for_log=lambda url: "kwai:test",
        is_kwai_url=lambda url: url.startswith("https://www.kwai.com/"),
        format_kwai_post_caption=lambda text, author: text,
        get_bot_url=AsyncMock(return_value="https://t.me/testbot"),
        load_user_settings=AsyncMock(return_value=settings),
        should_skip_duplicate_business_message=AsyncMock(return_value=False),
        claim_message_request=AsyncMock(return_value=lease),
        get_bot_avatar_thumbnail=AsyncMock(return_value=None),
        send_audio_with_thumbnail=audio,
    )
    for name in ("send_analytics", "react_to_message", "send_chat_action_if_needed",
                 "handle_download_error", "handle_video_too_large", "safe_edit_text",
                 "safe_delete_message", "maybe_delete_user_message", "remove_file"):
        namespace[name] = AsyncMock()
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(module, str(HANDLER), "exec"), namespace)
    namespace["_download"] = AsyncMock(return_value=result)
    namespace["_kwai_keyboard"] = lambda *a: SimpleNamespace(inline_keyboard=[])
    return namespace


@pytest.mark.asyncio
@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("as_document", [False, True])
async def test_new_and_cached_media_reply_to_each_original_link(tmp_path, monkeypatch, cached, as_document):
    ns = runtime(tmp_path, monkeypatch, cached=cached, as_document=as_document)
    first = Message(message_id=91)
    second = Message(message_id=92)
    await ns["process_kwai_link"](first)
    await ns["process_kwai_link"](second)
    for message in (first, second):
        kind, kwargs = message.sends[-1]
        assert kind == ("document" if as_document else "video")
        assert kwargs["reply_to_message_id"] == message.message_id
        assert kwargs["allow_sending_without_reply"] is True
    ns["logging"].exception.assert_not_called()
    if not cached:
        assert ns["_download"].await_args.args[1] == "/synthetic/profile"
    keys = [call.args[0] for call in ns["db"].get_file_id.await_args_list]
    assert keys and all("owner:2:chat:-123" in key for key in keys)


@pytest.mark.asyncio
@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("kind", ["document", "audio"])
async def test_callback_media_reply_to_video_in_same_chat(tmp_path, monkeypatch, cached, kind):
    ns = runtime(tmp_path, monkeypatch, cached=cached)
    message = Message(message_id=191)
    token = ns["_url_hash"](message.text)
    message.reply_markup = SimpleNamespace(inline_keyboard=[[
        SimpleNamespace(url=message.text),
    ]])
    call = SimpleNamespace(message=message, from_user=SimpleNamespace(id=2),
                           data=("doc:kwai:" if kind == "document" else "audio:kwai:") + token,
                           answer=AsyncMock())
    await ns["_callback_request"](call, kind)
    sent_kind, kwargs = message.sends[-1]
    assert sent_kind == kind
    assert kwargs["reply_to_message_id"] == 191
    assert kwargs["allow_sending_without_reply"] is True
    ns["logging"].exception.assert_not_called()
    cache_lookup = ns["db"].get_file_id.await_args.args[0]
    assert "owner:2:chat:-123" in cache_lookup


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [1, 2, 10, 11])
@pytest.mark.parametrize('as_document', [False, True])
async def test_photos_and_mixed_album_split_reply_and_cleanup(tmp_path, monkeypatch, count, as_document):
    ns = runtime(tmp_path, monkeypatch, cached=False, as_document=as_document)
    result = ns['_download'].return_value
    ns['remove_file'] = AsyncMock(side_effect=lambda path: Path(path).unlink(missing_ok=True))
    if count == 1:
        result.media_list = []
    for index in range(count - len(result.media_list)):
        path = tmp_path / f'photo{index}.jpg'
        path.write_bytes(b'photo')
        result.media_list.append(SimpleNamespace(url=str(path), type='photo', thumb=None))
    def entry(**kwargs): return kwargs
    ns['types'].InputMediaPhoto = entry
    ns['types'].InputMediaVideo = entry
    ns['types'].InputMediaDocument = entry
    message = Message()
    message.answer_photo = AsyncMock(return_value=SimpleNamespace())
    message.answer_media_group = AsyncMock(side_effect=lambda **k: [SimpleNamespace() for _ in k['media']])
    await ns['process_kwai_link'](message)
    ns['logging'].exception.assert_not_called()
    ns['handle_download_error'].assert_not_awaited()
    if count >= 2:
        groups = message.answer_media_group.await_args_list
        assert len(groups) == 1
        assert len(groups[0].kwargs['media']) == min(count, 10)
        assert groups[0].kwargs['reply_to_message_id'] == message.message_id
        assert groups[0].kwargs['media'][0]['caption'] == 'caption'
        if count == 11 and not as_document:
            assert message.answer_photo.await_args.kwargs['caption'] is None
    elif not as_document:
        message.answer_photo.assert_awaited_once()
    assert not any(Path(item.url).exists() for item in result.media_list)
