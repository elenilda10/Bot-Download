"""Shared platform API. Standalone extractors can be imported without bot configuration."""
from importlib import import_module as _import_module

__all__ = [
    "ABC",
    "abstractmethod",
    "Any",
    "Awaitable",
    "Callable",
    "Optional",
    "urlparse",
    "urlunparse",
    "DownloadConfig",
    "DownloadError",
    "DownloadMetrics",
    "DownloadQueueBusyError",
    "DownloadRateLimitError",
    "ResilientDownloader",
    "strip_url_query",
    "BasePlatformMediaService",
    "CobaltMediaService",
]


def __getattr__(name):
    if name not in __all__:
        raise AttributeError(name)
    value = getattr(_import_module(".base", __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
