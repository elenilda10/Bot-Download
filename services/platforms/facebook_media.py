"""Facebook extraction, privacy rules, album verification and URL handling.

Does not import Telegram or the Telegram bot. Generic yt-dlp/gallery-dl process
execution is reused from universal_extractor; platform-specific decisions stay
here. Browser authentication is opt-in only, passed by the admin's private-chat
message handler. Never share authenticated media through Telegram file_id cache.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, unquote, urljoin, urlsplit
from html import unescape

from services.logger import logger as logging

logging = logging.bind(service="facebook_media")

def _is_facebook_url(url: str) -> bool:
    """Match actual Facebook domains, not domains containing facebook.com."""
    try:
        hostname = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return (
        hostname == "facebook.com"
        or hostname.endswith(".facebook.com")
        or hostname in {"fb.watch", "fb.com", "www.fb.com", "fbs.watch"}
    )


def _facebook_failure_reason(lines: list[str] | str) -> str:
    """Categorize errors without logging URLs, cookie values or private data."""
    tail = "\n".join(lines) if isinstance(lines, list) else lines
    tail = tail.lower()
    if "cannot parse data" in tail or "unable to extract" in tail:
        return "extractor_changed"
    if "login" in tail or "cookies" in tail or "sign in" in tail:
        return "login_or_cookies_required"
    if "403" in tail or "forbidden" in tail:
        return "http_403"
    if "not available" in tail or "removed" in tail or "private" in tail:
        return "unavailable_or_private"
    if "geo" in tail or "region" in tail or "country" in tail:
        return "regional_restriction"
    return "unknown"


def _is_facebook_video_url(url: str) -> bool:
    if not _is_facebook_url(url):
        return False
    parsed = urlsplit(url)
    if parsed.hostname in {"fb.watch", "fbs.watch"}:
        return True
    path = parsed.path.lower()
    return (
        bool(re.search(r"/(?:reel|reels|videos|watch|video\.php)(?:/|$)", path))
        or path.startswith(("/share/v/", "/share/r/", "/share/video/"))
    )


def _is_facebook_media_link(url: str) -> bool:
    """Avoid accidentally downloading a whole Facebook account or feed."""
    if not _is_facebook_url(url):
        return False
    parsed = urlsplit(url)
    if parsed.hostname in {"fb.watch", "fbs.watch"}:
        return True
    path = parsed.path.lower()
    if _is_facebook_video_url(url):
        return True
    return bool(
        re.search(
            r"/(?:photo(?:s|\.php)?|posts|permalink\.php|story\.php|media/set|"
            r"share/(?:p|photo|post)|groups/[^/]+/(?:posts|permalink)|"
            r"[^/]+/posts)(?:/|$)",
            path,
        )
        or (path.startswith("/share/") and len(path.strip("/").split("/")) >= 2)
    )


# Domain-anchored pattern: not facebook.com.evil.example. No generic catch-all,
# because a matching aiogram handler stops propagation to other platform routers.
FACEBOOK_LINK_REGEX = (
    r"https?://(?:(?:[a-z0-9-]+\.)*facebook\.com|"
    r"(?:www\.)?fb\.com|(?:www\.)?fb\.watch|"
    r"(?:www\.)?fbs\.watch)"
    r"(?::443)?(?=[/?#\s]|$)[^\s<>]*"
)
_LINK_PATTERN = re.compile(FACEBOOK_LINK_REGEX, re.IGNORECASE)
_REEL_PATH = re.compile(r"/(?:reel|reels)/(\d+)/?", re.IGNORECASE)
_VIDEO_PATH = re.compile(r"/videos/(\d+)/?", re.IGNORECASE)
_GROUP_POST_PATH = re.compile(r"/groups/(\d+)/(permalink|posts)/(\d+)/?", re.IGNORECASE)
_SHORT_FB_HOSTS = {"fb.watch", "www.fb.watch", "fbs.watch", "www.fbs.watch"}


def _needs_redirect(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        (parsed.hostname or "").lower() in _SHORT_FB_HOSTS
        or parsed.path.lower().startswith("/share/")
    )


def _canonical_fb_media_url(url: str) -> str | None:
    """Preserve photo query IDs; strip tracking only for numeric video IDs."""
    if not _is_facebook_url(url) or not _is_facebook_media_link(url):
        return None

    parts = urlsplit(url)
    path = parts.path
    # A Facebook share/p redirect may end in story.php. gallery-dl cannot
    # reliably handle its tracking parameters, so use a numeric post URL only
    # when profile, story and composite post identity all agree.
    if path.lower() == "/story.php":
        query = parse_qs(parts.query)
        profile_ids = query.get("id", [])
        story_ids = query.get("story_fbid", [])
        post_ids = query.get("post_id", [])
        if (
            len(profile_ids) == 1 and len(story_ids) == 1
            and profile_ids[0].isdigit() and story_ids[0].isdigit()
            and (not post_ids or post_ids == [f"{profile_ids[0]}_{story_ids[0]}"])
        ):
            return (
                f"https://www.facebook.com/{profile_ids[0]}/"
                f"posts/{story_ids[0]}/"
            )
    match = _REEL_PATH.fullmatch(path)
    if match:
        return f"https://www.facebook.com/reel/{match.group(1)}/"
    match = _VIDEO_PATH.fullmatch(path)
    if match:
        return f"https://www.facebook.com/videos/{match.group(1)}/"
    match = _GROUP_POST_PATH.fullmatch(path)
    if match:
        # Gallery extraction must use the post URL itself, not a URL with
        # share_url/rdid tracking parameters.
        return (
            f"https://www.facebook.com/groups/{match.group(1)}/"
            f"{match.group(2).lower()}/{match.group(3)}/"
        )
    # Facebook /share/<token> resolves to /<numeric-profile>/posts/<post-id>/
    # with rdid/share_url tracking. Keep only the post's verified numeric
    # identity: the tracked URL can show a different page to gallery-dl.
    profile_post = re.fullmatch(r"/(\d+)/posts/(\d+)/?", path)
    if profile_post:
        return (
            f"https://www.facebook.com/{profile_post.group(1)}/"
            f"posts/{profile_post.group(2)}/"
        )
    # Do not drop 'fbid' on photo.php or 'v' on watch pages.
    return url.split("#", 1)[0]


async def _resolve_facebook_short_link(url: str) -> str | None:
    """Resolve Facebook short links using curl GETs without unsafe redirects.

    curl's TLS/HTTP behavior worked on the VPS for /share/r/ where aiohttp
    did not reliably resolve. Every hop is checked BEFORE requesting it,
    preventing Facebook link redirects to unrelated or internal hosts.
    No cookies or authenticated headers are passed to curl.
    """
    if not _is_facebook_url(url) or not _is_facebook_media_link(url):
        return None
    current = url
    for _ in range(5):
        if not _needs_redirect(current):
            return _canonical_fb_media_url(current)

        command = [
            "curl", "--silent", "--show-error",
            "--max-time", "12", "--connect-timeout", "5",
            "--proto", "=https",
            "--user-agent", "Mozilla/5.0",
            "--output", "/dev/null", "--dump-header", "-", current,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=14)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                logging.warning("Facebook short-link resolution timed out")
                return None
        except (OSError, ValueError):
            logging.warning("Facebook short-link resolver unavailable")
            return None

        if proc.returncode != 0:
            logging.warning("Facebook short-link resolver failed: code=%s", proc.returncode)
            return None

        # A single GET response is enough: read the Location response header
        # without following the redirect or downloading the target page.
        header_lines = stdout.decode("utf-8", errors="replace").splitlines()
        locations = [
            line.split(":", 1)[1].strip()
            for line in header_lines
            if line.lower().startswith("location:")
        ]
        if not locations:
            logging.warning("Facebook short link did not return a redirect")
            return None

        next_url = urljoin(current, locations[-1])
        if urlsplit(next_url).scheme != "https" or not _is_facebook_url(next_url):
            logging.warning("Facebook short-link redirect rejected (non-Facebook)")
            return None
        current = next_url

    logging.warning("Facebook short-link redirect limit reached")
    return None


def _facebook_group_post_id(url: str) -> Optional[str]:
    """Accept only a specific numeric group post, never a group feed."""
    if not _is_facebook_url(url):
        return None
    path = urlsplit(url).path
    match = re.fullmatch(
        r"/groups/\d+/(?:permalink|posts)/(\d+)/?", path, re.IGNORECASE
    )
    return match.group(1) if match else None


def _facebook_profile_post_id(url: str) -> Optional[str]:
    """Only specific numeric profile/page posts, never a profile timeline."""
    if not _is_facebook_url(url):
        return None
    path = urlsplit(url).path
    match = re.fullmatch(r"/\d+/posts/(\d+)/?", path, re.IGNORECASE)
    return match.group(1) if match else None


def _facebook_profile_photo_link_id(linked: object) -> str | None:
    if not isinstance(linked, str):
        return None
    link = unquote(unescape(linked.replace("\\/", "/")))
    parsed = urlsplit(link)
    if parsed.scheme != "https" or not _is_facebook_url(link):
        return None
    if parsed.path.lower().rstrip("/") not in {"/photo", "/photo.php"}:
        return None
    values = parse_qs(parsed.query).get("fbid", [])
    return values[0] if len(values) == 1 and values[0].isdigit() else None


def _facebook_login_prompt(metadata: dict) -> bool:
    """Reject known login/remember-password UI as a fake post caption."""
    strings = (metadata.get("post_text"), metadata.get("caption"))
    return any(
        isinstance(value, str)
        and value.strip().casefold() in {
            "remember password", "remember your password",
            "log in to facebook", "login to facebook", "forgot password?",
        }
        for value in strings
    )


def _facebook_profile_post_json(data: object, url: str) -> dict | None:
    """Validate gallery-dl's *media* messages against a specific profile post.

    gallery-dl emits [2, metadata] AND [3, url, metadata] for every photo.
    Counting both duplicates IDs. On albums, 'post_photo' and 'first_photo_id'
    reference the FIRST photo for every item, not each item's own ID.
    """
    post_id = _facebook_profile_post_id(url)
    if not post_id or not isinstance(data, list):
        return None

    photos = []
    for entry in data:
        if not isinstance(entry, (list, tuple)) or not entry:
            return None
        if entry[0] == -1:  # JSON output can contain errors despite exit code 0.
            return None
        if entry[0] != 3:
            continue
        if len(entry) < 3 or not isinstance(entry[2], dict):
            return None
        photos.append(entry[2])

    if not 1 <= len(photos) <= 10:
        return None

    ids = tuple(str(photo.get("id") or "") for photo in photos)
    if any(not photo_id.isdigit() for photo_id in ids):
        return None
    if len(set(ids)) != len(ids):
        return None

    first_id = ids[0]
    expected_set = f"pcb.{post_id}"
    texts = set()

    for index, photo in enumerate(photos):
        if _facebook_login_prompt(photo):
            return None
        if _facebook_profile_photo_link_id(photo.get("post_photo")) != first_id:
            return None
        first_photo_id = str(photo.get("first_photo_id") or "")
        if first_photo_id and first_photo_id != first_id:
            return None

        set_id = str(photo.get("set_id") or "")
        if (len(photos) > 1 or set_id) and set_id != expected_set:
            return None

        num = photo.get("num")
        if num is not None and str(num) != str(index + 1):
            return None

        next_id = str(photo.get("next_photo_id") or "")
        if index + 1 < len(photos):
            if next_id and next_id != ids[index + 1]:
                return None
        elif len(photos) > 1 and next_id and next_id not in {first_id, ids[-1]}:
            # A different next photo means gallery-dl stopped mid-album.
            return None

        text = photo.get("post_text")
        if isinstance(text, str) and text.strip():
            texts.add(text.strip())

    if len(texts) > 1:
        return None  # Conflicting text within the same post is not trustworthy.

    # For a single photo, 'post_text' is extracted from the first generic
    # text element on the Facebook post page and can belong to another post.
    # The photo-page caption is tied to the identified photo; if absent, do
    # not show an unverified fallback. Albums retain their post-level text.
    description = (
        facebook_photo_caption(photos[0]) if len(photos) == 1
        else next(iter(texts), "")
    )
    return {"ids": ids, "description": description}


async def _facebook_profile_post_metadata(
    url: str, browser_profile: Optional[str] = None,
) -> dict | None:
    """Read the requested post object, never gallery-dl's unscoped metadata.

    gallery-dl supplies its HTTP/cookie transport and writes the initial page.
    Its selected photo and JSON output are intentionally ignored. Private HTML
    is confined to a temporary directory removed even on failure/cancellation.
    """
    source_id = _facebook_profile_post_id(url)
    if not source_id:
        return None
    from services.platforms.facebook_post_identity import extract_post_identity, MAX_HTML
    from services.platforms.universal_extractor import GDL_BIN, _DEFAULT_UA
    binary = os.path.abspath(GDL_BIN) if os.path.isfile(GDL_BIN) else "gallery-dl"
    with tempfile.TemporaryDirectory(prefix="fb-post-identity-") as work:
        command = [
            binary, "--config-ignore", "--write-pages", "--dump-json",
            "--range", "1-1", "--retries", "0", "--http-timeout", "20",
            "--user-agent", _DEFAULT_UA,
            *(["--cookies-from-browser", f"firefox:{browser_profile}"]
              if browser_profile else []),
            url,
        ]
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *command, cwd=work, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=75)
            # gallery-dl 1.32.14 numbers response dumps starting at 01.
            # Never inspect later photo/set pages selected by its flawed parser.
            pages = list(Path(work).glob("01_*.txt"))
            if len(pages) != 1 or pages[0].stat().st_size > MAX_HTML:
                return None
            html = pages[0].read_text(encoding="utf-8", errors="replace")
            record = extract_post_identity(html, source_id)
            if record is None:
                logging.warning("Facebook requested post object was not verified")
            return record
        except (OSError, ValueError, asyncio.TimeoutError):
            logging.warning("Facebook post-bound HTML preflight failed")
            return None
        finally:
            if proc is not None and proc.returncode is None:
                proc.kill()
                await proc.communicate()

async def _facebook_profile_post_expected_photo_ids(
    url: str, browser_profile: Optional[str] = None,
) -> tuple[str, ...] | None:
    """Compatibility helper for ID-only diagnostics."""
    record = await _facebook_profile_post_metadata(url, browser_profile)
    return record["ids"] if record else None


def _order_facebook_photo_paths(
    paths: list[str], expected_ids: tuple[str, ...],
) -> list[str]:
    """Preserve the post's photo order regardless of os.walk() ordering."""
    position = {photo_id: index for index, photo_id in enumerate(expected_ids)}
    return sorted(
        paths,
        key=lambda path: position.get(
            os.path.splitext(os.path.basename(path))[0],
            len(position),
        ),
    )


def _verified_facebook_profile_photos(
    url: str, paths: list[str], expected_ids: tuple[str, ...] | None = None,
) -> bool:
    """Allow only photos traceable to the requested profile publication.

    The gallery-dl post parser places the photo link extracted from the
    publication into post_photo metadata. For a single-photo share the photo
    owner may differ from the sharing profile, and set_id may be empty.
    For multi-photo sets require pcb.<POST_ID> album identity.
    """
    post_id = _facebook_profile_post_id(url)
    # A sidecar validating against itself is not sufficient: require the
    # separate post-bound metadata read to agree on the complete photo set.
    if (
        not post_id or not 1 <= len(paths) <= 10
        or not expected_ids or len(paths) != len(expected_ids)
        or set(expected_ids) != {
            os.path.splitext(os.path.basename(path))[0] for path in paths
        }
    ):
        return False
    expected_set = f"pcb.{post_id}"
    multi = len(paths) > 1
    for path in paths:
        photo_id, ext = os.path.splitext(os.path.basename(path))
        if ext.lower() not in {".jpg", ".jpeg", ".png", ".webp"} or not photo_id.isdigit():
            return False
        try:
            with open(path + ".json", "r", encoding="utf-8") as stream:
                metadata = json.load(stream)
        except (OSError, ValueError):
            return False
        if not isinstance(metadata, dict) or str(metadata.get("id")) != photo_id:
            return False
        if _facebook_login_prompt(metadata):
            return False
        set_id = str(metadata.get("set_id") or "")
        # Single photo with no album token: require exact source photo ID.
        if not multi and set_id == "":
            if _facebook_profile_photo_link_id(metadata.get("post_photo")) != photo_id:
                return False
        else:
            # Never allow a photo set belonging to a different publication.
            if set_id != expected_set:
                return False
            if f"({expected_set})" not in os.path.basename(os.path.dirname(path)):
                return False
    return True


async def _download_facebook_profile_post_photos(
    url: str, output_dir: str, post_id: str,
    *, browser_profile: Optional[str], gallery_downloader,
):
    """Download only the ordered photo IDs bound to the requested post object."""
    record = await _facebook_profile_post_metadata(url, browser_profile)
    if not record or record.get("source_post_id") != _facebook_profile_post_id(url):
        logging.warning("Facebook profile post lacked post-bound metadata")
        return None
    ids = record["ids"]
    if not 1 <= len(ids) <= 10 or len(set(ids)) != len(ids):
        return None
    combined = None
    items = []
    for photo_id in ids:
        if not str(photo_id).isdigit():
            return None
        # Separate directories avoid accumulating earlier photos on album reads.
        photo_dir = os.path.join(output_dir, f"post-photo-{photo_id}")
        os.makedirs(photo_dir, exist_ok=True)
        direct_url = f"https://www.facebook.com/photo/?fbid={photo_id}&set="
        result = await gallery_downloader(
            direct_url, photo_dir, post_id,
            facebook_browser_profile=browser_profile,
        )
        if not result or len(result.media_list) != 1:
            return None
        item = result.media_list[0]
        if (item.type != "photo" or not os.path.isfile(item.url)
                or os.path.splitext(os.path.basename(item.url))[0] != photo_id):
            return None
        try:
            with open(item.url + ".json", encoding="utf-8") as stream:
                metadata = json.load(stream)
        except (OSError, ValueError):
            return None
        if (not isinstance(metadata, dict) or str(metadata.get("id")) != photo_id
                or _facebook_login_prompt(metadata)):
            return None
        items.append(item)
        if combined is None:
            combined = result
    combined.media_list = items
    for index, item in enumerate(items):
        item.index = index
    # Blank is deliberate when no post-bound message was found. Do not fall
    # back to a different photo owner's caption or a generic page text node.
    combined.description = record["description"]
    logging.info("Facebook photos verified against requested post object")
    return combined


def _verified_facebook_group_photos(url: str, paths: list[str]) -> bool:
    """Fail closed unless every image is tied to the requested post.

    gallery-dl labels a group post's image set as pcb.<POST_ID>. The
    downloaded directory AND the JSON sidecar must agree. A previous Facebook
    extraction downloaded an image from an unrelated post; merely receiving a
    JPEG or matching a profile name is not sufficient evidence.
    """
    post_id = _facebook_group_post_id(url)
    if not post_id or not (1 <= len(paths) <= 10):
        return False
    expected_set = f"pcb.{post_id}"
    for path in paths:
        name, ext = os.path.splitext(os.path.basename(path))
        if ext.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            return False
        if not name.isdigit():
            return False
        parent = os.path.basename(os.path.dirname(path))
        if f"({expected_set})" not in parent:
            return False
        # gallery-dl --write-metadata appends ".json" to the *full* media
        # filename (example: 1629584308563349.jpg.json), not to its stem.
        # The older "1629584308563349.json" lookup rejected valid images.
        sidecar = path + ".json"
        try:
            with open(sidecar, "r", encoding="utf-8") as fp:
                metadata = json.load(fp)
        except (OSError, ValueError):
            return False
        if not isinstance(metadata, dict):
            return False
        if str(metadata.get("set_id", "")) != expected_set:
            return False
        if str(metadata.get("id", "")) != name:
            return False
    return True




def _facebook_gallery_first_conflicts_with_post(url: str, paths: list[str]) -> bool:
    """Reject clear contradictions while allowing unverified post downloads.

    Gallery-first does NOT prove a photo belongs to a Facebook post. It only
    rejects mismatched album tokens, sidecar photo IDs and login screens.
    Missing post identity is still possible; never describe it as verified.
    """
    if not _is_facebook_url(url):
        return True
    group_id = _facebook_group_post_id(url)
    profile_id = _facebook_profile_post_id(url)
    expected_set = f"pcb.{group_id or profile_id}" if (group_id or profile_id) else None
    parsed = urlsplit(url)
    direct_id = None
    if parsed.path.lower().rstrip("/") in {"/photo", "/photo.php"}:
        ids = parse_qs(parsed.query).get("fbid", [])
        direct_id = ids[0] if len(ids) == 1 and ids[0].isdigit() else None

    for path in paths:
        name, ext = os.path.splitext(os.path.basename(path))
        if ext.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        if direct_id and name != direct_id:
            return True
        try:
            with open(path + ".json", "r", encoding="utf-8") as stream:
                meta = json.load(stream)
        except (OSError, ValueError):
            # Absence of metadata is not positive verification.
            continue
        if not isinstance(meta, dict):
            return True
        if _facebook_login_prompt(meta):
            return True
        if "id" in meta and str(meta["id"]) != name:
            return True
        set_id = str(meta.get("set_id") or "")
        if expected_set and set_id.startswith("pcb.") and set_id != expected_set:
            return True
        if direct_id and str(meta.get("id") or direct_id) != direct_id:
            return True
        # Album metadata may repeat the first photo's post_photo on every
        # item. Only compare this field for a standalone, non-album photo.
        if meta.get("post_photo") and not set_id and len(paths) == 1:
            referenced = _facebook_profile_photo_link_id(meta["post_photo"])
            if referenced is not None and referenced != name:
                return True
    return False


def facebook_gallery_arguments(
    browser_profile: Optional[str] = None,
) -> list[str]:
    """gallery-dl flags for a single Facebook media post or gallery."""
    args = [
        "--range", "1-10",
        "-o", "extractor.facebook.videos=ytdl",
    ]
    if browser_profile:
        args.extend(["--cookies-from-browser", f"firefox:{browser_profile}"])
    return args


def facebook_ytdlp_arguments(
    browser_profile: Optional[str] = None,
) -> list[str]:
    """Use the same Telegram-friendly codec preference as before."""
    args = ["--write-info-json", "-S", "+codec:avc:m4a"]
    if browser_profile:
        args.extend(["--cookies-from-browser", f"firefox:{browser_profile}"])
    return args


def facebook_photo_caption(metadata: dict) -> str:
    """Caption explicitly extracted from a photo page, not a generic post HTML text.

    A Facebook photo's 'post_text' can be an unrelated recommendation. Never
    use it to populate a missing caption for a single-photo delivery.
    """
    if not isinstance(metadata, dict):
        return ""
    caption = metadata.get("caption")
    return caption.strip() if isinstance(caption, str) else ""


def facebook_caption(metadata: dict) -> str:
    """Prioritize sharing-post text over the original photo's caption."""
    if not isinstance(metadata, dict):
        return ""
    for key in ("post_text", "caption"):
        original = metadata.get(key)
        if isinstance(original, str) and original.strip():
            return original.strip()
    return ""



def facebook_video_caption(metadata: dict) -> str:
    """Prefer the original Reel/video post description over a generic title.

    yt-dlp can produce title="Video" (and similar generated placeholders)
    even when the accompanying description contains the author's post text.
    Do not invent a caption if the remote metadata has no usable text.
    """
    if not isinstance(metadata, dict):
        return ""
    generic = {
        "video", "facebook video", "facebook reel", "reel",
        "watch video", "vídeo", "vídeo do facebook",
    }
    for key in ("caption", "description", "title", "fulltitle"):
        raw = metadata.get(key)
        if not isinstance(raw, str):
            continue
        value = raw.strip()
        if (
            value
            and value.casefold() not in generic
            and not re.fullmatch(r"Facebook video #\d+", value, re.IGNORECASE)
        ):
            return value
    return ""

async def download_facebook_media(
    url: str,
    output_dir: str = "downloads",
    on_progress: Optional[Callable[[int, Optional[int], Optional[float]], None]] = None,
    post_id: Optional[str] = None,
    facebook_browser_profile: Optional[str] = None,
    facebook_gallery_first_test: bool = False,
    facebook_gallery_first: bool = False,
):
    """Facebook-only media extraction, with conservative privacy boundaries.

    Browser cookies are NEVER obtained here automatically. The caller can
    explicitly pass a Firefox profile only from the verified admin/private-chat
    branch of the Telegram handler. Group media is validated before delivery.
    """
    # This is a transport dependency, not a Facebook handler dependency.
    # Import lazily so Universal can dispatch to this service without a cycle.
    from services.platforms.universal_extractor import (
        _download_with_gallery_dl, _download_with_ytdlp,
    )

    os.makedirs(output_dir, exist_ok=True)
    if not post_id:
        clean = url.split("#", 1)[0].strip()
        post_id = hashlib.blake2s(clean.encode("utf-8"), digest_size=8).hexdigest()

    if _is_facebook_url(url):
        if not _is_facebook_media_link(url):
            logging.warning("Facebook URL is not a single media link")
            return None

        # gallery-dl does not accept /share/r/ links. Resolve them BEFORE
        # extraction. A canonical Reel URL worked with Firefox auth on VPS.
        # All Facebook short links use a strict, HTTPS-only redirect resolver.
        # The generic Universal redirect client cannot reliably resolve /share/r/.
        resolved = await _resolve_facebook_short_link(url) if _needs_redirect(url) else url
        if resolved and _is_facebook_media_link(resolved):
            target = _canonical_fb_media_url(resolved) or resolved
        else:
            logging.warning("Facebook short link did not resolve to a media post")
            return None
        target_path = urlsplit(target).path.lower()
        if re.fullmatch(r"/reel/\d+/?", target_path):
            target = f"https://www.facebook.com{target_path.rstrip('/')}/"

        if facebook_gallery_first and not facebook_gallery_first_test:
            # Legacy FB_GALLERY_FIRST callers cannot bypass photo/post identity
            # checks. The old path sent unrelated photos from Facebook feeds.
            logging.warning("Facebook gallery-first ignored outside explicit admin test")

        if facebook_gallery_first_test:
            # Explicit private-admin /fbteste is diagnostic ONLY; never use
            # this unverified path for regular user downloads or callbacks.
            if not facebook_browser_profile:
                logging.warning("Facebook gallery-first test requires private admin authentication")
                return None
            gallery = await _download_with_gallery_dl(
                target, output_dir, post_id,
                facebook_browser_profile=facebook_browser_profile,
                facebook_gallery_test_skip_post_check=bool(facebook_browser_profile),
                facebook_gallery_public_unverified=not bool(facebook_browser_profile),
            )
            if gallery and gallery.media_list:
                logging.warning("Facebook gallery-first delivered media without post identity verification")
                return gallery
            # gallery-dl may not support standalone Reels/videos on every
            # Facebook page; use yt-dlp only when gallery-dl returned nothing.
            logging.info("Facebook gallery-first: trying yt-dlp fallback")
            return await _download_with_ytdlp(
                target, output_dir=output_dir, on_progress=on_progress,
                post_id=post_id, facebook_browser_profile=facebook_browser_profile,
            )

        # Authenticated downloads are restricted to the admin's PRIVATE chat
        # at the handler layer. Only direct image IDs or canonical video IDs
        # are eligible; gallery-dl may return the wrong photo from a post.
        if facebook_browser_profile:
            is_direct_photo = (
                target_path.rstrip("/") in {"/photo", "/photo.php"}
                and bool(re.search(r"(?:^|&)fbid=\d+(?:&|$)", urlsplit(target).query))
            )
            is_verified_group_post = _facebook_group_post_id(target) is not None
            is_verified_profile_post = _facebook_profile_post_id(target) is not None
            has_canonical_video_id = bool(
                re.fullmatch(r"/(?:reel|reels|videos)/\d+/?", target_path)
                or (target_path == "/watch/" and re.search(
                    r"(?:^|&)v=\d+(?:&|$)", urlsplit(target).query
                ))
            )
            if not (
                is_direct_photo or has_canonical_video_id
                or is_verified_group_post or is_verified_profile_post
            ):
                logging.warning("Facebook authenticated fallback requires direct media ID")
                return None
            if is_verified_profile_post:
                return await _download_facebook_profile_post_photos(
                    target, output_dir, post_id,
                    browser_profile=facebook_browser_profile,
                    gallery_downloader=_download_with_gallery_dl,
                )
            if is_verified_group_post:
                # This publication might be members-only: admin private chat
                # only, and Telegram file_id cache disabled for all Facebook.
                group_photos = await _download_with_gallery_dl(
                    target, output_dir, post_id,
                    facebook_browser_profile=facebook_browser_profile,
                )
                if group_photos and group_photos.media_list:
                    logging.info("Facebook group post photos verified")
                return group_photos
            if is_direct_photo:
                photo = await _download_with_gallery_dl(
                    target, output_dir, post_id,
                    facebook_browser_profile=facebook_browser_profile,
                )
                # Verify that the downloaded photo has the exact requested ID.
                expected = re.search(
                    r"(?:^|&)fbid=(\d+)(?:&|$)", urlsplit(target).query
                ).group(1)
                if photo and photo.media_list and all(
                    os.path.splitext(os.path.basename(item.url))[0] == expected
                    for item in photo.media_list
                ):
                    logging.info("Facebook authenticated direct photo verified")
                    return photo
                logging.warning("Facebook photo ID mismatch, refusing delivery")
                return None
            video = await _download_with_ytdlp(
                target, output_dir=output_dir, on_progress=on_progress,
                post_id=post_id, facebook_browser_profile=facebook_browser_profile,
            )
            if video and video.media_list:
                logging.info("Facebook authenticated video extracted")
            return video

        # Ordinary users: public/anonymous extraction only, never use the
        # Firefox account or other stored Facebook credentials.
        # Facebook post/album extractors have returned photos belonging to
        # different posts. Without a direct photo ID we cannot verify them.
        if not _is_facebook_video_url(target):
            if _facebook_profile_post_id(target):
                return await _download_facebook_profile_post_photos(
                    target, output_dir, post_id,
                    browser_profile=None,
                    gallery_downloader=_download_with_gallery_dl,
                )
            match = re.search(
                r"(?:^|&)fbid=(\d+)(?:&|$)", urlsplit(target).query
            ) if target_path.rstrip("/") in {"/photo", "/photo.php"} else None
            if match is None:
                logging.warning("Facebook post image ID cannot be verified; refusing delivery")
                return None
            expected_id = match.group(1)
            photo = await _download_with_gallery_dl(target, output_dir, post_id)
            if (photo and photo.media_list and all(
                item.type == "photo"
                and os.path.splitext(os.path.basename(item.url))[0] == expected_id
                for item in photo.media_list
            )):
                return photo
            logging.warning("Facebook anonymous photo ID mismatch")
            return None

        if _is_facebook_video_url(target):
            video = await _download_with_ytdlp(
                target, output_dir=output_dir, on_progress=on_progress,
                post_id=post_id,
            )
            if video and video.media_list:
                return video
            return await _download_with_gallery_dl(target, output_dir, post_id)
        gallery = await _download_with_gallery_dl(target, output_dir, post_id)
        if gallery and gallery.media_list:
            return gallery
        return await _download_with_ytdlp(
            target, output_dir=output_dir, on_progress=on_progress, post_id=post_id,
        )

