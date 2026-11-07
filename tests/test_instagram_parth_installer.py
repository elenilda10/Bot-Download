"""Tests for the conservative VPS migration (no network and no service)."""
import importlib.util
from pathlib import Path
import unittest

module = Path(__file__).resolve().parents[1] / "scripts/install_instagram_parth_vps.py"
spec = importlib.util.spec_from_file_location("_install_instagram_parth_test", module)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

LEGACY = '''
import os
import time
import asyncio
from services.platforms.instagram_saveinsta import download_saveinsta, fetch_post_metadata

EXTENSION_COOKIES_PATH = "/tmp/insta.cookies"
INSTAGRAM_FIREFOX_PROFILE = "/tmp/firefox"

def _run_gallery_dl(url, output_dir, post_id, story_id=None):
    cmd = ["gallery-dl"]
    if os.path.exists(EXTENSION_COOKIES_PATH):
        cmd.extend(["--cookies", EXTENSION_COOKIES_PATH])
    if story_id and INSTAGRAM_FIREFOX_PROFILE:
        cmd.extend(["--cookies-from-browser", INSTAGRAM_FIREFOX_PROFILE])
    cmd.append(url)
    return None

def _run_ytdlp(url, output_dir, post_id):
    metadata_cmd = ["yt-dlp"]
    if os.path.exists(EXTENSION_COOKIES_PATH):
        metadata_cmd.extend(["--cookies", EXTENSION_COOKIES_PATH])
    metadata_cmd.append(url)
    download_cmd = ["yt-dlp"]
    if os.path.exists(EXTENSION_COOKIES_PATH):
        download_cmd.extend(["--cookies", EXTENSION_COOKIES_PATH])
    download_cmd.append(url)
    return None

async def fetch_instagram_media(url, output_dir="/tmp"):
    if "/stories/" in url:
        return None
    story_id = None
    clean_url = url
    post_id = "abc"
    is_reel = False
    os.makedirs(output_dir, exist_ok=True)
    loop = asyncio.get_running_loop()

    _t = time.monotonic()
    res_gdl = await loop.run_in_executor(None, _run_gallery_dl, clean_url, output_dir, post_id, story_id)
    logger.warning("gallery")
    if res_gdl:
        return res_gdl

    if not story_id:
        items = await loop.run_in_executor(None, download_saveinsta, clean_url, output_dir)
        if items:
            return items

    _t = time.monotonic()
    res_ytdlp = await loop.run_in_executor(None, _run_ytdlp, clean_url, output_dir, post_id)
    logger.warning("yt")
    if res_ytdlp:
        return res_ytdlp

    payload = {"url": clean_url}
    _t = time.monotonic()
    try:
        data = await fetch_cobalt_data("url", "key", payload)
        if data:
            return data
    finally:
        logger.warning("cobalt")

    headers = {"Accept": "application/json"}
    _t_public = time.monotonic()
    async with httpx.AsyncClient() as client:
        await client.post("https://example.invalid", headers=headers)
    logger.warning("cobalt_public")
    return None
'''


class VpsInstallTests(unittest.TestCase):
    def test_migrate_pipeline_and_disable_login(self):
        updated = installer.patch_service(LEGACY)
        self.assertEqual(updated, installer.patch_service(updated))
        self.assertIn("from services.platforms.instagram_parth import download_parth", updated)
        self.assertIn("'--config-ignore'", updated)
        self.assertIn("'--no-cookies'", updated)
        self.assertNotIn('cmd.extend(["--cookies", EXTENSION_COOKIES_PATH])', updated)

        flow = updated.split("async def fetch_instagram_media(", 1)[1]
        steps = (
            "download_saveinsta, clean_url",
            "download_parth, clean_url",
            "fetch_cobalt_data(",
            "_run_ytdlp, clean_url",
            "_run_gallery_dl, clean_url",
        )
        indices = [flow.index(step) for step in steps]
        self.assertEqual(indices, sorted(indices))

    def test_missing_saveinsta_refuses_to_change(self):
        with self.assertRaises(RuntimeError):
            installer.patch_service(LEGACY.replace(
                "download_saveinsta, clean_url", "other_extractor, clean_url"
            ))


if __name__ == "__main__":
    unittest.main()
