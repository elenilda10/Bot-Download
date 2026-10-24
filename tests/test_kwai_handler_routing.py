"""Isolated tests for Kwai handler routing and callback URL recovery.

No bot token, PostgreSQL or app_context needed for these pure checks.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import os
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

HANDLER = Path(__file__).resolve().parents[1] / "handlers/kwai.py"
INIT = Path(__file__).resolve().parents[1] / "handlers/__init__.py"
BATCH = Path(__file__).resolve().parents[1] / "handlers/media_download.py"
DETECTION = Path(__file__).resolve().parents[1] / "services/links/detection.py"


def _load_helpers():
    tree = ast.parse(HANDLER.read_text("utf-8"))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in {"_KWAI_LINK_REGEX", "_KWAI_CALLBACK_HASH"}
            for t in node.targets
        ):
            selected.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
            "_extract_kwai_url", "_url_hash", "_callback_url",
            "_profile_for_request", "_cache_scope", "_cache_key",
        }:
            selected.append(node)

    def is_kwai_url(url):
        hostname = (urlparse(url).hostname or "").lower()
        return hostname in {"kwai.com", "kwai-video.com", "kw.ai"} or any(
            hostname.endswith("." + domain)
            for domain in ("kwai.com", "kwai-video.com", "kw.ai")
        )

    namespace = {
        "re": re,
        "hashlib": hashlib,
        "os": os,
        "ADMIN_ID": 1,
        "build_media_cache_key": lambda platform, *, variant: platform + ":" + variant,
        "is_kwai_url": is_kwai_url,
        "get_message_text": lambda message: message.text,
    }
    module = ast.Module(
        body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            *selected,
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    exec(compile(module, filename=str(HANDLER), mode="exec"), namespace)
    return namespace


def test_firefox_profile_is_explicit_owner_private_only(monkeypatch):
    from enum import Enum
    class ChatType(str, Enum):
        PRIVATE = "private"
    helpers = _load_helpers()
    profile = helpers["_profile_for_request"]
    private = SimpleNamespace(id=1, type="private")
    monkeypatch.delenv("KWAI_FIREFOX_PROFILE", raising=False)
    assert profile(1, private) is None
    monkeypatch.setenv("KWAI_FIREFOX_PROFILE", "/test/profile")
    assert profile(1, private) == "/test/profile"
    assert profile(1, SimpleNamespace(id=1, type=ChatType.PRIVATE)) == "/test/profile"
    assert profile(1, private, "business-id") is None
    assert profile(2, SimpleNamespace(id=2, type="private")) is None
    assert profile(1, SimpleNamespace(id=-100123, type="supergroup")) is None
    assert profile(1, SimpleNamespace(id=2, type="private")) is None


def test_authenticated_cache_and_callbacks_cannot_use_public_keys():
    helpers = _load_helpers()
    scope = helpers["_cache_scope"]
    key = helpers["_cache_key"]
    assert scope(None, 1) == "public"
    assert scope("/profile", 1) == "owner:1"
    for kind in ("video", "document", "audio", "caption"):
        assert key("token", kind, scope("/profile", 1)) != key("token", kind)
        assert key("token", kind, "owner:1") != key("token", kind, "owner:2")
        assert "v7:" in key("token", kind)


def test_handler_extracts_short_and_canonical_links():
    helpers = _load_helpers()
    extract = helpers["_extract_kwai_url"]
    short = "https://k.kwai.com/p/CMtEC74X"
    full = "https://www.kwai.com/@gabyvalence/video/5200118832330956026"
    assert extract(f"Veja este link: {short}") == short
    assert extract("(" + full + ")") == full
    assert extract("https://s.kw.ai/p/ABCD") == "https://s.kw.ai/p/ABCD"
    assert extract("https://evil.example/video/123") is None
    assert extract("https://kwai.com.evil.example/video/123") is None
    assert extract("https://fakekwai.com/video/123") is None


def test_callback_source_url_recovered_from_button_after_restart():
    helpers = _load_helpers()
    url = "https://k.kwai.com/p/CMtEC74X"
    token = helpers["_url_hash"](url)
    btn = SimpleNamespace(url=url)
    markup = SimpleNamespace(inline_keyboard=[[btn]])
    message = SimpleNamespace(reply_markup=markup, reply_to_message=None)
    assert helpers["_callback_url"](message, token) == url
    assert helpers["_callback_url"](message, "0" * 16) is None


def test_callback_source_url_recovered_from_original_reply():
    helpers = _load_helpers()
    url = "https://www.kwai.com/@gabyvalence/video/5200118832330956026"
    token = helpers["_url_hash"](url)
    orig = SimpleNamespace(text="Link: " + url)
    message = SimpleNamespace(reply_markup=None, reply_to_message=orig)
    assert helpers["_callback_url"](message, token) == url


def test_handler_registered_before_universal():
    tree = ast.parse(INIT.read_text("utf-8"))
    include_routers = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "include_routers"
    )
    routers = [
        n.value.id
        for arg in include_routers.args
        if isinstance(arg, ast.Attribute)
        and isinstance(arg.value, ast.Name)
        for n in [arg]
    ]
    assert routers.index("kwai") < routers.index("universal")


def test_batch_routing_uses_dedicated_handler():
    source = BATCH.read_text("utf-8")
    segment = source.split('if service == "kwai":', 1)[1].split("if service ==", 1)[0]
    assert "from handlers import kwai" in segment
    assert "kwai.process_kwai_link" in segment
    assert "universal.process_universal_link" not in segment


def test_dedicated_telegram_handlers_and_callbacks_registered():
    tree = ast.parse(HANDLER.read_text("utf-8"))
    funcs = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert {"process_kwai_link", "send_kwai_document", "send_kwai_mp3"} <= funcs.keys()
    msg_handler = funcs["process_kwai_link"]
    deco = "\n".join(ast.unparse(node) for node in msg_handler.decorator_list)
    assert ".message(" in deco
    assert ".business_message(" in deco
    assert "doc:kwai:" in "\n".join(ast.unparse(node) for node in funcs["send_kwai_document"].decorator_list)
    assert "audio:kwai:" in "\n".join(ast.unparse(node) for node in funcs["send_kwai_mp3"].decorator_list)


def test_kwai_recognized_in_multiple_supported_links():
    spec = importlib.util.spec_from_file_location("kwai_route_detection", DETECTION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    url = "https://k.kwai.com/p/CMtEC74X"
    result = module.extract_supported_link(f"Veja este video {url}")
    assert result == ("kwai", url)
    assert module.extract_supported_links(
        "Outras plataformas https://youtu.be/ExampleXYZ e " + url
    )[-1] == ("kwai", url)
    assert module.extract_supported_link("https://kwai.com.evil.example/a") is None
