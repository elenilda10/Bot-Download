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

With `KWAI_FIREFOX_PROFILE` explicitly configured, the Telegram handler enables
the profile fallback for users in normal private chats, groups and supergroups.
Business messages retain anonymous extraction. Authenticated cache entries
(video, document, MP3, caption) are separated by requesting user and chat;
forwarded buttons cannot read another user's/chat's cache. Cookies remain on
the server and are never included in Telegram messages or callbacks. Cache v7
also invalidates files created before exact string-ID checks.

Fresh and cached videos/documents reply to the user's original link message.
MP3/document callbacks reply to the bot video message carrying the buttons.
Every media reply keeps `allow_sending_without_reply=True`, allowing delivery
if the referenced message was deleted. This also works with the existing
deleted-reply fallback middleware on the VPS.

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
ffprobe helper (9 seconds). The integrated downloader subsequently passed the
VPS smoke test with exactly these dimensions/bytes and generated its thumbnail.
Telegram delivered the Gaby clip and additional short/web URLs with captions.
The reply/general-release update still requires its final Telegram check.

Run isolated regression tests without the bot's global conftest:

```bash
python -m pytest --noconftest -q tests/test_kwai_media.py \
  tests/test_kwai_firefox.py tests/test_kwai_service.py \
  tests/test_kwai_handler_routing.py tests/test_kwai_installer.py tests/test_kwai_reply.py
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
restart and verify private/group Telegram flows, cache replies, and MP3/document
buttons. The first integrated version was successfully smoke-tested on the VPS
and delivered multiple short and web URLs in Telegram; the reply/general-release
update still needs its final Telegram check.

### Vídeos compartilhados pelo Kwai Shop

Links `/w/` que redirecionam para `m-shop.kwai.com/krn-web/detail`
com `share_item_type=goods_video_distribute` são convertidos para a página
normal do vídeo usando `share_item_info` e `authorKwaiId`. O ID compartilhado
precisa constar nos parâmetros externos `photoId`; IDs de produto e URLs
aninhadas não selecionam o vídeo. A extração continua exigindo identidade
exata no registro dono do MP4. Cookies do perfil só chegam à página canônica,
nunca à loja. Links de produto sem identidade de vídeo não são convertidos.

Exemplo de teste na VPS:

```bash
/root/Bot-Download/venv/bin/python scripts/test_kwai_download.py \
  'https://k.kwai.com/w/0C00dDjZ' \
  --firefox-profile /home/firefoxbot/.config/mozilla/firefox/p2u2gipq.default-release
```
