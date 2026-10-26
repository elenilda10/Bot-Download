"""Synthetic Firefox profiles only; no real credentials or network."""
import ctypes
import ctypes.util
import importlib.util
import json
import sqlite3
import struct
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request

import pytest

SPEC = importlib.util.spec_from_file_location(
    "kwai_firefox_test",
    Path(__file__).resolve().parents[1] / "services/platforms/kwai_firefox.py",
)
kwai = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(kwai)
URL = "https://www.kwai.com/@gabyvalence/video/5200118832330956026"
SYNTHETIC_SECRET = "SYNTHETIC_TEST_COOKIE_DO_NOT_LOG"


def pack_state(data):
    libname = ctypes.util.find_library("lz4")
    if not libname:
        pytest.skip("liblz4 required for realistic Mozilla-LZ4 fixtures")
    lib = ctypes.CDLL(libname)
    lib.LZ4_compressBound.argtypes = [ctypes.c_int]
    lib.LZ4_compressBound.restype = ctypes.c_int
    lib.LZ4_compress_default.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
    ]
    lib.LZ4_compress_default.restype = ctypes.c_int
    raw = json.dumps(data).encode()
    source = ctypes.create_string_buffer(raw)
    capacity = lib.LZ4_compressBound(len(raw))
    output = ctypes.create_string_buffer(capacity)
    size = lib.LZ4_compress_default(source, output, len(raw), capacity)
    assert size > 0
    return b"mozLz40\0" + struct.pack("<I", len(raw)) + output.raw[:size]


@pytest.fixture
def profile(tmp_path):
    with sqlite3.connect(tmp_path / "cookies.sqlite") as db:
        db.execute("PRAGMA user_version=17")
        db.execute("CREATE TABLE moz_cookies (host, name, value, path, expiry, isSecure, originAttributes)")
        future = int((time.time() + 3600) * 1000)
        db.executemany("INSERT INTO moz_cookies VALUES (?, ?, ?, ?, ?, ?, ?)", [
            (".kwai.com", "did", "persistent", "/", future, 1, ""),
            (".kwai.com", "expired", "old", "/", 1000, 1, ""),
            (".kwai.com", "container", "other", "/", future, 1, "^userContextId=2"),
            (".kwai.com.evil.test", "foreign", "wrong", "/", future, 1, ""),
            (".example.com", "unrelated", "wrong", "/", future, 1, ""),
        ])
    state = {"cookies": [
        {"host": ".kwai.com", "name": "kwaipro.pwa_st", "value": SYNTHETIC_SECRET,
         "secure": True, "originAttributes": {"userContextId": 0, "privateBrowsingId": 0, "partitionKey": ""}},
        {"host": "www.kwai.com", "name": "sessionId", "value": "test-session", "path": "/"},
        {"host": ".kwai.com", "name": "did", "value": "session-newer"},
        {"host": "www.kwai.com", "name": "wrong_path", "value": "no", "path": "/account/"},
        {"host": ".kwai.com", "name": "partition", "value": "no", "originAttributes": {"partitionKey": "other"}},
        {"host": "kwai.com.evil.test", "name": "foreign", "value": "no"},
        {"host": "www.kwai.com", "name": "injection", "value": "bad\r\nX-Test: bad"},
    ]}
    (tmp_path / "sessionstore.jsonlz4").write_bytes(pack_state(state))
    return tmp_path


def test_combines_persistent_and_session_with_domain_path_and_expiry(profile):
    jar = kwai.load_kwai_cookies(str(profile))
    request = Request(URL)
    jar.add_cookie_header(request)
    cookie = request.get_header("Cookie")
    assert "kwaipro.pwa_st=" + SYNTHETIC_SECRET in cookie
    assert "did=session-newer" in cookie
    assert "sessionId=test-session" in cookie
    for name in ("expired", "container", "foreign", "unrelated", "partition", "wrong_path", "injection"):
        assert name not in cookie
    cdn = Request("https://aws-br-cdn.kwai.net/upic/clip.mp4")
    jar.add_cookie_header(cdn)
    assert cdn.get_header("Cookie") is None


def test_selects_latest_session_without_resurrecting_old_tokens(profile):
    import os
    old = profile / "sessionstore.jsonlz4"
    os.utime(old, (1, 1))
    recovery = profile / "sessionstore-backups/recovery.jsonlz4"
    recovery.parent.mkdir()
    recovery.write_bytes(pack_state({"cookies": [{
        "host": ".kwai.com", "name": "sessionId", "value": "new-visitor",
    }]}))
    jar = kwai.load_kwai_cookies(str(profile))
    assert "kwaipro.pwa_st" not in {c.name for c in jar}
    assert next(c.value for c in jar if c.name == "sessionId") == "new-visitor"


@pytest.mark.parametrize("raw", [
    b"not lz4",
    b"mozLz40\0" + struct.pack("<I", kwai.MAX_STATE_BYTES + 1),
    b"mozLz40\0" + struct.pack("<I", 100) + b"broken",
])
def test_rejects_invalid_or_oversized_state(raw):
    with pytest.raises((ValueError, OSError)):
        kwai._decode_session(raw)


def test_locked_database_snapshot_does_not_modify_profile(profile, monkeypatch):
    original = kwai._sqlite_cookies
    before = (profile / "cookies.sqlite").read_bytes()

    def read(database):
        if database == profile / "cookies.sqlite":
            raise sqlite3.OperationalError("database is locked")
        return original(database)

    monkeypatch.setattr(kwai, "_sqlite_cookies", read)
    assert any(c.name == "did" for c in kwai.load_kwai_cookies(str(profile)))
    assert (profile / "cookies.sqlite").read_bytes() == before


def test_fetch_sends_cookie_via_stdin_only_and_never_follows_redirect(profile, monkeypatch, caplog):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert SYNTHETIC_SECRET not in " ".join(command)
        assert SYNTHETIC_SECRET in kwargs["input"].decode()
        assert "--location" not in command and "-L" not in command
        assert command[0:2] == ["curl", "--disable"]
        Path(command[command.index("--output") + 1]).write_text("<html>post</html>")
        return SimpleNamespace(returncode=0, stdout=b"200")

    monkeypatch.setattr(kwai.subprocess, "run", run)
    assert kwai.fetch_kwai_firefox_page(URL, str(profile)) == (URL, "<html>post</html>")
    assert len(calls) == 1
    assert SYNTHETIC_SECRET not in caplog.text


def test_fetch_rejects_http_redirect_without_fetching_destination(profile, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=b"302")

    monkeypatch.setattr(kwai.subprocess, "run", run)
    assert kwai.fetch_kwai_firefox_page(URL, str(profile)) is None
    assert len(calls) == 1


@pytest.mark.parametrize("url", [
    "http://www.kwai.com/@user/video/5200118832330956026",
    "https://www.kwai.com.evil.test/@user/video/5200118832330956026",
    "https://k.kwai.com/p/short",
    "https://aws-br-cdn.kwai.net/upic/clip.mp4",
    "https://www.kwai.com/@user/video/5200118832330956026?redirect=bad",
])
def test_auth_transport_only_accepts_fixed_canonical_origin(url, monkeypatch):
    monkeypatch.setattr(kwai, "load_kwai_cookies", lambda *_: pytest.fail("must reject before reading cookies"))
    assert kwai.fetch_kwai_firefox_page(url, "/unused") is None


def test_failure_does_not_log_cookie_or_exception_body(profile, monkeypatch, caplog):
    def fail(*args, **kwargs):
        raise OSError(SYNTHETIC_SECRET)
    monkeypatch.setattr(kwai.subprocess, "run", fail)
    assert kwai.fetch_kwai_firefox_page(URL, str(profile)) is None
    assert SYNTHETIC_SECRET not in caplog.text
