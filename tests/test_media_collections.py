"""Collection metadata and sequential execution, without accounts or network access."""

import asyncio
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.media.collection_flow import (
    CollectionEntry,
    CollectionListing,
    run_collection,
)
from services.platforms import media_collections as media


@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "https://youtube.com/playlist?list=PLabcdefgh&si=123",
            "https://www.youtube.com/playlist?list=PLabcdefgh",
        ),
        (
            "https://youtube.com/watch?v=abcdefghijk&list=PLabcdefgh",
            "https://www.youtube.com/playlist?list=PLabcdefgh",
        ),
        (
            "https://music.youtube.com/playlist?list=OLAKabcdefgh",
            "https://music.youtube.com/playlist?list=OLAKabcdefgh",
        ),
        (
            "https://youtu.be/abcdefghijk?list=PLabcdefgh",
            "https://www.youtube.com/playlist?list=PLabcdefgh",
        ),
        ("https://youtube.com/watch?v=abcdefghijk", None),
        ("https://youtube.com.evil.test/playlist?list=PLabcdefgh", None),
        ("https://user:secret@youtube.com/playlist?list=PLabcdefgh", None),
        ("https://youtube.com/channel/PLabcdefgh?list=PLabcdefgh", None),
    ],
)
def test_youtube_collection_identity_and_single_link_behavior(url, expected):
    assert media.youtube_collection_url(url) == expected


def spotify_module(monkeypatch):
    module = ModuleType("services.platforms.spotify_media")
    module._get_token = AsyncMock(return_value="DO_NOT_EXPOSE")

    async def read(response, action):
        return response.data

    module._read_json_response = read
    module.SPOTIFY_MARKET = "BR"
    monkeypatch.setitem(sys.modules, "services.platforms.spotify_media", module)
    return module


def test_youtube_listing_is_bounded_verified_and_never_downloads(monkeypatch):
    youtube = ModuleType("services.platforms.youtube_media")
    youtube.build_ytdlp_youtube_options = lambda **kw: kw
    monkeypatch.setitem(sys.modules, "services.platforms.youtube_media", youtube)
    options = {}

    class YDL:
        def __init__(self, opts):
            options.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download):
            assert download is False
            return {
                "_type": "playlist",
                "id": "PLabcdefgh",
                "title": "<Album>",
                "playlist_count": 4,
                "entries": iter(
                    [
                        {"id": "abcdefghijk", "title": "Track"},
                        {"id": "lmnopqrstuv", "availability": "private"},
                        {"id": "zzzzzzzzzzz"},
                        {"id": "ooooooooooo"},
                    ]
                ),
            }

    listing = media.extract_youtube_listing(
        "https://youtube.com/playlist?list=PLabcdefgh", 2, YDL
    )
    assert listing.total == 4 and listing.limited
    assert [entry.url for entry in listing.entries] == [
        "https://www.youtube.com/watch?v=abcdefghijk",
        None,
    ]
    assert options["noplaylist"] is False and options["extract_flat"] == "in_playlist"
    assert options["playlistend"] == 3


def test_youtube_refuses_unrelated_playlist(monkeypatch):
    youtube = ModuleType("services.platforms.youtube_media")
    youtube.build_ytdlp_youtube_options = lambda **kw: kw
    monkeypatch.setitem(sys.modules, "services.platforms.youtube_media", youtube)
    factory = Mock()
    factory.return_value.__enter__ = Mock(
        return_value=SimpleNamespace(
            extract_info=lambda *a, **k: {"_type": "playlist", "id": "WRONG"}
        )
    )
    factory.return_value.__exit__ = Mock(return_value=False)
    with pytest.raises(media.CollectionError, match="identity"):
        media.extract_youtube_listing(
            "https://youtube.com/playlist?list=PLabcdefgh", 2, factory
        )


def embed(entity):
    data = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(data)
        + "</script>"
    )


def test_spotify_embed_checks_identity_keeps_order_and_reports_partial():
    listing = media.parse_spotify_embed(
        embed(
            {
                "uri": "spotify:album:abc123",
                "title": "Album",
                "coverArt": {"sources": [{"url": "https://i.scdn.co/image/cover"}]},
                "trackList": [
                    {
                        "uri": "spotify:track:one",
                        "title": "One",
                        "subtitle": "Artist",
                        "duration": 120000,
                    },
                    {"uri": "spotify:episode:two", "title": "Not a track"},
                    {"uri": "spotify:track:three", "title": "Three"},
                ],
            }
        ),
        "album",
        "abc123",
        2,
    )
    assert listing.partial and listing.limited
    assert listing.entries[0].metadata["duration"] == 120
    assert listing.entries[0].metadata["album"] == "Album"
    assert [entry.url for entry in listing.entries] == [
        "https://open.spotify.com/track/one",
        None,
    ]
    with pytest.raises(media.CollectionError, match="identity"):
        media.parse_spotify_embed(
            embed({"uri": "spotify:album:OTHER", "trackList": []}), "album", "abc123", 2
        )


@pytest.mark.asyncio
async def test_spotify_pagination_reconstructs_api_paths_and_handles_new_items(
    monkeypatch,
):
    spotify_module(monkeypatch)
    requests = []

    class Response:
        status = 200

        def __init__(self, data):
            self.data = data

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class Session:
        def get(self, url, **kwargs):
            requests.append((url, kwargs))
            if url.endswith("/playlists/abc123"):
                return Response({"id": "abc123", "name": "Playlist"})
            assert url == "https://api.spotify.com/v1/playlists/abc123/items"
            offset = kwargs["params"]["offset"]
            if offset == 0:
                return Response(
                    {
                        "items": [
                            {"item": {"id": "one", "type": "track", "name": "One"}}
                        ],
                        "total": 3,
                        "next": "https://evil.test/leak-token",
                    }
                )
            return Response(
                {
                    "items": [{"track": {"id": "two", "name": "Two"}}, {"track": None}],
                    "total": 3,
                    "next": None,
                }
            )

    listing = await media._spotify_api_listing(Session(), "playlist", "abc123", 3)
    assert [e.url for e in listing.entries] == [
        "https://open.spotify.com/track/one",
        "https://open.spotify.com/track/two",
        None,
    ]
    assert all(url.startswith("https://api.spotify.com/") for url, _ in requests)
    assert not listing.partial and not listing.limited


@pytest.mark.asyncio
async def test_spotify_album_metadata_propagates_to_tracks(monkeypatch):
    spotify_module(monkeypatch)

    class Response:
        status = 200

        def __init__(self, data):
            self.data = data

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class Session:
        def get(self, url, **kw):
            return Response(
                {
                    "id": "album1",
                    "name": "Album",
                    "images": [{"url": "cover"}],
                    "artists": [{"name": "Artist"}],
                }
                if url.endswith("/albums/album1")
                else {
                    "items": [
                        {
                            "id": "track1",
                            "name": "Track",
                            "track_number": 1,
                            "disc_number": 2,
                            "duration_ms": 121000,
                        }
                    ],
                    "total": 1,
                    "next": None,
                }
            )

    listing = await media._spotify_api_listing(Session(), "album", "album1", 5)
    assert listing.entries[0].metadata["album"] == "Album"
    assert listing.entries[0].metadata["disc_number"] == 2
    assert listing.entries[0].metadata["thumbnail"] == "cover"


@pytest.mark.asyncio
async def test_flow_preserves_duplicate_order_and_continues_failed_items():
    listing = CollectionListing(
        "List",
        [
            CollectionEntry("one"),
            CollectionEntry(None),
            CollectionEntry("one"),
            CollectionEntry("bad"),
            CollectionEntry("last"),
        ],
    )
    order = []

    async def deliver(entry):
        order.append(entry.url)
        if entry.url == "bad":
            raise ValueError("unavailable")
        return True

    result = await run_collection(listing, deliver, AsyncMock(), lambda: False)
    assert order == ["one", "one", "bad", "last"]
    assert (result.sent, result.failed, result.unavailable) == (3, 1, 1)


@pytest.mark.asyncio
async def test_cancellation_finishes_current_item_and_never_starts_next():
    event = asyncio.Event()

    async def deliver(entry):
        event.set()
        return True

    result = await run_collection(
        CollectionListing("List", [CollectionEntry("one"), CollectionEntry("two")]),
        deliver,
        AsyncMock(),
        event.is_set,
    )
    assert result.sent == 1 and result.cancelled


@pytest.mark.asyncio
async def test_backpressure_stops_remaining_items():
    class Busy(Exception):
        pass

    deliver = AsyncMock(side_effect=Busy())
    result = await run_collection(
        CollectionListing("List", [CollectionEntry("one"), CollectionEntry("two")]),
        deliver,
        AsyncMock(),
        lambda: False,
        stop_on=(Busy,),
    )
    assert result.stopped and result.failed == 1
    assert deliver.await_count == 1


@pytest.mark.asyncio
async def test_cleanup_removes_only_unique_item_outputs(tmp_path):
    from services.media.collection_flow import cleanup_collection_audio

    owned = tmp_path / "track_a1b2.mp3.part"
    owned.write_bytes(b"partial")
    other = tmp_path / "another.mp3"
    other.write_bytes(b"keep")

    async def remove(path):
        from pathlib import Path

        Path(path).unlink()

    await cleanup_collection_audio(tmp_path, "track_a1b2", remove)
    assert not owned.exists() and other.exists()
    await cleanup_collection_audio(tmp_path, "../another", remove)
    assert other.exists()


def test_link_detection_routes_spotify_collections_and_keeps_youtube_list():
    from services.links.detection import extract_supported_links

    assert extract_supported_links(
        "https://open.spotify.com/album/abc123?si=x https://open.spotify.com/playlist/def456"
    ) == [
        ("spotify", "https://open.spotify.com/album/abc123"),
        ("spotify", "https://open.spotify.com/playlist/def456"),
    ]
    assert extract_supported_links(
        "https://youtube.com/watch?v=abcdefghijk&list=PLabcdefgh"
    )[0][1].endswith("v=abcdefghijk&list=PLabcdefgh")


def test_collection_request_identity_does_not_drop_playlist_id():
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "services/runtime/request_dedupe.py"
    tree = ast.parse(source.read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "normalize_request_url"
    )
    namespace = {
        "normalize_request_service": lambda value: value,
        "_normalize_generic_url": lambda value: value,
    }
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"),
        namespace,
    )  # noqa: S102
    first = namespace["normalize_request_url"](
        "youtube_collection", "https://youtube.com/playlist?list=PLabcdefgh"
    )
    second = namespace["normalize_request_url"](
        "youtube_collection", "https://youtube.com/playlist?list=PLdifferent"
    )
    assert first != second
