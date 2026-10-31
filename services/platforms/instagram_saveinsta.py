"""Save Insta fallback for public posts. Uses the same curl flow as the VPS test."""

import base64
import binascii
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlencode, urlparse, parse_qs, unquote

BASE_URL = "https://saveinsta.to"


@dataclass
class SaveInstaItem:
    type: str
    links: list[tuple[str, str]] = field(default_factory=list)
    media_ids: set[str] = field(default_factory=set)


def _proxy_media_id(link: str) -> str | None:
    """Read the media PK from the Instagram cache key, including video posters.

    MP4 URLs often lack the PK. Their thumbnail in the same Save Insta <li>
    carries it. File basenames are upload IDs and must not be treated as PKs.
    """
    try:
        proxy = urlparse(link)
        if proxy.scheme != "https" or proxy.hostname not in {"dl.snapcdn.app", "i.snapcdn.app"}:
            return None
        token = parse_qs(proxy.query).get("token", [""])[0].split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        cdn = urlparse(payload.get("url", ""))
        host = cdn.hostname or ""
        if not (host.endswith(".cdninstagram.com") or host.endswith(".fbcdn.net")):
            return None
        cache_key = parse_qs(cdn.query).get("ig_cache_key", [""])[0]
        cache_key = unquote(unquote(cache_key)).split(".")[0]
        decoded = base64.urlsafe_b64decode(cache_key + "=" * (-len(cache_key) % 4)).decode()
        media_id = decoded.split("_")[0]
        return media_id if media_id.isdigit() else None
    except (ValueError, TypeError, AttributeError, IndexError, binascii.Error, UnicodeError):
        return None


class _MediaParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []
        self.current = None
        self.anchor = None
        self.label = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "li":
            self.current = SaveInstaItem("photo")
        if self.current is None:
            return
        media_id = _proxy_media_id(attrs.get("src", "") if tag == "img" else attrs.get("href", ""))
        if media_id:
            self.current.media_ids.add(media_id)
        if "icon-dlvideo" in attrs.get("class", "").split():
            self.current.type = "video"
        if tag == "a":
            self.anchor = attrs.get("href", "")
            self.label = attrs.get("title", "") + " "

    def handle_data(self, data):
        if self.anchor is not None:
            self.label += data

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None and self.anchor is not None:
            parsed = urlparse(self.anchor)
            if (parsed.scheme == "https" and parsed.hostname == "dl.snapcdn.app"
                    and not parsed.username and not parsed.password
                    and parsed.port in (None, 443)):
                self.current.links.append((self.anchor, self.label.strip().lower()))
            self.anchor = None
        if tag == "li" and self.current is not None:
            self.items.append(self.current)
            self.current = None


def parse_media_links(markup: str, story_id: str | None = None) -> list[tuple[str, str]]:
    parser = _MediaParser()
    parser.feed(markup)
    result = []
    for item in parser.items:
        if story_id is not None and item.media_ids != {story_id}:
            continue
        label = "download video" if item.type == "video" else "download image"
        candidates = [url for url, text in item.links if label in text]
        if not candidates:
            # Reject incomplete albums instead of silently returning a subset.
            raise ValueError("Save Insta returned an item without a media link")
        result.append((item.type, candidates[0]))
    if story_id is not None and len(result) != 1:
        raise ValueError("Requested Story ID not uniquely found; refusing other stories")
    return result


def _curl(url: str, *, data=None, destination=None, timeout=None) -> str:
    limit = timeout or (120 if destination else 30)
    args = [
        "curl", "--silent", "--show-error", "--fail", "--location",
        "--proto", "=https", "--proto-redir", "=https",
        "--connect-timeout", "10", "--max-time", str(limit), url,
    ]
    if data is not None:
        args += [
            "-H", "Content-Type: application/x-www-form-urlencoded",
            "-H", f"Origin: {BASE_URL}",
            "-H", f"Referer: {BASE_URL}/en1",
            "-H", "X-Requested-With: XMLHttpRequest",
            "--data-binary", "@-",
        ]
    if destination:
        args += ["--output", destination, "--write-out", "%{content_type}"]
    proc = subprocess.run(
        args, input=urlencode(data) if data is not None else None,
        capture_output=True, text=True, timeout=limit + 5,
    )
    if proc.returncode:
        # Do not log signed links, verification tokens or remote HTML.
        raise RuntimeError(f"Save Insta curl failed (exit {proc.returncode})")
    return proc.stdout


class _EmbedCaptionParser(HTMLParser):
    """Read visible embed caption, excluding author link and comments."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.caption_depth = None
        self.skip_depth = None
        self.parts = []
        self.author = ""
        self.shortcodes = set()
        self.found = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if tag == "a":
            parsed = urlparse(attrs.get("href", ""))
            match = re.fullmatch(r"/(?:p|reel|reels)/([A-Za-z0-9_-]+)/?", parsed.path)
            if match and (not parsed.netloc or parsed.hostname in {"instagram.com", "www.instagram.com"}):
                if attrs.get("data-log-event") in {"captionCommentsClick", "mediaClick", "timestampClick", "likeCountClick"}:
                    self.shortcodes.add(match.group(1))
        if tag == "br":
            if self.caption_depth is not None and self.skip_depth is None:
                self.parts.append("\n")
            return
        if tag in {"img", "input", "meta", "link", "hr", "source", "wbr", "area", "base", "embed", "param", "track", "col"}:
            return
        self.stack.append(tag)
        depth = len(self.stack)
        if tag == "div" and "Caption" in classes and not self.found:
            self.found = True
            self.caption_depth = depth
        if self.caption_depth is not None and self.skip_depth is None:
            if "CaptionUsername" in classes:
                self.author = urlparse(attrs.get("href", "")).path.strip("/")
                self.skip_depth = depth
            elif "CaptionComments" in classes or tag in {"script", "style"}:
                self.skip_depth = depth

    def handle_endtag(self, tag):
        if tag not in self.stack:
            return
        depth = len(self.stack) - self.stack[::-1].index(tag)
        if self.skip_depth is not None and depth <= self.skip_depth:
            self.skip_depth = None
        if self.caption_depth is not None and depth <= self.caption_depth:
            self.caption_depth = None
        del self.stack[depth - 1:]

    def handle_data(self, data):
        if self.caption_depth is not None and self.skip_depth is None:
            self.parts.append(data)


def parse_embed_metadata(markup: str, shortcode: str) -> dict:
    """Read full structured caption, never comments or unrelated page metadata."""
    for match in re.finditer(r'"contextJSON"\s*:\s*("(?:\\.|[^"\\])*")', markup):
        try:
            context = json.loads(json.loads(match.group(1)))
            media = context.get("gql_data", {}).get("shortcode_media", {})
            if (context.get("context", {}).get("shortcode") != shortcode
                    or media.get("shortcode") != shortcode):
                continue
            edges = media.get("edge_media_to_caption", {}).get("edges") or []
            caption = edges[0].get("node", {}).get("text", "") if edges else ""
            author = media.get("owner", {}).get("username", "")
            return {
                "description": caption.replace("\\r\\n", "\n").replace("\\n", "\n").strip()
                if isinstance(caption, str) else "",
                "author": author if isinstance(author, str) else "",
            }
        except (ValueError, TypeError, AttributeError, IndexError):
            continue
    parser = _EmbedCaptionParser()
    parser.feed(markup)
    if parser.found and shortcode in parser.shortcodes:
        return {"description": "".join(parser.parts).strip(), "author": parser.author}
    return {}


def fetch_post_metadata(url: str) -> dict:
    """Fetch Instagram's own embed, without cookies and with a twelve-second cap."""
    parsed = urlparse(url)
    match = re.fullmatch(r"/(?:p|reel|reels)/([A-Za-z0-9_-]+)/?", parsed.path)
    if (parsed.scheme != "https" or parsed.hostname not in {"instagram.com", "www.instagram.com"}
            or parsed.username or parsed.password or parsed.port not in (None, 443) or not match):
        return {}
    shortcode = match.group(1)
    markup = _curl(f"https://www.instagram.com/p/{shortcode}/embed/captioned/", timeout=12)
    return parse_embed_metadata(markup, shortcode)


def _probe(path: str, kind: str) -> dict:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration", "-of", "json", path],
        capture_output=True, text=True, timeout=15, check=True,
    )
    data = json.loads(proc.stdout)
    stream = (data.get("streams") or [{}])[0]
    if not stream.get("width") or not stream.get("height"):
        raise ValueError("Save Insta returned invalid media")
    return {
        "width": int(stream["width"]), "height": int(stream["height"]),
        "duration": int(float(data.get("format", {}).get("duration") or 0))
        if kind == "video" else 0,
    }


def download_saveinsta(url: str, output_dir: str) -> list[dict]:
    """Download a public post or the exact requested Story; clean up on failure.

    A Story must have a numeric ID. Username-only URLs and highlights are excluded.
    No Instagram credentials are sent to this third-party service.
    """
    parsed = urlparse(url)
    match = re.fullmatch(r"/(p|reel|reels)/([A-Za-z0-9_-]+)/?", parsed.path)
    story = re.fullmatch(r"/stories/([A-Za-z0-9_.]+)/(\d+)/?", parsed.path)
    if (parsed.scheme != "https" or parsed.hostname not in {"instagram.com", "www.instagram.com"}
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not (match or story) or (story and story.group(1).lower() == "highlights")):
        return []
    story_id = None
    if story:
        username, story_id = story.groups()
        post_id = story_id
        canonical = f"https://www.instagram.com/stories/{username}/"
    else:
        kind, post_id = match.groups()
        canonical = f"https://www.instagram.com/{'reel' if kind == 'reels' else kind}/{post_id}/"
    page = _curl(BASE_URL + "/en1")
    tokens = {}
    for key in ("k_exp", "k_token"):
        token = re.search(rf'{key}\s*=\s*["\x27]([^"\x27]+)', page)
        if not token:
            raise ValueError("Save Insta page tokens missing")
        tokens[key] = token.group(1)
    verification = json.loads(_curl(BASE_URL + "/api/userverify", data={"url": canonical}))
    if not verification.get("token"):
        raise ValueError("Save Insta verification failed")
    payload = json.loads(_curl(BASE_URL + "/api/ajaxSearch", data={
        **tokens, "q": canonical, "t": "media", "lang": "en", "v": "v2",
        "cftoken": verification["token"],
    }))
    if payload.get("status") != "ok" or not isinstance(payload.get("data"), str):
        raise ValueError("Save Insta search failed")
    links = parse_media_links(payload["data"], story_id=story_id)
    paths = []
    media = []
    os.makedirs(output_dir, exist_ok=True)
    try:
        for index, (kind, link) in enumerate(links):
            suffix = ".mp4" if kind == "video" else ".jpg"
            fd, path = tempfile.mkstemp(prefix=f"{post_id}_saveinsta_{index}_", suffix=suffix, dir=output_dir)
            os.close(fd)
            paths.append(path)
            content_type = _curl(link, destination=path).strip().lower()
            expected = "video/" if kind == "video" else "image/"
            if not content_type.startswith(expected) or os.path.getsize(path) == 0:
                raise ValueError("Save Insta media content type mismatch")
            media.append({"url": path, "type": kind, "index": index, **_probe(path, kind)})
        return media
    except BaseException:
        for path in paths:
            try:
                os.remove(path)
            except OSError:
                pass
        raise
