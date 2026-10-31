"""Identity, credential isolation, real PTY handoff, cleanup and subprocess limits."""

import asyncio

import pytest

from services.links.audible import (
    book_identity,
    book_url,
    extract_audible_url,
    valid_auth_url,
)
from services.links.detection import extract_supported_link
from services.platforms.audible_service import AudibleError, AudibleService

ASIN = "B0G3C6QT4Y"


@pytest.mark.parametrize(
    "suffix,market",
    [("com.br", "br"), ("com", "us"), ("co.uk", "uk"), ("de", "de"), ("co.jp", "jp")],
)
def test_exact_market_and_asin_detection(suffix, market):
    url = f"https://www.audible.{suffix}/pd/Book/{ASIN}?source=x"
    assert book_identity(url) == (market, ASIN)
    assert extract_supported_link(url) == ("audible", url.split("?")[0])
    assert extract_audible_url(url + ")") == url
    assert book_identity(book_url(market, ASIN)) == (market, ASIN)


@pytest.mark.parametrize(
    "url",
    [
        "https://audible.com.br.evil.test/pd/" + ASIN,
        "https://evil.audible.com.br/pd/" + ASIN,
        "https://user@audible.com.br/pd/" + ASIN,
        "https://audible.com.br:444/pd/" + ASIN,
        "https://audible.com.br/ap/maplanding?openid.oa2.authorization_code=private",
        "https://audible.com.br/pd/not-an-id",
        "http://audible.com.br/pd/" + ASIN,
    ],
)
def test_non_book_urls_cannot_be_extracted(url):
    assert book_identity(url) is None
    assert extract_audible_url(url) is None


def response(owner):
    return f"https://www.audible.com.br/ap/maplanding?openid.oa2.authorization_code={owner}"


def test_oauth_handoff_is_not_fetched_and_requires_exact_trusted_host():
    assert valid_auth_url(response(1), response=True)
    for url in [
        response(1).replace("audible.com.br", "audible.com.br.evil.test"),
        response(1).replace("/ap/maplanding", "/pd/test"),
        response(1).replace("https:", "http:"),
        response(1) + "\n",
    ]:
        assert not valid_auth_url(url, response=True)


@pytest.fixture
def service(tmp_path):
    binary = tmp_path / "libationcli"
    binary.write_text("""#!/usr/bin/env python3
import json,os,sys,time
from pathlib import Path
p=Path(os.environ['LIBATION_FILES_DIR'])
owner=p.parent.name
assert Path(os.environ['HOME']).is_relative_to(p)
assert Path(os.environ['LIBATION_MASTER_KEY_FILE']).parent == p
if sys.argv[1] == 'login-external':
 assert sys.stdin.isatty(), 'Libation requires non-redirected stdin'
 print('Open this URL:',flush=True)
 print('https://www.amazon.com.br/ap/signin?owner='+owner,flush=True)
 print('Paste URL: ',end='',flush=True)
 line=sys.stdin.readline().strip()
 if line.split('authorization_code=')[-1] != owner:
  print('Wrong authorization code',flush=True);sys.exit(3)
 (p/'AccountsSettings.json').write_text(json.dumps({'test_owner':owner}))
 print('Successfully authenticated account (test).',flush=True)
elif sys.argv[1] == 'scan':
 print('1',flush=True)
elif sys.argv[1] == 'liberate':
 assert sys.argv[2]=='--id' and len(sys.argv)==4
 asin=sys.argv[3]
 settings=json.loads((p/'Settings.json').read_text())
 books=Path(settings['Books']);books.mkdir(parents=True,exist_ok=True)
 actual=asin if asin != 'B000000000' else 'B999999999'
 (books/f'Book [{actual}].m4b').write_text(owner+':'+actual)
 print('Done. All books have been processed',flush=True)
elif sys.argv[1] == 'hang':
 time.sleep(60)
""")
    binary.chmod(0o700)
    return AudibleService(
        tmp_path / "users", str(binary), auth_ttl=2, start_timeout=1, timeout=1
    )


@pytest.mark.asyncio
async def test_two_users_login_independently_and_do_not_inherit_vps_account(
    service, monkeypatch
):
    monkeypatch.setenv("LIBATION_FILES_DIR", "/root/libation-teste/config")
    monkeypatch.setenv("LIBATION_MASTER_KEY", "must-not-inherit")
    first, second = await asyncio.gather(
        service.begin_login(101, "br", "one@example.test"),
        service.begin_login(202, "br", "two@example.test"),
    )
    assert "owner=101" in first and "owner=202" in second
    await service.finish_login(101, response(101))
    assert service.connected(101, "br") and not service.connected(202, "br")
    await service.finish_login(202, response(202))
    assert service.connected(202, "br") and not service.connected(101, "us")
    a, b = service.profile(101, "br"), service.profile(202, "br")
    assert (a / "master.key").read_bytes() != (b / "master.key").read_bytes()
    assert a.stat().st_mode & 0o777 == 0o700
    assert (a / "AccountsSettings.json").stat().st_mode & 0o777 == 0o600
    assert "one@example.test" not in (a / "Settings.json").read_text()
    await service.disconnect(101)
    assert not a.exists() and service.connected(202, "br")


@pytest.mark.asyncio
async def test_wrong_users_handoff_does_not_connect_another_session(service):
    await service.begin_login(1, "br", "one@example.test")
    await service.begin_login(2, "br", "two@example.test")
    with pytest.raises(AudibleError, match="login"):
        await service.finish_login(2, response(1))
    assert not service.connected(2, "br")
    await service.finish_login(1, response(1))
    assert service.connected(1, "br")


@pytest.mark.asyncio
async def test_login_expiration_and_cancel_reap_processes(service):
    service.auth_ttl = 0.1
    await service.begin_login(1, "br", "one@example.test")
    login = service.pending[1]
    assert not await login.task
    assert login.process.returncode is not None and 1 not in service.pending
    service.auth_ttl = 2
    await service.begin_login(2, "br", "two@example.test")
    login = service.pending[2]
    await service.cancel_login(2)
    assert login.process.returncode is not None and not service.pending


@pytest.mark.asyncio
async def test_invalid_email_and_owner_never_start_a_process(service):
    with pytest.raises(AudibleError, match="email"):
        await service.begin_login(1, "br", "not an email")
    with pytest.raises(AudibleError, match="identity"):
        service.profile("../another-user", "br")
    assert not service.pending and not service.starting


@pytest.mark.asyncio
async def test_book_is_exact_scoped_and_files_are_removed_after_upload(
    service, monkeypatch
):
    await service.begin_login(1, "br", "one@example.test")
    await service.finish_login(1, response(1))

    async def audio(path, source, max_size):
        assert source.read_text() == "1:" + ASIN
        result = path / "work/audio.m4a"
        result.write_bytes(b"audio")
        return result

    monkeypatch.setattr(service, "_audio", audio)
    async with service.book(1, "br", ASIN, max_size=100000) as (path, title):
        assert path.read_bytes() == b"audio" and title == "Book"
        assert service.jobs[1] is asyncio.current_task()
        with pytest.raises(AudibleError, match="busy"):
            async with service.book(1, "br", ASIN, max_size=100000):
                pytest.fail("duplicate job")
    assert service.connected(1, "br") and not path.exists() and not service.jobs
    with pytest.raises(AudibleError, match="session"):
        async with service.book(2, "br", ASIN, max_size=100000):
            pytest.fail("shared account")
    with pytest.raises(AudibleError, match="library"):
        async with service.book(1, "br", "B000000000", max_size=100000):
            pytest.fail("wrong book")
    assert not service.jobs


@pytest.mark.asyncio
async def test_upload_failure_also_cleans_media(service, monkeypatch):
    await service.begin_login(1, "br", "one@example.test")
    await service.finish_login(1, response(1))

    async def audio(path, source, max_size):
        output = path / "work/audio.m4a"
        output.write_bytes(b"audio")
        return output

    monkeypatch.setattr(service, "_audio", audio)
    with pytest.raises(RuntimeError, match="send failed"):
        async with service.book(1, "br", ASIN, max_size=100000) as (path, _):
            raise RuntimeError("send failed")
    assert not path.exists() and service.connected(1, "br") and not service.jobs


@pytest.mark.asyncio
async def test_subprocess_timeout_is_bounded(service):
    path = service._initialize(1, "br")
    with pytest.raises(AudibleError, match="timeout"):
        await service._run(path, "hang", timeout=0.05)


@pytest.mark.asyncio
async def test_disconnecting_cancels_active_job_and_preserves_other_user(
    service, monkeypatch
):
    for owner in [1, 2]:
        await service.begin_login(owner, "br", f"user{owner}@example.test")
        await service.finish_login(owner, response(owner))
    started = asyncio.Event()

    async def work():
        async with service.book(1, "br", ASIN, max_size=100000):
            started.set()
            await asyncio.Event().wait()

    async def audio(path, source, max_size):
        output = path / "work/audio.m4a"
        output.write_bytes(b"audio")
        return output

    monkeypatch.setattr(service, "_audio", audio)
    job = asyncio.create_task(work())
    await asyncio.wait_for(started.wait(), 1)
    await service.disconnect(1)
    assert job.cancelled() and not service.jobs and service.connected(2, "br")
    assert not service.profile(1, "br").exists()


@pytest.mark.asyncio
async def test_stale_media_from_restart_is_not_sent_after_unsuccessful_new_download(
    service, monkeypatch
):
    from unittest.mock import AsyncMock

    await service.begin_login(1, "br", "one@example.test")
    await service.finish_login(1, response(1))
    profile = service.profile(1, "br")
    stale = profile / "books" / f"Old [{ASIN}].m4b"
    stale.write_bytes(b"old media")
    monkeypatch.setattr(
        service, "_run", AsyncMock(return_value="Done, but no new audio")
    )
    audio = AsyncMock()
    monkeypatch.setattr(service, "_audio", audio)
    with pytest.raises(AudibleError, match="library"):
        async with service.book(1, "br", ASIN, max_size=100000):
            pytest.fail("must not send stale media")
    assert not stale.exists() and not service.jobs
    audio.assert_not_awaited()


def test_symlink_cannot_redirect_one_users_session_to_another(service):
    first = service._initialize(1, "br")
    (service.root / "2").symlink_to(first.parent, target_is_directory=True)
    with pytest.raises(AudibleError, match="configuration"):
        service.connected(2, "br")


@pytest.mark.asyncio
async def test_real_aac_audio_is_prepared_for_telegram_without_extra_streams(service):
    import json
    import shutil
    import subprocess

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg integration dependency not installed")
    profile = service._initialize(1, "br")
    source = profile / "books" / f"Book [{ASIN}].m4b"
    await asyncio.to_thread(
        subprocess.run,
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.2",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
    )
    output = await service._audio(profile, source, 1000000)
    raw = await asyncio.to_thread(
        subprocess.check_output,
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "json",
            str(output),
        ],
    )
    assert json.loads(raw)["streams"] == [{"codec_name": "aac"}]
