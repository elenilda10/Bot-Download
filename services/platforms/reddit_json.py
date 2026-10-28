"""Verified Reddit image galleries via the browser-compatible JSON endpoint."""
from __future__ import annotations

import asyncio
import copy
import html
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http.cookiejar import CookieJar, MozillaCookieJar
from pathlib import Path
from urllib.parse import urljoin, urlparse

from services.links.reddit import is_reddit_media_url, is_reddit_url, reddit_post_id

_HEADERS = {
    'Accept': 'application/json',
    'Sec-Fetch-Site': 'same-origin',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Dest': 'empty',
}
_IMAGE_HOSTS = {'i.redd.it', 'preview.redd.it', 'external-preview.redd.it'}


@dataclass
class RedditImage:
    url: str
    type: str = 'photo'
    thumb: str | None = None
    index: int = 0


@dataclass
class RedditGallery:
    id: str
    description: str
    author: str
    media_list: list[RedditImage]


def _reddit_domain(host):
    host = host.lstrip('.')
    return host == 'reddit.com' or host.endswith('.reddit.com')


def _load_cookies(profile):
    if profile:
        from yt_dlp.cookies import extract_cookies_from_browser
        original = extract_cookies_from_browser('firefox', profile=profile, container='none')
    else:
        original = CookieJar()
        for path in ('/root/Bot-Download/cookies/reddit_cookies.txt', '/root/Bot-Download/cookies.txt'):
            if os.path.isfile(path) and os.path.getsize(path):
                original = MozillaCookieJar(path)
                original.load(ignore_discard=True, ignore_expires=False)
                break
    jar = CookieJar()
    for cookie in original:
        if _reddit_domain(cookie.domain):
            jar.set_cookie(copy.copy(cookie))
    return jar


def _fetch_post_sync(url, profile):
    from curl_cffi import requests
    deadline = time.monotonic() + 20
    requested = reddit_post_id(url)
    url = urlparse(url)._replace(scheme='https').geturl()
    headers = {**_HEADERS, 'Referer': url}
    with requests.Session(impersonate='firefox147', cookies=requests.Cookies(_load_cookies(profile))) as session:
        # Resolve short /s/ shares without forwarding cookies off Reddit.
        for _ in range(5):
            if reddit_post_id(url):
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {'status': 'timeout'}
            response = session.get(url, headers={'Accept': 'text/html'},
                                   allow_redirects=False, stream=True, timeout=remaining)
            try:
                if response.status_code in {401, 403, 429}:
                    return {'status': 'blocked', 'http': response.status_code}
                if response.status_code not in {301, 302, 303, 307, 308}:
                    return {'status': 'unresolved', 'http': response.status_code}
                candidate = urljoin(url, response.headers.get('Location', ''))
                if not is_reddit_url(candidate) or urlparse(candidate).scheme != 'https':
                    return {'status': 'unresolved'}
                url = candidate
            finally:
                response.close()
        post_id = reddit_post_id(url)
        if not post_id or (requested and requested != post_id):
            return {'status': 'unresolved'}
        marker = f'/comments/{post_id}'
        path = urlparse(url).path
        referer_path = path.split(marker, 1)[0] + marker if marker in path else marker
        headers['Referer'] = 'https://www.reddit.com' + referer_path + '/'
        endpoint = f'https://www.reddit.com/comments/{post_id}.json?raw_json=1'
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {'status': 'timeout'}
        response = session.get(endpoint, headers=headers, allow_redirects=False,
                               stream=True, timeout=remaining)
        try:
            if response.status_code != 200:
                return {'status': 'blocked' if response.status_code in {401, 403, 429} else 'failed',
                        'http': response.status_code}
            raw = bytearray()
            for chunk in response.iter_content(64 * 1024):
                raw.extend(chunk)
                if len(raw) > 8 * 1024 * 1024:
                    return {'status': 'invalid'}
            data = json.loads(raw)
            listing = data[0] if isinstance(data, list) and data else data
            children = listing.get('data', {}).get('children', []) if isinstance(listing, dict) else []
            posts = [child['data'] for child in children
                     if isinstance(child, dict) and child.get('kind') == 't3'
                     and isinstance(child.get('data'), dict) and child['data'].get('id') == post_id]
            if len(posts) != 1:
                return {'status': 'invalid'}
            return {'status': 'ok', 'http': 200, 'post': posts[0],
                    'canonical': f'https://www.reddit.com/comments/{post_id}/'}
        finally:
            response.close()


def _allowed_image_url(url):
    try:
        parsed = urlparse(url)
        return (parsed.scheme == 'https' and parsed.hostname in _IMAGE_HOSTS
                and not parsed.username and not parsed.password and parsed.port in (None, 443))
    except ValueError:
        return False


def gallery_sources(post):
    """Return only static image sources in this post's declared gallery order."""
    gallery = post.get('gallery_data')
    if not isinstance(gallery, dict):
        return None
    items = gallery.get('items')
    metadata = post.get('media_metadata', {})
    if not isinstance(items, list) or not 1 <= len(items) <= 100 or not isinstance(metadata, dict):
        return None
    sources = []
    for item in items:
        record = metadata.get(item.get('media_id')) if isinstance(item, dict) else None
        if (not isinstance(record, dict) or record.get('e') != 'Image'
                or record.get('m') not in {'image/jpeg', 'image/jpg', 'image/png'}
                or record.get('status') not in (None, 'valid')):
            return None
        source = record.get('s', {})
        url = html.unescape(source.get('u', '')) if isinstance(source, dict) else ''
        if not _allowed_image_url(url):
            return None
        sources.append(url)
    return sources


def _download_image_sync(index, url, directory, max_bytes, deadline=None):
    from curl_cffi import requests
    from PIL import Image
    if not _allowed_image_url(url):
        raise ValueError('Invalid Reddit image host')
    temporary = directory / f'reddit_gallery_{index}.part'
    try:
        remaining = min(20, deadline - time.monotonic()) if deadline else 20
        if remaining <= 0:
            raise TimeoutError('Reddit gallery time limit')
        # Fresh CDN session: never copy the authenticated Reddit cookie jar.
        with requests.Session(impersonate='firefox147') as session:
            response = session.get(url, headers={'Referer': 'https://www.reddit.com/'},
                                   allow_redirects=False, stream=True, timeout=remaining)
            try:
                if response.status_code != 200:
                    raise ValueError('Reddit image HTTP failure')
                size = 0
                with temporary.open('wb') as output:
                    for chunk in response.iter_content(64 * 1024):
                        size += len(chunk)
                        if size > min(max_bytes, 10 * 1024 * 1024):
                            raise ValueError('Reddit image exceeds limit')
                        output.write(chunk)
            finally:
                response.close()
        with Image.open(temporary) as image:
            if image.format not in {'JPEG', 'PNG'} or image.width * image.height > 40_000_000:
                raise ValueError('Invalid Reddit image')
            extension = '.jpg' if image.format == 'JPEG' else '.png'
            image.verify()
        target = directory / f'reddit_gallery_{index}{extension}'
        temporary.replace(target)
        return RedditImage(url=str(target), index=index)
    finally:
        temporary.unlink(missing_ok=True)


def _download_gallery_sync(post, sources, directory, max_bytes):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 60
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(_download_image_sync, i, url, directory, max_bytes, deadline)
                       for i, url in enumerate(sources)]
            # Read results in post order, irrespective of completion order.
            items = [future.result() for future in futures]
        return RedditGallery(id=post['id'], description=post.get('title') or '',
                             author=post.get('author') or 'Reddit', media_list=items)
    except BaseException:
        for path in directory.glob('reddit_gallery_*'):
            if path.is_file():
                path.unlink(missing_ok=True)
        raise


async def _thread_complete(function, *args):
    # Finish bounded workers before the caller removes its temporary directory.
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            pass
        raise


async def fetch_reddit_post(url, profile):
    if not is_reddit_media_url(url):
        return {'status': 'invalid'}
    if urlparse(url).hostname in _IMAGE_HOSTS | {'v.redd.it'}:
        return {'status': 'direct'}
    try:
        return await _thread_complete(_fetch_post_sync, url, profile)
    except Exception as exc:
        return {'status': 'failed', 'error': type(exc).__name__}


async def download_reddit_gallery(post, sources, directory, max_bytes):
    return await _thread_complete(_download_gallery_sync, post, sources, directory, max_bytes)
