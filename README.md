# offtube

Paste-a-link YouTube keeper — local web UI powered by `yt-dlp` + `ffmpeg`.

- Paste **any** YouTube URL (video, playlist, unlisted)
- **Members-only / private** videos you can already watch → via cookies (upload `cookies.txt` or read from browser)
- **Quality** picker: Best / 2160p / 1440p / 1080p / 720p / 480p / 360p / audio-only MP3/M4A
- **From–to**: clip trim (`01:30` → `02:45`) *and* playlist ranges (`1-5,7`, start/end)
- Subtitles toggle, live progress, file browser

Only dependency: `yt-dlp`. No Flask/FastAPI — backend is Python stdlib.

> Only download content you own or are authorized to keep. Members-only videos
> require **your own** active membership. This tool cannot bypass paywalls —
> if yt-dlp says “Join this channel to get access”, your cookies are expired,
> from the wrong account, or your tier doesn’t cover that video.

## Quick start

**macOS / Linux**
```bash
cd offtube
./run.sh                    # creates .venv/, installs deps if missing, serves
./run.sh --upgrade          # force refresh yt-dlp
# manual equivalent:
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python app.py
```

**Windows**
```bat
run.bat                     @rem same, via .venv\Scripts\python
```

Then open **http://127.0.0.1:8000**.

## Docker & background services

Same server, three ways to keep it running without a foreground terminal.
The API stays at `http://127.0.0.1:8000`, so the web UI and extension work unchanged.

```bash
docker compose up -d --build   # service mode (restart: unless-stopped)
docker compose logs -f
docker compose down
```

- Image: `python:3.12-slim` + ffmpeg + node + yt-dlp. Healthcheck hits `/api/health`.
- Port is published on loopback only (`127.0.0.1:8000:8000`); inside the
  container `HOST=0.0.0.0` is required (a container has its own loopback).
- Native `./run.sh` defaults to `HOST=127.0.0.1`; `HOST=0.0.0.0` is docker-only.
- Limitation: cookies **from browser** can't work in docker (no host browser
  profile inside the container) — use the cookies-file upload/paste instead.
  Native mode + the OS services below keep from-browser working.

```bash
# macOS (native, auto-start on login)
sed "s|REPLACE_ME|$USER|g" deploy/com.offtube.plist > ~/Library/LaunchAgents/com.offtube.plist
launchctl load ~/Library/LaunchAgents/com.offtube.plist

# Linux (native, user systemd unit)
cp deploy/offtube.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now offtube
journalctl --user -u offtube -f
```

Prerequisites: Python 3.10+, `ffmpeg` on PATH, and a JS runtime for YouTube's
proof-of-origin challenges — Deno (any recent 2.x) or Node.js ≥ 23.5
(older Node, e.g. Debian's 18.x, is rejected by yt-dlp as unsupported).
The Docker image already includes Deno.

| Tool | macOS | Windows |
|---|---|---|
| ffmpeg | `brew install ffmpeg` | `winget install Gyan.FFmpeg` (reopen terminal after) |
| JS runtime | `brew install deno` | `winget install DenoLand.Deno` |
| Python deps | `pip install -r requirements.txt` | `python -m pip install -r requirements.txt` |

## How to use

1. **Paste link** → **Fetch info** (shows title, thumbnail, available qualities).
2. **Quality** — pick max height, Best, or Audio only.
3. **Clip trim (optional)** — `From: 01:30`, `To: 02:45`. Accepts `90`, `1:30`, `01:30`, `1:02:03`. Uses yt-dlp `--download-sections` (needs ffmpeg). Leave empty for full video.
4. **Playlist** — “Just this video” or “Whole playlist / range” with Start/End or Items like `1-5,7,10-12`.
5. **Cookies** — only needed for members-only/private/age-restricted:
   - **Upload cookies.txt**: in Chrome, install [*Get cookies.txt Locally*](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc), open YouTube logged in as the member account, export Netscape format, upload here. Re-export when downloads start failing (cookies expire).
   - **From browser**: reads Chrome/Edge/Firefox/… cookies directly from this machine.
   - Tip from the field: export in an incognito window with only `youtube.com/robots.txt` open if you hit weird auth errors; disable adblockers while exporting; make sure only the member account is signed in.
6. **⬇ Download** — watch live progress; finished files appear below with direct download links. Files also land in `downloads/`.

## Project layout

```
offtube/
  app.py            # backend: stdlib HTTP server + yt-dlp engine
  web/
    index.html      # UI
    styles.css
    app.js
  tests/            # pytest: helper units + live-HTTP API tests (stubbed yt-dlp)
  downloads/        # finished files (contents git-ignored, dir kept)
  cookies/          # per-job isolates (never committed; cleaned on boot + job end)
  requirements.txt  # runtime: pinned yt-dlp
  requirements-dev.txt  # test: pytest
  run.sh / run.bat  # venv launchers (install-on-missing, --upgrade to refresh)
```

## Testing

```bash
./.venv/bin/pip install -q -r requirements-dev.txt
./.venv/bin/python -m pytest tests -q   # 65 tests, ~9s, no network
```

`tests/test_api.py` boots the real `Handler` on an ephemeral port with a
stubbed `YoutubeDL`, covering health/headers, traversal blocks, info
validation, download → files → delete, bad input, oversized bodies,
queued + running cancel, origin blocks, temp-file hiding, disposition,
jobs limit, and disk guard.

## API (for scripting)

- `GET /api/health` → `{checks: {yt_dlp, ffmpeg, node, deno}}` (cached 60s)
- `POST /api/info` `{url, cookies_mode, cookies_text?, cookies_browser?}` → video/playlist metadata (side-effect-free; pasted cookies use an ephemeral file)
- `POST /api/download` `{url, quality, clip_from, clip_to, playlist_mode, playlist_start, playlist_end, playlist_items, cookies_*, subtitles, sub_lang}` → `{job_id}`
- `GET /api/job?id=…` → `{status, progress, detail, log, files}` (`status`: queued/starting/downloading/processing/done/error/cancelled)
- `POST /api/cancel` `{job_id}` → `{cancelled: true}` (queued cancels instantly; running jobs stop cooperatively at the next progress callback)
- `GET /api/jobs?limit=50` → recent job records, logs trimmed to 20 lines (terminal jobs evicted past 50)
- `GET /api/files` → `{files, disk_free, disk_free_str}` (in-progress `.part`/`.temp` hidden); `GET /files/<name>` downloads one (RFC 5987 filename)
- `POST /api/files/delete` `{name}` → `{deleted}`

Only `youtube.com` / `youtu.be` URLs are accepted (400 otherwise). Bodies are
capped at ~2 MB (413). Past 5 active downloads `/api/download` returns 429.
Under 200 MB free disk `/api/download` returns 507. Cross-site POSTs with a
foreign `Origin`/`Referer` return 403 (localhost CSRF guard). Unknown `/api/*`
routes return JSON 404. Jobs and UI prefs are in-memory/local only — a restart
clears history; in-progress temp files and orphaned job cookies are cleaned on boot.

Quality ids: `best, 2160, 1440, 1080, 720, 480, 360, audio_mp3, audio_m4a`.

## Troubleshooting

- **“Sign in to confirm you’re not a bot” (even on public videos)** → YouTube
  is challenging this network, not a tool bug. Add cookies and retry:
  export `cookies.txt` (step 5 above) and use the Cookies-file mode, or run
  natively (`./run.sh`) with Access → Browser. In Docker you must use the
  file — the container can't see your host browser. A YouTube-only export is
  enough: other sites' cookies are trimmed automatically, and full-browser
  pastes no longer hit the size cap. Natively, also make sure a supported JS
  runtime is installed (Deno or Node ≥ 23.5 — older Nodes can't answer
  YouTube's challenges); the Docker image ships Deno.
- **“The page needs to be reloaded”** → the cookie session was rejected
  (stale, mixed accounts, or exported from the wrong domain). offtube
  automatically retries these with alternate player clients
  (`web_creator`/`tv`/`mweb`) before giving up — if it still fails, re-export
  fresh while on `youtube.com`, logged into a single account, then retry.
- **“Join this channel to get access”** → not a tool bug: wrong/expired cookies, wrong account, or tier too low. Re-export cookies, verify you can watch in the browser first.
- **Only image formats / no video streams** → same cause, or missing `ejs` component: install Node.js and refresh deps (`./run.sh --upgrade`).
- **ffmpeg not recognized** → reopen the terminal after install (PATH refresh), then `ffmpeg -version`.
- **`npm` execution-policy error (Windows)** → use `npm.cmd --version`.
- **Cookies stopped working after weeks** → normal, re-export.
- **Large playlists** → 1080p videos are hundreds of MB each; ensure tens of GB free.

## Credits

Workflow inspired by the community gist on downloading private/member videos with `yt-dlp --cookies` (Netscape format), `--playlist-start/end/items`, `-F`/`-f` quality selection, and `--write-auto-sub`.
