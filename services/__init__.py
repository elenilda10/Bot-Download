"""Compatibility exports, loaded only when requested."""
from importlib import import_module


_MODULES = {
    "db": "services.storage.db",
    "download_queue": "services.download.queue",
    "download_worker_cli": "services.download.worker_cli",
    "inline_album_links": "services.inline.album_links",
    "inline_service_icons": "services.inline.service_icons",
    "inline_video_requests": "services.inline.video_requests",
    "link_detection": "services.links.detection",
    "pending_requests": "services.runtime.pending_requests",
    "runtime_state_store": "services.runtime.state_store",
    "runtime_stats": "services.runtime.stats",
}

__all__ = list(_MODULES)


def __getattr__(name):
    if name not in _MODULES:
        raise AttributeError(name)
    value = import_module(_MODULES[name])
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
