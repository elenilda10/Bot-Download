"""Synthetic reproductions of recommendation contamination; no private HTML."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.platforms.facebook_post_identity import extract_post_identity
from services.platforms import facebook_media, universal_extractor

POST = "1511496317669924"
PHOTO = "1511496267669929"
URL = f"https://www.facebook.com/100064287116075/posts/{POST}/"


def story(post=POST, photos=(PHOTO,), message="Raphael Veiga não larga o Verdão."):
    return {"post_id": post, "message": {"text": message}, "attachments": [
        {"media": {"__typename": "Photo", "id": pid}} for pid in photos
    ]}


def html(*objects):
    return '<script type="application/json">' + json.dumps({"require": list(objects)}) + '</script>'


def test_recommendation_before_requested_post_is_not_selected():
    wrong = story("999", ("1688791432604752",), "Only one Harry ❤️")
    data = extract_post_identity(html(wrong, story()), POST)
    assert data == {"ids": (PHOTO,), "description": "Raphael Veiga não larga o Verdão.", "source_post_id": POST}


def test_album_order_and_duplicate_style_media():
    target = story(photos=("111", "222", "333"))
    target["attachments"][0]["styles"] = {"attachment": {"media": {"__typename": "Photo", "id": "111"}}}
    assert extract_post_identity(html(target), POST)["ids"] == ("111", "222", "333")


def test_only_url_containing_id_does_not_verify_post():
    assert extract_post_identity(html({"url": URL}, story("999")), POST) is None


def test_comments_and_shared_story_are_not_caption_fallbacks():
    target = story(message="")
    target["feedback"] = {"message": {"text": "recommendation"}}
    target["attached_story"] = story("999", message="another author")
    assert extract_post_identity(html(target), POST)["description"] == ""


@pytest.mark.parametrize("other", [story(photos=("999",)), story(message="different text")])
def test_conflicting_copies_are_rejected(other):
    assert extract_post_identity(html(story(), other), POST) is None


def test_same_post_message_in_renderer_section():
    target = story(message="")
    del target["message"]
    target["comet_sections"] = {"content": {"story": {"post_id": POST, "message": {"text": "bound"}}}}
    assert extract_post_identity(html(target), POST)["description"] == "bound"
    target["comet_sections"]["content"]["story"]["post_id"] = "999"
    assert extract_post_identity(html(target), POST)["description"] == ""


@pytest.mark.parametrize("attachment", [
    {"media": {"__typename": "Video", "id": "111"}},
    {"media": {"__typename": "Photo"}},
    {"subattachments": {"page_info": {"has_next_page": True}, "nodes": []}},
    {"shared_story": story("999")},
])
def test_unsupported_or_incomplete_media_fail_closed(attachment):
    target = story()
    target["attachments"].append(attachment)
    assert extract_post_identity(html(target), POST) is None


def test_more_than_ten_is_not_silently_truncated():
    assert extract_post_identity(html(story(photos=tuple(map(str, range(11))))), POST) is None


@pytest.mark.asyncio
async def test_preflight_uses_only_initial_page_and_cleans_private_html(monkeypatch):
    seen = []
    async def spawn(*args, **kwargs):
        directory = Path(kwargs["cwd"])
        seen.append(directory)
        (directory / "01_post.txt").write_text(html(story()))
        (directory / "02_photo.txt").write_text(html(story(photos=("999",))))
        return SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"", b"")))
    monkeypatch.setattr(facebook_media.asyncio, "create_subprocess_exec", spawn)
    result = await facebook_media._facebook_profile_post_metadata(URL, "/profile")
    assert result["ids"] == (PHOTO,)
    assert not seen[0].exists()


@pytest.mark.asyncio
async def test_preflight_does_not_fall_back_to_gallery_metadata(monkeypatch):
    async def spawn(*args, **kwargs):
        (Path(kwargs["cwd"]) / "01_post.txt").write_text(html(story("999")))
        return SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"[]", b"")))
    monkeypatch.setattr(facebook_media.asyncio, "create_subprocess_exec", spawn)
    assert await facebook_media._facebook_profile_post_metadata(URL) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_id", [False, True])
async def test_download_binds_each_photo_and_keeps_post_message(monkeypatch, tmp_path, wrong_id):
    monkeypatch.setattr(facebook_media, "_facebook_profile_post_metadata", AsyncMock(return_value={
        "source_post_id": POST, "ids": (PHOTO, "222"), "description": "bound post text",
    }))
    calls = []
    async def download(url, directory, *args, **kwargs):
        calls.append(url)
        identity = "999" if wrong_id else (PHOTO if len(calls) == 1 else "222")
        path = Path(directory) / f"{identity}.jpg"
        path.write_bytes(b"image")
        Path(str(path) + ".json").write_text(json.dumps({"id": identity, "caption": "photo owner text"}))
        return SimpleNamespace(description="Only one Harry", media_list=[SimpleNamespace(url=str(path), type="photo", index=0)])
    result = await facebook_media._download_facebook_profile_post_photos(
        URL, str(tmp_path), "test", browser_profile=None, gallery_downloader=download)
    if wrong_id:
        assert result is None
    else:
        assert result.description == "bound post text"
        assert [Path(i.url).stem for i in result.media_list] == [PHOTO, "222"]
        assert [i.index for i in result.media_list] == [0, 1]
        assert all("/photo/?fbid=" in u for u in calls)


def test_installer_preserves_local_edits_and_backs_up(tmp_path):
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    target = tmp_path / 'bot'
    platform = target / 'services/platforms'
    platform.mkdir(parents=True)
    # Simulate untracked/newer local Facebook files and unrelated custom code.
    old_media = (root / 'services/platforms/facebook_media.py').read_text() + '\nLOCAL_SENTINEL = 42\n'
    (platform / 'facebook_media.py').write_text(old_media)
    old_cache = (root / 'services/platforms/facebook_cache.py').read_text().replace('CACHE_VERSION = 2', 'CACHE_VERSION = 1').replace('facebook:v2:', 'facebook:v1:') + '\nLOCAL_CACHE_SENTINEL = 7\n'
    (platform / 'facebook_cache.py').write_text(old_cache)
    fixture = tmp_path / 'post.html'
    fixture.write_text(html(story()))
    command = [sys.executable, str(root / 'scripts/apply_facebook_post_identity.py'),
               '--target', str(target), '--html', str(fixture), '--post-id', POST,
               '--expect-photo', PHOTO]
    diagnostic = subprocess.run(command, capture_output=True, text=True)
    assert diagnostic.returncode == 0, diagnostic.stderr
    assert not (platform / 'facebook_post_identity.py').exists()
    applied = subprocess.run(command + ['--apply'], capture_output=True, text=True)
    assert applied.returncode == 0, applied.stderr
    assert 'LOCAL_SENTINEL = 42' in (platform / 'facebook_media.py').read_text()
    assert 'LOCAL_CACHE_SENTINEL = 7' in (platform / 'facebook_cache.py').read_text()
    assert 'facebook:v2:' in (platform / 'facebook_cache.py').read_text()
    backup = next(target.glob('facebook-identity-*'))
    assert (backup / 'services/platforms/facebook_media.py').read_text() == old_media
    repeat = subprocess.run(command + ['--apply'], capture_output=True, text=True)
    assert repeat.returncode != 0


def test_installer_refuses_wrong_fixture(tmp_path):
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    fixture = tmp_path / 'post.html'
    fixture.write_text(html(story('999')))
    result = subprocess.run([sys.executable, str(root / 'scripts/apply_facebook_post_identity.py'),
        '--html', str(fixture), '--post-id', POST, '--expect-photo', PHOTO, '--apply',
        '--target', str(tmp_path / 'untouched')], capture_output=True, text=True)
    assert result.returncode != 0
    assert not (tmp_path / 'untouched').exists()
