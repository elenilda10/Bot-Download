"""Pinterest fallback tests: public pin scope, anonymous subprocess, media parsing."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.platforms import pinterest_media as pm


def _media_record(url: str, ext: str, **metadata: str) -> list:
    return [2, url, {"extension": ext, **metadata}]


@pytest.mark.parametrize(
    "url",
    [
        "https://www.pinterest.com/pin/123456789/",
        "https://pinterest.com/pin/123456789",
        "https://m.pinterest.com/pin/123456789/",
        "https://www.pinterest.com.br/pin/123456789/",
        "https://pin.it/aB1234c",
    ],
)
def test_fallback_accepts_public_pin_links(url):
    assert pm._public_pin_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.pinterest.com/search/pins/?q=cat",
        "https://www.pinterest.com/user/board/",
        "https://evilpinterest.com/pin/12345/",
        "https://pinterest.evil.com/pin/12345/",
        "http://www.pinterest.com/pin/12345/",
        "https://evil:password@www.pinterest.com/pin/12345/",
        "https://www.pinterest.com:444/pin/12345/",
        "https://pin.it/",
        "https://pin.it/../danger",
        "https://localhost/pin/12345/",
    ],
)
def test_fallback_rejects_unrelated_or_unsafe_urls(url):
    assert not pm._public_pin_url(url)


def test_gallery_parses_pictures_videos_and_deduplicates_in_order():
    raw = "\n".join(
        json.dumps(record)
        for record in [
            [1, {"category": "pinterest"}],
            _media_record(
                "https://i.pinimg.com/originals/one.jpg", "jpg",
                title="Pinterest public pin",
            ),
            _media_record(
                "https://v1.pinimg.com/videos/two.mp4", "mp4",
            ),
            _media_record("https://i.pinimg.com/originals/one.jpg", "jpg"),
        ]
    )
    post = pm.parse_pinterest_gallery_dump(
        raw, "https://www.pinterest.com/pin/987654321/"
    )
    assert post is not None
    assert post.id == "987654321"
    assert post.description == "Pinterest public pin"
    assert [(m.type, m.url) for m in post.media_list] == [
        ("photo", "https://i.pinimg.com/originals/one.jpg"),
        ("video", "https://v1.pinimg.com/videos/two.mp4"),
    ]


def test_gallery_handles_one_json_record_without_wrapping_list():
    raw = json.dumps(_media_record("https://i.pinimg.com/picture.png", "png"))
    post = pm.parse_pinterest_gallery_dump(raw, "https://pin.it/AbCd12")
    assert post is not None
    assert len(post.media_list) == 1
    assert post.media_list[0].type == "photo"
    assert len(post.id) == 16


def test_gallery_handles_list_of_records_and_wrapped_list():
    records = [
        [1, {"category": "pinterest"}],
        _media_record("https://i.pinimg.com/picture.jpg", "jpg"),
    ]
    for raw in [json.dumps(records), json.dumps([records])]:
        post = pm.parse_pinterest_gallery_dump(raw, "https://pin.it/AbCd12")
        assert post is not None
        assert len(post.media_list) == 1


def test_gallery_blocks_non_pinterest_media_and_unsupported_types():
    raw = "\n".join(
        json.dumps(_media_record(url, ext))
        for url, ext in [
            ("http://i.pinimg.com/photo.jpg", "jpg"),
            ("https://evil.pinimg.com.attacker.test/photo.jpg", "jpg"),
            ("https://169.254.169.254/private.jpg", "jpg"),
            ("https://i.pinimg.com/animated.gif", "gif"),
            ("https://v1.pinimg.com/video.m3u8", "m3u8"),
            ("https://v1.pinimg.com/video.mp4", "mp4"),
        ]
    )
    post = pm.parse_pinterest_gallery_dump(
        raw, "https://www.pinterest.com/pin/123/"
    )
    assert post is not None
    assert len(post.media_list) == 1
    assert post.media_list[0].url == "https://v1.pinimg.com/video.mp4"


def test_gallery_does_not_fabricate_media_from_unrecognized_entries():
    raw = "\n".join([json.dumps([1, {"a": "b"}]), "NOT JSON"])
    assert pm.parse_pinterest_gallery_dump(
        raw, "https://www.pinterest.com/pin/123/"
    ) is None


def test_gallery_enforces_item_count_limit():
    records = [
        _media_record(f"https://i.pinimg.com/originals/{i}.jpg", "jpg")
        for i in range(30)
    ]
    post = pm.parse_pinterest_gallery_dump(
        json.dumps(records), "https://www.pinterest.com/pin/123/"
    )
    assert post is not None
    assert len(post.media_list) == pm._GALLERY_MAX_ITEMS


@pytest.mark.asyncio
async def test_gallery_subprocess_is_anonymous_and_uses_bounded_selection(monkeypatch):
    process = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(
            return_value=(json.dumps(
                _media_record("https://i.pinimg.com/pin.jpg", "jpg")
            ).encode(), b"")
        ),
    )
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(pm.asyncio, "create_subprocess_exec", spawn)

    result = await pm._fetch_public_pin_gallery(
        "https://www.pinterest.com/pin/555/"
    )
    assert result is not None and result.media_list[0].type == "photo"
    args = spawn.await_args.args
    assert "--config-ignore" in args
    assert "--no-input" in args
    assert "-J" in args
    assert "--range" in args
    assert "1-20" in args
    assert "--cookies" not in args
    assert "--cookies-from-browser" not in args
    assert args[-1] == "https://www.pinterest.com/pin/555/"


@pytest.mark.asyncio
async def test_gallery_never_spawns_process_for_boards_or_unrelated_websites(monkeypatch):
    spawn = AsyncMock()
    monkeypatch.setattr(pm.asyncio, "create_subprocess_exec", spawn)
    assert await pm._fetch_public_pin_gallery(
        "https://www.pinterest.com/search/pins/?q=test"
    ) is None
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_gallery_returns_none_if_binary_is_unavailable(monkeypatch):
    spawn = AsyncMock(side_effect=FileNotFoundError())
    monkeypatch.setattr(pm.asyncio, "create_subprocess_exec", spawn)
    assert await pm._fetch_public_pin_gallery(
        "https://www.pinterest.com/pin/555/"
    ) is None


@pytest.mark.asyncio
async def test_cobalt_remains_primary_and_gallery_is_not_used_when_cobalt_works(monkeypatch, tmp_path):
    async def _fetch(*args, **kwargs):
        return {
            "status": "tunnel", "url": "https://cdn.example.com/video.mp4",
            "filename": "clip.mp4",
        }

    async def _retry(*args, **kwargs):
        return None

    service = pm.PinterestMediaService(
        str(tmp_path), cobalt_api_url="https://cobalt.test",
        cobalt_api_key="fake",
        fetch_cobalt_data_func=_fetch,
        retry_async_operation_func=_retry,
    )
    backup = AsyncMock()
    monkeypatch.setattr(pm, "_fetch_public_pin_gallery", backup)

    result = await service.fetch_post("https://www.pinterest.com/pin/555/")
    assert result is not None
    assert result.media_list[0].url == "https://cdn.example.com/video.mp4"
    backup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cobalt_result", [None, {"status": "error", "error": {"code": "unknown"}}])
async def test_gallery_fallback_when_cobalt_returns_no_media(monkeypatch, tmp_path, cobalt_result):
    async def _fetch(*args, **kwargs):
        return cobalt_result

    async def _retry(*args, **kwargs):
        return None

    service = pm.PinterestMediaService(
        str(tmp_path), cobalt_api_url="https://cobalt.test",
        cobalt_api_key="fake",
        fetch_cobalt_data_func=_fetch,
        retry_async_operation_func=_retry,
    )
    expected = pm.PinterestPost(
        "555", "Recovered", [pm.PinterestMedia("https://i.pinimg.com/a.jpg", "photo")]
    )
    fallback = AsyncMock(return_value=expected)
    monkeypatch.setattr(pm, "_fetch_public_pin_gallery", fallback)

    result = await service.fetch_post("https://www.pinterest.com/pin/555/")
    assert result is expected
    fallback.assert_awaited_once_with("https://www.pinterest.com/pin/555/")


@pytest.mark.asyncio
async def test_gallery_fallback_on_cobalt_exception(monkeypatch, tmp_path):
    async def _fetch(*args, **kwargs):
        raise RuntimeError("Cobalt unavailable")

    async def _retry(*args, **kwargs):
        return None

    service = pm.PinterestMediaService(
        str(tmp_path), cobalt_api_url="https://cobalt.test",
        cobalt_api_key="fake",
        fetch_cobalt_data_func=_fetch,
        retry_async_operation_func=_retry,
    )
    expected = pm.PinterestPost(
        "555", "Recovered", [pm.PinterestMedia("https://i.pinimg.com/a.jpg", "photo")]
    )
    fallback = AsyncMock(return_value=expected)
    monkeypatch.setattr(pm, "_fetch_public_pin_gallery", fallback)
    assert await service.fetch_post("https://www.pinterest.com/pin/555/") is expected
