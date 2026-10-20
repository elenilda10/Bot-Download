"""Security-aware Facebook Telegram media cache and callback records.

Authenticated downloads use private, per-user namespaces, including callback
records. Public entries are populated only after successful anonymous download.
Never derive a public entry from an authenticated Firefox profile.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time

CACHE_VERSION = 1
_CALLBACK_RE = re.compile(r"^[a-f0-9]{24}$")
_MAX_AGE = 7 * 24 * 3600


def scope_for(profile: str | None, user_id: int | None) -> str:
    if profile:
        if user_id is None:
            raise ValueError("Authenticated Facebook cache requires a user")
        return f"owner:{user_id}"
    return "public"


def key_for(url: str, scope: str, kind: str) -> str:
    if not url.startswith("https://"):
        raise ValueError("Expected an HTTPS URL")
    if not (scope == "public" or re.fullmatch(r"owner:\d+", scope)):
        raise ValueError("Invalid Facebook cache scope")
    if kind not in {"media", "document", "audio"}:
        raise ValueError("Unknown media type")
    identity = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return f"facebook:v1:{scope}:{kind}:{identity}"


def callback_key(token: str) -> str:
    if not _CALLBACK_RE.fullmatch(token):
        raise ValueError("Invalid Facebook callback token")
    return f"facebook:v1:callback:{token}"


def new_token() -> str:
    return secrets.token_hex(12)


def encode(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def decode(raw: str | None) -> dict | None:
    if not isinstance(raw, str) or len(raw) > 35000:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) and data.get("version") == CACHE_VERSION else None


def callback_record(
    url: str, scope: str, owner_id: int | None, chat_id: int,
) -> dict:
    return {
        "version": CACHE_VERSION, "url": url, "scope": scope,
        "owner_id": owner_id, "chat_id": chat_id,
        "created_at": int(time.time()),
    }


def callback_allowed(
    record: dict | None, user_id: int | None, chat_id: int,
    chat_type: str, *, now: int | None = None,
) -> bool:
    if not record:
        return False
    stamp = record.get("created_at")
    if not isinstance(stamp, int):
        return False
    at = int(time.time()) if now is None else now
    if stamp > at + 60 or at - stamp > _MAX_AGE:
        return False
    scope = record.get("scope")
    if scope == "public":
        return True
    return (
        isinstance(user_id, int)
        and isinstance(chat_id, int)
        and scope == f"owner:{user_id}"
        and record.get("owner_id") == user_id
        and record.get("chat_id") == chat_id
        and chat_type == "private"
    )


def media_entries(record: dict | None) -> list[dict] | None:
    if not record or record.get("version") != CACHE_VERSION:
        return None
    items = record.get("items")
    if not isinstance(items, list) or not (1 <= len(items) <= 10):
        return None
    if not isinstance(record.get("description", ""), str):
        return None
    for entry in items:
        if (
            not isinstance(entry, dict)
            or entry.get("kind") not in {"video", "photo", "document"}
            or not isinstance(entry.get("file_id"), str)
            or not entry["file_id"]
        ):
            return None
    return items
