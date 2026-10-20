"""Service-level Facebook extraction routing and privacy regressions."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.platforms import facebook_media, universal_extractor


REEL = "https://www.facebook.com/reel/2480096172822069/"
GROUP = "https://www.facebook.com/groups/256772258373648/permalink/2061916421192547/"


def test_facebook_service_rejects_fake_hosts_and_profile_feeds():
    assert facebook_media._is_facebook_url(REEL)
    assert not facebook_media._is_facebook_url(
        "https://facebook.com.evil.example/reel/2480096172822069/"
    )
    assert not facebook_media._is_facebook_media_link(
        "https://www.facebook.com/some-profile"
    )
    assert facebook_media._facebook_group_post_id(GROUP) == "2061916421192547"


@pytest.mark.asyncio
async def test_universal_delegates_facebook_to_specialized_service(monkeypatch, tmp_path):
    downstream = AsyncMock(return_value=None)
    monkeypatch.setattr(universal_extractor, "download_facebook_media", downstream)
    assert await universal_extractor.download_universal_media(
        REEL,
        output_dir=str(tmp_path),
        facebook_browser_profile="/home/firefoxbot/private-profile",
    ) is None
    downstream.assert_awaited_once()
    assert downstream.await_args.kwargs["facebook_browser_profile"] == (
        "/home/firefoxbot/private-profile"
    )


@pytest.mark.asyncio
async def test_facebook_reel_calls_shared_ytdlp_with_explicit_auth(monkeypatch, tmp_path):
    media = SimpleNamespace(media_list=[SimpleNamespace(type="video")])
    downloader = AsyncMock(return_value=media)
    monkeypatch.setattr(universal_extractor, "_download_with_ytdlp", downloader)
    gallery = AsyncMock(return_value=None)
    monkeypatch.setattr(universal_extractor, "_download_with_gallery_dl", gallery)

    result = await facebook_media.download_facebook_media(
        REEL, output_dir=str(tmp_path),
        facebook_browser_profile="/home/firefoxbot/private-profile",
    )

    assert result is media
    assert downloader.await_args.kwargs["facebook_browser_profile"] == (
        "/home/firefoxbot/private-profile"
    )
    gallery.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_post_rejects_anonymous_request_without_extraction(
    monkeypatch, tmp_path,
):
    downloader = AsyncMock()
    monkeypatch.setattr(universal_extractor, "_download_with_gallery_dl", downloader)
    assert await facebook_media.download_facebook_media(
        GROUP, output_dir=str(tmp_path), facebook_browser_profile=None,
    ) is None
    downloader.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_post_routes_to_gallery_with_explicit_admin_profile(
    monkeypatch, tmp_path,
):
    media = SimpleNamespace(media_list=[SimpleNamespace(type="photo")])
    gallery = AsyncMock(return_value=media)
    monkeypatch.setattr(universal_extractor, "_download_with_gallery_dl", gallery)
    assert await facebook_media.download_facebook_media(
        GROUP, output_dir=str(tmp_path),
        facebook_browser_profile="/home/firefoxbot/private-profile",
    ) is media
    gallery.assert_awaited_once()
    assert gallery.await_args.kwargs["facebook_browser_profile"] == (
        "/home/firefoxbot/private-profile"
    )


def test_facebook_tool_args_never_enable_cookies_by_default():
    assert not any(
        "cookies" in token for token in facebook_media.facebook_gallery_arguments()
    )
    assert not any(
        "cookies" in token for token in facebook_media.facebook_ytdlp_arguments()
    )
    assert "--cookies-from-browser" in facebook_media.facebook_gallery_arguments(
        "/private/firefox-profile"
    )
    assert "--cookies-from-browser" in facebook_media.facebook_ytdlp_arguments(
        "/private/firefox-profile"
    )



def test_video_caption_uses_author_description_before_generic_title():
    assert facebook_media.facebook_video_caption({
        "title": "Video",
        "description": "Texto original do Reel 🔥",
    }) == "Texto original do Reel 🔥"
    assert facebook_media.facebook_video_caption({
        "caption": "  Legenda do criador\n",
        "description": "Outra descrição",
        "title": "Video",
    }) == "Legenda do criador"
    assert facebook_media.facebook_video_caption({
        "title": "Video",
        "description": "",
    }) == ""
    assert facebook_media.facebook_video_caption({
        "title": "Facebook video #2480096172822069",
    }) == ""
    assert facebook_media.facebook_video_caption({
        "title": "Título real",
    }) == "Título real"



@pytest.mark.asyncio
async def test_direct_facebook_photo_slash_url_works_with_admin_profile(
    monkeypatch, tmp_path,
):
    photo_path = tmp_path / "1528748809298491.jpg"
    photo_path.write_bytes(b"fake-photo")
    result = SimpleNamespace(media_list=[SimpleNamespace(
        type="photo", url=str(photo_path),
    )])
    gallery = AsyncMock(return_value=result)
    monkeypatch.setattr(universal_extractor, "_download_with_gallery_dl", gallery)
    url = "https://www.facebook.com/photo/?fbid=1528748809298491&set="
    assert await facebook_media.download_facebook_media(
        url, output_dir=str(tmp_path),
        facebook_browser_profile="/home/firefoxbot/private-profile",
    ) is result
    gallery.assert_awaited_once()
    assert gallery.await_args.args[0] == url
    assert gallery.await_args.kwargs["facebook_browser_profile"] == (
        "/home/firefoxbot/private-profile"
    )


@pytest.mark.asyncio
async def test_direct_facebook_photo_slash_url_works_without_admin_cookies(
    monkeypatch, tmp_path,
):
    photo_path = tmp_path / "1528748809298491.jpg"
    photo_path.write_bytes(b"fake-photo")
    result = SimpleNamespace(media_list=[SimpleNamespace(
        type="photo", url=str(photo_path),
    )])
    gallery = AsyncMock(return_value=result)
    monkeypatch.setattr(universal_extractor, "_download_with_gallery_dl", gallery)
    url = "https://www.facebook.com/photo/?fbid=1528748809298491&set="
    assert await facebook_media.download_facebook_media(
        url, output_dir=str(tmp_path), facebook_browser_profile=None,
    ) is result
    gallery.assert_awaited_once()
    assert "facebook_browser_profile" not in gallery.await_args.kwargs


@pytest.mark.asyncio
async def test_direct_photo_rejects_different_downloaded_photo_id(
    monkeypatch, tmp_path,
):
    wrong_path = tmp_path / "1162826316324741.jpg"
    wrong_path.write_bytes(b"fake-photo")
    result = SimpleNamespace(media_list=[SimpleNamespace(
        type="photo", url=str(wrong_path),
    )])
    gallery = AsyncMock(return_value=result)
    monkeypatch.setattr(universal_extractor, "_download_with_gallery_dl", gallery)
    url = "https://www.facebook.com/photo/?fbid=1528748809298491&set="
    assert await facebook_media.download_facebook_media(
        url, output_dir=str(tmp_path),
        facebook_browser_profile="/home/firefoxbot/private-profile",
    ) is None
