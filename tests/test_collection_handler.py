"""Test the actual coordinator without importing unrelated bot routers."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.media.collection_flow import CollectionEntry, CollectionListing

ROOT = Path(__file__).resolve().parents[1]


def load(monkeypatch):
    def stub(name, **attrs):
        module = ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    stub("app_context", bot=Mock(), db=Mock())
    lease = SimpleNamespace(mark_success=Mock(), finish=Mock())
    stub("handlers.request_dedupe", claim_message_request=AsyncMock(return_value=lease))
    user = stub("handlers.user", update_info=AsyncMock())
    ui = stub(
        "handlers.utils",
        handle_download_error=AsyncMock(),
        load_user_settings=AsyncMock(return_value={"delete_message": "on"}),
        maybe_delete_user_message=AsyncMock(),
        safe_edit_text=AsyncMock(),
        should_skip_duplicate_business_message=AsyncMock(return_value=False),
    )
    errors = {
        name: type(name, (Exception,), {})
        for name in ["DownloadQueueBusyError", "DownloadRateLimitError"]
    }
    stub("utils.download_manager", **errors)
    stub("services.logger", logger=Mock())
    spec = importlib.util.spec_from_file_location(
        "isolated_collection_handler", ROOT / "handlers/collections.py"
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module, lease, ui, user


def message(user=1, chat=10, status_id=88):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user),
        chat=SimpleNamespace(id=chat),
        business_connection_id=None,
        reply=AsyncMock(return_value=SimpleNamespace(message_id=status_id)),
    )


@pytest.mark.asyncio
async def test_collection_dispatches_each_item_in_order_and_removes_state(monkeypatch):
    module, lease, ui, user = load(monkeypatch)
    entries = [
        CollectionEntry("https://open.spotify.com/track/a", metadata={"title": "a"}),
        CollectionEntry("https://open.spotify.com/track/b", metadata={"title": "b"}),
    ]
    module.get_spotify_collection = AsyncMock(
        return_value=CollectionListing("A < B", entries)
    )
    spotify = ModuleType("handlers.spotify")
    spotify.process_spotify = AsyncMock(return_value=True)
    monkeypatch.setitem(sys.modules, "handlers.spotify", spotify)
    assert await module.process_collection(
        message(), "https://open.spotify.com/album/test", platform="Spotify", audio=True
    )
    assert [
        c.kwargs["direct_url"] for c in spotify.process_spotify.await_args_list
    ] == [e.url for e in entries]
    assert all(
        c.kwargs["collection_item"] for c in spotify.process_spotify.await_args_list
    )
    assert not module._owners and not module._jobs
    lease.mark_success.assert_called_once()
    lease.finish.assert_called_once()
    ui.maybe_delete_user_message.assert_awaited_once()
    user.update_info.assert_awaited_once()
    assert "&lt;" in ui.safe_edit_text.await_args.args[1]


@pytest.mark.asyncio
async def test_cancel_callback_checks_owner_and_message(monkeypatch):
    module, *_ = load(monkeypatch)
    job = module.Job(1, 10, None, "token")
    job.status_id = 88
    module._jobs["token"] = job
    call = SimpleNamespace(
        data="collection:cancel:token",
        message=SimpleNamespace(
            chat=SimpleNamespace(id=10), message_id=88, business_connection_id=None
        ),
        from_user=SimpleNamespace(id=2),
        answer=AsyncMock(),
    )
    await module.cancel_collection(call)
    assert not job.cancelled.is_set()
    call.from_user.id = 1
    call.message.message_id = 99
    await module.cancel_collection(call)
    assert not job.cancelled.is_set()
    call.message.message_id = 88
    await module.cancel_collection(call)
    assert job.cancelled.is_set()


@pytest.mark.asyncio
async def test_error_and_duplicate_paths_release_state(monkeypatch):
    module, lease, ui, _ = load(monkeypatch)
    module.get_youtube_collection = AsyncMock(side_effect=ValueError("No collection"))
    assert not await module.process_collection(
        message(), "https://youtube.com/playlist?list=PLabcdefgh", platform="YouTube"
    )
    assert not module._jobs and not module._owners
    ui.handle_download_error.assert_awaited_once()
    lease.finish.assert_called_once()
