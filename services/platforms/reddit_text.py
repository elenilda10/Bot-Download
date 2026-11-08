"""Recognize Reddit text and link posts after checking for native media."""
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlsplit


@dataclass(frozen=True)
class RedditText:
    text: str


def text_post(post):
    if (post.get("removed_by_category") or post.get("crosspost_parent")
            or post.get("crosspost_parent_list") or post.get("is_gallery")
            or post.get("is_video") or post.get("media_metadata")
            or post.get("media") or post.get("secure_media")
            or post.get("post_hint") in {"image", "hosted:video", "rich:video"}):
        return None
    body = (post.get("selftext") or "").strip()
    if body in {"[removed]", "[deleted]"}:
        return None
    title = (post.get("title") or "").strip()
    parts = [value for value in (title, body) if value]
    if not parts:
        return None
    if not post.get("is_self"):
        url = post.get("url_overridden_by_dest") or post.get("url") or ""
        try:
            parsed = urlsplit(url)
            host = parsed.hostname or ""
        except ValueError:
            return None
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return None
        # An article link is content in its own right. Keep unclassified links
        # on the existing extractor path, including direct media/CDN links.
        suffix = PurePosixPath(parsed.path).suffix.lower()
        if post.get("post_hint") != "link" and not (
            host.endswith(".sports.yahoo.com") or host == "sports.yahoo.com"
        ):
            return None
        if suffix not in {"", ".html", ".htm"} or any(
            host == domain or host.endswith("." + domain) for domain in
            ("reddit.com", "redd.it", "youtube.com", "youtu.be", "vimeo.com",
             "streamable.com", "imgur.com", "giphy.com", "redgifs.com", "twitch.tv")
        ):
            return None
        if url not in parts:
            parts.append(url)
    return RedditText("\n\n".join(dict.fromkeys(parts)))


def message_chunks(text):
    # Telegram offsets count UTF-16 units. Split only between code points.
    start = units = 0
    for index, char in enumerate(text):
        size = 2 if ord(char) > 0xFFFF else 1
        if units + size > 4096:
            yield text[start:index]
            start, units = index, 0
        units += size
    if start < len(text):
        yield text[start:]
