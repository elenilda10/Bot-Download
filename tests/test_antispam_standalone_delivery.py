"""Ensure download delivery never depends on a link message still existing.

This is a stdlib-only static guard so it runs even if optional platform
dependencies cannot be installed in CI.
"""
from __future__ import annotations

import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

DOWNLOAD_MODULES = (
    "handlers/instagram.py",
    "handlers/facebook.py",
    "handlers/universal.py",
    "handlers/youtube.py",
    "handlers/tiktok.py",
    "handlers/twitter.py",
    "handlers/threads.py",
    "handlers/pinterest.py",
    "handlers/spotify.py",
    "handlers/deezer.py",
    "handlers/soundcloud.py",
    "handlers/mediafire.py",
    "handlers/mega.py",
    "handlers/google_drive.py",
    "handlers/xvideos.py",
    "handlers/xvideos_actions.py",
    "handlers/pornhub.py",
    "handlers/eporner.py",
    "handlers/music.py",
    "handlers/media_download.py",
    "handlers/telegram_ui_utils.py",
    "services/media/delivery.py",
    "services/media/orchestration.py",
    # The onboarding middleware runs before the download handlers. It also
    # must not reply to a link that a moderation bot may have removed.
    "middlewares/private_chat_guard.py",
)


def is_incoming_message(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "message"
    if isinstance(node, ast.Attribute):
        return node.attr == "message"
    return False


class TestStandaloneDeliveryAfterLinkDeletion(unittest.TestCase):
    def test_no_media_or_notice_sends_reply_to_incoming_message(self):
        for name in DOWNLOAD_MODULES:
            source = (ROOT / name).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=name)
            bad = []
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and is_incoming_message(node.value)
                    and (node.attr == "reply" or node.attr.startswith("reply_"))
                    and node.attr != "reply_to_message"
                ):
                    bad.append((node.lineno, node.attr))
                if (
                    isinstance(node, ast.keyword)
                    and node.arg in ("reply_to_message_id", "reply_parameters")
                ):
                    bad.append((node.value.lineno, node.arg))
            self.assertEqual([], bad, f"{name} still depends on source-message replies")

    def test_shared_media_sender_does_not_attach_deleted_message_id(self):
        code = (ROOT / "services/media/delivery.py").read_text(encoding="utf-8")
        self.assertNotIn('"reply_to_message_id"', code)
        self.assertIn("message.answer_media_group(**send_kwargs)", code)
        self.assertIn("message.answer_video(", code)
        self.assertIn("message.answer_document(", code)

    def test_download_error_messages_are_standalone(self):
        code = (ROOT / "handlers/telegram_ui_utils.py").read_text(encoding="utf-8")
        self.assertIn('method: str = "answer"', code)
        self.assertIn('safe_method = method.replace("reply", "answer"', code)
        self.assertNotIn('"reply_parameters"', code)


if __name__ == "__main__":
    unittest.main()
