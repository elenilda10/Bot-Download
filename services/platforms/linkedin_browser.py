"""Render a public LinkedIn post with Chromium; also runnable with python -m."""

import argparse
import asyncio
import logging
from pathlib import Path
from urllib.parse import urlsplit

from services.links.linkedin import resolve_linkedin_url
from services.platforms.linkedin_photo import PostPhoto, _identity, _photo_url

log = logging.getLogger(__name__)
_browser_slots = asyncio.Semaphore(1)

# Read only the requested article. For the dedicated embed, main is the post.
_READ_POST = """({postId, embed}) => {
    const uris = [...document.querySelectorAll('link[rel="canonical"],meta[property="og:url"]')]
        .map(node => node.href || node.content).filter(Boolean);
    // Dedicated embeds are already bound to the post by their final URL.
    // Generic metadata can omit an ID; reject metadata identifying another post.
    const conflictingEmbed = uri => {
        const match = uri.match(/(?:urn:li:(?:activity|share|ugcPost):|-(?:activity|share|ugcPost)-)([0-9]+)/i);
        return match && match[1] !== postId;
    };
    if (embed && uris.some(conflictingEmbed)) return false;
    if (!embed && uris.some(uri => !new RegExp('(?:-|:)' + postId + '(?:-|/|[?#]|$)').test(uri))) return false;
    const roots = [...document.querySelectorAll('[data-urn], [data-id], [data-entity-urn]')];
    const root = embed ? document.body : roots.find(el => ['data-urn','data-id','data-entity-urn']
        .some(attr => ['activity','share','ugcPost'].some(kind =>
            el.getAttribute(attr) === 'urn:li:' + kind + ':' + postId)));
    const meta = name => document.querySelector('meta[property="' + name + '"]')?.content || '';
    if (meta('og:video') || meta('og:video:url') || meta('og:type').startsWith('video')
        || root?.querySelector('video')) return {video: true};
    const candidates = [];
    const images = root ? root.querySelectorAll('img') : [];
    for (const img of images) {
        if (img.closest('aside, nav, header, footer, [class*="comment-item"], [class*="comments-"], [class*="related"]')) continue;
        const options = [img.currentSrc, img.dataset.delayedUrl, img.dataset.src, img.src];
        for (const value of options) {
            if (value && value.includes('/feedshare-')) candidates.push(value);
        }
    }
    candidates.push(meta('og:image:secure_url'), meta('og:image'),
        document.querySelector('meta[name="twitter:image"]')?.content);
    const urls = candidates.filter(s => {
        try { const u = new URL(s); return u.protocol === 'https:' &&
            ['media.licdn.com','media-exp1.licdn.com'].includes(u.host) &&
            u.pathname.includes('/dms/image/') && u.pathname.includes('/feedshare-'); }
        catch { return false; }
    });
    return urls.length ? {url: urls[0], description:
        root?.querySelector('[data-test-id="main-feed-activity-card__commentary"],.share-update-card__update-text')
            ?.textContent.trim() || meta('og:description')} : false;
}"""


async def _render(page, url, post_id, embed):
    response = await page.goto(url, wait_until="domcontentloaded", timeout=10000)
    if not response or not response.ok or _identity(page.url) != post_id:
        return None
    handle = await page.wait_for_function(
        _READ_POST, arg={"postId": post_id, "embed": embed}, timeout=5000,
    )
    try:
        if _identity(page.url) != post_id:
            return None
        return await handle.json_value()
    finally:
        await handle.dispose()


async def browser_photo(post_url):
    post_id = _identity(post_url)
    if not post_id:
        return None
    try:
        from playwright.async_api import Error, async_playwright
    except ImportError:
        log.warning("LinkedIn photo renderer unavailable: install Python Playwright and Chromium")
        return None
    kind = "ugcPost" if "-ugcPost-" in post_url else "share" if "-share-" in post_url else "activity"
    embed_url = f"https://www.linkedin.com/embed/feed/update/urn:li:{kind}:{post_id}"

    async def restrict(route):
        host = urlsplit(route.request.url).hostname or ""
        if (route.request.resource_type in {"media", "font"}
                or not any(host == d or host.endswith("." + d) for d in ("linkedin.com", "licdn.com"))):
            await route.abort()
        else:
            await route.continue_()

    try:
        async with asyncio.timeout(40), _browser_slots, async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(service_workers="block")
                await context.route("**/*", restrict)
                page = await context.new_page()
                for url in (embed_url, post_url):
                    try:
                        data = await _render(page, url, post_id, url == embed_url)
                    except Error:
                        continue
                    if data and data.get("video"):
                        return None
                    if data and (image := _photo_url(data.get("url", ""))):
                        return PostPhoto(image, data.get("description") or "")
            finally:
                await browser.close()
    except (Error, TimeoutError, OSError) as exc:
        log.warning("LinkedIn Chromium render failed: %s", (str(exc) or type(exc).__name__).partition("\n")[0])
    return None


async def _download(url, output):
    import aiohttp

    post = await resolve_linkedin_url(url)
    photo = await browser_photo(post)
    if not photo:
        raise SystemExit("Nenhuma foto pública identificada pelo Chromium.")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as client:
        async with client.get(photo.url, allow_redirects=False) as response:
            if response.status != 200 or not response.content_type.startswith("image/"):
                raise SystemExit(f"CDN não retornou uma imagem (HTTP {response.status}).")
            # Read completely before creating the destination or reporting success.
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > 32 * 1024 * 1024:
                    raise SystemExit("Imagem maior que 32 MiB.")
    if not data:
        raise SystemExit("O CDN retornou uma imagem vazia.")
    with output.open("xb") as f:
        f.write(data)
    print(f"Foto salva em {output.resolve()} ({len(data)} bytes)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(_download(args.url, args.output))
