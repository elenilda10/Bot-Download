"""Catalog contracts, request isolation and real language-selection functions."""
import ast
import asyncio
import importlib.util
from pathlib import Path
import re
from string import Formatter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import messages as bm
from i18n import (SUPPORTED_LOCALES, bot_commands, catalog, get_locale,
                  normalize_locale, reset_locale, set_locale, t)
from keyboards.inline_keyboards import language_keyboard, stats_keyboard

ROOT = Path(__file__).resolve().parents[1]


def isolated_module(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(('value', 'expected'), [('es-MX', 'es'), ('es_ES', 'es'), ('pt-BR', 'pt'), ('EN', 'en'), ('fr', 'en'), ('', 'en'), (None, 'en')])
def test_locale_variants_and_fallback(value, expected):
    assert normalize_locale(value) == expected


def test_catalogs_have_identical_keys_placeholders_markup_and_emoji_ids():
    source = catalog('en')
    for lang in SUPPORTED_LOCALES:
        translated = catalog(lang)
        assert translated.keys() == source.keys()
        for key, value in translated.items():
            fields = {field for _, field, _, _ in Formatter().parse(source[key]) if field is not None}
            assert {field for _, field, _, _ in Formatter().parse(value) if field is not None} == fields, (lang, key)
            assert re.findall(r'<[^>]+>', value) == re.findall(r'<[^>]+>', source[key]), (lang, key)
            assert value.strip(), (lang, key)
            if fields:
                value.format_map({field: 'sample' for field in fields})


def test_missing_translation_falls_back_to_english_without_blank_text(monkeypatch):
    es = dict(catalog('es'))
    es.pop('language.prompt')
    monkeypatch.setattr('i18n.catalog', lambda lang: es if lang == 'es' else catalog(lang))
    assert t('language.prompt', 'es') == catalog('en')['language.prompt']


def test_spanish_download_upload_preserve_premium_emojis_and_external_caption():
    token = set_locale('es')
    try:
        download = bm.downloading_video_status(platform='Instagram')
        upload = bm.uploading_status(platform='Instagram')
        assert 'Descargando contenido' in download and '5435977112538324435' in download
        assert 'Enviando a Telegram' in upload and '5866355487255039002' in upload
        caption = bm.captions('on', 'Olá! <b>Hello!</b> & hola', 'https://t.me/test')
        assert 'Olá! &lt;b&gt;Hello!&lt;/b&gt; &amp; hola' in caption
        assert 'Desarrollado por' in caption
        assert 'https://t.me/test' in caption
        assert len(bm.captions('on', 'x' * 6000, 'https://t.me/test')) <= 1024
    finally:
        reset_locale(token)


@pytest.mark.parametrize('lang', SUPPORTED_LOCALES)
def test_language_and_stats_buttons_keep_callback_protocol(lang):
    buttons = [button for row in language_keyboard(lang).inline_keyboard for button in row]
    selected = [b for b in buttons if b.text.startswith('✅')]
    assert len(selected) == 1 and selected[0].callback_data == 'set_lang:' + lang
    assert {'set_lang:pt', 'set_lang:en', 'set_lang:es'} <= {b.callback_data for b in buttons}
    markup = stats_keyboard(lang=lang)
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert any(data.startswith('stats:Week:') for data in callbacks if data)
    if lang == 'es':
        assert any('Mes' in b.text for row in markup.inline_keyboard for b in row)


def test_command_descriptions_are_localized_and_names_stay_stable():
    for lang in SUPPORTED_LOCALES:
        commands = bot_commands(lang)
        assert [c['command'] for c in commands] == ['start', 'help', 'trad', 'settings', 'stats', 'ping']
        assert all(1 <= len(c['description']) <= 256 for c in commands)
    assert bot_commands('es')[3]['description'] == 'Configuración'


@pytest.mark.asyncio
async def test_localization_prefers_saved_group_or_user_and_isolates_concurrent_requests():
    module = isolated_module('middlewares/localization.py', 'i18n_middleware_test')
    records = {-100: 'es', 20: 'en'}
    async def get_record(*, user_id):
        return SimpleNamespace(language=records[user_id]) if user_id in records else None
    database = SimpleNamespace(get_user_info=AsyncMock(side_effect=get_record))
    middleware = module.LocalizationMiddleware(database)
    group = SimpleNamespace(chat=SimpleNamespace(id=-100, type='supergroup'), from_user=SimpleNamespace(id=20, language_code='pt'))
    private = SimpleNamespace(chat=SimpleNamespace(id=20, type='private'), from_user=SimpleNamespace(id=20, language_code='es'))
    callback = SimpleNamespace(message=group, from_user=group.from_user)
    async def handler(event, data):
        initial = get_locale()
        await asyncio.sleep(0)
        assert get_locale() == initial
        assert data['user_lang'] == initial
        return bm.media_transfer_status('Reddit', lang='pt')
    results = await asyncio.gather(middleware(handler, callback, {}), middleware(handler, private, {}))
    assert 'Descargando' in results[0] and 'Downloading' in results[1]
    assert get_locale() == 'en'
    new = SimpleNamespace(from_user=SimpleNamespace(id=99, language_code='es-MX'))
    assert 'Descargando' in await middleware(handler, new, {})


@pytest.mark.asyncio
async def test_locale_resets_on_cancellation():
    module = isolated_module('middlewares/localization.py', 'i18n_cancel_test')
    middleware = module.LocalizationMiddleware(SimpleNamespace(get_user_info=AsyncMock(return_value=None)))
    event = SimpleNamespace(from_user=SimpleNamespace(id=7, language_code='es'))
    async def cancelled(*args):
        assert get_locale() == 'es'
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await middleware(cancelled, event, {})
    assert get_locale() == 'en'


class FakeCallback:
    def __init__(self, data='set_lang:es'):
        self.data = data
        self.from_user = SimpleNamespace(id=20, full_name='Test', language_code='en')
        self.message = SimpleNamespace(chat=SimpleNamespace(id=-100, type='supergroup'), edit_text=AsyncMock())
        self.answer = AsyncMock()


def selection_runtime(*, admin=True, fails=False):
    source = ast.parse((ROOT / 'handlers/settings_menu.py').read_text())
    names = {'handle_language_selection', '_resolve_settings_target', 'show_language_view'}
    tree = ast.Module(body=[n for n in source.body if isinstance(n, ast.AsyncFunctionDef) and n.name in names], type_ignores=[])
    records = {}
    async def save(target, lang):
        if fails:
            raise RuntimeError('database offline')
        records[target] = lang
    namespace = {
        'types': SimpleNamespace(CallbackQuery=FakeCallback), '_t': t, 'SUPPORTED_LOCALES': SUPPORTED_LOCALES,
        'set_locale': set_locale, 'reset_locale': reset_locale,
        'kb': SimpleNamespace(language_keyboard=language_keyboard),
        'bm': bm, 'TelegramBadRequest': type('TelegramBadRequest', (Exception,), {}),
        '_is_message_not_modified_error': lambda _: False,
        'logging': SimpleNamespace(exception=lambda *args: None),
        '_ensure_settings_entities': AsyncMock(),
        'user_mod': SimpleNamespace(db=SimpleNamespace(set_language=AsyncMock(side_effect=save)), _update_info_cache={}, _is_group_admin=AsyncMock(return_value=admin)),
    }
    exec(compile(tree, 'selection', 'exec'), namespace)
    return namespace, records


@pytest.mark.asyncio
async def test_language_selection_saves_group_not_clicking_user_and_uses_new_locale():
    ns, records = selection_runtime()
    call = FakeCallback()
    await ns['handle_language_selection'](call)
    assert records == {-100: 'es'}
    assert 'español' in call.answer.await_args.args[0]
    assert 'Selecciona' in call.message.edit_text.await_args.args[0]
    assert get_locale() == 'en'


@pytest.mark.asyncio
@pytest.mark.parametrize(('admin', 'data', 'fails'), [(False, 'set_lang:es', False), (True, 'set_lang:xx', False), (True, 'set_lang:es', True)])
async def test_denied_invalid_or_failed_save_does_not_claim_success(admin, data, fails):
    ns, records = selection_runtime(admin=admin, fails=fails)
    call = FakeCallback(data)
    await ns['handle_language_selection'](call)
    assert records == {}
    call.message.edit_text.assert_not_awaited()
    assert 'Idioma cambiado' not in call.answer.await_args.args[0]
    assert get_locale() == 'en'


@pytest.mark.asyncio
async def test_sqlite_persists_language_and_profile_refresh_does_not_overwrite_it(monkeypatch):
    import sys
    from unittest.mock import Mock
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    settings = isolated_module('services/settings.py', 'i18n_settings_test')
    monkeypatch.setitem(sys.modules, 'services.settings', settings)
    models = isolated_module('services/storage/models.py', 'i18n_models_test')
    monkeypatch.setitem(sys.modules, 'services.storage.models', models)
    logger = Mock()
    logger.bind.return_value = logger
    monkeypatch.setitem(sys.modules, 'services.logger', SimpleNamespace(logger=logger))
    repository = isolated_module('services/storage/user_repository.py', 'i18n_repository_test')
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)
    class Repo(repository.UserRepositoryMixin):
        def __init__(self):
            self.SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
            self._dialect_name = 'sqlite'
            self._status_cache = {}
    try:
        repo = Repo()
        await repo.upsert_chat(20, 'Test', None, 'private', language='pt-BR')
        await repo.set_language(20, 'es')
        await repo.upsert_chat(20, 'New name', None, 'private', language='en')
        assert await Repo().get_language(20) == 'es'
        await repo.upsert_chat(-100, 'Group', None, 'public', language='es-MX')
        assert await Repo().get_language(-100) == 'es'
    finally:
        await engine.dispose()


def test_spanish_chart_labels_without_changing_technical_period_values():
    import datetime
    tree = ast.parse((ROOT / 'services/stats/chart.py').read_text())
    names = {'_month_label', '_weekday_label', '_period_label', '_stats_bucket_name'}
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {'_t': t, 'datetime': datetime}
    exec(compile(ast.Module(body=body, type_ignores=[]), 'chart_labels', 'exec'), namespace)
    assert namespace['_period_label']('Year', 'es') == 'Año'
    assert namespace['_month_label'](8, 'es') == 'ago'
    assert namespace['_weekday_label'](datetime.datetime(2026, 10, 28), 'es') == 'mié'
    assert namespace['_stats_bucket_name']('Year', 'es') == 'mes'


@pytest.mark.asyncio
async def test_background_progress_callback_keeps_request_language():
    from i18n import bind_locale
    token = set_locale('es')
    async def progress():
        await asyncio.sleep(0)
        return bm.media_transfer_status('TikTok')
    callback = bind_locale(progress)
    reset_locale(token)
    other = set_locale('en')
    try:
        assert 'Descargando' in await callback()
        assert get_locale() == 'en'
    finally:
        reset_locale(other)


def test_every_static_catalog_call_in_runtime_points_to_an_existing_key():
    paths = [*ROOT.glob('handlers/*.py'), *ROOT.glob('messages/*.py'),
             *ROOT.glob('keyboards/*.py'), *ROOT.glob('middlewares/*.py'),
             ROOT / 'services/settings.py', ROOT / 'services/stats/chart.py', ROOT / 'services/inline/send_flow.py']
    keys = catalog('en')
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == '_t' and node.args
                    and isinstance(node.args[0], ast.Constant)):
                assert node.args[0].value in keys, (path, node.lineno, node.args[0].value)


def test_translation_command_omits_generated_footers_in_all_supported_languages():
    tree = ast.parse((ROOT / 'handlers/commands.py').read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_clean_translation_input')
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'translation_cleanup', 'exec'), namespace)
    for footer in ('Powered by Save Vídeo DL Bot', 'Desenvolvido por Save Vídeo DL Bot', 'Desarrollado por Save Vídeo DL Bot'):
        assert namespace['_clean_translation_input']('Hello / Olá / Hola\n\n🚀 ' + footer) == 'Hello / Olá / Hola'
