"""Local catalogs for bot interface text; never translate user or platform content."""
from contextvars import ContextVar
from functools import lru_cache
import json
import logging
from pathlib import Path

SUPPORTED_LOCALES = ('pt', 'en', 'es', 'de')
DEFAULT_LOCALE = 'en'
LOCALE_NAMES = {'pt': '🇧🇷 Português', 'en': '🇺🇸 English', 'es': '🇪🇸 Español', 'de': '🇩🇪 Deutsch'}
_locale = ContextVar('bot_interface_locale', default=None)


def normalize_locale(value: str | None, fallback: str = DEFAULT_LOCALE) -> str:
    code = str(value or '').strip().lower().replace('_', '-').split('-', 1)[0]
    return code if code in SUPPORTED_LOCALES else fallback if fallback in SUPPORTED_LOCALES else DEFAULT_LOCALE


def get_locale() -> str:
    return _locale.get() or DEFAULT_LOCALE


def set_locale(value: str | None):
    return _locale.set(normalize_locale(value))


def reset_locale(token) -> None:
    _locale.reset(token)


@lru_cache(maxsize=None)
def catalog(locale: str) -> dict[str, str]:
    path = Path(__file__).with_name('locales') / (normalize_locale(locale) + '.json')
    return json.loads(path.read_text(encoding='utf-8'))


def t(key: str, lang: str | None = None, **values) -> str:
    locale = effective_locale(lang)
    text = catalog(locale).get(key)
    if text is None:
        text = catalog(DEFAULT_LOCALE).get(key)
        logging.getLogger(__name__).warning('Missing interface translation: locale=%s key=%s', locale, key)
    if text is None:
        raise KeyError('Unknown interface translation key: ' + key)
    return text.format_map(values) if values else text


def request_locale() -> str | None:
    return _locale.get()


def bot_commands(lang: str):
    return [{"command": name, "description": t("commands." + name, lang)}
            for name in ("start", "help", "trad", "settings", "stats", "ping")]


def effective_locale(lang: str | None = None) -> str:
    """The saved request locale wins over legacy per-handler Telegram/user defaults."""
    return request_locale() or normalize_locale(lang)


def bind_locale(callback):
    """Keep request language when a queue worker invokes a progress callback."""
    locale = get_locale()
    async def localized(*args, **kwargs):
        token = set_locale(locale)
        try:
            return await callback(*args, **kwargs)
        finally:
            reset_locale(token)
    return localized
