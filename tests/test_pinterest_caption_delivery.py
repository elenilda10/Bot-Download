"""Pinterest captions from original metadata to single photo and Telegram album."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from messages.user_messages import captions
from services.media.delivery import send_cached_media_entries
from services.platforms.pinterest_media import parse_pinterest_gallery_dump


def _pinterest_post(*, two_images: bool):
    records = [
        [1, {"description": "Legenda original <&> do Pinterest"}],
        [2, "https://i.pinimg.com/a.jpg", {"extension": "jpg"}],
    ]
    if two_images:
        records.append(
            [2, "https://i.pinimg.com/b.jpg", {"extension": "jpg"}]
        )
    post = parse_pinterest_gallery_dump(
        json.dumps(records), "https://www.pinterest.com/pin/123456789/"
    )
    assert post is not None
    return post


@pytest.mark.asyncio
async def test_original_photo_caption_is_sent_as_html_escaped_telegram_caption():
    post = _pinterest_post(two_images=False)
    message = SimpleNamespace(
        answer_photo=AsyncMock(return_value=SimpleNamespace(
            photo=[SimpleNamespace(file_id="telegram-photo")],
        )),
    )
    db = SimpleNamespace(add_file=AsyncMock())
    caption = captions("on", post.description, "https://t.me/SaveVidDLBot")
    assert "&lt;&amp;&gt;" in caption
    result = await send_cached_media_entries(
        message,
        [{
            "type": "photo",
            "file_id": "telegram-photo",
            "cache_key": "pinterest-single",
            "cached": True,
        }],
        db_service=db,
        caption=caption,
        kind_key="type",
    )
    assert result is not None
    sent = message.answer_photo.await_args.kwargs
    assert sent["caption"] == caption
    assert sent["photo"] == "telegram-photo"
    assert sent["parse_mode"] == "HTML"
    db.add_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_original_album_caption_is_attached_to_first_photo_only():
    post = _pinterest_post(two_images=True)
    message = SimpleNamespace(
        answer_media_group=AsyncMock(
            return_value=[
                SimpleNamespace(photo=[SimpleNamespace(file_id="one")]),
                SimpleNamespace(photo=[SimpleNamespace(file_id="two")]),
            ]
        )
    )
    db = SimpleNamespace(add_file=AsyncMock())
    caption = captions("on", post.description, "https://t.me/SaveVidDLBot")
    result = await send_cached_media_entries(
        message,
        [
            {"type": "photo", "file_id": "one", "cache_key": "pin-1", "cached": True},
            {"type": "photo", "file_id": "two", "cache_key": "pin-2", "cached": True},
        ],
        db_service=db,
        caption=caption,
        kind_key="type",
    )
    assert result is not None
    sent = message.answer_media_group.await_args.kwargs["media"]
    assert len(sent) == 2
    assert sent[0].caption == caption
    assert sent[1].caption is None


def test_pinterest_caption_setting_off_is_still_respected():
    post = _pinterest_post(two_images=True)
    caption = captions("off", post.description, "https://t.me/SaveVidDLBot")
    assert "Legenda original" not in caption
    assert "Powered by" in caption


def test_no_description_means_no_fabricated_caption():
    caption = captions("on", "", "https://t.me/SaveVidDLBot")
    assert "Powered by" in caption
    assert "original" not in caption
