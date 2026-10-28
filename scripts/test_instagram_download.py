"""Test Instagram's local extractors with Firefox cookies, without Telegram."""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import tempfile
import types
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    parser.add_argument('--firefox-profile')
    parser.add_argument('--cookies-file')
    args = parser.parse_args()
    from urllib.parse import urlparse
    parsed = urlparse(args.url)
    if parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'instagram.com', 'www.instagram.com', 'm.instagram.com'} or parsed.username:
        raise SystemExit('Use an Instagram URL.')
    if args.firefox_profile:
        os.environ['INSTAGRAM_FIREFOX_PROFILE'] = args.firefox_profile
    if args.cookies_file:
        os.environ['INSTAGRAM_COOKIES_FILE'] = args.cookies_file
    # Load only the media service; no bot secrets or live database required.
    config = types.ModuleType('config')
    config.COBALT_API_KEY = config.COBALT_API_URL = None
    cobalt = types.ModuleType('utils.cobalt_client')
    async def no_cobalt(*_args, **_kwargs): return None
    cobalt.fetch_cobalt_data = no_cobalt
    sys.modules['config'] = config
    sys.modules['utils.cobalt_client'] = cobalt
    path = Path(__file__).resolve().parents[1] / 'services/platforms/instagram_media.py'
    spec = importlib.util.spec_from_file_location('instagram_smoke', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    url = module.strip_instagram_url(args.url)
    output = tempfile.mkdtemp(prefix='instagram-smoke-')
    post_id = module._extract_instagram_post_id(url)
    if not post_id:
        raise SystemExit('Use a post, Reel or Story link with an ID.')
    cookie_args = module._instagram_cookie_args(url, 'gallery-dl')
    print('Cookies:', 'Firefox' if '--cookies-from-browser' in cookie_args else 'arquivo' if cookie_args else 'sem configuração')
    print('Testando Instagram...', flush=True)
    result = module._run_gallery_dl(url, output, post_id, module._extract_story_target_id(url))
    if not result:
        result = module._run_ytdlp(url, output, post_id)
    if not result or not result.media_list:
        raise SystemExit('FALHOU: extratores locais não retornaram mídia. Arquivos temporários: ' + output)
    print('SUCESSO:', len(result.media_list), 'mídia(s)')
    for item in result.media_list:
        print(item.type, Path(item.url).stat().st_size, 'bytes:', item.url)


if __name__ == '__main__':
    main()
