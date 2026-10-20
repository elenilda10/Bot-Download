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


async def _facebook_profile_post_metadata(
    url: str, browser_profile: Optional[str] = None,
) -> dict | None:
    """Get post-bound media IDs independently before the actual download.

    A downloaded gallery sidecar can consistently reference its own photo
    even if the Facebook session was redirected to an unrelated post or login
    screen. Requiring a separate read of the *requested post* to identify the
    same photo prevents accepting that sort of mismatch. Failure is denial.
    """
    if not _facebook_profile_post_id(url):
        return None
    from services.platforms.universal_extractor import GDL_BIN, _DEFAULT_UA
    binary = GDL_BIN if os.path.isfile(GDL_BIN) else "gallery-dl"
    command = [
        binary, "--dump-json", "--range", "1-10",
        "--user-agent", _DEFAULT_UA,
        "-o", "extractor.facebook.videos=ytdl",
        *(
            ["--cookies-from-browser", f"firefox:{browser_profile}"]
            if browser_profile else []
        ),
        url,
    ]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=75)
        if proc.returncode != 0 or len(stdout) > 2_000_000:
            return None
        data = json.loads(stdout)
    except (OSError, ValueError, asyncio.TimeoutError):
        logging.warning("Facebook profile post preflight failed")
        return None
    finally:
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.communicate()

    # gallery-dl emits a JSON array of directory/media entries; only the
    # media entries with the post_photo link establish IDs.
    records = []
    def walk(value):
        if isinstance(value, dict):
            if "post_photo" in value and "id" in value:
                records.append(value)
            for inner in value.values():
                walk(inner)
        elif isinstance(value, list):
            for inner in value:
                walk(inner)
    walk(data)
    if not 1 <= len(records) <= 10:
        logging.warning("Facebook profile preflight had no bounded media set")
        return None
    expected = []
    for metadata in records:
        media_id = str(metadata["id"])
        link_id = _facebook_profile_photo_link_id(metadata.get("post_photo"))
        if not media_id.isdigit() or link_id != media_id or _facebook_login_prompt(metadata):
            logging.warning("Facebook profile preflight identity/login check failed")
            return None
        expected.append(media_id)
    if len(set(expected)) != len(expected):
        return None
    # The author's shared-post text must come from the publication's own
    # preflight, never from a separately downloaded photo page.
    description = facebook_caption(records[0])
    return {"ids": tuple(expected), "description": description}


async def _facebook_profile_post_expected_photo_ids(
    url: str, browser_profile: Optional[str] = None,
) -> tuple[str, ...] | None:
    """Compatibility helper for ID-only diagnostics."""
    record = await _facebook_profile_post_metadata(url, browser_profile)
    return record["ids"] if record else None


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
    """Download a validated post's photo by *exact* Facebook photo ID.

    gallery-dl may select a different photo when given a profile post URL.
    For one photo, first resolve its numeric fbid from the post page, then
    fetch the photo URL directly and require the local file and JSON sidecar
    to have that exact id. Multi-photo posts use the stricter post album path
    until each individual item can be tied to the album.
    """
    record = await _facebook_profile_post_metadata(url, browser_profile)
    if not record:
        logging.warning("Facebook profile post lacked valid preflight metadata")
        return None

    ids = record["ids"]
    if len(ids) == 1:
        photo_id = ids[0]
        # Photo ID comes exclusively from the validated post metadata, not
        # from a user-controlled URL, and is strictly numerical.
        direct_url = f"https://www.facebook.com/photo/?fbid={photo_id}&set="
        result = await gallery_downloader(
            direct_url, output_dir, post_id,
            facebook_browser_profile=browser_profile,
        )
        if not result or len(result.media_list) != 1:
            logging.warning("Facebook profile post direct photo extraction failed")
            return None
        item = result.media_list[0]
        photo_path = item.url
        if (
            item.type != "photo"
            or os.path.splitext(os.path.basename(photo_path))[0] != photo_id
            or not os.path.isfile(photo_path)
        ):
            logging.warning("Facebook direct photo ID mismatch")
            return None
        try:
            with open(photo_path + ".json", "r", encoding="utf-8") as stream:
                metadata = json.load(stream)
        except (OSError, ValueError):
            logging.warning("Facebook direct photo metadata missing")
            return None
        if (
            not isinstance(metadata, dict)
            or str(metadata.get("id")) != photo_id
            or _facebook_login_prompt(metadata)
        ):
            logging.warning("Facebook direct photo metadata ID mismatch")
            return None
    else:
        # For albums, never accept a self-consistent but unrelated gallery;
        # check the complete ID set against the independently parsed post.
        result = await gallery_downloader(
            url, output_dir, post_id,
            facebook_browser_profile=browser_profile,
            facebook_expected_photo_ids=ids,
        )
        if not result or len(result.media_list) != len(ids):
            return None

    # The photo's own caption may be different from what the sharing account
    # wrote. Prefer the verified source post text when available.
    if record["description"]:
        result.description = record["description"]
    logging.info("Facebook profile post photos verified by preflight IDs")
    return result


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
            target = resolved
        else:
            logging.warning("Facebook short link did not resolve to a media post")
            return None
        target_path = urlsplit(target).path.lower()
        if re.fullmatch(r"/reel/\d+/?", target_path):
            target = f"https://www.facebook.com{target_path.rstrip('/')}/"

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

