"""The VPS deployer must patch the exact failure without overwriting handlers."""
from __future__ import annotations

import ast
from pathlib import Path
import runpy

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATCHER = runpy.run_path(str(ROOT / "scripts/apply_pinterest_video_send.py"))
patch_handler = PATCHER["patch_pinterest_source"]
patch_inline = PATCHER["patch_inline_source"]

BROKEN_HANDLER = '''import asyncio
async def process_pinterest_single_media(message, media_kind):
    async def _send_downloaded(path: str):
        if media_kind == "video":
            return await message.reply_video(
                video=FSInputFile(path),
                allow_sending_without_reply=True,
                caption="Example",
                **(await build_video_send_kwargs(path)),
            )
        return await message.answer_photo(photo=FSInputFile(path))

    async def _inspect_metrics(metrics):
        pass
'''


@pytest.mark.parametrize("method", ["reply_video", "answer_video"])
def test_patcher_handles_both_vps_and_main_telegram_send_forms(method):
    before = BROKEN_HANDLER.replace("message.reply_video(", f"message.{method}(")
    after = patch_handler(before)
    ast.parse(after)
    assert "video_kwargs, thumbnail_path = await build_video_send_kwargs(path)" in after
    assert "return await message.answer_video(" in after
    assert "**video_kwargs," in after
    assert "**(await build_video_send_kwargs(path))" not in after
    assert "await remove_file(thumbnail_path)" in after
    assert "return await message.answer_photo(" in after
    assert patch_handler(after) == after


def test_patcher_rejects_unrecognized_broken_handler():
    with pytest.raises(ValueError):
        patch_handler("print('custom handler')")


def test_main_pinterest_handler_matches_fixed_structure():
    source = (ROOT / "handlers/pinterest.py").read_text(encoding="utf-8")
    assert patch_handler(source) == source


def test_inline_patch_preserves_existing_non_metadata_code():
    before = (
        "async def run():\n"
        + PATCHER["OLD_INLINE"]
        + "\n        await another_task()\n"
    )
    after = patch_inline(before)
    assert "video_kwargs, thumbnail_path = await build_video_send_kwargs(metrics.path)" in after
    assert "state.register_download_path(thumbnail_path)" in after
    assert "await another_task()" in after
    assert patch_inline(after) == after


def test_inline_patcher_rejects_unrecognized_changes():
    with pytest.raises(ValueError):
        patch_inline("async def completely_custom_sender():\n    pass\n")
