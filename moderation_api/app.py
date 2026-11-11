"""Private NudeNet inference API. Does not save images."""
import asyncio
import os
import secrets
from aiohttp import web
from nudenet import NudeDetector

secret = os.getenv("NSFW_API_KEY", "")
if not secret:
    raise RuntimeError("NSFW_API_KEY must be set")
detector = NudeDetector()
sem = asyncio.Semaphore(2)


async def health(request):
    return web.json_response({"ok": True})


async def classify(request):
    if not secrets.compare_digest(request.headers.get("Authorization", ""),
                                  "Bearer " + secret):
        raise web.HTTPUnauthorized()
    if request.content_length is not None and request.content_length > 8 * 1024 * 1024:
        raise web.HTTPRequestEntityTooLarge(max_size=8 * 1024 * 1024,
                                            actual_size=request.content_length)
    raw = await request.read()
    if not raw:
        raise web.HTTPBadRequest(text="Empty image")
    try:
        async with sem:
            results = await asyncio.to_thread(detector.detect, raw)
    except Exception:
        raise web.HTTPBadRequest(text="Invalid image")
    return web.json_response({"detections": results})


app = web.Application(client_max_size=8 * 1024 * 1024)
app.router.add_get("/health", health)
app.router.add_post("/classify", classify)
if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
