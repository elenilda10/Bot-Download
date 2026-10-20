from aiogram import Router
from . import user, tiktok, youtube, spotify, admin, twitter, instagram, soundcloud, pinterest, threads, deezer, facebook, universal, music, guest, mediafire, mega, google_drive, pornhub, eporner

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
    mega.router,
    google_drive.router,
    pornhub.router,
    eporner.router,
    facebook.router,
    universal.router,
    guest.router,
)

__all__ = [
    "router",
]
