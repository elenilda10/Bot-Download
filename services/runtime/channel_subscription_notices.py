from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from services.runtime.state_store import load_bucket, save_bucket

BUCKET = "channel_subscription_notices"
TTL = 24 * 60 * 60
MAX_PER_USER = 8
MAX_USERS = 4096


@dataclass(frozen=True)
class SubscriptionNotice:
    chat_id: int
    message_id: int
    ephemeral_message_id: int | None
    lang: str
    created_at_epoch: float


def _load() -> dict:
    value = load_bucket(BUCKET, dict)
    return dict(value) if isinstance(value, dict) else {}


def _decode(item) -> SubscriptionNotice | None:
    try:
        notice = SubscriptionNotice(
            chat_id=int(item["chat_id"]),
            message_id=int(item["message_id"]),
            ephemeral_message_id=(
                int(item["ephemeral_message_id"])
                if item.get("ephemeral_message_id") is not None
                else None
            ),
            lang=str(item.get("lang") or "pt"),
            created_at_epoch=float(item["created_at_epoch"]),
        )
    except (TypeError, ValueError, KeyError, AttributeError):
        return None

    if time.time() - notice.created_at_epoch > TTL:
        return None

    if notice.message_id <= 0 and notice.ephemeral_message_id is None:
        return None

    return notice


def list_user_notices(user_id: int) -> list[SubscriptionNotice]:
    rows = _load().get(str(user_id), [])
    if not isinstance(rows, list):
        return []

    notices = []
    for row in rows:
        notice = _decode(row)
        if notice is not None:
            notices.append(notice)
    return notices


def remember_notice(user_id: int, message, lang: str) -> bool:
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    message_id = getattr(message, "message_id", 0) or 0
    ephemeral_id = getattr(
        message, "ephemeral_message_id", None
    )

    if chat_id is None:
        return False

    notice = _decode({
        "chat_id": chat_id,
        "message_id": message_id,
        "ephemeral_message_id": ephemeral_id,
        "lang": lang,
        "created_at_epoch": time.time(),
    })

    if notice is None:
        return False

    state = _load()
    current = list_user_notices(user_id)

    current = [
        item for item in current
        if not (
            item.chat_id == notice.chat_id
            and item.message_id == notice.message_id
            and item.ephemeral_message_id
                == notice.ephemeral_message_id
        )
    ]

    current.append(notice)

    state[str(user_id)] = [
        asdict(item)
        for item in current[-MAX_PER_USER:]
    ]

    if len(state) > MAX_USERS:
        ordered = sorted(
            state,
            key=lambda key: max(
                (
                    float(row.get("created_at_epoch", 0))
                    for row in state[key]
                ),
                default=0,
            ),
            reverse=True,
        )
        state = {
            key: state[key]
            for key in ordered[:MAX_USERS]
        }

    save_bucket(BUCKET, state)
    return True


def remove_notice(user_id: int, notice: SubscriptionNotice):
    state = _load()
    remaining = [
        item for item in list_user_notices(user_id)
        if item != notice
    ]

    if remaining:
        state[str(user_id)] = [
            asdict(item) for item in remaining
        ]
    else:
        state.pop(str(user_id), None)

    save_bucket(BUCKET, state)
