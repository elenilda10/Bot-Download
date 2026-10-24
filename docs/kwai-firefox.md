# Kwai: Firefox session and exact post identity

Some requests for a valid Kwai video URL return HTTP 200 with unrelated
recommended posts. HTTP status, the address bar, visible caption and the first
Nuxt array entry are not proof that a video belongs to the requested post.

The extractor matches the exact string `photo_id_str` and reads `main_mv_urls`
from that same record. It does not compare the rounded JavaScript `photo_id`
number. Multiple different file paths, foreign domains, missing identities and
recommendations fail closed. Empty captions do not prevent a verified download.
Nuxt caption aliases are decoded as string literals without executing page JS.

## Explicit Firefox fallback

Anonymous extraction remains the default. The optional profile fallback reads
both persistent cookies (`cookies.sqlite`) and session cookies (the newest of
`sessionstore.jsonlz4` and the current recovery state). It does not read old
`previous.jsonlz4` sessions or merge historical authentication tokens. Cookies
are filtered by domain, path, expiry and normal-profile context. Other sites,
private windows and containers are excluded.

The authenticated request uses the system `curl` and the document headers
validated on the VPS. Credentials are supplied on stdin, not command arguments,
logs or export files. It only requests a canonical HTTPS document on
`www.kwai.com`, does not follow redirects and never passes cookies to the media
CDN. Media downloads retain their byte limit, ffprobe validation and cleanup.

Runtime requirements: Python's sqlite3, system curl, ffmpeg/ffprobe, and system
liblz4 for Firefox session decoding. No new Python package is required. A running
Firefox may hold its SQLite DB locked; the loader can use a temporary snapshot
of the DB and WAL. A session being rewritten may temporarily fail validation;
retry after Firefox has saved the session, rather than importing an old session.

The Telegram handler only enables profile use for `ADMIN_ID` in that user's
normal private chat when `KWAI_FIREFOX_PROFILE` is explicitly configured.
Groups, business messages and other users retain anonymous extraction. Auth
cache entries (video, document, MP3, caption) use an owner namespace, and forwarded
callbacks do not acquire the administrator's profile or cache. Cache v7 also
invalidates files created before exact string-ID checks.

Example configuration in the bot's existing environment file:

```dotenv
KWAI_FIREFOX_PROFILE=/home/firefoxbot/.config/mozilla/firefox/p2u2gipq.default-release
```

## Test before installing

Use a separate temporary checkout/worktree of this branch, with the VPS's
existing virtual environment. The smoke script has no Telegram or database
side effects, and does not need bot secrets:

```bash
/root/Bot-Download/venv/bin/python scripts/test_kwai_download.py \
  'https://www.kwai.com/@gabyvalence/video/5200118832330956026' \
  --firefox-profile /home/firefoxbot/.config/mozilla/firefox/p2u2gipq.default-release
```

The manual VPS investigation verified this post as an 8.9-second H.264/AAC
video, 576x1024, 940133 bytes. Those numbers describe this one sample, not a
selection rule. The smoke script reports duration rounded by the existing
ffprobe helper (9 seconds). Test another public post and a mismatched/missing
post as well. Manual investigation succeeded with combined profile cookies;
the newly integrated code still requires a VPS/Telegram smoke test.

Run isolated regression tests without the bot's global conftest:

```bash
python -m pytest --noconftest -q tests/test_kwai_media.py \
  tests/test_kwai_firefox.py tests/test_kwai_service.py \
  tests/test_kwai_handler_routing.py tests/test_kwai_installer.py
```

## Selective installation

From the separate source checkout, preview changes to the modified VPS checkout:

```bash
/root/Bot-Download/venv/bin/python scripts/install_kwai_vps.py --target /root/Bot-Download
```

After successful smoke tests and review of the preview, install with `--apply`.
The installer backs up overwritten files and validates syntax before writing.
It copies only Kwai modules and surgically patches existing route registrations;
it never performs a whole-checkout pull/reset, changes the environment file,
or restarts a service. Keep the backup path printed by the installer. Configure
the explicit profile environment variable separately before a controlled bot
restart and verify the private Telegram flow, MP3 and document buttons.
