"""Optional Firefox access for selected local media extractors."""
from __future__ import annotations

import os
from urllib.parse import urlparse

_DOMAINS = {
    'twitter': ('twitter.com', 'x.com', 't.co'),
    'tiktok': ('tiktok.com',),
    'reddit': ('reddit.com', 'redd.it'),
    'youtube': ('youtube.com', 'youtu.be', 'youtube-nocookie.com'),
}


def firefox_profile(platform: str) -> str | None:
    if platform not in _DOMAINS:
        return None
    # An explicitly empty per-platform setting disables the shared profile.
    configured = os.getenv(platform.upper() + '_FIREFOX_PROFILE')
    if configured is None:
        configured = os.getenv('MEDIA_FIREFOX_PROFILE', '')
    profile = configured.strip()
    database = os.path.join(profile, 'cookies.sqlite') if profile else ''
    return profile if database and os.path.isfile(database) and os.access(database, os.R_OK) else None


def firefox_ytdlp_options(platform: str, cookie_file: str | None = None) -> dict:
    profile = firefox_profile(platform)
    if profile:
        return {'cookiesfrombrowser': ('firefox', profile, None, None)}
    return {'cookiefile': cookie_file} if cookie_file else {}


def firefox_cookie_args(url: str, extractor: str, cookie_file: str | None = None) -> list[str]:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        if parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password:
            return ['--cookies', cookie_file] if cookie_file else []
    except ValueError:
        return []
    platform = next((key for key, domains in _DOMAINS.items()
                     if any(host == domain or host.endswith('.' + domain) for domain in domains)), None)
    profile = firefox_profile(platform) if platform else None
    if profile:
        # gallery-dl's domain filter can omit X's alternate domain. Let the
        # browser cookie jar enforce cookie domain matching for both CLIs.
        return ['--cookies-from-browser', 'firefox:' + profile]
    return ['--cookies', cookie_file] if cookie_file else []
