"""Keep extractor failure diagnostics isolated to one incoming update."""
from aiogram import BaseMiddleware

from utils.download_errors import close_failure_context, failure_context


class DownloadFailureMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        token = failure_context()
        try:
            return await handler(event, data)
        finally:
            close_failure_context(token)
