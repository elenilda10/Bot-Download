"""Collection metadata only. Item downloads use the existing single-media flows."""

from __future__ import annotations

import asyncio
import html
import itertools
import json
import os
import re
from urllib.parse import parse_qs, urlparse

from services.media.collection_flow import CollectionEntry, CollectionListing


class CollectionError(RuntimeError):
    pass


def collection_limit():
    try:
        return max(1, min(200, int(os.getenv("COLLECTION_MAX_ITEMS", "50"))))
    except ValueError:
        return 50


def youtube_collection_url(url):
    try:
        parsed = urlparse(url)
        if (
            parsed.scheme not in {"https", "http"}
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 80, 443)
            or parsed.hostname
            not in {
                "youtube.com",
                "www.youtube.com",
                "m.youtube.com",
                "music.youtube.com",
                "youtu.be",
                "www.youtu.be",
            }
        ):
            return None
        if parsed.hostname not in {"youtu.be", "www.youtu.be"} and parsed.path.rstrip(
            "/"
        ) not in {"/playlist", "/watch", "/embed/videoseries"}:
            return None
        playlist = parse_qs(parsed.query).get("list", [""])[0]
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", playlist):
            return None
        host = (
            "music.youtube.com"
            if parsed.hostname == "music.youtube.com"
            else "www.youtube.com"
        )
        return f"https://{host}/playlist?list={playlist}"
    except ValueError:
        return None


def youtube_collection_prefers_audio(url):
    canonical = youtube_collection_url(url)
    if not canonical:
        return False
    parsed = urlparse(canonical)
    playlist = parse_qs(parsed.query)["list"][0]
    return parsed.hostname == "music.youtube.com" or playlist.startswith("OLAK")


def spotify_collection_url(url):
    from services.platforms.spotify_media import parse_spotify_url

    try:
        source = urlparse(url)
        if (
            source.scheme not in {"https", "http", "spotify"}
            or source.username
            or source.password
        ):
            return None
        if source.port not in (None, 80, 443):
            return None
    except ValueError:
        return None
    parsed = parse_spotify_url(url)
    if parsed and parsed[0] in {"album", "playlist"}:
        return f"https://open.spotify.com/{parsed[0]}/{parsed[1]}"
    return None


def _positive_int(value):
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )


def extract_youtube_listing(url, limit, factory=None):
    from services.platforms.youtube_media import build_ytdlp_youtube_options

    if factory is None:
        from yt_dlp import YoutubeDL

        factory = YoutubeDL
    canonical = youtube_collection_url(url)
    if not canonical:
        raise CollectionError("Invalid YouTube playlist URL")
    options = build_ytdlp_youtube_options(
        noplaylist=False,
        skip_download=True,
        extract_flat="in_playlist",
        lazy_playlist=True,
        playlistend=limit + 1,
        ignoreerrors=True,
        retries=1,
        extractor_retries=1,
        socket_timeout=10,
    )
    with factory(options) as ydl:
        data = ydl.extract_info(canonical, download=False)
        expected = parse_qs(urlparse(canonical).query)["list"][0]
        if not isinstance(data, dict) or data.get("_type") not in {
            "playlist",
            "multi_video",
        }:
            raise CollectionError("YouTube did not return playlist metadata")
        if data.get("id") != expected:
            raise CollectionError("YouTube playlist identity mismatch")
        raw = list(itertools.islice(data.get("entries") or [], limit + 1))
        entries = []
        for item in raw[:limit]:
            item = item if isinstance(item, dict) else {}
            video_id = str(item.get("id") or "")
            unavailable = (
                item.get("availability")
                in {"private", "premium_only", "subscriber_only", "needs_auth"}
                or item.get("title") in {"[Private video]", "[Deleted video]"}
                or item.get("is_live")
                or item.get("live_status") in {"is_live", "is_upcoming"}
            )
            target = (
                f"https://www.youtube.com/watch?v={video_id}"
                if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) and not unavailable
                else None
            )
            entries.append(CollectionEntry(target, str(item.get("title") or "")))
        total = _positive_int(data.get("playlist_count")) or _positive_int(
            data.get("n_entries")
        )
        overflow = len(raw) > limit
        if total is None and not overflow:
            total = len(raw)
        return CollectionListing(
            str(data.get("title") or "YouTube"),
            entries,
            total,
            limited=overflow or (total is not None and total > limit),
        )


async def get_youtube_collection(url, *, limit=None):
    return await asyncio.wait_for(
        asyncio.to_thread(extract_youtube_listing, url, limit or collection_limit()), 60
    )


def _spotify_entry(track, *, album=None):
    from services.media.artist_names import normalize_artist_names

    track = track if isinstance(track, dict) else {}
    album = track.get("album") or album or {}
    track_id = str(track.get("id") or "")
    playable = (
        re.fullmatch(r"[A-Za-z0-9]{1,96}", track_id)
        and not track.get("is_local")
        and track.get("type", "track") == "track"
        and track.get("is_playable") is not False
    )
    url = f"https://open.spotify.com/track/{track_id}" if playable else None
    images = album.get("images") or []
    metadata = {
        "id": f"spotify:{track_id}",
        "spotify_id": track_id,
        "title": track.get("name") or "",
        "artists": normalize_artist_names(track.get("artists")) or "Unknown artist",
        "album": album.get("name"),
        "album_artist": normalize_artist_names(album.get("artists")),
        "date": album.get("release_date"),
        "duration": round((track.get("duration_ms") or 0) / 1000) or None,
        "track_number": track.get("track_number"),
        "disc_number": track.get("disc_number"),
        "thumbnail": images[0].get("url") if images else None,
        "url": url,
        "source_url": url,
    }
    return CollectionEntry(url, metadata["title"], metadata)


async def _spotify_api_listing(session, kind, spotify_id, limit):
    from services.platforms.spotify_media import (
        SPOTIFY_MARKET,
        _get_token,
        _read_json_response,
    )

    token = await _get_token(session)
    headers = {"Authorization": f"Bearer {token}"}

    async def get(path, **params):
        async with session.get(
            "https://api.spotify.com/v1/" + path, headers=headers, params=params
        ) as response:
            if response.status >= 400:
                raise CollectionError(
                    f"Spotify collection metadata: HTTP {response.status}"
                )
            return await _read_json_response(response, "loading collection")

    plural = "albums" if kind == "album" else "playlists"
    metadata = await get(f"{plural}/{spotify_id}", market=SPOTIFY_MARKET or "UA")
    if metadata.get("id") != spotify_id:
        raise CollectionError("Spotify collection identity mismatch")
    endpoint = "tracks" if kind == "album" else "items"
    entries, offset, total, more = [], 0, None, True
    while more and len(entries) <= limit:
        try:
            page = await get(
                f"{plural}/{spotify_id}/{endpoint}",
                limit=min(50, limit + 1 - len(entries)),
                offset=offset,
                market=SPOTIFY_MARKET or "UA",
            )
        except CollectionError as exc:
            # Extended-quota apps may still expose the older playlist endpoint.
            if endpoint == "items" and offset == 0 and "HTTP 404" in str(exc):
                endpoint = "tracks"
                continue
            raise
        items = page.get("items")
        if not isinstance(items, list):
            raise CollectionError("Spotify returned invalid collection items")
        total = _positive_int(page.get("total"))
        if not items:
            break
        for item in items:
            track = (
                item
                if kind == "album"
                else (item.get("item") or item.get("track"))
                if isinstance(item, dict)
                else None
            )
            if kind == "playlist" and isinstance(item, dict) and item.get("is_local"):
                track = {**(track or {}), "is_local": True}
            entries.append(
                _spotify_entry(track, album=metadata if kind == "album" else None)
            )
        offset += len(items)
        more = bool(page.get("next"))
        # Never follow a next URL with the bearer token; reconstruct the API path.
    return CollectionListing(
        str(metadata.get("name") or "Spotify"),
        entries[:limit],
        total,
        partial=bool(
            (more or (total is not None and total > len(entries)))
            and len(entries) <= limit
        ),
        limited=len(entries) > limit or (total is not None and total > limit),
    )


def parse_spotify_embed(page, kind, spotify_id, limit):
    match = re.search(
        r'<script\b[^>]*\bid=["\x27]__NEXT_DATA__["\x27][^>]*>(.*?)</script>',
        page,
        re.S | re.I,
    )
    if not match:
        raise CollectionError("Spotify did not expose public collection metadata")
    try:
        data = json.loads(match[1])
        state = data["props"]["pageProps"]["state"]["data"]
        entity = state["entity"]
    except (ValueError, KeyError, TypeError) as exc:
        raise CollectionError("Spotify returned invalid embedded metadata") from exc
    if entity.get("uri") != f"spotify:{kind}:{spotify_id}":
        raise CollectionError("Spotify embedded collection identity mismatch")
    tracks = entity.get("trackList")
    if not isinstance(tracks, list):
        raise CollectionError("Spotify did not expose a public track list")
    title = str(entity.get("name") or entity.get("title") or "Spotify")
    cover = (entity.get("coverArt") or {}).get("sources") or []
    entries = []
    for item in tracks[:limit]:
        item = item if isinstance(item, dict) else {}
        uri = str(item.get("uri") or "")
        track_id = (
            uri.removeprefix("spotify:track:")
            if uri.startswith("spotify:track:")
            else ""
        )
        track = {
            "id": track_id,
            "name": html.unescape(str(item.get("title") or "")),
            "artists": [{"name": str(item.get("subtitle") or "")}],
            "duration_ms": item.get("duration") or 0,
            "is_playable": item.get("isPlayable", True),
        }
        album = {"name": title if kind == "album" else None, "images": cover}
        entries.append(_spotify_entry(track, album=album))
    total = _positive_int(entity.get("totalTrackCount")) or _positive_int(
        entity.get("trackCount")
    )
    # Public embeds can omit tracks. Never claim that this is a complete playlist.
    return CollectionListing(
        title,
        entries,
        total,
        partial=True,
        limited=len(tracks) > limit or (total is not None and total > limit),
    )


async def get_spotify_collection(url, *, limit=None):
    import aiohttp

    canonical = spotify_collection_url(url)
    if not canonical:
        raise CollectionError("Invalid Spotify collection URL")
    _, kind, spotify_id = urlparse(canonical).path.split("/")
    maximum = limit or collection_limit()
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30)
    ) as session:
        try:
            return await _spotify_api_listing(session, kind, spotify_id, maximum)
        except (
            CollectionError,
            RuntimeError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
        ):
            pass
        async with session.get(
            f"https://open.spotify.com/embed/{kind}/{spotify_id}",
            headers={"User-Agent": "Mozilla/5.0"},
        ) as response:
            if response.status >= 400:
                raise CollectionError(
                    f"Spotify public collection: HTTP {response.status}"
                )
            page = await response.text()
            if len(page) > 3_000_000:
                raise CollectionError("Spotify collection response too large")
            return parse_spotify_embed(page, kind, spotify_id, maximum)
