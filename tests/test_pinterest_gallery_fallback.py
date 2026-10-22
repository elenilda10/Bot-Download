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
        "https://br.pinterest.com/pin/123456789/",
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


def test_gallery_uses_directory_caption_for_single_pin_photo():
    """gallery-dl emits the Pinterest Pin's original text in a type-1 record."""
    raw = json.dumps([
        [1, "", {
            "description": "Legenda original da imagem #Pinterest",
            "title": "Um título menor",
            "id": "1234567890123",
        }],
        _media_record("https://i.pinimg.com/originals/photo.jpg", "jpg"),
    ])
    post = pm.parse_pinterest_gallery_dump(
        raw, "https://www.pinterest.com/pin/1234567890123/"
    )
    assert post is not None
    assert post.description == "Legenda original da imagem #Pinterest"
    assert [m.type for m in post.media_list] == ["photo"]


def test_gallery_uses_one_original_caption_for_complete_carousel():
    """Story/carousel metadata precedes all downloaded image and video entries."""
    raw = json.dumps([
        [1, "", {
            "title": "Álbum de viagem",
            "closeup_unified_description": "Legenda completa do álbum 🌴",
        }],
        _media_record("https://i.pinimg.com/a.jpg", "jpg"),
        _media_record("https://i.pinimg.com/b.png", "png"),
        _media_record("https://v1.pinimg.com/c.mp4", "mp4"),
    ])
    post = pm.parse_pinterest_gallery_dump(
        raw, "https://www.pinterest.com/pin/1122334455667/"
    )
    assert post is not None
    assert post.description == "Legenda completa do álbum 🌴"
    assert [m.type for m in post.media_list] == ["photo", "photo", "video"]


def test_gallery_caption_falls_back_to_file_metadata_or_title():
    raw = json.dumps([
        [1, "", {"description": " "}],
        _media_record(
            "https://i.pinimg.com/picture.jpg", "jpg", title="Foto com título"
        ),
    ])
    post = pm.parse_pinterest_gallery_dump(
        raw, "https://www.pinterest.com/pin/1122334455667/"
    )
    assert post is not None
    assert post.description == "Foto com título"


def test_gallery_redirect_to_canonical_pin_strips_tracking_and_rejects_other_sites():
    redirect = json.dumps([
        [6, "https://www.pinterest.com/pin/987654321/sent/?invite_code=PRIVATE&sender=18", {}]
    ])
    assert pm._canonical_pin_from_gallery_output(redirect) == (
        "https://www.pinterest.com/pin/987654321/"
    )
    assert pm._canonical_pin_from_gallery_output(json.dumps([
        [6, "https://www.pinterest.com.evil.test/pin/987654321/", {}]
    ])) is None
    assert pm._canonical_pin_from_gallery_output(json.dumps([
        [6, "http://www.pinterest.com/pin/987654321/", {}]
    ])) is None
    assert pm._canonical_pin_from_gallery_output(json.dumps([
        [6, "https://www.pinterest.com/user/board/", {}]
    ])) is None


@pytest.mark.asyncio
async def test_pin_it_redirect_recovers_original_photo_caption_and_media(monkeypatch):
    """A short link first yields a redirect; canonical -J yields caption + file."""
    def proc(data):
        return SimpleNamespace(
            returncode=0,
            communicate=AsyncMock(return_value=(data, b"")),
        )

    redirect = json.dumps([
        [6, "https://www.pinterest.com/pin/987654321/sent/?invite_code=secret&sender=123", {}],
    ]).encode()
    detail = json.dumps([
        [1, "", {"description": "Legenda original do Pin"}],
        _media_record("https://i.pinimg.com/originals/pin.jpg", "jpg"),
    ]).encode()
    spawn = AsyncMock(side_effect=[proc(redirect), proc(detail)])
    monkeypatch.setattr(pm.asyncio, "create_subprocess_exec", spawn)

    result = await pm._fetch_public_pin_gallery("https://pin.it/5Qs3W9mxR")
    assert result is not None
    assert result.id == "987654321"
    assert result.description == "Legenda original do Pin"
    assert result.media_list[0].url == "https://i.pinimg.com/originals/pin.jpg"
    assert spawn.await_count == 2
    assert spawn.await_args_list[1].args[-1] == (
        "https://www.pinterest.com/pin/987654321/"
    )
    assert "-G" not in spawn.await_args_list[1].args


@pytest.mark.asyncio
async def test_pin_it_album_captions_recovered_from_canonical_gallery(monkeypatch):
    def proc(data):
        return SimpleNamespace(
            returncode=0,
            communicate=AsyncMock(return_value=(data, b"")),
        )

    redirect = json.dumps([
        [6, "https://br.pinterest.com/pin/987654321/sent/?code=short", {}],
    ]).encode()
    detail = json.dumps([
        [1, "", {"description": "Todas as imagens deste álbum"}],
        _media_record("https://i.pinimg.com/a.jpg", "jpg"),
        _media_record("https://i.pinimg.com/b.jpg", "jpg"),
    ]).encode()
    spawn = AsyncMock(side_effect=[proc(redirect), proc(detail)])
    monkeypatch.setattr(pm.asyncio, "create_subprocess_exec", spawn)
    post = await pm._fetch_public_pin_gallery("https://pin.it/5Qs3W9mxR")
    assert post is not None
    assert post.id == "987654321"
    assert post.description == "Todas as imagens deste álbum"
    assert len(post.media_list) == 2
    assert all(m.type == "photo" for m in post.media_list)


@pytest.mark.asyncio
async def test_shortlink_falls_back_to_media_urls_when_canonical_metadata_unavailable(monkeypatch):
    """A missing Pinterest caption must never prevent a successful download."""
    def proc(data):
        return SimpleNamespace(
            returncode=0,
            communicate=AsyncMock(return_value=(data, b"")),
        )

    redirect = json.dumps([
        [6, "https://www.pinterest.com/pin/987654321/sent/?sfo=1", {}],
    ]).encode()
    spawn = AsyncMock(side_effect=[
        proc(redirect), proc(b""), proc(b"https://i.pinimg.com/originals/foto.jpg\n"),
    ])
    monkeypatch.setattr(pm.asyncio, "create_subprocess_exec", spawn)

    post = await pm._fetch_public_pin_gallery("https://pin.it/5Qs3W9mxR")
    assert post is not None
    assert post.media_list[0].type == "photo"
    assert post.description == ""
    assert spawn.await_count == 3
    assert "-G" in spawn.await_args_list[2].args


def test_gallery_url_mode_accepts_only_direct_pinimg_media():
    post = pm.parse_pinterest_gallery_urls(
        "\n".join([
            "https://www.pinterest.com/pin/123/sent/?code=secret",
            "https://i.pinimg.com/originals/photo.jpg?token=test",
            "https://v1.pinimg.com/videos/video.mp4",
            "https://i.pinimg.com/originals/photo.jpg?token=test",
            "https://evil.pinimg.com.example.com/originals/attack.jpg",
            "https://i.pinimg.com/originals/animation.gif",
            "| https://untrusted.example/fallback.jpg",
        ]),
        "https://pin.it/5Qs3W9mxR",
    )
    assert post is not None
    assert [m.type for m in post.media_list] == ["photo", "video"]
    assert len(post.media_list) == 2


def test_gallery_url_mode_rejects_only_shortlink_redirect():
    assert pm.parse_pinterest_gallery_urls(
        "https://www.pinterest.com/pin/123/sent/?code=abc",
        "https://pin.it/5Qs3W9mxR",
    ) is None


@pytest.mark.asyncio
async def test_pin_it_json_redirect_recovers_jpg_via_resolved_urls(monkeypatch):
    """Regression: direct gallery-dl downloads JPG but JSON has only type 6."""
    def fake_process(data: bytes, status: int = 0, stderr: bytes = b""):
        return SimpleNamespace(
            returncode=status,
            communicate=AsyncMock(return_value=(data, stderr)),
        )

    redirect = json.dumps([
        [6, "https://www.pinterest.com/pin/987654321/sent/?code=sample", {}],
    ]).encode("utf-8")
    metadata = fake_process(redirect)
    resolved = fake_process(
        b"https://i.pinimg.com/originals/photo.jpg\n"
    )
    # The canonical metadata attempt can fail independently from URL
    # resolution, in which case we still recover the actual picture via -G.
    canonical_failed = fake_process(b"", 1)
    spawn = AsyncMock(side_effect=[metadata, canonical_failed, resolved])
    monkeypatch.setattr(pm.asyncio, "create_subprocess_exec", spawn)

    post = await pm._fetch_public_pin_gallery(
        "https://pin.it/5Qs3W9mxR"
    )
    assert post is not None
    assert len(post.media_list) == 1
    assert post.media_list[0].type == "photo"
    assert post.media_list[0].url == "https://i.pinimg.com/originals/photo.jpg"
    args = [call.args for call in spawn.await_args_list]
    assert len(args) == 3
    assert "-J" in args[0]
    assert "-J" in args[1]
    assert args[1][-1] == "https://www.pinterest.com/pin/987654321/"
    assert "-G" in args[2]
    assert all("--no-input" in a for a in args)
    assert all("--cookies" not in a for a in args)


@pytest.mark.asyncio
async def test_gallery_supports_older_config_flag(monkeypatch):
    def fake_process(data: bytes, status: int = 0, stderr: bytes = b""):
        return SimpleNamespace(
            returncode=status,
            communicate=AsyncMock(return_value=(data, stderr)),
        )

    spawn = AsyncMock(side_effect=[
        fake_process(b"", 2, b"error: unrecognized arguments: --config-ignore"),
        fake_process(
            json.dumps(_media_record(
                "https://i.pinimg.com/originals/photo.jpg", "jpg"
            )).encode()
        ),
    ])
    monkeypatch.setattr(pm.asyncio, "create_subprocess_exec", spawn)
    result = await pm._fetch_public_pin_gallery(
        "https://pin.it/5Qs3W9mxR"
    )
    assert result is not None
    assert result.media_list[0].type == "photo"
    assert "--config-ignore" in spawn.await_args_list[0].args
    assert "--ignore-config" in spawn.await_args_list[1].args


@pytest.mark.asyncio
async def test_gallery_redirect_resolution_never_returns_untrusted_webpage(monkeypatch):
    def fake_process(data: bytes):
        return SimpleNamespace(
            returncode=0,
            communicate=AsyncMock(return_value=(data, b"")),
        )
    spawn = AsyncMock(side_effect=[
        fake_process(json.dumps([
            [6, "https://pinterest.com/pin/123", {}]
        ]).encode()),
        fake_process(b"https://pinterest.com/pin/123\nhttps://169.254.169.254/image.jpg\n"),
    ])
    monkeypatch.setattr(pm.asyncio, "create_subprocess_exec", spawn)
    result = await pm._fetch_public_pin_gallery(
        "https://pin.it/5Qs3W9mxR"
    )
    assert result is None


@pytest.mark.asyncio
async def test_gallery_uses_executable_next_to_venv_python(monkeypatch, tmp_path):
    gallery_bin = tmp_path / "gallery-dl"
    gallery_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.delenv("PINTEREST_GALLERY_DL_BIN", raising=False)
    monkeypatch.setattr(pm.sys, "executable", str(tmp_path / "python3"))
    process = SimpleNamespace(
        returncode=1,
        communicate=AsyncMock(return_value=(b"", b"")),
    )
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(pm.asyncio, "create_subprocess_exec", spawn)
    assert await pm._fetch_public_pin_gallery(
        "https://www.pinterest.com/pin/555/"
    ) is None
    assert spawn.await_args.args[0] == str(gallery_bin)


@pytest.mark.asyncio
async def test_gallery_honors_explicit_binary_override(monkeypatch):
    monkeypatch.setenv("PINTEREST_GALLERY_DL_BIN", "/opt/gallery/bin/gallery-dl")
    process = SimpleNamespace(
        returncode=1,
        communicate=AsyncMock(return_value=(b"", b"")),
    )
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(pm.asyncio, "create_subprocess_exec", spawn)
    assert await pm._fetch_public_pin_gallery(
        "https://www.pinterest.com/pin/555/"
    ) is None
    assert spawn.await_args.args[0] == "/opt/gallery/bin/gallery-dl"


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
async def test_cobalt_remains_fallback_after_local_extractors_fail(monkeypatch, tmp_path):
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
    gallery = AsyncMock(return_value=None)
    video = AsyncMock(return_value=None)
    monkeypatch.setattr(pm, "_fetch_public_pin_gallery", gallery)
    monkeypatch.setattr(pm, "_fetch_public_pin_yt_dlp", video)

    url = "https://www.pinterest.com/pin/555/"
    result = await service.fetch_post(url)
    assert result is not None
    assert result.media_list[0].url == "https://cdn.example.com/video.mp4"
    gallery.assert_awaited_once_with(url)
    video.assert_awaited_once_with(url)


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
    monkeypatch.setattr(pm, "_fetch_public_pin_yt_dlp", AsyncMock(return_value=None))
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
    monkeypatch.setattr(pm, "_fetch_public_pin_yt_dlp", AsyncMock(return_value=None))
    monkeypatch.setattr(pm, "_fetch_public_pin_gallery", fallback)
    assert await service.fetch_post("https://www.pinterest.com/pin/555/") is expected
