"""Regression cases for real Facebook photo/album gallery-dl JSON messages."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.platforms import facebook_media, universal_extractor


POST_ID = "1726664292157547"
URL = f"https://www.facebook.com/100044419778231/posts/{POST_ID}/"
IDS = (
    "1726663952157581",
    "1726664042157572",
    "1726664032157573",
    "1726663962157580",
    "1726663955490914",
)
TEXT = (
    "🚨 Flamengo tem três desfalques confirmados para enfrentar o Estudiantes. "
    "❌ Arrascaeta; ❌ Danilo; ❌ Evertton Araújo; ⚠️ Saúl."
)


def album_messages(ids=IDS, text=TEXT):
    """gallery-dl v1.32.14 emits Directory + Url for each album photo."""
    messages = []
    for index, photo_id in enumerate(ids):
        meta = {
            "id": photo_id,
            "set_id": f"pcb.{POST_ID}",
            "first_photo_id": ids[0],
            "post_photo": f"https://www.facebook.com/photo/?fbid={ids[0]}&set=pcb.{POST_ID}",
            "post_text": text,
            "caption": "",
            "num": index + 1,
            "next_photo_id": ids[index + 1] if index + 1 < len(ids) else ids[0],
            "extension": "png",
            "username": "Coluna do Fla / Flamengo",
        }
        messages.append([2, dict(meta)])
        messages.append([3, f"https://example-cdn.invalid/{photo_id}.png", dict(meta)])
    return messages


def test_album_parses_only_url_messages_and_preserves_order_and_post_text():
    data = album_messages()
    record = facebook_media._facebook_profile_post_json(data, URL)
    assert record == {"ids": IDS, "description": TEXT}
    assert len(record["ids"]) == 5


@pytest.mark.parametrize("change", [
    lambda rows: rows[3][2].update(id=IDS[0]),
    lambda rows: rows[3][2].update(set_id="pcb.999999999999"),
    lambda rows: rows[3][2].update(post_photo="https://www.facebook.com/photo/?fbid=12345678"),
    lambda rows: rows[3][2].update(first_photo_id="12345678"),
    lambda rows: rows[3][2].update(num=7),
    lambda rows: rows[3][2].update(next_photo_id="12345678"),
    lambda rows: rows[3][2].update(post_text="Texto de outra publicação"),
])
def test_album_rejects_conflicting_media_metadata(change):
    data = album_messages()
    change(data)
    assert facebook_media._facebook_profile_post_json(data, URL) is None


def test_album_rejects_error_record_even_when_other_photos_exist():
    data = album_messages()
    data.append([-1, {"error": "AuthRequired", "message": "Login required"}])
    assert facebook_media._facebook_profile_post_json(data, URL) is None


def test_album_rejects_truncated_sequence_with_unseen_next_id():
    data = album_messages()
    data[-1][2]["next_photo_id"] = "999999999999"
    assert facebook_media._facebook_profile_post_json(data, URL) is None


def test_album_rejects_wrong_publication_url():
    assert facebook_media._facebook_profile_post_json(
        album_messages(),
        "https://www.facebook.com/100044419778231/posts/999999999999/",
    ) is None


def test_album_paths_follow_preflight_order_not_filesystem_order():
    paths = [f"/tmp/gallery/{photo_id}.png" for photo_id in reversed(IDS)]
    assert facebook_media._order_facebook_photo_paths(paths, IDS) == [
        f"/tmp/gallery/{photo_id}.png" for photo_id in IDS
    ]


def test_single_photo_ignores_unrelated_post_text_and_keeps_photo_caption():
    single = "1577004777801677"
    src = {
        "id": single,
        "set_id": "",
        "post_photo": f"https://www.facebook.com/photo/?fbid={single}",
        "post_text": "Mr. Worldwide back in the 305 — Pitbull",
        "caption": "Do lixo à organização! Transforme a embalagem em um cestinho.",
        "num": 1,
        "next_photo_id": "999999999999",  # Navigation to other photos is not an album.
    }
    url = "https://www.facebook.com/100064765086437/posts/1577004794468342/"
    assert facebook_media._facebook_profile_post_json(
        [[2, dict(src)], [3, "https://example.invalid/photo.png", src]], url
    ) == {
        "ids": (single,),
        "description": "Do lixo à organização! Transforme a embalagem em um cestinho.",
    }


def test_single_photo_with_no_photo_caption_drops_untrusted_post_text():
    single = "1577004777801677"
    src = {
        "id": single,
        "set_id": "",
        "post_photo": f"https://www.facebook.com/photo/?fbid={single}",
        "post_text": "Mr. Worldwide back in the 305 — Pitbull",
        "caption": "",
    }
    url = "https://www.facebook.com/100064765086437/posts/1577004794468342/"
    assert facebook_media._facebook_profile_post_json(
        [[2, dict(src)], [3, "https://example.invalid/photo.png", src]], url,
    ) == {"ids": (single,), "description": ""}


@pytest.mark.asyncio
@pytest.mark.parametrize("caption, expected", [
    ("Do lixo à organização! Transforme uma embalagem em um cestinho!",
     "Do lixo à organização! Transforme uma embalagem em um cestinho!"),
    ("", ""),
])
async def test_gallery_first_single_photo_never_uses_unrelated_post_text(
    monkeypatch, tmp_path, caption, expected,
):
    photo_id = "1577004777801677"
    media_dir = tmp_path / "gdl_test"
    media_dir.mkdir()
    photo_path = media_dir / f"{photo_id}.jpg"
    photo_path.write_bytes(b"fake photo")
    (media_dir / f"{photo_id}.jpg.json").write_text(json.dumps({
        "id": photo_id,
        "post_photo": f"https://www.facebook.com/photo/?fbid={photo_id}",
        "set_id": "",
        "caption": caption,
        "post_text": "Mr. Worldwide back in the 305 — Pitbull",
        "title": "Photos from some Facebook post",
    }))
    proc = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(return_value=(b"", b"")),
    )
    monkeypatch.setattr(
        universal_extractor.asyncio, "create_subprocess_exec",
        AsyncMock(return_value=proc),
    )
    url = "https://www.facebook.com/100064765086437/posts/1577004794468342/"
    result = await universal_extractor._download_with_gallery_dl(
        url, str(tmp_path), "test", facebook_gallery_public_unverified=True,
    )
    assert result is not None
    assert len(result.media_list) == 1
    assert result.description == expected



def test_verified_album_sidecars_must_match_post_identity_and_photo_ids(tmp_path):
    parent = tmp_path / f"Photos (pcb.{POST_ID})"
    parent.mkdir()
    paths = []
    for photo_id in IDS:
        photo = parent / f"{photo_id}.png"
        photo.write_bytes(b"fake image bytes")
        (parent / f"{photo_id}.png.json").write_text(
            json.dumps({"id": photo_id, "set_id": f"pcb.{POST_ID}"})
        )
        paths.append(str(photo))

    assert facebook_media._verified_facebook_profile_photos(URL, paths, IDS)
    assert not facebook_media._verified_facebook_profile_photos(
        URL, paths, IDS[:-1] + ("999999999999",)
    )
    (parent / f"{IDS[2]}.png.json").write_text(
        json.dumps({"id": IDS[2], "set_id": "pcb.999999999999"})
    )
    assert not facebook_media._verified_facebook_profile_photos(URL, paths, IDS)
