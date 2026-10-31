"""Recognize book identities only; authentication URLs are never media links."""

import re
from urllib.parse import parse_qs, urlparse

MARKETS = {
    "com.br": "br",
    "com": "us",
    "co.uk": "uk",
    "de": "de",
    "fr": "fr",
    "it": "it",
    "es": "es",
    "ca": "ca",
    "com.au": "au",
    "co.jp": "jp",
    "in": "in",
}
AUDIBLE_URL_REGEX = r"https://(?:www\.)?audible\.(?:com\.br|com\.au|co\.uk|co\.jp|com|de|fr|it|es|ca|in)/pd/[^\s<>\x27\"]+"


def book_identity(url):
    try:
        p = urlparse(url)
        host = (p.hostname or "").removeprefix("www.")
        market = (
            MARKETS.get(host.removeprefix("audible."))
            if host.startswith("audible.")
            else None
        )
        if (
            p.scheme != "https"
            or p.username
            or p.password
            or p.port not in (None, 443)
            or not market
        ):
            return None
        match = re.fullmatch(r"/pd/(?:[^/]+/)?([A-Za-z0-9]{10})/?", p.path)
        return (market, match[1].upper()) if match else None
    except ValueError:
        return None


def extract_audible_url(text):
    for match in re.finditer(AUDIBLE_URL_REGEX, text or "", re.IGNORECASE):
        url = match[0].rstrip(".,;!?)\"']}>")
        if book_identity(url):
            return url
    return None


def book_url(market, asin):
    suffix = next((suffix for suffix, code in MARKETS.items() if code == market), None)
    if not suffix or not re.fullmatch(r"[A-Z0-9]{10}", asin):
        raise ValueError("Invalid Audible identity")
    return f"https://www.audible.{suffix}/pd/{asin}"


def valid_auth_url(url, *, response=False):
    """Validate locally; never fetch a URL supplied in the OAuth handoff."""
    try:
        p = urlparse(url)
        host = (p.hostname or "").removeprefix("www.")
        allowed = any(
            host in {"amazon." + suffix, "audible." + suffix} for suffix in MARKETS
        )
        if len(url) > 8192 or any(c.isspace() for c in url) or not allowed:
            return False
        if p.scheme != "https" or p.username or p.password or p.port not in (None, 443):
            return False
        if response:
            codes = parse_qs(p.query).get("openid.oa2.authorization_code", [])
            return (
                p.path == "/ap/maplanding"
                and len(codes) == 1
                and 1 <= len(codes[0]) <= 2048
            )
        return p.path.startswith("/ap/")
    except ValueError:
        return False
