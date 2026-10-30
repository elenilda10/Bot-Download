"""Execute the real metadata methods in isolation, including their thread boundary."""
import ast
import asyncio
from pathlib import Path
from unittest.mock import Mock

import pytest

from utils.download_errors import (
    best_failure,
    close_failure_context,
    failure_context,
    note_failure,
)

ROOT = Path(__file__).resolve().parents[1]


def method(path, name, namespace):
    tree = ast.parse((ROOT / path).read_text())
    function = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    function.decorator_list = []
    future = ast.parse('from __future__ import annotations').body[0]
    exec(compile(ast.Module(body=[future, function], type_ignores=[]), str(path), 'exec'), namespace)  # noqa: S102
    return namespace[name]


@pytest.mark.asyncio
async def test_metadata_geoblock_reaches_handler_from_worker_thread():
    error = type('DownloadError', (Exception,), {})('The uploader has not made this video available in your country')
    ydl = Mock()
    ydl.extract_info.side_effect = error
    manager = Mock()
    manager.__enter__ = Mock(return_value=ydl)
    manager.__exit__ = Mock(return_value=False)
    service = Mock()
    service._youtube_dl_factory.return_value = manager
    get = method('services/platforms/youtube_media.py', 'get_youtube_video', {
        'build_ytdlp_youtube_options': lambda **kwargs: kwargs, 'note_failure': note_failure, 'logging': Mock(),
    })
    fetch = method('handlers/youtube.py', '_get_youtube_video_with_timeout', {
        'asyncio': asyncio, 'failure_context': failure_context, 'close_failure_context': close_failure_context,
        'note_failure': note_failure, 'best_failure': best_failure,
        'get_youtube_video': lambda url: get(service, url), 'YOUTUBE_INFO_TIMEOUT_SECONDS': 1,
    })
    token = failure_context()
    try:
        assert await fetch('https://youtu.be/GAozEIDKArM') is None
        failure = best_failure()
        assert failure.reason == 'region'
        assert failure.error_type == 'DownloadError'
        assert failure.message == str(error)
    finally:
        close_failure_context(token)
