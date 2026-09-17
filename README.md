# yt-downloader

Paste-a-link YouTube downloader — local web UI powered by `yt-dlp` + `ffmpeg`.

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
cd yt-downloader
./run.sh
# or: pip install -r requirements.txt && python app.py
```

**Windows**
```bat
run.bat
:: or: python -m pip install -r requirements.txt & python app.py
```

Then open **http://127.0.0.1:8000**.

Prerequisites: Python 3.10+, `ffmpeg` on PATH, Node.js (optional, helps with YouTube bot-checks).

| Tool | macOS | Windows |
|---|---|---|
| ffmpeg | `brew install ffmpeg` | `winget install Gyan.FFmpeg` (reopen terminal after) |
| Node | `brew install node` | installer from nodejs.org |
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
yt-downloader/
  app.py            # backend: stdlib HTTP server + yt-dlp engine
  web/
    index.html      # UI
    styles.css
    app.js
  downloads/        # finished files (git-ignored except .gitkeep)
  cookies/          # session cookies.txt (never committed)
  requirements.txt  # yt-dlp
  run.sh / run.bat  # launchers
```

## API (for scripting)

- `GET /api/health` → `{checks: {yt_dlp, ffmpeg, node, deno}}`
- `POST /api/info` `{url, cookies_mode, cookies_text?, cookies_browser?}` → video/playlist metadata
- `POST /api/download` `{url, quality, clip_from, clip_to, playlist_mode, playlist_start, playlist_end, playlist_items, cookies_*, subtitles, sub_lang}` → `{job_id}`
- `GET /api/job?id=…` → `{status, progress, detail, log, files}`
- `GET /api/files` → downloaded files; `GET /files/<name>` downloads one

Quality ids: `best, 2160, 1440, 1080, 720, 480, 360, audio_mp3, audio_m4a`.

## Troubleshooting

- **“Join this channel to get access”** → not a tool bug: wrong/expired cookies, wrong account, or tier too low. Re-export cookies, verify you can watch in the browser first.
- **Only image formats / no video streams** → same cause, or missing `ejs` component: install Node.js and update yt-dlp (`pip install -U yt-dlp`).
- **ffmpeg not recognized** → reopen the terminal after install (PATH refresh), then `ffmpeg -version`.
- **`npm` execution-policy error (Windows)** → use `npm.cmd --version`.
- **Cookies stopped working after weeks** → normal, re-export.
- **Large playlists** → 1080p videos are hundreds of MB each; ensure tens of GB free.

## Credits

Workflow inspired by the community gist on downloading private/member videos with `yt-dlp --cookies` (Netscape format), `--playlist-start/end/items`, `-F`/`-f` quality selection, and `--write-auto-sub`.
