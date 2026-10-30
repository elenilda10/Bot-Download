"""Download failure codes and bounded diagnostic excerpts with sensitive fields removed."""
import asyncio
import re
import subprocess
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from urllib.parse import urlparse

REASONS = frozenset({'unknown', 'timeout', 'authentication', 'access_denied', 'rate_limited',
                     'not_found', 'region', 'unsupported', 'connection', 'redirect_loop',
                     'restricted', 'too_large', 'delivery', 'internal', 'verification'})
_PRIORITY = {'verification': 80, 'redirect_loop': 75, 'authentication': 70, 'rate_limited': 65,
             'region': 60, 'restricted': 55, 'access_denied': 50, 'not_found': 45,
             'timeout': 40, 'connection': 35, 'too_large': 30, 'unsupported': 20}
_failures = ContextVar('download_failure_codes', default=())


@dataclass(frozen=True)
class Failure:
    reason: str
    message: str = ""
    error_type: str = ""


def classify_failure(error) -> Failure:
    if isinstance(error, Failure):
        return error if error.reason in REASONS else Failure('unknown')
    if isinstance(error, str) and error in REASONS:
        return Failure(error)
    name = type(error).__name__.lower()
    if name.startswith('telegram'):
        return Failure('delivery')
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)) or 'timeout' in name:
        return Failure('timeout')
    if 'toomanyredirects' in name:
        return Failure('redirect_loop')
    if name in {'mediaprivateerror'}:
        return Failure('restricted')
    if 'geoblocked' in name or 'georestricted' in name:
        return Failure('region')
    if 'toolarge' in name:
        return Failure('too_large')
    if 'ratelimit' in name:
        return Failure('rate_limited')
    response = getattr(error, 'response', None)
    status = getattr(error, 'status_code', None) or getattr(error, 'status', None) or getattr(response, 'status_code', None) or getattr(response, 'status', None)
    statuses = {401: 'authentication', 403: 'access_denied', 404: 'not_found', 410: 'not_found', 429: 'rate_limited'}
    if isinstance(status, int) and status in statuses:
        return Failure(statuses[status])
    if isinstance(error, (TypeError, NameError, AttributeError, FileNotFoundError)):
        return Failure('internal')
    if isinstance(error, ConnectionError) or name in {'clientconnectorerror', 'connecterror', 'gaierror'}:
        return Failure('connection')
    code = getattr(error, 'code', '')
    if isinstance(code, str):
        status_match = re.fullmatch(r'(?:image|native_video)_http_(401|403|404|410|429)', code)
        if status_match:
            return Failure(statuses[int(status_match[1])])
        if code in {'image_size_limit', 'image_dimensions_limit'}:
            return Failure('too_large')
        if code in {'image_redirect_loop', 'image_redirect_limit'}:
            return Failure('redirect_loop')
    # Bound the scan and accept only recognizable error phrases, not bare words or URLs.
    text = str(error or '')[-8000:].lower()
    phrases = (
        ('redirect_loop', ('too many redirects', 'toomanyredirects', 'exceeded 30 redirects')),
        ('authentication', ('login required', 'please log in', 'authentication required', 'sign in to confirm')),
        ('access_denied', ("blocked by network security", 'access denied')),
        ('rate_limited', ('too many requests', 'rate limit exceeded')),
        ('region', ('not available in your country', 'geo-restricted', 'region-restricted', 'not made this video available in your country', 'not available from your location', 'georestrictederror', 'geo restricted')),
        ('restricted', ('private account', 'private video', 'private post')),
        ('unsupported', ('unsupported url:', 'unsupported url ')),
        ('timeout', ('timed out', 'timeouterror', 'readtimeout', 'connecttimeout')),
        ('connection', ('connection refused', 'connection reset', 'name resolution failed')),
    )
    for reason, patterns in phrases:
        if any(pattern in text for pattern in patterns):
            return Failure(reason)
    match = re.search(r'\b(?:http(?: error|error)?|status(?: code)?)[ :\-=]+(401|403|404|410|429)\b', text)
    if match:
        return Failure(statuses[int(match[1])])
    return Failure('unknown')


def safe_error_message(error):
    """Keep a bounded diagnostic excerpt; discard credentials and transport details."""
    if isinstance(error, Failure):
        return error.message
    raw = str(error or '')[-8000:]
    raw = re.sub(r'\x1b\[[0-9;]*m', '', raw)
    sensitive = re.compile(r'cookie|authorization|bearer|password|passwd|secret|token|sessionid|api[_-]?key', re.IGNORECASE)
    lines = []
    for line in raw.splitlines():
        if sensitive.search(line):
            continue
        if line.lstrip().startswith(('Traceback ', 'File "')):
            continue
        line = re.sub(r'https?://[^\s<>]+', '[URL]', line)
        line = re.sub(r'/(?:root|home|tmp|data|workspace|etc|var|usr)/[^\s]+', '[PATH]', line)
        line = re.sub(r'\b[A-Za-z0-9_+./=-]{32,}\b', '[REDACTED]', line)
        line = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', line).strip()
        if line:
            lines.append(line)
    return '\n'.join(lines)[:500]


def diagnostic_failure(error):
    failure = classify_failure(error)
    if isinstance(error, Failure):
        return failure
    message = '' if isinstance(error, str) and error in REASONS else safe_error_message(error)
    error_type = type(error).__name__ if isinstance(error, BaseException) else ''
    return Failure(failure.reason, message, error_type)


def note_failure(error, sink=None):
    failure = diagnostic_failure(error)
    if failure.reason != 'unknown' or failure.message:
        if sink is not None:
            sink.append(failure)
            del sink[:-8]
        else:
            _failures.set((*_failures.get(), failure)[-8:])
    return failure


def best_failure():
    return max(_failures.get(), key=lambda f: _PRIORITY.get(f.reason, 0), default=Failure('unknown'))


def reset_failures():
    _failures.set(())


def failure_context():
    return _failures.set(())


def close_failure_context(token):
    _failures.reset(token)


def download_attempt(function):
    """Preserve failure codes for a failed attempt and discard them after a successful fallback."""
    @wraps(function)
    async def wrapped(*args, **kwargs):
        inherited = _failures.get()
        token = _failures.set(())
        succeeded = False
        cancelled = False
        try:
            result = await function(*args, **kwargs)
            succeeded = bool(result)
            return result
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            failures = _failures.get()
            _failures.reset(token)
            if not cancelled:
                _failures.set(()) if succeeded else _failures.set((*inherited, *failures)[-8:])
    return wrapped


def platform_name(text):
    names = {'instagram.com': 'Instagram', 'facebook.com': 'Facebook', 'fb.watch': 'Facebook',
             'youtube.com': 'YouTube', 'youtu.be': 'YouTube', 'kwai.com': 'Kwai',
             'reddit.com': 'Reddit', 'redd.it': 'Reddit', 'tiktok.com': 'TikTok',
             'x.com': 'X/Twitter', 'twitter.com': 'X/Twitter', 'threads.net': 'Threads',
             'pinterest.com': 'Pinterest', 'pin.it': 'Pinterest', 'soundcloud.com': 'SoundCloud',
             'spotify.com': 'Spotify', 'deezer.com': 'Deezer', 'mediafire.com': 'MediaFire',
             'mega.nz': 'MEGA', 'pornhub.com': 'Pornhub', 'xvideos.com': 'Xvideos', 'eporner.com': 'Eporner'}
    for url in re.findall(r'https?://[^\s<>]+', text or ''):
        try:
            host = (urlparse(url).hostname or '').lower()
        except ValueError:
            continue
        for domain, label in names.items():
            if host == domain or host.endswith('.' + domain):
                return label
    return None
