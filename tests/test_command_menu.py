"""Native menus follow the saved chat locale without affecting request isolation."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from i18n import get_locale, reset_locale, set_locale
from middlewares.localization import LocalizationMiddleware
from services.runtime import command_menu


@pytest.fixture(autouse=True)
def clear_cache():
    command_menu._synced.clear()
    yield
    command_menu._synced.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize('chat_id', [20, -100])
async def test_chat_scope_overrides_telegram_language_and_is_cached(chat_id):
    bot = SimpleNamespace(id=1, set_my_commands=AsyncMock(return_value=True))
    token = set_locale('pt')
    try:
        assert await command_menu.sync_command_menu(bot, chat_id, 'de')
        assert get_locale() == 'pt'
        kwargs = bot.set_my_commands.call_args.kwargs
        assert kwargs['scope'].chat_id == chat_id
        assert kwargs['language_code'] == ''
        assert kwargs['commands'][0]['description'] == 'Starten'
        assert kwargs['commands'][-1] == {'command': 'lang', 'description': 'Sprache ändern'}
        assert await command_menu.sync_command_menu(bot, chat_id, 'de')
        assert bot.set_my_commands.await_count == 1
        assert await command_menu.sync_command_menu(bot, chat_id, 'es')
        assert bot.set_my_commands.await_count == 2
        assert bot.set_my_commands.call_args.kwargs['commands'][-1]['description'] == 'Cambiar idioma'
    finally:
        reset_locale(token)


@pytest.mark.asyncio
async def test_api_failure_retries_without_breaking_handler_or_locale():
    bot = SimpleNamespace(id=1, set_my_commands=AsyncMock(side_effect=TimeoutError('unavailable')))
    db = SimpleNamespace(get_user_info=AsyncMock(return_value=SimpleNamespace(language='de')))
    event = SimpleNamespace(chat=SimpleNamespace(id=20, type='private'), from_user=SimpleNamespace(id=20, language_code='pt'))
    handler = AsyncMock(return_value='handled')
    middleware = LocalizationMiddleware(db)
    for _ in range(2):
        assert await middleware(handler, event, {'bot': bot}) == 'handled'
    assert handler.await_count == bot.set_my_commands.await_count == 2
    assert not command_menu._synced
    assert get_locale() == 'en'


@pytest.mark.asyncio
async def test_existing_saved_locale_is_synced_on_next_callback():
    bot = SimpleNamespace(id=1, set_my_commands=AsyncMock(return_value=True))
    db = SimpleNamespace(get_user_info=AsyncMock(return_value=SimpleNamespace(language='de')))
    message = SimpleNamespace(chat=SimpleNamespace(id=-100, type='supergroup'))
    event = SimpleNamespace(message=message, from_user=SimpleNamespace(id=20, language_code='pt'))
    await LocalizationMiddleware(db)(AsyncMock(), event, {'bot': bot})
    db.get_user_info.assert_awaited_once_with(user_id=-100)
    assert bot.set_my_commands.call_args.kwargs['scope'].chat_id == -100
    assert bot.set_my_commands.call_args.kwargs['commands'][0]['description'] == 'Starten'
