"""Recognized failures are useful, localized, isolated, and never expose stderr."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import messages as bm
from utils.download_errors import (
    REASONS,
    best_failure,
    classify_failure,
    close_failure_context,
    download_attempt,
    failure_context,
    note_failure,
    platform_name,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_request():
    token = failure_context()
    yield
    close_failure_context(token)


@pytest.mark.parametrize('error,expected', [
    (TimeoutError(), 'timeout'), (ConnectionError(), 'connection'),
    (TypeError('secret'), 'internal'),
    ('HttpError: TooManyRedirects: Exceeded 30 redirects.', 'redirect_loop'),
    ('ERROR: HTTP Error 403: Forbidden', 'access_denied'),
    ('HTTP 401', 'authentication'), ('status code: 404', 'not_found'),
    ('HTTP Error 429', 'rate_limited'),
    ('[reddit][error] "You\'ve been blocked by network security."', 'access_denied'),
    ('ERROR: Login required', 'authentication'),
    ('ERROR: Unsupported URL: https://test.invalid/', 'unsupported'),
    ('Video not available in your country', 'region'),
    ('private video', 'restricted'),
    ('https://example.invalid/private/login?cookie=SECRET', 'unknown'),
    (ValueError('none found'), 'unknown'),
])
def test_classifies_only_supported_evidence(error, expected):
    assert classify_failure(error).reason == expected


@pytest.mark.parametrize('lang', ['pt', 'en', 'es'])
@pytest.mark.parametrize('reason', sorted(REASONS))
def test_every_reason_has_localized_action_without_raw_output(lang, reason):
    text = bm.download_failure_message(reason, lang=lang, platform='Instagram', reference='A1B2C3D4')
    assert 'Instagram' in text and 'A1B2C3D4' in text
    assert 'download_error.' not in text
    assert len(text.split('\n\n')) == 4
    assert len(text) < 1200
    assert 'cookie' not in text.lower() and 'traceback' not in text.lower()


def test_escape_and_unknown_fallback():
    text = bm.download_failure_message('SECRET', platform='<x>', reference='<y>', lang='pt')
    assert 'SECRET' not in text
    assert '&lt;x&gt;' in text and '&lt;y&gt;' in text
    assert 'Não recebemos uma causa específica' in text


def test_http_exception_and_provider_code():
    assert classify_failure(SimpleNamespace(status_code=403)).reason == 'access_denied'
    assert classify_failure(SimpleNamespace(code='image_http_429')).reason == 'rate_limited'
    assert classify_failure(SimpleNamespace(code='native_video_missing_or_oversized')).reason == 'unknown'
    assert classify_failure(type('TelegramBadRequest', (Exception,), {})('secret')).reason == 'delivery'


@pytest.mark.asyncio
async def test_failed_fallback_preserves_specific_cause_success_clears_it():
    @download_attempt
    async def attempt(result):
        note_failure('redirect_loop')
        note_failure('unsupported')
        return result
    assert await attempt(None) is None
    assert best_failure().reason == 'redirect_loop'
    assert await attempt(object())
    assert best_failure().reason == 'unknown'


@pytest.mark.asyncio
async def test_concurrent_requests_and_cancellation_are_isolated():
    async def request(reason):
        token = failure_context()
        try:
            note_failure(reason)
            await asyncio.sleep(0)
            return best_failure().reason
        finally:
            close_failure_context(token)
    assert await asyncio.gather(request('timeout'), request('authentication')) == ['timeout', 'authentication']
    assert best_failure().reason == 'unknown'
    @download_attempt
    async def cancelled():
        note_failure('timeout')
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await cancelled()
    assert best_failure().reason == 'unknown'


@pytest.mark.parametrize('url,expected', [
    ('https://k.kwai.com/p/x', 'Kwai'), ('https://www.instagram.com/p/X', 'Instagram'),
    ('https://instagram.com.evil.test', None), ('https://evil.test/instagram.com', None),
])
def test_platform_from_verified_hostname(url, expected):
    assert platform_name(url) == expected


def error_handler():
    source = ast.parse((ROOT / 'handlers/telegram_ui_utils.py').read_text())
    function = next(n for n in source.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'handle_download_error')
    module = ast.Module(body=[function], type_ignores=[])
    resolve = AsyncMock(return_value='pt')
    send = AsyncMock()
    logger = Mock()
    namespace = {'types': SimpleNamespace(Message=object), 'Optional': __import__('typing').Optional,
                 'Any': __import__('typing').Any, 'bm': bm, 'best_failure': best_failure,
                 'classify_failure': classify_failure, 'platform_name': platform_name,
                 'secrets': SimpleNamespace(token_hex=lambda _: 'a1b2c3d4'),
                 '_resolve_user_lang': resolve, '_send_with_reaction': send, 'logging': logger,
                 'get_message_text': lambda m: m.text}
    exec(compile(module, '<error-handler>', 'exec'), namespace)  # noqa: S102 - isolated production function
    return namespace['handle_download_error'], send, logger


@pytest.mark.asyncio
async def test_handler_formats_cause_and_logs_same_reference_without_secrets():
    handle, send, logger = error_handler()
    await handle(SimpleNamespace(text='https://instagram.com/p/X'),
                 error=Exception('HttpError: TooManyRedirects cookie=SECRET'), business_id='connection')
    text = send.await_args.args[1]
    assert 'Instagram' in text and 'redirecionamentos' in text
    assert 'A1B2C3D4' in text and 'SECRET' not in text and 'HttpError' not in text
    assert 'error' not in send.await_args.kwargs
    assert send.await_args.kwargs['parse_mode'] == 'HTML'
    assert logger.event.call_args.kwargs['error_reference'] == 'A1B2C3D4'
    assert logger.event.call_args.kwargs['failure_code'] == 'redirect_loop'


@pytest.mark.asyncio
async def test_handler_retains_collected_cause_and_explicit_text_override():
    handle, send, logger = error_handler()
    note_failure('authentication')
    await handle(SimpleNamespace(text=''), error=ValueError('unknown'))
    assert 'solicitou login' in send.await_args.args[1]
    await handle(SimpleNamespace(text=''), text='Custom message')
    assert send.await_args.args[1] == 'Custom message'
    assert logger.event.call_count == 1
