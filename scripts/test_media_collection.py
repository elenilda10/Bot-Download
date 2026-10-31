"""List playlist/album metadata without downloading media or sending Telegram messages."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument(
        "--env-file", type=Path, default=Path("/root/Bot-Download/.env")
    )
    parser.add_argument("--firefox-profile")
    args = parser.parse_args()
    from dotenv import load_dotenv

    load_dotenv(args.env_file)
    if args.firefox_profile:
        os.environ["YOUTUBE_FIREFOX_PROFILE"] = args.firefox_profile
    from services.platforms.media_collections import (
        get_spotify_collection,
        get_youtube_collection,
        spotify_collection_url,
        youtube_collection_url,
    )

    canonical = spotify_collection_url(args.url) or youtube_collection_url(args.url)
    if not canonical:
        parser.error("Send a YouTube playlist/album or Spotify playlist/album URL.")
    resolver = (
        get_spotify_collection if "spotify.com" in canonical else get_youtube_collection
    )
    listing = await resolver(canonical, limit=max(1, min(200, args.limit)))
    print("Title:", listing.title)
    print("Items:", len(listing.entries), "Total:", listing.total)
    print("Partial:", listing.partial, "Limited:", listing.limited)
    for index, entry in enumerate(listing.entries, 1):
        print(index, entry.title, entry.url or "[unavailable]")
    if not listing.entries:
        raise SystemExit("No collection items returned.")


if __name__ == "__main__":
    asyncio.run(main())
