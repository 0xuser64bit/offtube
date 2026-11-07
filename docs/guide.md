# The offtube guide

[Home](../README.md) · [Chrome extension](../extension/README.md) ·
[API](api.md) · [Contributing](../CONTRIBUTING.md)

Start a local server, point it at media you have permission to save, and
keep the resulting files. The browser is the control surface; the server
does the downloading and writes to `downloads/` beside `app.py`.

**In this guide:** [Native installation](#native-installation) ·
[Keeping media](#keeping-media) · [Sign-in](#sign-in-and-cookies) ·
[Docker](#docker) · [Background services](#background-services) ·
[Troubleshooting](#troubleshooting)

## Native installation

### Prerequisites

| Tool | Why it is needed |
| --- | --- |
| Python 3.10+ | Runs the stdlib HTTP server and the download engines. |
| FFmpeg and ffprobe | Merge audio/video, extract audio, trim clips, and create GIFs. Install both through your FFmpeg distribution. |
| Deno 2.x, or Node.js 23.5+ | Handles supported YouTube JavaScript challenges. offtube prefers Deno when both are available. |
| Git | Clones the repository; downloading and extracting the source ZIP also works. |

The Python dependencies are pinned in [`requirements.txt`](../requirements.txt).
There is no frontend build, database setup, or npm dependency for normal use.

**macOS with Homebrew**

```sh
brew install python ffmpeg deno
```

**Debian / Ubuntu**

```sh
sudo apt update
sudo apt install python3 python3-venv ffmpeg
```

Install Deno separately using its [official installation
instructions](https://docs.deno.com/runtime/getting_started/installation/).
If your distribution provides an older Node.js, do not assume it can handle
YouTube's challenges; offtube rejects Node versions below 23.5.

**Windows**

Install Python 3.10+ from [python.org](https://www.python.org/downloads/windows/)
and make its `python` command available on `PATH`. For the other tools:

```powershell
winget install Gyan.FFmpeg
winget install DenoLand.Deno
```

Reopen your terminal after installation so it sees the updated `PATH`.
Other FFmpeg builds work too; see [FFmpeg's download
page](https://ffmpeg.org/download.html).

### Start the server

```sh
git clone https://github.com/0xuser64bit/offtube.git
cd offtube
./run.sh
```

On Windows, use `run.bat`. Both launchers create `.venv/` and install
missing Python dependencies. You do not need to activate the environment.
Open **[http://127.0.0.1:8000](http://127.0.0.1:8000)**.

For a manual macOS/Linux setup:

```sh
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python app.py
```

On Windows the equivalent interpreter is `.venv\Scripts\python.exe`.
If a site stops working, run `./run.sh --upgrade` or `run.bat --upgrade`.
That refreshes the pinned dependencies and then installs the latest
`yt-dlp` and `gallery-dl` releases, because site changes usually need a
newer engine rather than the version pinned in this checkout. Review the
engine changelogs if you need to stay on the pins instead.

### Check your environment

```sh
python3 --version
ffmpeg -version
ffprobe -version
deno --version
curl http://127.0.0.1:8000/api/health
```

Use `python --version` on Windows, and `node --version` if you chose Node
instead of Deno. The health response reports engine versions, FFmpeg, and
the detected JS runtime. Its HTTP 200 / `ok: true` means the server is
reachable, **not** that every dependency is installed. Checks are cached
for up to 60 seconds; restart the server after changing its `PATH`.

### Another port or IPv6

```sh
PORT=8001 ./run.sh
```

Open the matching address and change the extension's **Server** setting
too. Native IPv6 loopback is supported with `HOST=::1 ./run.sh`; use
`http://[::1]:8000`. The extension accepts only loopback HTTP addresses.
Keep the native server on loopback; `HOST=0.0.0.0` is for the container's
internal interface, not for sharing offtube over a network.

## Keeping media

1. **Paste and Preview.** Use a specific post, video, pin, track, board, or
   playlist URL. The preview shows what was found and only the controls
   relevant to that media.
2. **Choose the output.** Pick a video quality, Audio only, or GIF. For video,
   choose normal speed or 1.25×, 1.5×, or 2× export. Image posts keep their
   original files; audio-only sources select audio output.
3. **Open an option if needed.** Trim, Collection, Subtitles, and Sign-in
   stay folded until you need them.
4. **Keep.** Follow progress in the session strip. Queue another link, cancel
   a job, inspect its log, or retry a finished job.
5. **Find it under Kept.** Files are already on disk in `downloads/`. Save
   retrieves a copy through the browser; Delete removes the file from that
   folder, not just from the view.

### Video, audio, and subtitles

- **Best available** uses the best streams the engine can obtain. Height
  choices run from 2160p to 360p; they request an available stream at or
  below that height, with an engine fallback if none matches. They do not
  upscale media or guarantee a file size.
- **Audio only** offers MP3 or M4A. Extraction and conversion need FFmpeg.
- **Speed up video** offers 1.25×, 1.5×, and 2×, with 1× as the normal
  default. offtube makes a new H.264/AAC MP4 with matched video and audio
  timing, so an accelerated export takes an FFmpeg conversion even when the
  original file already fits the X-ready shape. Once it finishes, the desk or
  extension shows the real local export in a video preview alongside **Save**;
  it does not generate a separate remote preview before keeping the file.
- **X-ready video** means every kept video is checked with ffprobe. When it
  is not already an MP4 with H.264 video, yuv420p pixels and AAC audio
  (mono or stereo, at most 60 fps), offtube converts it once with FFmpeg so
  X/Twitter and most editors accept it. A file that already fits is left
  byte-for-byte unchanged. This does not crop, shorten, or resize a video,
  so X's file-size, duration, and account limits still apply.
- **Trim** accepts seconds (`90`, `1.25`), `MM:SS` (`01:30`), or `HH:MM:SS`
  (`1:02:03`). Leave either endpoint empty to use the start or end of the
  video. To must be after From. Image galleries do not have video trimming.
- **Subtitles** use language codes such as `en` or `en,de`, with automatic
  captions when requested and available. They are saved as separate files,
  not burned into the picture. GIFs have no subtitles or audio.

### Collections

Circle the individual previews you want, choose **Whole collection**, or
enter **Start / End** or **Items** such as `1-5,7,10-12`. Item positions are
one-based. This applies to the collections supported by the selected
engine: playlists, boards, albums, and multi-photo posts. **This item**
keeps one file from a gallery, not the rest of the board or album it came
from.

The contact sheet is a bounded preview, not necessarily the entire
collection. Use ranges or Whole collection for items beyond the previews.
On YouTube, `/playlist?list=…` inspects as a playlist. A
`watch?v=…&list=…` link inspects as one video even when it mentions a
playlist. Channel, home, and search pages are refused.

### GIFs

Choose **GIF**, then a short From / To range, or use the five-second
shortcut. Empty endpoints mean the whole video, which can make a very
large file.

| Control | Choices | Default |
| --- | --- | --- |
| Longest edge | 320, 480, 640, 960 pixels | 480 |
| Frame rate | 10, 15, 20, 25, 30 fps | 15 |
| Loop | Forever or no repeat | Forever |

GIFs are silent, preserve display aspect ratio, and use an optimized color
palette. Mixed collections make one GIF per video and skip still images.
Live and upcoming streams must finish first; audio-only links cannot
become GIFs. Both FFmpeg and ffprobe are required. The result offers a
preview and **Save GIF**; temporary source videos are not added to the
public library.

### Queue, library, and stopping

Up to **five** jobs can be queued or running, with **two** workers running
at once. New jobs are refused below **200 MiB** free disk, and running jobs
also check for low space. Plan for the full size of large collections,
not just the preview.

Library search and media filters cover the whole downloads folder, even
when only a page of results is loaded. GIFs are image files, with a separate
GIF filter. Subtitle sidecars remain files too. Dotfiles, unfinished
fragments, symlinks, and special files are not part of the library. The grid
loads small JPEG previews from a `.thumbs/` cache inside `downloads/`
instead of decoding full-size images and videos; opening or saving a file
still uses the original.

**Clear finished** removes session records, not saved media. Job history
and retry parameters live in server memory and disappear on restart;
finished files remain. UI preferences are local to the browser.

Ctrl+C, a service stop, or `docker compose down` refuses new jobs, cancels
queued work, stops running process trees, and cleans per-job cookies. The
shutdown grace period is about five seconds. A second Ctrl+C exits
immediately. Retry an interrupted job explicitly after restarting.

## Sign-in and cookies

Use sign-in only for content your account can already access: private or
members-only posts, age gates, or site bot checks. First verify that the
same account can open the media in your browser. offtube cannot grant
membership, unlock a paywall, or remove DRM.

### From browser — native server

Under **Sign-in**, select Browser and choose the browser you use for that
site. The server reads that local browser's cookies through the engines;
OS encryption or browser-profile restrictions can prevent access.

The desk and extension do not have a profile selector. If the default
profile is not the signed-in one you need, use a cookies export from the
correct profile. API clients can set
[`cookies_browser_profile`](api.md#cookie-fields) explicitly.

This requires a native server on the same machine and account as the
browser profile. A Docker container cannot see the host browser's profile.
The extension's popup and right-click actions honor the saved sign-in
choice.

### Cookies file — native or Docker

1. Sign into the source site as the account that can access the media.
2. Export that site's cookies in **Netscape cookies.txt** format using a
   cookie-export tool you trust. Treat the export like a password.
3. Open **Sign-in → Cookies file** in the web desk or side panel, then
   upload the file or paste its contents.
4. Preview again and retry. Re-export when the session expires.

The extension popup does not hold an uploaded export. When Cookies file
is selected, right-click actions open the side panel so it can supply the
export; paste or upload it there if needed.

### What is retained?

Cookie text is not saved in browser preferences or extension storage.
For Netscape exports, the server trims unrelated sites before writing
request-local cookie files; YouTube retains the related Google cookies it
needs. Temporary copies are owner-only on POSIX and cleaned after the
request/job, at shutdown, and at startup. Engine writeback does not replace
the separate saved session export, if one exists.

**Retry needs the original request:** a pasted export also stays in that
job's private in-memory payload until the record is cleared, evicted, or
the server stops. A browser textarea can retain its contents while the
page stays open. Never commit, share, or attach cookies to a bug report.
See the [trust boundary](../ARCHITECTURE.md#trust-boundary).

## Docker

From the clone, with Docker and Compose installed:

```sh
docker compose up -d --build
docker compose logs -f
```

Open `http://127.0.0.1:8000`; the extension uses the same address. The image
includes Python, FFmpeg/ffprobe, Deno, and both download engines. `tini`
forwards stop signals and reaps orphaned child processes.

The container runs as an unprivileged user, because files it creates land
in the bind-mounted folders and should stay editable on the host. The image
defaults to UID/GID `1000`, the first account on most Linux systems. If
`id -u` or `id -g` is different, build once with those IDs:

```sh
OFFTUBE_UID="$(id -u)" OFFTUBE_GID="$(id -g)" docker compose up -d --build
```

If you used an older root-running image, reclaim its files first with
`sudo chown -R "$(id -u):$(id -g)" downloads cookies`. On macOS and Windows,
Docker Desktop maps ownership itself; the defaults are fine.

- `./downloads` and `./cookies` are bind-mounted, so completed files stay
  in the checkout when the container is removed.
- Compose restarts the service unless you explicitly stop it.
- Keep `127.0.0.1:8000:8000` as the published port. The container's internal
  `HOST=0.0.0.0` is necessary and does not make public port publishing safe.
- Use **Cookies file**, not From browser. Do not mount your whole browser
  profile into the container as a workaround.
- Do not run a native server and the container on the same port.

To stop, or rebuild after updating the checkout:

```sh
docker compose down
docker compose up -d --build
```

## Background services

Run the native launcher once first, verify that the desk and dependencies
work, and stop it before installing a service. Services keep native
browser-cookie support. Install these templates **from the clone's root**;
reinstall them if you move the checkout.

### macOS: launchd

```sh
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
DIR="$(pwd)"
sed -e "s|REPLACE_ME|$USER|g" -e "s|REPLACE_DIR|$DIR|g" \
  deploy/com.offtube.plist > "$HOME/Library/LaunchAgents/com.offtube.plist"
launchctl load "$HOME/Library/LaunchAgents/com.offtube.plist"
```

It starts at login and writes to `~/Library/Logs/offtube.log`. The template
includes the usual Apple Silicon and Intel Homebrew paths. To stop it:

```sh
launchctl unload "$HOME/Library/LaunchAgents/com.offtube.plist"
```

### Linux: systemd user service

```sh
mkdir -p "$HOME/.config/systemd/user"
sed "s|REPLACE_DIR|$PWD|g" deploy/offtube.service \
  > "$HOME/.config/systemd/user/offtube.service"
systemctl --user daemon-reload
systemctl --user enable --now offtube
journalctl --user -u offtube -f
```

This is a user service, normally active while your user manager is
running. It is not a system-wide server. To stop and disable autostart:

```sh
systemctl --user disable --now offtube
```

Services do not inherit your terminal's `PATH`. The systemd template adds
`~/.deno/bin` and `~/.local/bin`; launchd includes Homebrew locations. If
you installed tools elsewhere, adjust the generated service's `PATH` and
reload it. Do not expose the server to solve a service-startup problem.

## Troubleshooting

| What you see | What to do next |
| --- | --- |
| Server unreachable | Start `./run.sh` or `run.bat`; check the address and port in the extension. For services or Docker, read their logs. |
| Port already in use | Check whether offtube is already running. Stop the duplicate, or choose another `PORT` and update the client address. |
| Unsupported link | Use a specific media or collection URL. No generic scraper is used; an engine must have a dedicated extractor. |
| Login required / private post | Verify access in the browser, then use your own browser cookies or a fresh Netscape export. Docker needs the file route. |
| YouTube asks you to confirm you are not a bot | Check the JS runtime in `/api/health`, then supply fresh YouTube cookies. A runtime alone may not satisfy a site's account or network challenge. |
| The page needs to be reloaded | The session was rejected. offtube tries alternate YouTube player clients; if it still fails, export fresh cookies from one signed-in account. |
| Join this channel to get access | Verify the account and membership tier. Cookies cannot grant access the account does not have. |
| Only images / no video streams | Check sign-in and the JS runtime, then reinstall the checkout's pinned dependencies with `--upgrade`. Inspect the job log if it persists. |
| Both engines failed | Read the per-engine hints; the post may be unavailable, restricted, or require sign-in. |
| FFmpeg missing / GIF creation refused | Verify both `ffmpeg -version` and `ffprobe -version` in the server's environment; restart it after installation. |
| Too many active downloads | Wait for, or cancel, one of the five active jobs. |
| Disk almost full | Free space in the filesystem holding `downloads/`, then retry. The threshold is a guard, not a size estimate. |
| Server restarted during a keep | Saved files remain, but the old job is unknown. Preview and queue the link again. |

Still stuck? [Report a reproducible issue](../CONTRIBUTING.md#report-a-problem)
with the error and relevant versions, **without cookies or private URLs**.
