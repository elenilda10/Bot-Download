"""Reddit JSON/gallery regression tests; no real credentials or network."""
import importlib.util
import io
import json
import sys
import threading
from http.cookiejar import CookieJar
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def module(monkeypatch):
    spec = importlib.util.spec_from_file_location('json_links_test', ROOT / 'services/links/reddit.py')
    links = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(links)
    monkeypatch.setitem(sys.modules, 'services.links.reddit', links)
    spec = importlib.util.spec_from_file_location('reddit_json_test', ROOT / 'services/platforms/reddit_json.py')
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_load_cookies', lambda _: CookieJar())
    return module


def post():
    return {'id': '1xgtvr5', 'title': 'Original', 'author': 'author',
            'gallery_data': {'items': [{'media_id': 'b'}, {'media_id': 'a'}, {'media_id': 'c'}]},
            'media_metadata': {key: {'e': 'Image', 'm': 'image/png', 'status': 'valid',
                                     's': {'u': f'https://preview.redd.it/{key}.png?x=1&amp;y=2'}}
                               for key in ('a', 'b', 'c')}}


class Response:
    def __init__(self, data=b'', status=200, headers=None):
        self.data, self.status_code, self.headers = data, status, headers or {}
        self.closed = False
    def iter_content(self, size):
        yield self.data
    def close(self):
        self.closed = True


def transport(monkeypatch, get):
    sessions = []
    class Session:
        def __init__(self, **kwargs):
            self.options = kwargs
            self.calls = []
            sessions.append(self)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return get(url, kwargs)
    curl = ModuleType('curl_cffi')
    curl.requests = SimpleNamespace(Session=Session, Cookies=lambda jar: jar)
    monkeypatch.setitem(sys.modules, 'curl_cffi', curl)
    return sessions


def test_gallery_uses_declared_order_and_preserves_signed_queries(module):
    data = post()
    data['media_metadata']['foreign'] = {'s': {'u': 'https://evil.test/a.jpg'}}
    sources = module.gallery_sources(data)
    assert sources == [f'https://preview.redd.it/{key}.png?x=1&y=2' for key in ('b', 'a', 'c')]


@pytest.mark.parametrize('url', ['https://evil.test/a.jpg', 'http://i.redd.it/a.jpg',
                                 'https://i.redd.it.evil.test/a.jpg', 'https://user:pass@i.redd.it/a.jpg'])
def test_gallery_rejects_external_and_insecure_sources(module, url):
    data = post()
    data['media_metadata']['a']['s']['u'] = url
    assert module.gallery_sources(data) is None


def test_incomplete_or_animated_galleries_do_not_silently_drop_items(module):
    data = post()
    del data['media_metadata']['a']
    assert module.gallery_sources(data) is None
    data = post()
    data['media_metadata']['a']['e'] = 'AnimatedImage'
    assert module.gallery_sources(data) is None


@pytest.mark.parametrize('actual_id,status', [('1xgtvr5','ok'), ('foreign','invalid')])
def test_json_requires_exact_post_id_and_browser_transport(module, monkeypatch, actual_id, status):
    data = post()
    data['id'] = actual_id
    response = Response(json.dumps([{'data': {'children': [{'kind': 't3', 'data': data}]}}]).encode())
    sessions = transport(monkeypatch, lambda *_: response)
    result = module._fetch_post_sync('https://redd.it/1xgtvr5', '/profile')
    assert result['status'] == status
    assert response.closed
    assert sessions[0].options['impersonate'] == 'firefox147'
    assert sessions[0].calls[0][0] == 'https://www.reddit.com/comments/1xgtvr5.json?raw_json=1'
    assert sessions[0].calls[0][1]['allow_redirects'] is False


def test_short_share_resolves_to_canonical_json(module, monkeypatch):
    redirect = Response(status=302, headers={'Location': '/r/test/comments/1xgtvr5/title/'})
    metadata = Response(json.dumps([{'data': {'children': [{'kind': 't3','data': post()}]}}]).encode())
    sessions = transport(monkeypatch, lambda url, _: metadata if '.json?' in url else redirect)
    result = module._fetch_post_sync('https://www.reddit.com/r/test/s/AbCdEf', '/profile')
    assert result['status'] == 'ok'
    assert len(sessions[0].calls) == 2
    assert redirect.closed and metadata.closed


def test_short_share_never_follows_external_redirect(module, monkeypatch):
    sessions = transport(monkeypatch, lambda *_: Response(status=302, headers={'Location': 'https://evil.test/comments/1xgtvr5/'}))
    assert module._fetch_post_sync('https://www.reddit.com/r/test/s/AbCdEf', '/profile')['status'] == 'unresolved'
    assert len(sessions[0].calls) == 1


@pytest.mark.parametrize('status', [401, 403, 429])
def test_json_block_is_returned_without_retries(module, monkeypatch, status):
    sessions = transport(monkeypatch, lambda *_: Response(status=status))
    assert module._fetch_post_sync('https://redd.it/1xgtvr5', '/profile')['status'] == 'blocked'
    assert len(sessions[0].calls) == 1


@pytest.mark.asyncio
async def test_diagnostic_errors_do_not_expose_values(module, monkeypatch):
    def fail(*args): raise ValueError('SECRET_COOKIE_VALUE')
    monkeypatch.setattr(module, '_fetch_post_sync', fail)
    result = await module.fetch_reddit_post('https://redd.it/1xgtvr5', '/profile')
    assert result == {'status': 'failed', 'error': 'ValueError'}


def image_bytes():
    stream = io.BytesIO()
    Image.new('RGB', (2, 2)).save(stream, 'PNG')
    return stream.getvalue()


@pytest.mark.asyncio
async def test_images_download_in_parallel_keep_order_and_do_not_receive_cookies(module, monkeypatch, tmp_path):
    barrier = threading.Barrier(3, timeout=3)
    payload = image_bytes()
    def get(url, kwargs):
        assert kwargs['allow_redirects'] is False
        assert 'Cookie' not in kwargs['headers']
        barrier.wait()
        return Response(payload)
    sessions = transport(monkeypatch, get)
    sources = module.gallery_sources(post())
    result = await module.download_reddit_gallery(post(), sources, str(tmp_path), 1000)
    assert [item.index for item in result.media_list] == [0, 1, 2]
    assert len(sessions) == 3
    assert all('cookies' not in session.options for session in sessions)
    assert all(Path(item.url).read_bytes() == payload for item in result.media_list)
    assert not list(tmp_path.glob('*.part'))


@pytest.mark.parametrize('failure', ['http', 'oversized', 'not_image'])
def test_image_failure_cleans_whole_partial_album(module, monkeypatch, tmp_path, failure):
    payload = image_bytes()
    def get(url, kwargs):
        if '/a.png' in url:
            return Response(b'<html>not an image</html>' if failure == 'not_image' else payload,
                            status=500 if failure == 'http' else 200)
        return Response(payload)
    transport(monkeypatch, get)
    (tmp_path / 'unrelated.txt').write_text('preserve')
    with pytest.raises(Exception):
        module._download_gallery_sync(post(), module.gallery_sources(post()), tmp_path, 1 if failure == 'oversized' else 1000)
    assert sorted(path.name for path in tmp_path.iterdir()) == ['unrelated.txt']


def test_persistent_cookie_reader_uses_normal_context_and_filters_other_domains(module, monkeypatch):
    from http.cookiejar import Cookie
    jar = CookieJar()
    for domain in ('.reddit.com', '.other.test'):
        jar.set_cookie(Cookie(0, 'session', 'SECRET_COOKIE_VALUE', None, False, domain,
                              True, True, '/', True, True, None, True, None, None, {}))
    native = ModuleType('yt_dlp.cookies')
    native.extract_cookies_from_browser = Mock(return_value=jar)
    monkeypatch.setitem(sys.modules, 'yt_dlp.cookies', native)
    # Fixture normally stubs the reader for transport-only tests.
    source = (ROOT / 'services/platforms/reddit_json.py').read_text()
    import ast
    tree = ast.parse(source)
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_load_cookies')
    namespace = module.__dict__.copy()
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), 'reader', 'exec'), namespace)
    cookies = namespace['_load_cookies']('/profile')
    assert len(cookies) == 1
    assert next(iter(cookies)).domain == '.reddit.com'
    native.extract_cookies_from_browser.assert_called_once_with('firefox', profile='/profile', container='none')


def test_large_json_is_rejected_before_parsing(module, monkeypatch):
    response = Response(b'x' * (8 * 1024 * 1024 + 1))
    transport(monkeypatch, lambda *_: response)
    assert module._fetch_post_sync('https://redd.it/1xgtvr5', None)['status'] == 'invalid'
    assert response.closed
