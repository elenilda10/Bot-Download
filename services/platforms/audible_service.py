"""Private, per-Telegram-user Libation sessions; no shared account or media cache."""

import asyncio
import json
import os
import pty
import re
import shutil
import signal
import termios
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from services.links.audible import MARKETS, valid_auth_url


class AudibleError(Exception):
    """Only stable error codes cross the process boundary; never raw CLI output."""


@dataclass
class Login:
    process: asyncio.subprocess.Process
    master: int
    url: asyncio.Future
    task: asyncio.Task | None = None
    submitted: bool = False


class AudibleService:
    def __init__(
        self, root=None, binary=None, *, auth_ttl=600, start_timeout=30, timeout=1200
    ):
        self.root = Path(
            root
            or os.getenv(
                "AUDIBLE_DATA_DIR",
                str(Path.home() / ".local/share/savevid-audible/users"),
            )
        ).absolute()
        self.binary = binary or os.getenv("LIBATION_CLI", "libationcli")
        self.auth_ttl, self.start_timeout, self.timeout = (
            auth_ttl,
            start_timeout,
            timeout,
        )
        self.pending = {}
        self.jobs = {}
        self.starting = {}

    def profile(self, owner, market):
        if type(owner) is not int or owner <= 0 or market not in MARKETS.values():
            raise AudibleError("identity")
        path = self.root / str(owner) / market
        for folder in (self.root, path.parent, path):
            if folder.is_symlink():
                raise AudibleError("configuration")
        return path

    def _initialize(self, owner, market):
        path = self.profile(owner, market)
        for folder in (
            self.root,
            path.parent,
            path,
            path / "books",
            path / "work",
            path / "home",
        ):
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            if folder.is_symlink():
                raise AudibleError("configuration")
            folder.chmod(0o700)
        key = path / "master.key"
        if key.is_symlink():
            raise AudibleError("configuration")
        try:
            fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if key.stat().st_size != 32:
                raise AudibleError("configuration") from None
            key.chmod(0o600)
        else:
            with os.fdopen(fd, "wb") as file:
                file.write(os.urandom(32))
        settings = path / "Settings.json"
        if settings.is_symlink():
            raise AudibleError("configuration")
        settings.write_text(
            json.dumps(
                {
                    "Books": str(path / "books"),
                    "InProgress": str(path / "work"),
                    "TokenStorageMethod": "Encrypted",
                    "ImportEpisodes": False,
                    # Prevent CLI/SDK diagnostics containing credentials from reaching disk.
                    "Serilog": {
                        "MinimumLevel": "Fatal",
                        "WriteTo": [{"Name": "File", "Args": {"path": "/dev/null"}}],
                    },
                }
            )
        )
        settings.chmod(0o600)
        return path

    def _env(self, path):
        env = {k: v for k, v in os.environ.items() if not k.startswith("LIBATION_")}
        env.update(
            LIBATION_FILES_DIR=str(path),
            LIBATION_MASTER_KEY_FILE=str(path / "master.key"),
            HOME=str(path / "home"),
            XDG_DATA_HOME=str(path / "home/.local/share"),
            XDG_CONFIG_HOME=str(path / "home/.config"),
        )
        return env

    def connected(self, owner, market):
        path = self.profile(owner, market)
        return (path / ".connected").is_file() and (
            path / "AccountsSettings.json"
        ).is_file()

    @staticmethod
    async def _stop(process):
        # Also reap descendants if the launcher exited while a child still owns
        # a pipe. Drain pipes after killing so a full stdout buffer cannot keep
        # asyncio's subprocess transport waiting forever.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.communicate(), 5)
        except TimeoutError:
            # The process group has already been killed; cleanup must be bounded.
            pass

    async def _spawn(self, *args, **kwargs):
        task = asyncio.create_task(asyncio.create_subprocess_exec(*args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            process = await task
            await self._stop(process)
            raise

    async def _capture(self, process, on_output=None, quota=None):
        tail = b""
        while True:
            try:
                chunk = await asyncio.wait_for(process.stdout.read(4096), 1)
            except TimeoutError:
                if quota and await asyncio.to_thread(self._bytes, quota[0]) > quota[1]:
                    raise AudibleError("too_large")
                continue
            if not chunk:
                break
            tail = (tail + chunk)[-65536:]
            if on_output:
                on_output(tail.decode(errors="replace"))
            if quota and await asyncio.to_thread(self._bytes, quota[0]) > quota[1]:
                raise AudibleError("too_large")
        await process.wait()
        return tail.decode(errors="replace")

    @staticmethod
    def _bytes(path):
        return sum(
            p.stat().st_size
            for p in path.rglob("*")
            if p.is_file() and not p.is_symlink()
        )

    async def _run(self, path, *args, timeout=None, quota=None):
        try:
            process = await self._spawn(
                self.binary,
                *args,
                env=self._env(path),
                cwd=path,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError:
            raise AudibleError("unavailable") from None
        try:
            output = await asyncio.wait_for(
                self._capture(process, quota=quota), timeout or self.timeout
            )
            if process.returncode:
                raise AudibleError(
                    "session" if "login is required" in output.lower() else "download"
                )
            return output
        except TimeoutError:
            raise AudibleError("timeout") from None
        finally:
            await self._stop(process)

    async def begin_login(self, owner, market, email):
        if not re.fullmatch(r"[^\s@<>]{1,128}@[^\s@<>]{1,128}\.[A-Za-z]{2,24}", email):
            raise AudibleError("email")
        if owner in self.jobs or owner in self.starting:
            raise AudibleError("busy")
        if owner in self.pending:
            await self.cancel_login(owner)
        if len(self.pending) + len(self.starting) >= 4:
            raise AudibleError("busy")
        self.starting[owner] = asyncio.current_task()
        master = slave = None
        try:
            path = self._initialize(owner, market)
            # Start a fresh account only inside this user's chosen marketplace.
            (path / ".connected").unlink(missing_ok=True)
            (path / "AccountsSettings.json").unlink(missing_ok=True)
            master, slave = pty.openpty()
            os.set_blocking(master, False)
            attributes = termios.tcgetattr(slave)
            attributes[3] &= ~termios.ECHO
            termios.tcsetattr(slave, termios.TCSANOW, attributes)
            process = await self._spawn(
                self.binary,
                "login-external",
                "--account",
                email,
                "--locale",
                market,
                stdin=slave,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self._env(path),
                cwd=path,
                start_new_session=True,
            )
            os.close(slave)
            slave = None
            login = Login(process, master, asyncio.get_running_loop().create_future())
            self.pending[owner] = login
            master = None
            login.task = asyncio.create_task(self._watch_login(owner, path, login))
            return await asyncio.wait_for(asyncio.shield(login.url), self.start_timeout)
        except OSError:
            await self.cancel_login(owner)
            raise AudibleError("unavailable") from None
        except (TimeoutError, asyncio.CancelledError):
            await self.cancel_login(owner)
            raise
        finally:
            self.starting.pop(owner, None)
            for descriptor in (master, slave):
                if descriptor is not None:
                    os.close(descriptor)

    async def _watch_login(self, owner, path, login):
        def find_url(output):
            if login.url.done():
                return
            for url in re.findall(r'https://[^\s<>"\x27]+', output):
                # Read only complete printed lines, never a partial URL chunk.
                if valid_auth_url(url) and (
                    url + "\n" in output or url + "\r" in output
                ):
                    login.url.set_result(url)
                    return

        try:
            output = await asyncio.wait_for(
                self._capture(login.process, find_url), self.auth_ttl
            )
            success = login.process.returncode == 0 and (
                "Successfully authenticated account" in output
                or "already authenticated." in output
            )
            if success and (path / "AccountsSettings.json").is_file():
                (path / "AccountsSettings.json").chmod(0o600)
                marker = path / ".connected"
                marker.write_text("1")
                marker.chmod(0o600)
                return True
            return False
        except (TimeoutError, OSError, AudibleError):
            return False
        finally:
            await self._stop(login.process)
            os.close(login.master)
            if not login.url.done():
                login.url.set_result(None)
            if self.pending.get(owner) is login:
                self.pending.pop(owner, None)

    async def finish_login(self, owner, response):
        login = self.pending.get(owner)
        if not login or login.submitted:
            raise AudibleError("expired")
        if not valid_auth_url(response, response=True):
            raise AudibleError("response")
        login.submitted = True

        # Input is a small, validated URL; echo was disabled on this PTY.
        async def feed():
            payload = (response + "\n").encode()
            while payload:
                try:
                    payload = payload[os.write(login.master, payload) :]
                except BlockingIOError:
                    await asyncio.sleep(0.02)

        try:
            await asyncio.wait_for(feed(), 5)
        except (OSError, TimeoutError):
            await self.cancel_login(owner)
            raise AudibleError("login") from None
        if not await login.task:
            raise AudibleError("login")

    async def cancel_login(self, owner):
        login = self.pending.get(owner)
        if login and login.task:
            login.task.cancel()
            await asyncio.gather(login.task, return_exceptions=True)

    async def cancel(self, owner):
        await self.cancel_login(owner)
        job = self.jobs.get(owner) or self.starting.get(owner)
        if job and job is not asyncio.current_task():
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)

    async def disconnect(self, owner):
        await self.cancel(owner)
        path = self.profile(owner, "br").parent
        if path.exists():
            await asyncio.to_thread(shutil.rmtree, path)

    async def _audio(self, path, source, max_size):
        probe = await self._spawn(
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "json",
            str(source),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            raw = await asyncio.wait_for(self._capture(probe), 30)
            try:
                codec = json.loads(raw)["streams"][0]["codec_name"]
            except (ValueError, KeyError, IndexError, TypeError):
                raise AudibleError("download") from None
            if probe.returncode:
                raise AudibleError("download")
        finally:
            await self._stop(probe)
        encoding = (
            ["-c:a", "copy"] if codec == "aac" else ["-c:a", "aac", "-b:a", "64k"]
        )
        output = path / "work/audio.m4a"
        process = await self._spawn(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-map_chapters",
            "-1",
            *encoding,
            str(output),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            await asyncio.wait_for(
                self._capture(process, quota=(path / "work", max_size)), self.timeout
            )
            if process.returncode or not output.is_file() or output.stat().st_size == 0:
                raise AudibleError("download")
            if output.stat().st_size > max_size:
                raise AudibleError("too_large")
            return output
        finally:
            await self._stop(process)

    @asynccontextmanager
    async def book(self, owner, market, asin, *, max_size):
        if not re.fullmatch("[A-Z0-9]{10}", asin):
            raise AudibleError("identity")
        if (
            owner in self.jobs
            or owner in self.pending
            or owner in self.starting
            or len(self.jobs) >= 2
        ):
            raise AudibleError("busy")
        if not self.connected(owner, market):
            raise AudibleError("session")
        path = self._initialize(owner, market)
        self.jobs[owner] = asyncio.current_task()
        try:
            # A restart may leave partial files. Never deliver a previous run's
            # output when a new license/download attempt produces no media.
            for folder in (path / "books", path / "work"):
                await asyncio.to_thread(shutil.rmtree, folder)
                folder.mkdir(mode=0o700)
            await self._run(path, "scan", timeout=180)
            await self._run(path, "liberate", "--id", asin, quota=(path, max_size * 3))
            files = [
                p
                for p in (path / "books").rglob("*")
                if p.suffix.lower() in {".m4b", ".m4a", ".mp3"}
                and p.is_file()
                and not p.is_symlink()
                and f"[{asin}]" in str(p.relative_to(path / "books"))
            ]
            if len(files) != 1:
                raise AudibleError("library")
            output = await self._audio(path, files[0], max_size)
            yield output, files[0].stem.split(" [")[0]
        finally:
            # Authentication remains; every downloaded/converted media file is private and temporary.
            try:
                for folder in (path / "books", path / "work"):
                    if folder.exists():
                        await asyncio.to_thread(shutil.rmtree, folder)
            finally:
                self.jobs.pop(owner, None)


service = AudibleService()
