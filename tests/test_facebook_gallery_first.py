"""Experimental admin-only Facebook gallery-first flow regressions."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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



@pytest.mark.asyncio
@pytest.mark.parametrize("profile", [None, "/firefox/admin"])
async def test_standard_gallery_first_respects_cookie_scope(monkeypatch, profile):
    url = "https://www.facebook.com/100044420049874/posts/1725673385589980/"
    fake = SimpleNamespace(
        media_list=[SimpleNamespace(type="photo", url="/tmp/test.jpg")],
        description="Foto",
    )
    gallery = AsyncMock(return_value=fake)
    monkeypatch.setattr(universal_extractor, "_download_with_gallery_dl", gallery)
    monkeypatch.setattr(universal_extractor, "_download_with_ytdlp", AsyncMock())
    result = await facebook_media.download_facebook_media(
        url, output_dir="/tmp",
        facebook_gallery_first=True,
        facebook_browser_profile=profile,
    )
    assert result is fake
    assert gallery.await_args.kwargs["facebook_browser_profile"] == profile
    assert gallery.await_args.kwargs["facebook_gallery_public_unverified"] == (profile is None)
    assert gallery.await_args.kwargs["facebook_gallery_test_skip_post_check"] == bool(profile)


def test_gallery_first_rejects_obviously_wrong_album_and_login_screen(tmp_path):
    import json

    path = tmp_path / "154.jpg"
    path.write_bytes(b"image")
    url = "https://www.facebook.com/groups/137676310429643/permalink/2237506800446573/"
    meta = {
        "id": "154", "set_id": "pcb.999999",
        "post_photo": "https://www.facebook.com/photo/?fbid=154",
    }
    (tmp_path / "154.jpg.json").write_text(json.dumps(meta), encoding="utf-8")
    assert facebook_media._facebook_gallery_first_conflicts_with_post(
        url, [str(path)],
    )

    meta["set_id"] = "pcb.2237506800446573"
    (tmp_path / "154.jpg.json").write_text(json.dumps(meta), encoding="utf-8")
    assert not facebook_media._facebook_gallery_first_conflicts_with_post(
        url, [str(path)],
    )

    meta["post_text"] = "Remember password"
    (tmp_path / "154.jpg.json").write_text(json.dumps(meta), encoding="utf-8")
    assert facebook_media._facebook_gallery_first_conflicts_with_post(
        url, [str(path)],
    )


@pytest.mark.asyncio
async def test_normal_handler_gallery_first_album_has_no_progress_or_extra_url(monkeypatch, tmp_path):
    monkeypatch.setenv("FB_GALLERY_FIRST", "1")
    monkeypatch.setattr(facebook, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(facebook, "_create_callback_token", AsyncMock(return_value="a" * 24))
    old_cache = AsyncMock()
    monkeypatch.setattr(facebook, "_load_cache", old_cache)
    fake_db = SimpleNamespace(
        get_language=AsyncMock(return_value="pt"),
        add_file=AsyncMock(),
    )
    monkeypatch.setattr(facebook, "db", fake_db)
    monkeypatch.setattr(facebook, "_resolve_facebook_short_link", AsyncMock(
        return_value="https://www.facebook.com/100044420049874/posts/1725673385589980/"
    ))
    monkeypatch.setattr(facebook, "get_message_text", lambda _:
        "https://www.facebook.com/share/1MGrq5g49B/")
    monkeypatch.setattr(facebook.cmd_mod, "is_banned", AsyncMock(return_value=False))
    monkeypatch.setattr(facebook, "should_skip_duplicate_business_message", AsyncMock(return_value=False))
    lease = SimpleNamespace(mark_success=Mock(), finish=Mock())
    monkeypatch.setattr(facebook, "claim_message_request", AsyncMock(return_value=lease))
    monkeypatch.setattr(facebook, "load_user_settings", AsyncMock(
        return_value={"language": "pt", "captions": "on"}
    ))
    monkeypatch.setattr(facebook, "get_bot_url", AsyncMock(return_value="https://t.me/test"))
    monkeypatch.setattr(facebook, "react_to_message", AsyncMock())
    monkeypatch.setattr(facebook, "send_analytics", AsyncMock())
    monkeypatch.setattr(facebook, "safe_edit_text", AsyncMock())
    monkeypatch.setattr(facebook, "safe_delete_message", AsyncMock())
    monkeypatch.setattr(facebook, "maybe_delete_user_message", AsyncMock())
    monkeypatch.setattr(facebook, "bot", SimpleNamespace(send_chat_action=AsyncMock()))
    monkeypatch.delenv("FACEBOOK_FIREFOX_PROFILE", raising=False)

    async def gallery_download(url, *, output_dir, **options):
        assert options["facebook_gallery_first"] is True
        assert options["facebook_browser_profile"] is None
        from pathlib import Path
        p1, p2 = Path(output_dir) / "one.jpg", Path(output_dir) / "two.jpg"
        p1.write_bytes(b"photo-one")
        p2.write_bytes(b"photo-two")
        return SimpleNamespace(
            description="Legenda original",
            media_list=[
                SimpleNamespace(url=str(p1), type="photo", thumb=None),
                SimpleNamespace(url=str(p2), type="photo", thumb=None),
            ],
        )

    downloader = AsyncMock(side_effect=gallery_download)
    monkeypatch.setattr(facebook, "download_facebook_media", downloader)

    status = SimpleNamespace()
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=1234),
        chat=SimpleNamespace(id=1234, type="private"),
        message_id=42,
        answer=AsyncMock(return_value=status),
        reply_media_group=AsyncMock(return_value=[
            SimpleNamespace(photo=[SimpleNamespace(file_id="id1")], video=None, document=None, audio=None),
            SimpleNamespace(photo=[SimpleNamespace(file_id="id2")], video=None, document=None, audio=None),
        ]),
    )
    await facebook.process_facebook_link(message)
    downloader.assert_awaited_once()
    message.reply_media_group.assert_awaited_once()
    # Only the download status message, no obsolete "Publicação original" reply.
    assert message.answer.await_count == 1
    assert "reply_markup" not in message.answer.await_args.kwargs
    old_cache.assert_not_awaited()
    fake_db.add_file.assert_not_awaited()



def test_gallery_first_preserves_multi_photo_album_when_post_photo_repeats(tmp_path):
    import json

    files = []
    for photo_id in ("111", "222"):
        photo = tmp_path / f"{photo_id}.jpg"
        photo.write_bytes(b"photo")
        (tmp_path / f"{photo_id}.jpg.json").write_text(
            json.dumps({
                "id": photo_id, "set_id": "pcb.2237506800446573",
                "post_photo": "https://www.facebook.com/photo/?fbid=111",
            }),
            encoding="utf-8",
        )
        files.append(str(photo))
    url = "https://www.facebook.com/groups/137676310429643/permalink/2237506800446573/"
    assert not facebook_media._facebook_gallery_first_conflicts_with_post(url, files)
