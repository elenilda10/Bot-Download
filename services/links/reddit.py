"""Reddit URL recognition shared by message and batch routing."""
from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

REDDIT_URL_REGEX = r"https?://(?:[A-Za-z0-9-]+\.)*(?:reddit\.com|redd\.it)(?=[/?#\s<>'\"]|$)(?:/[^\s<>'\"]*)?"


def is_reddit_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        return (parsed.scheme in {'http', 'https'} and not parsed.username
                and not parsed.password and parsed.port in (None, 80, 443)
                and any(host == domain or host.endswith('.' + domain)
                        for domain in ('reddit.com', 'redd.it')))
    except ValueError:
        return False


def reddit_post_id(url: str) -> str | None:
    if not is_reddit_url(url):
        return None
    parsed = urlparse(url)
    match = re.search(r'/(?:comments|gallery)/([A-Za-z0-9]+)(?:/|$)', parsed.path)
    if match:
        return match.group(1).lower()
    if parsed.hostname in {'redd.it', 'www.redd.it'}:
        match = re.fullmatch(r'/([A-Za-z0-9]+)/?', parsed.path)
        return match.group(1).lower() if match else None
    return None


def is_reddit_media_url(url: str) -> bool:
    if not is_reddit_url(url):
        return False
    parsed = urlparse(url)
    return bool(reddit_post_id(url)
                or re.fullmatch(r'/(?:r/[^/]+/)?s/[A-Za-z0-9]+/?', parsed.path)
                or (parsed.hostname in {'i.redd.it', 'v.redd.it', 'preview.redd.it'}
                    and parsed.path.strip('/')))


def normalize_reddit_url(url: str) -> str:
    parsed = urlparse(url)
    query = parsed.query if parsed.hostname in {'i.redd.it', 'v.redd.it', 'preview.redd.it'} else ''
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, '', query, ''))


def extract_reddit_url(text: str | None) -> str | None:
    for match in re.finditer(REDDIT_URL_REGEX, text or '', re.I):
        candidate = match.group(0).rstrip('.,;!?)\"\']}>')
        if is_reddit_url(candidate):
            return normalize_reddit_url(candidate)
    return None
