"""Isolated routing and actual Reddit send-flow tests, with no network/secrets."""
from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib.util
import json
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(file, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


links = load('services/links/reddit.py', 'reddit_links_test')
detection = load('services/links/detection.py', 'reddit_detection_test')


@pytest.mark.parametrize('url', [
    'https://www.reddit.com/r/test/comments/abc123/title/',
    'https://old.reddit.com/comments/abc123/',
    'https://www.reddit.com/gallery/abc123', 'https://redd.it/abc123',
    'https://www.reddit.com/r/test/s/AbCdEf',
    'https://v.redd.it/video123', 'https://i.redd.it/photo.jpg',
])
def test_reddit_links_reach_dedicated_detector(url):
    assert links.is_reddit_media_url(url)
    assert links.extract_reddit_url('Veja: (' + url + ')') == url
    assert detection.extract_supported_link(url) == ('reddit', url)


@pytest.mark.parametrize('url', ['https://reddit.com.evil.test/a', 'https://fakereddit.com/a',
                                 'https://example.com/reddit.com/a', 'https://reddit.com@evil.test/a'])
def test_lookalike_hosts_are_rejected(url):
    assert not links.is_reddit_media_url(url)
    assert links.extract_reddit_url(url) is None
    assert detection.extract_supported_link(url) is None


def test_profiles_and_subreddit_listings_are_not_mass_downloaded():
    assert not links.is_reddit_media_url('https://www.reddit.com/r/test/')
    assert not links.is_reddit_media_url('https://www.reddit.com/user/test/')
    preview = 'https://preview.redd.it/a.jpg?width=100&signature=abc'
    assert links.normalize_reddit_url(preview) == preview
    assert detection.extract_supported_link(preview) == ('reddit', preview)


def test_router_batch_pending_and_multilink_integration():
    source = (ROOT / 'handlers/__init__.py').read_text()
    assert source.index('reddit.router') < source.index('universal.router')
    source = (ROOT / 'handlers/media_download.py').read_text()
    assert 'await reddit.process_reddit(message, direct_url=url)' in source
    assert detection.extract_supported_links('https://redd.it/abc123 https://youtu.be/demo') == [
        ('reddit', 'https://redd.it/abc123'), ('youtube', 'https://youtu.be/demo')]
    tree = ast.parse((ROOT / 'handlers/reddit.py').read_text())
    process = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'process_reddit')
    decorators = ' '.join(ast.unparse(n) for n in process.decorator_list)
    assert 'router.message' in decorators and 'router.business_message' in decorators


class Message:
    def __init__(self, message_id=91):
        self.message_id = message_id
        self.from_user = SimpleNamespace(id=2)
        self.chat = SimpleNamespace(id=-123, type='supergroup')
        self.business_connection_id = None
        self.text = 'https://www.reddit.com/r/test/comments/abc123/title/'
        self.reply_markup = None
        self.sends = []
    async def answer(self, *args, **kwargs):
        return SimpleNamespace(message_id=100)
    async def send(self, kind, **kwargs):
        self.sends.append((kind, kwargs))
        result = SimpleNamespace(document=None, video=None, photo=None, audio=None)
        setattr(result, kind, [SimpleNamespace(file_id='photo-id')] if kind == 'photo' else SimpleNamespace(file_id=kind + '-id'))
        return result
    async def answer_photo(self, **kwargs): return await self.send('photo', **kwargs)
    async def answer_video(self, **kwargs): return await self.send('video', **kwargs)
    async def answer_document(self, **kwargs): return await self.send('document', **kwargs)
    async def answer_audio(self, **kwargs): return await self.send('audio', **kwargs)
    async def answer_media_group(self, **kwargs):
        self.sends.append(('group', kwargs))
        results = []
        for item in kwargs['media']:
            result = SimpleNamespace(document=None, video=None, photo=None)
            setattr(result, item.kind, [SimpleNamespace(file_id='photo-id')] if item.kind == 'photo' else SimpleNamespace(file_id=item.kind + '-id'))
            results.append(result)
        return results


def runtime(tmp_path, *, kinds=('video',), as_document=False):
    tree = ast.parse((ROOT / 'handlers/reddit.py').read_text())
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if hasattr(node, 'decorator_list'):
                node.decorator_list = []
            nodes.append(node)
    store = {}
    async def get(key): return store.get(key)
    async def add(key, value, kind): store[key] = value
    async def download(url, *, output_dir, max_file_size):
        items = []
        for index, kind in enumerate(kinds):
            path = Path(output_dir) / f'{index}.{ "jpg" if kind == "photo" else "mp4"}'
            path.write_bytes(b'local-media')
            items.append(SimpleNamespace(type=kind, url=str(path)))
        return SimpleNamespace(media_list=items, description='Original Reddit caption')
    async def video_kwargs(path):
        thumb = str(Path(path).with_suffix('.thumb.jpg'))
        Path(thumb).write_bytes(b'thumb')
        return {'supports_streaming': True}, thumb
    ns = dict(asyncio=asyncio, hashlib=hashlib, json=json, shutil=shutil, tempfile=tempfile,
              Path=Path, FSInputFile=lambda path: str(path), types=SimpleNamespace(
                  Message=Message, InputMediaPhoto=lambda **k: SimpleNamespace(kind='photo', **k),
                  InputMediaVideo=lambda **k: SimpleNamespace(kind='video', **k),
                  InputMediaDocument=lambda **k: SimpleNamespace(kind='document', **k)),
              TelegramBadRequest=type('TelegramBadRequest', (Exception,), {}),
              build_video_send_kwargs=video_kwargs, OUTPUT_DIR=str(tmp_path), MAX_FILE_SIZE=1000,
              bot=object(), logging=Mock(), REDDIT_URL_REGEX=links.REDDIT_URL_REGEX,
              extract_reddit_url=links.extract_reddit_url,
              firefox_profile=lambda platform: '/fake-profile',
              build_media_cache_key=lambda base, variant: base + ':' + variant,
              db=SimpleNamespace(user_settings=AsyncMock(return_value={'captions': 'on'}), get_file_id=AsyncMock(side_effect=get), add_file=AsyncMock(side_effect=add), get_language=AsyncMock(return_value='pt')),
              cmd_mod=SimpleNamespace(is_banned=AsyncMock(return_value=False)),
              ChatAction=SimpleNamespace(UPLOAD_VIDEO='video'),
              bm=SimpleNamespace(captions=lambda mode, caption, bot: caption if mode != 'off' else '', downloading_video_status=lambda **_: 'downloading'),
              load_user_settings=AsyncMock(return_value={'as_document': 'on' if as_document else 'off', 'captions': 'on'}),
              get_bot_url=AsyncMock(return_value='https://t.me/bot'),
              get_message_text=lambda m: m.text,
              download_reddit_media=AsyncMock(side_effect=download),
              should_skip_duplicate_business_message=AsyncMock(return_value=False),
              claim_message_request=AsyncMock(return_value=SimpleNamespace(mark_success=Mock(), finish=Mock())),
              )
    for name in ('send_analytics','react_to_message','send_chat_action_if_needed','safe_delete_message',
                 'maybe_delete_user_message','handle_download_error'):
        ns[name] = AsyncMock()
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(module, 'handlers/reddit.py', 'exec', flags=__import__('__future__').annotations.compiler_flag), ns)
    ns['_keyboard'] = lambda *args: None
    return ns, store


@pytest.mark.asyncio
@pytest.mark.parametrize('kinds', [('photo',), ('video',), ('photo','video'), tuple(['photo'] * 11)])
@pytest.mark.parametrize('as_document', [False, True])
async def test_fresh_cached_single_and_mixed_albums_reply_cleanup_and_keep_captions(tmp_path, kinds, as_document):
    ns, _ = runtime(tmp_path, kinds=kinds, as_document=as_document)
    for index in (91, 92):
        message = Message(index)
        await ns['process_reddit'](message)
        ns['handle_download_error'].assert_not_awaited()
        assert all(k['reply_to_message_id'] == index and k['allow_sending_without_reply'] for _, k in message.sends)
        assert message.sends[0][1].get('caption', 'Original Reddit caption') == 'Original Reddit caption'
        if len(kinds) == 11:
            assert len(message.sends[0][1]['media']) == 10
            assert len(message.sends) == 2
            assert not message.sends[1][1].get('caption')
    assert ns['download_reddit_media'].await_count == 1
    assert not list(tmp_path.iterdir())
    keys = [call.args[0] for call in ns['db'].get_file_id.await_args_list]
    assert all('owner:2:chat:-123' in key for key in keys)


@pytest.mark.asyncio
async def test_ban_and_duplicate_stop_before_download(tmp_path):
    ns, _ = runtime(tmp_path)
    ns['cmd_mod'].is_banned.return_value = True
    await ns['process_reddit'](Message())
    ns['download_reddit_media'].assert_not_awaited()
    ns['cmd_mod'].is_banned.return_value = False
    ns['claim_message_request'].return_value = None
    await ns['process_reddit'](Message())
    ns['download_reddit_media'].assert_not_awaited()


@pytest.mark.asyncio
async def test_partly_sent_cached_album_is_not_restarted(tmp_path):
    ns, store = runtime(tmp_path, kinds=('photo',))
    message = Message()
    url = message.text
    store[ns['_cache_key'](url, ns['_scope'](message), 'media')] = json.dumps({
        'entries': [{'kind': 'photo', 'file_id': 'cached'}] * 11, 'caption': 'caption'})
    message.answer_photo = AsyncMock(side_effect=ns['TelegramBadRequest']('deleted'))
    await ns['process_reddit'](message)
    assert len(message.sends) == 1
    ns['download_reddit_media'].assert_not_awaited()
    ns['handle_download_error'].assert_awaited_once()


def test_callback_source_recovers_from_source_button_after_restart(tmp_path):
    ns, _ = runtime(tmp_path)
    message = Message()
    message.reply_markup = SimpleNamespace(inline_keyboard=[[SimpleNamespace(url=message.text)]])
    assert ns['_callback_source'](message, ns['_token'](message.text)) == message.text
    assert ns['_callback_source'](message, 'wrong') is None


def test_selective_installer_preserves_local_changes_and_is_idempotent(tmp_path):
    installer = load('scripts/install_reddit_handler_vps.py', 'reddit_installer_test')
    target = tmp_path / 'target'
    names = ['handlers/__init__.py','handlers/media_download.py','services/links/detection.py','services/stats/constants.py']
    for name in names:
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        source = (ROOT / name).read_text()
        if name.endswith('__init__.py'):
            source = source.replace('reddit, ', '').replace('    reddit.router,\n', '')
        elif name.endswith('media_download.py'):
            start = source.index('    if service == "reddit":')
            end = source.index('    if service == "instagram":', start)
            source = source[:start] + source[end:]
        elif name.endswith('detection.py'):
            start = source.index('    (\n        "reddit",')
            end = source.index('    (\n        "instagram",', start)
            source = source[:start] + source[end:]
        source += '\nLOCAL_VPS_SETTING = 42\n'
        path.write_text(source)
    changes = installer.prepare(target, ROOT)
    for name, text in changes.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    assert installer.prepare(target, ROOT) == {}
    assert 'LOCAL_VPS_SETTING = 42' in (target / 'handlers/__init__.py').read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize('cached', [False, True])
async def test_document_callback_replies_and_survives_restart(tmp_path, cached):
    ns, store = runtime(tmp_path, kinds=('photo',))
    message = Message()
    message.reply_markup = SimpleNamespace(inline_keyboard=[[SimpleNamespace(url=message.text)]])
    if cached:
        store[ns['_cache_key'](message.text, ns['_scope'](message), 'documents')] = json.dumps({
            'entries': [{'kind': 'photo', 'file_id': 'doc-id'}], 'caption': 'original'})
    call = SimpleNamespace(message=message, from_user=message.from_user,
                           data='reddit:doc:' + ns['_token'](message.text), answer=AsyncMock())
    await ns['reddit_media_action'](call)
    ns['handle_download_error'].assert_not_awaited()
    assert message.sends[0][0] == 'document'
    assert message.sends[0][1]['reply_to_message_id'] == message.message_id
    assert ns['download_reddit_media'].await_count == (0 if cached else 1)
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_audio_callback_caches_result_and_cleans_conversion_files(tmp_path, monkeypatch):
    ns, _ = runtime(tmp_path)
    message = Message()
    message.reply_markup = SimpleNamespace(inline_keyboard=[[SimpleNamespace(url=message.text)]])
    ns['get_bot_avatar_thumbnail'] = AsyncMock(return_value=None)
    async def audio(send, **kwargs):
        for name in ('audio_path', 'bot_avatar', 'bot_url'):
            kwargs.pop(name, None)
        return await send(**kwargs)
    ns['send_audio_with_thumbnail'] = audio
    async def ffmpeg(*args, **kwargs):
        Path(args[-1]).write_bytes(b'mp3')
        return SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b'', b'')))
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', ffmpeg)
    call = SimpleNamespace(message=message, from_user=message.from_user,
                           data='reddit:audio:' + ns['_token'](message.text), answer=AsyncMock())
    await ns['reddit_media_action'](call)
    await ns['reddit_media_action'](call)
    ns['handle_download_error'].assert_not_awaited()
    assert ns['download_reddit_media'].await_count == 1
    assert all(kind == 'audio' and kwargs['reply_to_message_id'] == message.message_id for kind, kwargs in message.sends)
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_download_failure_cleans_partial_files_and_finishes_lease(tmp_path):
    ns, _ = runtime(tmp_path)
    async def fail(url, *, output_dir, max_file_size):
        (Path(output_dir) / 'partial.mp4').write_bytes(b'partial')
        raise ValueError('download failed')
    ns['download_reddit_media'].side_effect = fail
    await ns['process_reddit'](Message())
    assert not list(tmp_path.iterdir())
    ns['claim_message_request'].return_value.finish.assert_called_once()
    ns['claim_message_request'].return_value.mark_success.assert_not_called()
