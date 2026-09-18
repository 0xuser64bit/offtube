# offtube extension (MV3, unpacked)

Thin client over your local offtube server. It auto-reads the YouTube URL
from the active tab (override manually if needed) and reuses the same
`POST /api/info` + `POST /api/download` + `GET /api/job` API as the web UI.

## Why this shape

- yt-dlp + ffmpeg can't run inside an extension, so the extension never
  downloads directly — it drives `app.py` on `http://127.0.0.1:8000`.
- `tabs` permission is required: without it `tab.url` is silently `undefined`.
- `host_permissions` is scoped to `127.0.0.1` + `localhost` only (bypasses
  CORS for the local API, no `<all_urls>`).
- Popup = quick save. Side panel = full controls + progress (popups close
  when you click away, side panels stay open).
- No icons bundled (omitted per MV3 guidance — Chrome uses a default).
- No inline scripts, `async`/`await` only, no state in a service worker
  (polling lives in popup/sidepanel, which is why there is no background SW).

## Load it

1. Start the server: `./run.sh` (must be running — the badge shows
   "server unreachable" otherwise).
2. `chrome://extensions` → Developer mode → Load unpacked → `extension/`.
3. Open a YouTube video → click the offtube icon.
   - Popup shows the tab title/URL prefilled → Inspect → Download.
   - "Open side panel →" gives quality/trim/playlist/access + queue/library.

## Backend requirement

`app.py` allows `Origin: chrome-extension://…` and answers `OPTIONS`
preflights with CORS headers. `https://evil.com` origins are still 403.
See `Handler.check_origin` + `do_OPTIONS`.
