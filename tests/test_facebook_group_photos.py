"""Facebook group photo verification: refuse unrelated posts and filenames."""
import json
from unittest.mock import AsyncMock

import pytest

from services.platforms import universal_extractor

from services.platforms.universal_extractor import (
    _facebook_group_post_id,
    _verified_facebook_group_photos,
)


GROUP_URL = (
    "https://www.facebook.com/groups/256772258373648/"
    "permalink/2061916421192547/"
)


def _create_photo(tmp_path, *, set_id="pcb.2061916421192547", photo_id="1629584308563349"):
    folder = (
        tmp_path / "facebook" / "William gomes"
        / f"Photos from William gomes's post ({set_id})"
    )
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{photo_id}.jpg"
    path.write_bytes(b"test-image")
    path.with_name(path.name + ".json").write_text(
        json.dumps({"id": photo_id, "set_id": set_id}),
        encoding="utf-8",
    )
    return path


def test_recognizes_only_specific_numeric_group_post():
    assert _facebook_group_post_id(GROUP_URL) == "2061916421192547"
    assert _facebook_group_post_id(
        "https://www.facebook.com/groups/256772258373648/posts/2061916421192547/"
    ) == "2061916421192547"
    assert _facebook_group_post_id("https://www.facebook.com/groups/256772258373648/") is None
    assert _facebook_group_post_id("https://evil.example/groups/1/posts/2/") is None


def test_verified_facebook_group_photo_matches_album_and_metadata(tmp_path):
    photo = _create_photo(tmp_path)
    assert _verified_facebook_group_photos(GROUP_URL, [str(photo)])


def test_rejects_wrong_group_photo_set(tmp_path):
    photo = _create_photo(tmp_path, set_id="pcb.1743744837114727")
    assert not _verified_facebook_group_photos(GROUP_URL, [str(photo)])


def test_rejects_unverified_metadata(tmp_path):
    photo = _create_photo(tmp_path)
    photo.with_name(photo.name + ".json").unlink()
    assert not _verified_facebook_group_photos(GROUP_URL, [str(photo)])


def test_rejects_mismatched_media_id(tmp_path):
    photo = _create_photo(tmp_path)
    photo.with_name(photo.name + ".json").write_text(
        json.dumps({"id": "999999", "set_id": "pcb.2061916421192547"}),
        encoding="utf-8",
    )
    assert not _verified_facebook_group_photos(GROUP_URL, [str(photo)])


def test_rejects_mixed_album(tmp_path):
    valid = _create_photo(tmp_path)
    invalid = _create_photo(tmp_path, set_id="pcb.123", photo_id="123456789")
    assert not _verified_facebook_group_photos(GROUP_URL, [str(valid), str(invalid)])


def test_rejects_non_photo(tmp_path):
    path = _create_photo(tmp_path).with_suffix(".mp4")
    path.write_bytes(b"test-video")
    assert not _verified_facebook_group_photos(GROUP_URL, [str(path)])



@pytest.mark.asyncio
async def test_download_prefers_original_facebook_caption_over_album_title(tmp_path, monkeypatch):
    album = tmp_path / "gdl_captiontest" / "facebook" / "William gomes" / (
        "Photos from William gomes's post (pcb.2061916421192547)"
    )
    album.mkdir(parents=True)
    image = album / "1629584308563349.jpg"
    image.write_bytes(b"image")
    image.with_name(image.name + ".json").write_text(
        json.dumps({
            "id": "1629584308563349",
            "set_id": "pcb.2061916421192547",
            "caption": "Sim ou não???",
            "title": "Photos from William gomes's post",
        }),
        encoding="utf-8",
    )
    process = type("Process", (), {
        "returncode": 0,
        "communicate": AsyncMock(return_value=(b"", b"")),
    })()
    monkeypatch.setattr(
        universal_extractor.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=process),
    )
    media = await universal_extractor._download_with_gallery_dl(
        GROUP_URL, str(tmp_path), "captiontest",
        facebook_browser_profile="test-profile",
    )
    assert media is not None
    assert len(media.media_list) == 1
    assert media.description == "Sim ou não???"
