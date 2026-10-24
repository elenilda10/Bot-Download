"""Read an explicitly selected Firefox profile for Kwai page requests only.

Persistent cookies live in cookies.sqlite; session cookies live in Firefox's
Mozilla-LZ4 session state. No profile files are changed and no cookie values
are logged, exported, passed on argv, or sent to a CDN/redirect destination.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import http.cookiejar
import json
import logging
import re
import shutil
import sqlite3
import struct
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request

logger = logging.getLogger(__name__)
MAX_STATE_BYTES = 64 * 1024 * 1024
MAX_PAGE_BYTES = 8 * 1024 * 1024
FIREFOX_HEADERS = (
    "User-Agent: Mozilla/5.0 (X11; Linux x86_64; rv:158.0) Gecko/20100101 Firefox/158.0",
    "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language: pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding: gzip, deflate, br, zstd",
    "Upgrade-Insecure-Requests: 1",
    "Sec-Fetch-Dest: document",
    "Sec-Fetch-Mode: navigate",
    "Sec-Fetch-Site: none",
    "Sec-Fetch-User: ?1",
    "Connection: keep-alive",
    "Priority: u=0, i",
)


def _kwai_cookie_host(host: str) -> bool:
    host = host.lstrip(".").lower()
    return host == "kwai.com" or host.endswith(".kwai.com")


def _normal_context(attrs) -> bool:
    if not attrs:
        return True
    # Firefox can serialize default attributes explicitly (e.g.
    # userContextId:0, privateBrowsingId:0, partitionKey:"").
    return isinstance(attrs, dict) and all(
        value in (None, "", 0, False) for value in attrs.values()
    )


def _decode_session(raw: bytes) -> dict:
    if len(raw) < 12 or raw[:8] != b"mozLz40\0":
        raise ValueError("Invalid Firefox session header")
    size = struct.unpack("<I", raw[8:12])[0]
    if not 0 < size <= MAX_STATE_BYTES or len(raw) > MAX_STATE_BYTES:
        raise ValueError("Firefox session exceeds size limit")
    library = ctypes.util.find_library("lz4")
    if not library:
        raise OSError("liblz4 is unavailable")
    # Keep the CDLL alive while calling its function pointer.
    lib = ctypes.CDLL(library)
    decode = lib.LZ4_decompress_safe
    decode.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    decode.restype = ctypes.c_int
    output = ctypes.create_string_buffer(size)
    source = ctypes.create_string_buffer(raw[12:])
    if decode(source, output, len(raw) - 12, size) != size:
        raise ValueError("Invalid Firefox session block")
    data = json.loads(output.raw)
    if not isinstance(data, dict):
        raise ValueError("Invalid Firefox session state")
    return data


def _session_cookies(profile: Path) -> list[dict]:
    candidates = [profile / "sessionstore.jsonlz4"] + [
        profile / "sessionstore-backups" / name
        for name in ("recovery.jsonlz4", "recovery.baklz4")
    ]
    existing = [p for p in candidates if p.is_file()]
    if not existing:
        return []
    # Do not merge historical sessions: logout/rotation must not resurrect
    # an older token. recovery.jsonlz4 supports an already running Firefox.
    newest = max(existing, key=lambda p: p.stat().st_mtime_ns)
    if newest.stat().st_size > MAX_STATE_BYTES:
        raise ValueError("Firefox session file exceeds size limit")
    state = _decode_session(newest.read_bytes())
    cookies = state.get("cookies", [])
    if not isinstance(cookies, list):
        raise ValueError("Invalid Firefox session cookies")
    return [c for c in cookies if isinstance(c, dict)]


def _sqlite_cookies(database: Path) -> list[dict]:
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1) as db:
        rows = db.execute(
            "SELECT host, name, value, path, expiry, isSecure, originAttributes "
            "FROM moz_cookies WHERE host IN ('kwai.com', '.kwai.com') "
            "OR host LIKE '%.kwai.com'"
        ).fetchall()
        version = db.execute("PRAGMA user_version").fetchone()[0]
    return [
        dict(host=h, name=n, value=v, path=p,
             expiry=e / 1000 if version >= 16 and e else e,
             secure=bool(s), originAttributes=a)
        for h, n, v, p, e, s, a in rows
    ]


def _persistent_cookies(profile: Path) -> list[dict]:
    database = profile / "cookies.sqlite"
    try:
        return _sqlite_cookies(database)
    except sqlite3.OperationalError:
        # Firefox may hold an exclusive lock. Read a private temporary copy
        # (including WAL) and reject invalid/racing snapshots, never alter
        # the original database or take control of the browser process.
        with tempfile.TemporaryDirectory(prefix="kwai-profile-") as directory:
            copy = Path(directory) / "cookies.sqlite"
            shutil.copyfile(database, copy)
            wal = profile / "cookies.sqlite-wal"
            if wal.is_file():
                shutil.copyfile(wal, Path(str(copy) + "-wal"))
            return _sqlite_cookies(copy)


def load_kwai_cookies(profile: str) -> http.cookiejar.CookieJar:
    directory = Path(profile).expanduser().resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("Firefox profile must be a directory")
    jar = http.cookiejar.CookieJar()
    for record in _persistent_cookies(directory) + _session_cookies(directory):
        host = str(record.get("host", ""))
        attrs = record.get("originAttributes") or {}
        # Do not mix private windows, containers or partitioned cookies into
        # the normal profile's session, even when names/hosts are identical.
        if not _normal_context(attrs) or not _kwai_cookie_host(host):
            continue
        name, value = record.get("name"), record.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        if any(c in name + value for c in "\r\n\0") or not name:
            continue
        expiry = record.get("expiry")
        if expiry is not None:
            expiry = float(expiry)
            if expiry and expiry <= time.time():
                continue
            expiry = int(expiry) if expiry else None
        path = str(record.get("path") or "/")
        jar.set_cookie(http.cookiejar.Cookie(
            version=0, name=name, value=value, port=None, port_specified=False,
            domain=host, domain_specified=host.startswith("."),
            domain_initial_dot=host.startswith("."), path=path,
            path_specified=True, secure=bool(record.get("secure")),
            expires=expiry, discard=expiry is None, comment=None,
            comment_url=None, rest={},
        ))
    return jar


def fetch_kwai_firefox_page(url: str, profile: str) -> tuple[str, str] | None:
    """Replay a bounded HTTPS document request; never follow auth redirects."""
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.netloc != "www.kwai.com"
            or parsed.query or parsed.fragment
            or not re.fullmatch(r"/@[^/]+/(?:video|photo)/\d{8,}/?", parsed.path)):
        return None
    try:
        jar = load_kwai_cookies(profile)
        request = Request(url)
        jar.add_cookie_header(request)
        cookie = request.get_header("Cookie")
        if not cookie or len(cookie) > 65536:
            return None
        headers = (*FIREFOX_HEADERS, "Cookie: " + cookie)
        # stdin config keeps authentication out of argv and process listings.
        config = "\n".join("header = " + json.dumps(h, ensure_ascii=False) for h in headers)
        with tempfile.TemporaryDirectory(prefix="kwai-page-") as directory:
            output = Path(directory) / "page.html"
            output.touch(mode=0o600)
            result = subprocess.run(
                ["curl", "--disable", "--silent", "--show-error", "--globoff",
                 "--compressed", "--proto", "=https", "--max-time", "25",
                 "--max-filesize", str(MAX_PAGE_BYTES), "--config", "-",
                 "--output", str(output), "--write-out", "%{http_code}", url],
                input=config.encode("utf-8"), capture_output=True, timeout=30,
            )
            if result.returncode or result.stdout.strip() != b"200":
                logger.warning("Kwai Firefox page request did not return HTTP 200")
                return None
            if output.stat().st_size > MAX_PAGE_BYTES:
                return None
            return url, output.read_text("utf-8", errors="replace")
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as exc:
        logger.warning("Kwai Firefox page request failed: %s", type(exc).__name__)
        return None
