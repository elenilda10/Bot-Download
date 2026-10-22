"""Experimental admin-only Facebook gallery-first flow regressions."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from handlers import facebook
from services.platforms import facebook_media, universal_extractor


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "https://www.facebook.com/100044420049874/posts/1725673385589980/",
    "https://www.facebook.com/groups/137676310429643/permalink/2237506800446573/",
    "https://www.facebook.com/reel/1125782696733705/",
])
async def test_admin_gallery_first_uses_resolved_url_for_any_post(monkeypatch, url):
    gallery_result = SimpleNamespace(
        media_list=[SimpleNamespace(url="/tmp/photo.jpg", type="photo")],
        description="Foto para teste",
    )
    gallery = AsyncMock(return_value=gallery_result)
    video = AsyncMock()
    monkeypatch.setattr(universal_extractor, "_download_with_gallery_dl", gallery)
    monkeypatch.setattr(universal_extractor, "_download_with_ytdlp", video)

    result = await facebook_media.download_facebook_media(
        url, output_dir="/tmp", facebook_browser_profile="/firefox/admin",
        facebook_gallery_first_test=True,
    )
    assert result is gallery_result
    assert gallery.await_args.args[0] == url
    assert gallery.await_args.kwargs["facebook_browser_profile"] == "/firefox/admin"
    assert gallery.await_args.kwargs["facebook_gallery_test_skip_post_check"] is True
    video.assert_not_awaited()


@pytest.mark.asyncio
async def test_gallery_first_retries_video_only_when_gallery_is_empty(monkeypatch):
    gallery = AsyncMock(return_value=None)
    fallback_result = SimpleNamespace(
        media_list=[SimpleNamespace(type="video")]
    )
    video = AsyncMock(return_value=fallback_result)
    monkeypatch.setattr(universal_extractor, "_download_with_gallery_dl", gallery)
    monkeypatch.setattr(universal_extractor, "_download_with_ytdlp", video)
    url = "https://www.facebook.com/reel/1881222329921321/"
    result = await facebook_media.download_facebook_media(
        url, output_dir="/tmp", facebook_browser_profile="/firefox/admin",
        facebook_gallery_first_test=True,
    )
    assert result is fallback_result
    gallery.assert_awaited_once()
    video.assert_awaited_once()
    assert video.await_args.args[0] == url


@pytest.mark.asyncio
async def test_gallery_first_refuses_nonadmin_without_browser_profile(monkeypatch):
    gallery = AsyncMock()
    monkeypatch.setattr(universal_extractor, "_download_with_gallery_dl", gallery)
    assert await facebook_media.download_facebook_media(
        "https://www.facebook.com/100044420049874/posts/1725673385589980/",
        output_dir="/tmp", facebook_gallery_first_test=True,
    ) is None
    gallery.assert_not_awaited()


@pytest.mark.asyncio
async def test_command_only_invokes_experiment_in_admin_private_chat(monkeypatch):
    handler = AsyncMock()
    monkeypatch.setattr(facebook, "process_facebook_link", handler)
    monkeypatch.setattr(facebook, "get_message_text", lambda _:
        "/fbteste https://www.facebook.com/share/p/1GdzWY4FEU/")
    monkeypatch.setattr(facebook, "_auth_profile", lambda *a, **kw: "/firefox/admin")
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=facebook.ADMIN_ID),
        chat=SimpleNamespace(type="private"),
        answer=AsyncMock(),
    )
    await facebook.facebook_gallery_first_test_command(message)
    handler.assert_awaited_once_with(message, facebook_gallery_first_test=True)

    handler.reset_mock()
    monkeypatch.setattr(facebook, "_auth_profile", lambda *a, **kw: None)
    message.chat.type = "group"
    await facebook.facebook_gallery_first_test_command(message)
    handler.assert_not_awaited()
    assert message.answer.await_count == 1



def test_story_php_with_consistent_ids_normalizes_to_profile_post():
    assert facebook_media._canonical_fb_media_url(
        "https://www.facebook.com/story.php?story_fbid=1138762232003523"
        "&id=100076092710306&post_id=100076092710306_1138762232003523"
        "&rdid=tracking"
    ) == "https://www.facebook.com/100076092710306/posts/1138762232003523/"


def test_story_php_with_conflicting_post_id_is_not_rewritten():
    url = (
        "https://www.facebook.com/story.php?story_fbid=1138762232003523"
        "&id=100076092710306&post_id=100076092710306_999999999"
    )
    assert facebook_media._canonical_fb_media_url(url) == url

@pytest.mark.asyncio
async def test_universal_bypass_requires_both_fb_url_and_browser_profile(tmp_path):
    assert await universal_extractor._download_with_gallery_dl(
        "https://www.facebook.com/100044420049874/posts/1725673385589980/",
        str(tmp_path), "test",
        facebook_gallery_test_skip_post_check=True,
    ) is None
    assert await universal_extractor._download_with_gallery_dl(
        "https://example.com/test/post",
        str(tmp_path), "test",
        facebook_browser_profile="/firefox/admin",
        facebook_gallery_test_skip_post_check=True,
    ) is None


@pytest.mark.parametrize("legacy_setting", [None, "0", "1", "true", "yes"])
def test_unverified_gallery_first_is_never_enabled_for_normal_users(
    monkeypatch, legacy_setting,
):
    if legacy_setting is None:
        monkeypatch.delenv("FB_GALLERY_FIRST", raising=False)
    else:
        monkeypatch.setenv("FB_GALLERY_FIRST", legacy_setting)
    assert facebook._facebook_gallery_first_enabled() is False


@pytest.mark.asyncio
@pytest.mark.parametrize("browser_profile", [None, "/firefox/admin"])
async def test_legacy_gallery_first_flag_cannot_bypass_profile_post_verification(
    monkeypatch, tmp_path, browser_profile,
):
    url = "https://www.facebook.com/100044419778231/posts/1726664292157547/"
    safe_result = SimpleNamespace(
        description="Verified original post", media_list=[SimpleNamespace(type="photo")]
    )
    verified = AsyncMock(return_value=safe_result)
    gallery = AsyncMock(return_value=SimpleNamespace(
        description="Madrid,Spain", media_list=[SimpleNamespace(type="photo")]
    ))
    monkeypatch.setattr(
        facebook_media, "_download_facebook_profile_post_photos", verified,
    )
    monkeypatch.setattr(universal_extractor, "_download_with_gallery_dl", gallery)

    result = await facebook_media.download_facebook_media(
        url,
        output_dir=str(tmp_path),
        facebook_browser_profile=browser_profile,
        facebook_gallery_first=True,
    )

    assert result is safe_result
    verified.assert_awaited_once()
    assert verified.await_args.kwargs["browser_profile"] == browser_profile
    gallery.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_gallery_first_no_longer_delivers_unverified_gallery(
    monkeypatch, tmp_path,
):
    url = "https://www.facebook.com/100044419778231/posts/1726664292157547/"
    verified = AsyncMock(return_value=None)
    gallery = AsyncMock(return_value=SimpleNamespace(
        description="Madrid,Spain", media_list=[SimpleNamespace(type="photo")]
    ))
    monkeypatch.setattr(
        facebook_media, "_download_facebook_profile_post_photos", verified,
    )
    monkeypatch.setattr(universal_extractor, "_download_with_gallery_dl", gallery)

    result = await facebook_media.download_facebook_media(
        url, output_dir=str(tmp_path), facebook_gallery_first=True,
    )

    assert result is None
    verified.assert_awaited_once()
    gallery.assert_not_awaited()
