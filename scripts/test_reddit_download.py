"""Smoke-test Reddit extraction/download without Telegram or a database."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import time
import types
from pathlib import Path


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    parser.add_argument('--firefox-profile')
    args = parser.parse_args()
    if args.firefox_profile:
        os.environ['MEDIA_FIREFOX_PROFILE'] = args.firefox_profile
    root = Path(__file__).resolve().parents[1]
    for name, path in (('services', 'services'), ('services.links', 'services/links'),
                       ('services.platforms', 'services/platforms')):
        module = types.ModuleType(name)
        module.__path__ = [str(root / path)]
        sys.modules[name] = module
    from services.platforms.firefox_cookies import firefox_profile
    from services.platforms.reddit_download import download_reddit_media
    output = tempfile.mkdtemp(prefix='reddit-smoke-')
    print('Cookies:', 'Firefox' if firefox_profile('reddit') else 'sem perfil configurado')
    print('Testando Reddit...', flush=True)
    started = time.monotonic()
    result = await download_reddit_media(args.url, output_dir=output, max_file_size=64 * 1024 * 1024)
    print('TEMPO TOTAL: %.2f s' % (time.monotonic() - started))
    if not result:
        raise SystemExit('FALHOU: nenhuma mídia. Temporários: ' + output)
    print('SUCESSO:', len(result.media_list), 'mídia(s)')
    for item in result.media_list:
        print(item.type, Path(item.url).stat().st_size, 'bytes:', item.url)


if __name__ == '__main__':
    asyncio.run(main())
