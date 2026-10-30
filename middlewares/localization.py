"""Resolve saved chat/user locale before guards and isolate it per request."""
from aiogram import BaseMiddleware
from i18n import normalize_locale, reset_locale, set_locale


class LocalizationMiddleware(BaseMiddleware):
    def __init__(self, database=None):
        if database is None:
            from app_context import db
            database = db
        self._db = database

    async def __call__(self, handler, event, data):
        user = getattr(event, 'from_user', None)
        message = getattr(event, 'message', None) or event
        chat = getattr(message, 'chat', None)
        group = chat is not None and getattr(chat, 'type', None) in {'group', 'supergroup'}
        target_id = getattr(chat, 'id', None) if group else getattr(user, 'id', None)
        saved = None
        if target_id is not None:
            try:
                record = await self._db.get_user_info(user_id=target_id)
                saved = getattr(record, 'language', None) if record else None
            except Exception:
                pass
        suggestion = getattr(chat, 'language_code', None) if group else getattr(user, 'language_code', None)
        locale = normalize_locale(saved or suggestion)
        token = set_locale(locale)
        data['user_lang'] = locale
        data['lang'] = locale
        try:
            return await handler(event, data)
        finally:
            reset_locale(token)
