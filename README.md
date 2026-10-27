# offtube

Paste-a-link media keeper — local web UI + Chrome extension powered by
`yt-dlp`, `gallery-dl` and `ffmpeg`. Videos, audio, images, boards and
playlists from YouTube, TikTok, Instagram, Pinterest, X, Reddit, Facebook,
Vimeo, Twitch, SoundCloud, Bandcamp, Flickr, DeviantArt and 2,000+ more sites.

- Paste a **video, post, pin, board, track or playlist** link (private /
  members-only / sign-in-only ones your own account can see, via cookies)
- Chrome extension: a popup that opens on the tab's media ready to keep in
  one press, a side panel that follows your tabs with every option, and
  right-click any link or page → Keep with offtube
- **Format**: Best / 2160p … 360p / audio-only MP3/M4A; image posts and
  boards are kept as the original files
- **GIFs**: turn a whole video or an exact time range into an animated GIF,
  with size, frame rate and looping controls, a preview and a Save GIF action
- **Collections** as a contact sheet: circle the prints you want, or keep the
  whole playlist/board, or give a range (`1-5,7`, start/end)
- **Trim** a clip (`01:30` → `02:45`), subtitles, live progress, a session
  strip (up to 5 at once) and a visual library of everything kept

The design is a darkroom contact sheet: every link becomes a print in a
black film border edge-printed with its site and kind; a crimson grease
pencil circles what you choose, ticks what was kept and strikes what
failed; a print develops from negative to positive while it downloads.
Hover any print with a mouse and a loupe magnifies it; everything you've
kept lives on one continuous contact sheet. Light by default, dark
follows your OS.

Dependencies: `yt-dlp` + `gallery-dl`. No Flask/FastAPI — backend is Python stdlib.

> Only download content you own or are authorized to keep. Private and
> members-only posts require **your own** access. This tool cannot bypass
> paywalls or DRM — if a site says “Join this channel” or “login required”,
> your cookies are expired, from the wrong account, or that account can't
> see the post.

## Supported sites

A link works when one of two engines has a **dedicated extractor** for it —
the same one that engine would pick on its own:

| Engine | Covers | Examples |
|---|---|---|
| `yt-dlp` | video & audio, ~1,800 sites | YouTube, TikTok, Instagram reels, X videos, Reddit, Facebook, Vimeo, Twitch, Dailymotion, Bilibili, SoundCloud, Bandcamp, Mixcloud |
| `gallery-dl` | images, galleries, boards, ~300 sites | Pinterest pins & boards, Instagram/X/Reddit/Tumblr/Bluesky photo posts, Flickr, Imgur, DeviantArt, ArtStation, Behance, pixiv |

- **Engine chain.** yt-dlp goes first where it matches; when it finds no video
  (an image-only tweet, a Flickr photo) gallery-dl takes the post. Pinterest
  and Imgur go gallery-first, since yt-dlp only sees their videos.
- **No catch-alls.** yt-dlp's Generic page scraper and gallery-dl's
  directlink/generic extractors are never used, and IP / LAN hostnames are
  refused, so outbound fetches stay on sites those projects explicitly support.
- **DRM services** (Spotify, Netflix, Disney+, …) are refused up front.
- YouTube keeps its special handling: channel/home/search pages are rejected
  (thousands of videos), `watch?v=…&list=…` inspects as one video, and
  bot-check failures retry with alternate player clients.

Ask the server about any link without fetching it:
`GET /api/source?url=…` → `{supported, source: {id, label}, engines}`.

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

Then open **http://127.0.0.1:8000**. Optional: load `extension/` unpacked in
`chrome://extensions` (Developer mode) for a toolbar button and
right-click → Keep with offtube on any link. See `extension/README.md`.

## Docker & background services

Same server, three ways to keep it running without a foreground terminal.
The API stays at `http://127.0.0.1:8000`, so the web UI and extension work unchanged.

```bash
docker compose up -d --build   # service mode (restart: unless-stopped)
docker compose logs -f
docker compose down
```

- Image: `python:3.12-slim` + ffmpeg + pinned Deno + yt-dlp + gallery-dl, with `tini` as PID 1 to pass on `docker stop` and reap stopped jobs' processes. Healthcheck hits `/api/health`.
- Port is published on loopback only (`127.0.0.1:8000:8000`); inside the
  container `HOST=0.0.0.0` is required (a container has its own loopback).
- Native `./run.sh` defaults to `HOST=127.0.0.1`; `HOST=0.0.0.0` is docker-only.
- Native IPv6 loopback is supported with `HOST=::1 ./run.sh`; open
  `http://[::1]:8000` and use that address in the extension's server setting.
- Limitation: cookies **from browser** can't work in docker (no host browser
  profile inside the container) — use the cookies-file upload/paste instead.
  Native mode + the OS services below keep from-browser working.
- Stopping — Ctrl+C, `docker compose down`, `systemctl --user stop offtube`
  or `launchctl unload` — refuses new work, cancels queued jobs, stops
  running ones together with their processes, removes per-job cookie copies
  and exits within about 5 seconds. A second Ctrl+C exits at once.

```bash
# macOS (native, auto-start on login) — run from this clone
DIR="$(pwd)"
sed -e "s|REPLACE_ME|$USER|g" -e "s|REPLACE_DIR|$DIR|g" \
  deploy/com.offtube.plist > ~/Library/LaunchAgents/com.offtube.plist
launchctl load ~/Library/LaunchAgents/com.offtube.plist

# Linux (native, user systemd unit) — run from this clone
mkdir -p ~/.config/systemd/user
sed "s|REPLACE_DIR|$PWD|g" deploy/offtube.service \
  > ~/.config/systemd/user/offtube.service
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

1. **Paste a link** (or open the extension on a tab) → it's previewed as a print: site, kind, title, thumbnail, available formats — or a contact sheet for boards, playlists and multi-photo posts. Unsupported links are refused before anything is fetched.
2. **Format** — pick max height, Best, Audio only, or GIF. Audio-only sources (SoundCloud, Bandcamp, podcasts) switch to Audio on their own; image posts and boards keep the original files. Mixed collections can turn their video items into GIFs.
3. **Trim (optional, video or GIF)** — `From: 01:30`, `To: 02:45`. Accepts `90`, `1:30`, `01:30`, `1:02:03`, and fractional seconds such as `1.25`. Leave empty for the whole video. GIF size is the longest edge (320, 480, 640 or 960 pixels); motion is 10–30 fps. Defaults are 480 px, 15 fps and looping forever. Short clips make smaller files; the 5-second shortcut helps pick a moment. GIFs are silent, preserve the display aspect ratio and use an optimized color palette. GIF creation needs both `ffmpeg` and `ffprobe`, supplied by the ffmpeg installation. Live streams must finish first.
4. **Collection** — circle prints on the sheet to keep only those, or choose “Whole collection” with Start/End or Items like `1-5,7,10-12`. Works for playlists, boards, albums, profiles and multi-photo posts; pasting a collection link switches it on.
5. **Sign-in** — only needed for private / members-only / age-gated posts, most Instagram and X content, and YouTube bot-checks:
   - **Upload cookies.txt**: in Chrome, install [*Get cookies.txt Locally*](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc), open the site signed in as the account that can see the post, export Netscape format, upload here. Only that site's cookies are kept (YouTube also keeps Google's); the rest of a full-browser export is trimmed before it touches disk. Re-export when downloads start failing (cookies expire).
   - **From browser**: reads Chrome/Edge/Firefox/… cookies directly from this machine. Pick the browser under Sign-in (web app or side panel); the popup and the extension's right-click "Keep with offtube" honor that choice.
   - Tip from the field (YouTube): export in an incognito window with only `youtube.com/robots.txt` open if you hit weird auth errors; disable adblockers while exporting; make sure only the member account is signed in.
6. **Keep** — the print develops as it downloads; queue another while one runs. Finished files get a pencil tick and appear under Kept and in `downloads/`. A running job stops itself if free disk space drops under 200 MB.

Cookie files created by offtube are published atomically with owner-only
permissions on POSIX. To reuse a manually exported file, save it as
`cookies/session_cookies.txt` (protect it with `chmod 600` on POSIX). Every
inspection and download gets a separate, site-filtered copy, removed when that
request finishes; engine cookie updates never overwrite the saved export.

## Project layout

```
offtube/
  app.py            # backend: stdlib HTTP server + source resolver + yt-dlp/gallery-dl engines
  web/              # local UI (no build step)
  extension/        # MV3 Chrome extension (popup + side panel + context menu)
  tests/            # pytest: helper units + live-HTTP API tests (stubbed yt-dlp)
  downloads/        # finished files (contents git-ignored, dir kept)
  cookies/          # per-job isolates (never committed; cleaned on boot + job end)
  deploy/           # launchd plist + systemd user unit (path-templated)
  tools/            # isolated GIF source worker, validation gate, icon generator
  ARCHITECTURE.md   # system map, invariants and prioritized engineering risks
  Dockerfile, docker-compose.yml
  tools/gen_icons.py
  requirements.txt  # runtime: pinned yt-dlp + gallery-dl
  run.sh / run.bat  # venv launchers (install-on-missing, --upgrade to refresh)
```

## Testing

For the complete local gate, install the Python dependencies below and make
Node.js, ffmpeg and ffprobe available on PATH, then run `bash tools/check.sh`.
It runs Python tests, real media encoding, JavaScript syntax/tests and patch
whitespace checks. Override the interpreter with `PYTHON=/path/to/python`.

```bash
./.venv/bin/pip install -q -r requirements-dev.txt
./.venv/bin/python -m pytest tests -q   # no external network
node --test tests/extension_gif.test.js tests/test_web_gif.mjs
```

GitHub CI runs the full gate on Python 3.10 and 3.12, builds the Docker image,
checks the packaged GIF worker and smoke-tests HTTP startup. See
`ARCHITECTURE.md` for the system map and remaining production-hardening work.

`tests/test_api.py` boots the real `Handler` on an ephemeral port with a
stubbed `YoutubeDL` and a fake gallery-dl CLI, covering health/headers,
traversal blocks, info validation, `/api/source`, download → files →
delete, bad input, oversized bodies, queued + running cancel (including
killing a gallery-dl process), origin/CORS, payload stripping, channel
rejection, temp/dotfile hiding, disposition, jobs limit, disk guard, the
public-job field whitelist, Host-header guard, `/api/retry`, and
`/api/jobs/clear`. `tests/test_helpers.py` checks source resolution against
the real extractor tables (no network) — including every site named in the
web UI's “works with” strip — plus engine order and fallback, per-site
cookie trimming, failure hints, and filename byte caps. `tests/test_gif.py`
covers GIF validation, cancellation, isolated temporary sources, cleanup and
real generated-media encoding (when ffmpeg and ffprobe are on PATH), including
duration, frame count, dimensions, looping and anamorphic display ratios.

## API (for scripting)

Media requests validate field types before inspection or enqueueing and return
JSON `{ok: false, error}` with HTTP 400 for malformed fields. URLs, clip times,
quality IDs, cookie fields and subtitle languages must be strings; `subtitles`
and `auto_subs` must be JSON booleans, not strings. Playlist bounds accept
integers or text. `playlist_mode` is `single` or `playlist`; `cookies_mode` is
`none`, `browser`, `upload`, `text` or `file`. Optional null values use defaults.

- `GET /api/health` → `{checks: {yt_dlp, gallery_dl, ffmpeg, deno, node, node_supported, js_runtime, js_runtime_ok}}` (cached 60s)
- `GET /api/source?url=…` → `{supported, source: {id, label}, engines}` or `{supported: false, error}` (local extractor match, no network)
- `POST /api/info` `{url, cookies_mode, cookies_text?, cookies_browser?}` → metadata with `source`, `engine`, `type` (`video` | `playlist` | `gallery`) and `media` (`video` | `audio` | `image` | `mixed`); galleries carry `count`, `more` and `entries` with thumbnails (side-effect-free; pasted cookies use an ephemeral file)
- `POST /api/download` `{url, quality, clip_from, clip_to, gif_size, gif_fps, gif_loop, playlist_mode, playlist_start, playlist_end, playlist_items, cookies_*, subtitles, sub_lang}` → `{job_id}`
- `GET /api/job?id=…` → `{status, source, progress, detail, log, files}` (`status`: queued/starting/downloading/processing/done/error/cancelled)
- `POST /api/cancel` `{job_id}` → `{cancelled: true}` (queued cancels instantly; running yt-dlp jobs stop cooperatively at the next progress callback; gallery-dl, the GIF worker and ffmpeg are stopped together with every process they started)
- `POST /api/retry` `{job_id}` → `{job_id, retried}` (re-runs a finished job from its stored params; 404 unknown, 400 still running, 400 if the stored params no longer validate)
- `POST /api/jobs/clear` `{}` → `{cleared}` (drops finished job records)
- `GET /api/jobs?limit=50` → recent job records, logs trimmed to 20 lines (terminal jobs evicted past 50)
- `GET /api/files` → `{files, disk_free, disk_free_str}`, newest first (up to 500); `GET /files/<name>` downloads one (RFC 5987 filename). A single `Range: bytes=…` is answered with `206` (or `416` past the end), so media elements fetch only the index and frames they need; `ETag`/`Last-Modified` let `If-Range` resume only an unchanged file
- `POST /api/files/delete` `{name}` → `{deleted}` (the exact file name; 404 if it isn't a kept file)

The library is the regular files directly in `downloads/`. Dotfiles,
in-progress `.part`/`.temp` files, symlinks and special files are never
listed, served or deleted, so every listed name is exactly what
`/files/<name>` returns and deleting a name can never reach another file.

Only links with a dedicated yt-dlp or gallery-dl extractor are accepted (400
otherwise, also for IP/LAN hosts and DRM services). YouTube channel, home,
and search pages are rejected. Inspect treats `watch?v=&list=` as a single
video; only `/playlist?list=` is a playlist. Bodies are capped at ~2 MB (413).
A request must arrive whole within 30 seconds, and the server serves 64
connections at once; past that it answers 503 with `Retry-After: 1` at once
instead of queueing. Past 5 active downloads `/api/download` returns 429. Under 200 MB free disk
`/api/download` returns 507. Cross-site POSTs with a foreign `Origin`/`Referer`
return 403, as do non-loopback `Host` headers. JSON responses reflect
`Access-Control-Allow-Origin` only for localhost and `chrome-extension://` —
never `*`. Unknown `/api/*` routes return JSON 404. Job records expose a fixed
whitelist of fields — internal paths and the stored request payload never
leave the server. Jobs and UI prefs are in-memory/local only — a restart
clears history; leftover `.part` files and orphaned job cookies are cleaned
on boot and when a job ends.

Quality ids: `best, 2160, 1440, 1080, 720, 480, 360, audio_mp3, audio_m4a, gif`
(gallery-dl keeps original files unless `quality` is `gif`, which selects video items).
For GIFs, `gif_size` is an integer in `320, 480, 640, 960` (default `480`),
`gif_fps` is an integer in `10, 15, 20, 25, 30` (default `15`), and
`gif_loop` is a boolean (default `true`). `clip_from` / `clip_to` are optional
time strings. Subtitles are ignored for GIFs. Source videos and encoder scratch
files stay in a private job folder and are removed after success, failure or
cancellation; abandoned GIF job folders are cleaned on server startup.

## Troubleshooting

- **“… only shows this to a signed-in account”** (Instagram, X, Reddit,
  Vimeo, private TikToks, age-gated videos) → the site wants a login. Open
  Sign-in: Browser (native server) or a cookies file exported while signed in
  to that site.
- **“offtube doesn't support that … link”** → neither engine has an extractor
  for that page. Paste the specific post / video / board, not a home or search
  page. Short links (`pin.it`, `youtu.be`, `redd.it`) are fine.
- **Both engines failed** → the error lists each (`yt-dlp: … · gallery-dl: …`);
  usually it's the same login problem, or a deleted/private post.

- **“Sign in to confirm you’re not a bot” (even on public videos)** → YouTube
  is challenging this network, not a tool bug. Add cookies and retry:
  export `cookies.txt` (step 5 above) and use the Cookies-file mode, or run
  natively (`./run.sh`) with Sign-in → Browser. In Docker you must use the
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

## License

MIT. See `LICENSE`.

## Credits

Workflow inspired by the community gist on downloading private/member videos with `yt-dlp --cookies` (Netscape format), `--playlist-start/end/items`, `-F`/`-f` quality selection, and `--write-auto-sub`.
