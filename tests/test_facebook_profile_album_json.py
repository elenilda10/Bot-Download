"""Regression cases for real Facebook photo/album gallery-dl JSON messages."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.platforms import facebook_media


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


def test_single_photo_keeps_post_caption_without_album_set():
    single = "1577004777801677"
    src = {
        "id": single,
        "set_id": "",
        "post_photo": f"https://www.facebook.com/photo/?fbid={single}",
        "post_text": "Legenda original do Flamengo",
        "caption": "Legenda da foto",
        "num": 1,
        "next_photo_id": "999999999999",  # Navigation to other photos is not an album.
    }
    url = "https://www.facebook.com/100064765086437/posts/1577004794468342/"
    assert facebook_media._facebook_profile_post_json(
        [[2, dict(src)], [3, "https://example.invalid/photo.png", src]], url
    ) == {"ids": (single,), "description": "Legenda original do Flamengo"}


@pytest.mark.asyncio
async def test_album_preflight_passes_only_unique_media_ids(monkeypatch):
    data = album_messages()
    proc = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(return_value=(json.dumps(data).encode(), b"")),
    )
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(facebook_media.asyncio, "create_subprocess_exec", spawn)
    # No real subprocess or Firefox session in this test.
    record = await facebook_media._facebook_profile_post_metadata(URL, "/firefox/test")
    assert record == {"ids": IDS, "description": TEXT}
    assert "--cookies-from-browser" in spawn.await_args.args


@pytest.mark.asyncio
@pytest.mark.parametrize("second_caption, expected", [
    (TEXT, TEXT),
    ("Konstantin Rokossovsky", ""),
    ("Photos from an unrelated post", ""),
])
async def test_verified_album_does_not_forward_conflicting_captions(
    monkeypatch, second_caption, expected,
):
    monkeypatch.setattr(
        facebook_media, "_facebook_profile_post_metadata",
        AsyncMock(return_value={"ids": IDS, "description": TEXT}),
    )
    result = SimpleNamespace(
        description=second_caption,
        media_list=[SimpleNamespace(type="photo") for _ in IDS],
    )
    gallery = AsyncMock(return_value=result)
    final = await facebook_media._download_facebook_profile_post_photos(
        URL, "/tmp", "test",
        browser_profile="/firefox/test", gallery_downloader=gallery,
    )
    assert final is result
    assert final.description == expected
    assert gallery.await_args.kwargs["facebook_expected_photo_ids"] == IDS


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
