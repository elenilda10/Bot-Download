import time
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from services.logger import logger as logging
from services.settings import SETTING_DISABLED, SETTING_FIELDS, SETTING_VALUES, normalize_setting_value
from services.storage.models import DEFAULT_USER_SETTINGS, Settings, User

logging = logging.bind(service="db_users")


class UserRepositoryMixin:
    def _insert(self, model):
        if self._dialect_name == "postgresql":
            return pg_insert(model)
        return sqlite_insert(model)

    async def upsert_chat(
        self,
        user_id: int,
        user_name: str | None,
        user_username: str | None,
        chat_type: str | None,
        language: str | None = None,
        status: str = "active",
        referred_by: int | None = None,
        source: str | None = None,
    ) -> None:
        values = {
            "user_name": user_name,
            "user_username": user_username,
            "chat_type": chat_type,
            "language": language,
            "status": status,
        }
        if referred_by is not None:
            values["referred_by"] = referred_by
        if source is not None:
            values["source"] = source

        async with self.SessionLocal() as session:
            async with session.begin():
                stmt = (
                    self._insert(User)
                    .values(user_id=user_id, **values)
                    .on_conflict_do_update(index_elements=[User.user_id], set_=values)
                )
                await session.execute(stmt)
        self._status_cache[int(user_id)] = (time.monotonic(), status)

    async def delete_user(self, user_id: int) -> None:
        async with self.SessionLocal() as session:
            async with session.begin():
                await session.execute(delete(Settings).where(Settings.user_id == user_id))
                await session.execute(delete(User).where(User.user_id == user_id))
        user_id_int = int(user_id)
        self._status_cache.pop(user_id_int, None)
        self._settings_cache.pop(user_id_int, None)

    async def get_user_counts(self) -> dict[str, int]:
        async with self.SessionLocal() as session:
            result = await session.execute(
                select(
                    func.count(User.user_id).label("user_count"),
                    func.count(User.user_id).filter(User.status == "active").label("active_user_count"),
                    func.count(User.user_id).filter(User.status != "active").label("inactive_user_count"),
                    func.count(User.user_id).filter(User.chat_type == "private").label("private_chat_count"),
                    func.count(User.user_id).filter(
                        User.chat_type != "private", User.chat_type.isnot(None)
                    ).label("group_chat_count"),
                )
            )
            row = result.one()
            return {
                "user_count": row.user_count,
                "active_user_count": row.active_user_count,
                "inactive_user_count": row.inactive_user_count,
                "private_chat_count": row.private_chat_count,
                "group_chat_count": row.group_chat_count,
            }

    async def get_user_setting(self, user_id: int, field: str) -> str | None:
        settings = await self.user_settings(user_id)
        return settings.get(field)

    async def user_settings(self, user_id: int) -> dict[str, str]:
        user_id_int = int(user_id)
        now = time.monotonic()
        cached = self._settings_cache.get(user_id_int)
        self._prune_local_caches(now)
        if cached and now - cached[0] <= self._settings_ttl_seconds:
            return dict(cached[1])

        try:
            async with self.SessionLocal() as session:
                result = await session.execute(
                    select(Settings).where(Settings.user_id == user_id_int).limit(1)
                )
                settings = result.scalar_one_or_none()
                if settings:
                    payload = {
                        "captions": settings.captions or SETTING_DISABLED,
                        "delete_message": settings.delete_message or SETTING_DISABLED,
                        "info_buttons": settings.info_buttons or SETTING_DISABLED,
                        "url_button": settings.url_button or SETTING_DISABLED,
                        "audio_button": settings.audio_button or SETTING_DISABLED,
                        "music_button": getattr(settings, "music_button", None) or SETTING_DISABLED,
                        "file_button": getattr(settings, "file_button", None) or SETTING_DISABLED,
                        "video_quality": getattr(settings, "video_quality", None) or "best",
                        "as_document": getattr(settings, "as_document", None) or SETTING_DISABLED,
                        "audio_format": getattr(settings, "audio_format", None) or "mp3",
                    }
                    self._settings_cache[user_id_int] = (now, payload)
                    return dict(payload)
        except Exception as exc:
            if cached:
                logging.warning(
                    "Failed to fetch user settings, using stale cache: user_id=%s error=%s",
                    user_id_int,
                    exc,
                )
                return dict(cached[1])
            logging.warning(
                "Failed to fetch user settings, using defaults: user_id=%s error=%s",
                user_id_int,
                exc,
            )

        payload = dict(DEFAULT_USER_SETTINGS)
        self._settings_cache[user_id_int] = (now, payload)
        return dict(payload)

    async def set_user_setting(self, user_id: int, field: str, value: str) -> None:
        user_id_int = int(user_id)
        if field not in SETTING_FIELDS:
            raise ValueError(f"Unsupported user setting field: {field}")

        normalized_value = normalize_setting_value(value)
        if normalized_value is None or normalized_value not in SETTING_VALUES:
            raise ValueError(f"Unsupported user setting value for {field}: {value}")

        async with self.SessionLocal() as session:
            async with session.begin():
                values = {"user_id": user_id_int, field: normalized_value}
                stmt = (
                    self._insert(Settings)
                    .values(**values)
                    .on_conflict_do_update(
                        index_elements=[Settings.user_id],
                        set_={field: normalized_value},
                    )
                )
                await session.execute(stmt)
        self._settings_cache.pop(user_id_int, None)
        updated = await self.user_settings(user_id_int)
        updated[field] = normalized_value
        self._settings_cache[user_id_int] = (time.monotonic(), updated)

    async def set_inactive(self, user_id: int) -> None:
        async with self.SessionLocal() as session:
            async with session.begin():
                user_id_int = int(user_id)
                await session.execute(update(User).where(User.user_id == user_id_int).values(status="inactive"))
        self._status_cache[int(user_id)] = (time.monotonic(), "inactive")

    async def set_active(self, user_id: int) -> None:
        async with self.SessionLocal() as session:
            async with session.begin():
                user_id_int = int(user_id)
                await session.execute(update(User).where(User.user_id == user_id_int).values(status="active"))
        self._status_cache[int(user_id)] = (time.monotonic(), "active")

    async def status(self, user_id: int) -> str | None:
        user_id_int = int(user_id)
        now = time.monotonic()
        self._prune_local_caches(now)
        cached = self._status_cache.get(user_id_int)
        if cached and now - cached[0] <= self._status_ttl_seconds:
            return cached[1]

        async with self.SessionLocal() as session:
            result = await session.execute(select(User.status).where(User.user_id == user_id_int))
            value = result.scalar()
            self._status_cache[user_id_int] = (now, value)
            return value

    async def get_user_info(self, user_id: int) -> Any:
        async with self.SessionLocal() as session:
            result = await session.execute(
                select(User.user_name, User.user_username, User.status, User.language).where(User.user_id == user_id)
            )
            return result.first()

    async def get_all_users_info(self) -> list[Any]:
        async with self.SessionLocal() as session:
            result = await session.execute(
                select(User.user_id, User.chat_type, User.user_name, User.user_username, User.language, User.status)
            )
            return result.all()

    async def ban_user(self, user_id: int) -> None:
        async with self.SessionLocal() as session:
            async with session.begin():
                user_id_int = int(user_id)
                await session.execute(update(User).where(User.user_id == user_id_int).values(status="ban"))
        self._status_cache[int(user_id)] = (time.monotonic(), "ban")

    async def get_language(self, user_id: int) -> str:
        try:
            async with self.SessionLocal() as session:
                result = await session.execute(
                    select(User.language).where(User.user_id == int(user_id))
                )
                val = result.scalar()
                return str(val).lower() if val else "pt"
        except Exception:
            return "pt"

    async def set_language(self, user_id: int, lang: str) -> None:
        async with self.SessionLocal() as session:
            async with session.begin():
                await session.execute(
                    update(User).where(User.user_id == int(user_id)).values(language=lang)
                )
