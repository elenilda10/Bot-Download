"""Download Reddit-native video manifests obtained from verified post JSON."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path


class RedditVideoError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass
class NativeVideo:
    url: str
    type: str = 'video'
    thumb: str | None = None
    has_audio: bool = False


@dataclass
class NativeVideoPost:
    id: str
    description: str
    author: str
    media_list: list[NativeVideo]


async def _run(command, timeout):
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name == 'posix',
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        return process.returncode, stdout, stderr
    finally:
        if process.returncode is None:
            if os.name == 'posix':
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            await process.communicate()


async def download_native_video(post, source, directory, max_bytes):
    # All requests target the manifest found in the owning post JSON. No
    # authenticated Reddit-page extraction or Firefox cookie loading here.
    from services.platforms.reddit_json import allowed_video_url
    if not allowed_video_url(source['url']):
        raise RedditVideoError('native_video_invalid_source')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    template = directory / 'reddit_native.%(ext)s'
    command = [
        sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-playlist',
        '--no-warnings', '--no-progress', '--quiet', '--socket-timeout', '15',
        '--retries', '1', '--fragment-retries', '1', '--concurrent-fragments', '3',
        '--impersonate', 'firefox', '--add-header', 'Referer:https://www.reddit.com/',
        '--max-filesize', str(int(max_bytes)),
        '-f', 'bestvideo*+bestaudio/best', '--merge-output-format', 'mp4',
        '--remux-video', 'mp4', '-o', str(template), source['url'],
    ]
    completed = False
    try:
        code, _, stderr = await _run(command, 240)
        if code:
            lowered = stderr.lower()
            if b'403' in lowered:
                raise RedditVideoError('native_video_http_403')
            if b'impersonat' in lowered and (b'not available' in lowered or b'not found' in lowered):
                raise RedditVideoError('native_video_transport_unavailable')
            raise RedditVideoError('native_video_download_failed')
        path = directory / 'reddit_native.mp4'
        if not path.is_file() or not 0 < path.stat().st_size < max_bytes:
            raise RedditVideoError('native_video_missing_or_oversized')
        code, stdout, _ = await _run([
            'ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type',
            '-of', 'json', str(path),
        ], 20)
        if code:
            raise RedditVideoError('native_video_probe_failed')
        try:
            streams = json.loads(stdout).get('streams', [])
            kinds = {stream.get('codec_type') for stream in streams if isinstance(stream, dict)}
        except (ValueError, TypeError, AttributeError):
            raise RedditVideoError('native_video_probe_invalid') from None
        if 'video' not in kinds:
            raise RedditVideoError('native_video_no_video_track')
        has_audio = 'audio' in kinds
        if source.get('expects_audio') and not has_audio:
            raise RedditVideoError('native_video_missing_audio')
        completed = True
        return NativeVideoPost(id=post['id'], description=post.get('title') or '',
                               author=post.get('author') or 'Reddit',
                               media_list=[NativeVideo(str(path), has_audio=has_audio)])
    finally:
        for path in directory.glob('reddit_native*'):
            if path.is_file() and (not completed or path.name != 'reddit_native.mp4'):
                path.unlink(missing_ok=True)
