"""Facebook profile-post photo identity and original shared-post captions."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.platforms import facebook_media as fb
from services.platforms import universal_extractor as universal


PROFILE_POST = "https://www.facebook.com/100077921513450/posts/1162826406324732/"
PHOTO_ID = "1528748809298491"
PHOTO_OWNER = "100064901583665"
POST_OWNER = "100077921513450"


def _photo(tmp_path, *, photo_id=PHOTO_ID, meta_id=PHOTO_ID,
           post_photo=None, set_id="", caption="Legenda original",
           post_text="Texto compartilhado", dirname="Single Post"):
    folder = tmp_path / "facebook" / "other-owner" / dirname
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{photo_id}.jpg"
    path.write_bytes(b"fake image")
    metadata = {
        "id": meta_id, "user_id": PHOTO_OWNER,
        "post_photo": post_photo if post_photo is not None
            else f"https://www.facebook.com/photo/?fbid={PHOTO_ID}&set=",
        "set_id": set_id, "caption": caption, "post_text": post_text,
        "subcategory": "set", "title": "Photos from shared post",
    }
    (folder / (path.name + ".json")).write_text(json.dumps(metadata))
    return path


def test_accept_only_specific_numeric_profile_publications():
    assert fb._facebook_profile_post_id(PROFILE_POST) == "1162826406324732"
    assert fb._facebook_profile_post_id("https://www.facebook.com/100077921513450/") is None
    assert fb._facebook_profile_post_id("https://www.facebook.com/groups/123/posts/456/") is None
    assert fb._facebook_profile_post_id("https://facebook.com.evil.invalid/123/posts/456/") is None


def test_shared_photo_owner_may_differ_from_post_owner(tmp_path):
    image = _photo(tmp_path)
    assert PHOTO_OWNER != POST_OWNER
    assert fb._verified_facebook_profile_photos(PROFILE_POST, [str(image)], (PHOTO_ID,))
    # A sidecar matching its own photo URL does not prove that this was
    # the photo identified by the requested publication's preflight.
    assert not fb._verified_facebook_profile_photos(
        PROFILE_POST, [str(image)], ("1162826316324741",),
    )
    assert not fb._verified_facebook_profile_photos(PROFILE_POST, [str(image)])


@pytest.mark.parametrize("bad_url", [
    "https://www.facebook.com/photo/?fbid=999999",
    "https://example.com/photo/?fbid=1528748809298491",
    "http://www.facebook.com/photo/?fbid=1528748809298491",
    "https://www.facebook.com/photo/?fbid=15287488092984910",
    "https://www.facebook.com/100077921513450/",
])
def test_wrong_photo_link_rejected(tmp_path, bad_url):
    path = _photo(tmp_path, post_photo=bad_url)
    assert not fb._verified_facebook_profile_photos(PROFILE_POST, [str(path)], (PHOTO_ID,))


def test_mismatched_file_and_sidecar_ids_rejected(tmp_path):
    path = _photo(tmp_path, meta_id="999999")
    assert not fb._verified_facebook_profile_photos(PROFILE_POST, [str(path)], (PHOTO_ID,))


def test_missing_photo_metadata_rejected(tmp_path):
    path = _photo(tmp_path)
    (path.parent / (path.name + ".json")).unlink()
    assert not fb._verified_facebook_profile_photos(PROFILE_POST, [str(path)], (PHOTO_ID,))


def test_wrong_album_post_id_rejected(tmp_path):
    path = _photo(tmp_path, set_id="pcb.999999", dirname="Photos (pcb.999999)")
    assert not fb._verified_facebook_profile_photos(PROFILE_POST, [str(path)], (PHOTO_ID,))


def test_shared_post_text_before_original_photo_caption():
    assert fb.facebook_caption({
        "post_text": "Mensagem de quem compartilhou",
        "caption": "Mensagem do autor da foto",
    }) == "Mensagem de quem compartilhou"
    assert fb.facebook_caption({"post_text": "", "caption": "Foto original"}) == "Foto original"


@pytest.mark.asyncio
async def test_gallery_rejects_unverified_profile_photo_before_delivery(monkeypatch, tmp_path):
    target_dir = tmp_path / "gdl_profiletest"
    path = _photo(target_dir, post_photo="https://www.facebook.com/photo/?fbid=999999")
    proc = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"", b"")))
    monkeypatch.setattr(
        universal.asyncio, "create_subprocess_exec",
        AsyncMock(return_value=proc),
    )
    result = await universal._download_with_gallery_dl(
        PROFILE_POST, str(tmp_path), "profiletest", facebook_browser_profile="demo",
        facebook_expected_photo_ids=(PHOTO_ID,),
    )
    assert result is None


@pytest.mark.asyncio
async def test_gallery_accepts_exact_link_and_prefers_shared_caption(monkeypatch, tmp_path):
    target_dir = tmp_path / "gdl_profileok"
    _photo(target_dir)
    proc = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"", b"")))
    monkeypatch.setattr(
        universal.asyncio, "create_subprocess_exec",
        AsyncMock(return_value=proc),
    )
    result = await universal._download_with_gallery_dl(
        PROFILE_POST, str(tmp_path), "profileok", facebook_browser_profile="demo",
        facebook_expected_photo_ids=(PHOTO_ID,),
    )
    assert result is not None
    assert len(result.media_list) == 1
    assert result.media_list[0].type == "photo"
    assert result.description == "Texto compartilhado"


@pytest.mark.asyncio
async def test_facebook_service_routes_only_specific_profile_post_to_gallery(monkeypatch, tmp_path):
    expected = SimpleNamespace(media_list=[SimpleNamespace(type="photo")])
    gallery = AsyncMock(return_value=expected)
    monkeypatch.setattr(universal, "_download_with_gallery_dl", gallery)
    monkeypatch.setattr(
        fb, "_facebook_profile_post_expected_photo_ids",
        AsyncMock(return_value=(PHOTO_ID,)),
    )
    result = await fb.download_facebook_media(
        PROFILE_POST, output_dir=str(tmp_path),
        facebook_browser_profile="/private/firefox",
    )
    assert result is expected
    assert gallery.await_args.kwargs["facebook_browser_profile"] == "/private/firefox"
    assert gallery.await_args.kwargs["facebook_expected_photo_ids"] == (PHOTO_ID,)



def test_login_screen_caption_rejected_even_if_photo_id_matches(tmp_path):
    path = _photo(tmp_path, post_text="Remember password")
    assert not fb._verified_facebook_profile_photos(PROFILE_POST, [str(path)], (PHOTO_ID,))


def test_facebook_profile_photo_link_id_accepts_exact_link_only():
    assert fb._facebook_profile_photo_link_id(
        "https://www.facebook.com/photo/?fbid=1528748809298491&set="
    ) == PHOTO_ID
    assert fb._facebook_profile_photo_link_id(
        "https://www.facebook.com/photo/?fbid=15287488092984911"
    ) != PHOTO_ID


@pytest.mark.asyncio
async def test_profile_preflight_fails_closed_on_login_prompt(monkeypatch):
    fake_dump = [[], {"id": PHOTO_ID, "post_photo":
                      "https://www.facebook.com/photo/?fbid=" + PHOTO_ID,
                      "post_text": "Remember password"}]
    proc = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(return_value=(json.dumps(fake_dump).encode(), b"")),
    )
    monkeypatch.setattr(fb.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    assert await fb._facebook_profile_post_expected_photo_ids(PROFILE_POST) is None


@pytest.mark.asyncio
async def test_profile_preflight_requires_fbid_matching_media_id(monkeypatch):
    fake_dump = [[], {"id": PHOTO_ID, "post_photo":
                      "https://www.facebook.com/photo/?fbid=1162826316324741",
                      "post_text": "legitimate user text"}]
    proc = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(return_value=(json.dumps(fake_dump).encode(), b"")),
    )
    monkeypatch.setattr(fb.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    assert await fb._facebook_profile_post_expected_photo_ids(PROFILE_POST) is None


@pytest.mark.asyncio
async def test_profile_preflight_returns_exact_confirmed_ids(monkeypatch):
    fake_dump = [[], {"id": PHOTO_ID, "post_photo":
                      "https://www.facebook.com/photo/?fbid=" + PHOTO_ID,
                      "post_text": "legitimate user text"}]
    proc = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(return_value=(json.dumps(fake_dump).encode(), b"")),
    )
    monkeypatch.setattr(fb.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    assert await fb._facebook_profile_post_expected_photo_ids(PROFILE_POST) == (PHOTO_ID,)
