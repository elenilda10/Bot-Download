from aiogram import Router
from . import user, tiktok, youtube, spotify, admin, twitter, instagram, soundcloud, pinterest, threads, deezer, universal, music, guest, mediafire

router = Router(name=__name__)
router.include_routers(
    user.router,
    tiktok.router,
    youtube.router,
    spotify.router,
    admin.router,
    twitter.router,
    instagram.router,
    threads.router,
    soundcloud.router,
    pinterest.router,
    deezer.router,
    music.router,
    mediafire.router,
    universal.router,
    guest.router,
)

__all__ = [
    "router",
]
