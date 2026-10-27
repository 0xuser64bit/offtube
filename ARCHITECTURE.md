# Architecture and engineering priorities

## Product boundary

offtube is a single-user, local media keeper, not a hosted download service.
Keep the no-build web app, the MV3 extension, progressive controls and the
contact-sheet design described in `PRODUCT.md` and `DESIGN.md`. There is no
account system, database, payment provider or cloud backend. Adding those is
a product decision, not a prerequisite for reliable local operation.

## System map

| Area | Implementation | Important constraints |
| --- | --- | --- |
| HTTP and configuration | `app.py`, `LocalServer`, `Handler`, `DeadlineReader`, `main` | `HOST` defaults to IPv4 loopback; `::1` selects IPv6; `PORT` defaults to 8000. Docker alone binds all interfaces internally. At most 64 connections get a thread; past that the accept loop reads the request (waiting at most 0.2 s) and answers 503. A request must arrive whole within 30 s. The port is claimed exclusively on Windows. |
| Source resolution | `validate_url`, `resolve_source`, extractor tables | Dedicated yt-dlp/gallery-dl extractors only; no generic catch-all. YouTube channel/home/search pages are refused. |
| Inspection | `fetch_info`, `inspect_ytdlp`, `inspect_gallery` | Two concurrent inspections; request-local cookies; engine fallback; bounded gallery preview. Counts and durations reported by extractors are coerced before they reach clients. |
| Queue | `jobs`, `futures`, `jobs_lock`, `enqueue_download`, `shutdown_jobs` | Two executor workers, five active jobs, fifty stored records; retry revalidates the original private payload. Shutdown refuses new jobs, cancels queued ones and gives running ones `SHUTDOWN_GRACE` seconds. |
| Download engines | `run_download`, `run_ytdlp`, `worker_request`, `run_ytdlp_worker`, `tools/ytdlp_worker.py`, `run_gallery` | Every yt-dlp download runs in the worker process, which reports JSON events (progress, files, errors) through a file the job's thread polls while it checks cancel and free disk. gallery-dl runs as a child process with the same watchdog. The server process uses yt-dlp only to inspect. Engine order depends on the source. |
| Child processes | `OWN_PROCESS_GROUP`, `stop_process_tree`, `watch_job_process` | gallery-dl, the yt-dlp worker and ffmpeg encodes each lead their own process group, so cancel and shutdown stop everything a job started. |
| GIF pipeline | `run_gif_ytdlp`, `run_ytdlp_worker`, FFmpeg | Sources fetched by the yt-dlp worker into private job directories; two-pass palette encoding; both ffmpeg and ffprobe required. |
| Cookies | `resolve_cookies`, `write_cookie_file` | Site-filtered, atomically published per-request copies; owner-only permissions on POSIX; removed when a request or job ends, on shutdown and at startup. Saved session exports remain separate from engine writeback. |
| Library | `library_name`, `library_snapshot`, `library_page`, `open_library_file`, `delete_library_file` | The regular files directly in `downloads/` - never dotfiles, temps, symlinks or special files - for listing, serving and deleting alike. The server searches and pages the whole library; clients never filter a partial list. `/files/` answers single byte ranges. Job history is intentionally in memory and clears on restart. No database migrations are needed. |
| Web client | `web/index.html`, `web/app.js`, `web/styles.css` | Plain JS state, polling, local preferences, self-hosted fonts, restrictive CSP, escaped generated markup. |
| Extension | `extension/shared.js`, popup, side panel, background worker | Chrome storage for preferences and pending selections; loopback server URLs only; background worker has no durable global state. |
| Deployment | launchers, Docker/Compose, `deploy/` | Python 3.10+; pinned engine versions; native browser cookies are unavailable in Docker. Docker must package the yt-dlp worker as well as `app.py` and `web/`, and runs `tini` as PID 1. SIGTERM stops the server like Ctrl+C. Services don't get a login shell's PATH, so the launchd agent and the systemd unit set one that reaches Homebrew, `~/.deno/bin` and `~/.local/bin`. |

## Invariants to preserve

- Validate media field types, quality ids and access/collection modes before
  engine work or queue mutation. Invalid client input must produce JSON
  errors, not broken connections or unexpected broad collection downloads.
- Treat Host validation, foreign-Origin POST rejection and CORS as separate
  layers. Validate Host on preflight too. Support bracketed IPv6 authorities
  without accepting malformed, missing HTTP/1.1 or duplicate Host headers.
- Never expose stored request payloads or subprocess objects in public jobs.
  Cookies are credentials, not ordinary application data.
- Values reported by sites and extractors (titles, counts, durations) are
  data: coerce numbers on the server and escape everything a client puts
  into markup.
- Maintain per-job ownership of temporary files; cleanup must not delete a
  concurrent job's outputs. Never put source videos in the public GIF library.
- Every child process offtube starts leads its own process group and is
  stopped with `stop_process_tree`; nothing a job started may outlive it.
  Downloads never run yt-dlp in the server process, so a cancel or shutdown
  never waits for yt-dlp to reach a callback. The worker's request is plain
  JSON; hooks, loggers and filters are created inside the worker.
  `stop_process_tree(wait=None)` only signals and is the form to use while
  holding `jobs_lock`.
- One definition of a kept file serves listing, `/files/` and delete, and a
  file's bytes and length come from the same open descriptor.
- Preserve the web/extension feature contract together. The engine and media
  type, not the URL hostname alone, determine available controls.
- Verify the packaged layout, not just execution from a source checkout.

## Trust boundary

offtube has no authentication. It is safe as a single-user tool on a
machine whose local programs and accounts are trusted, and on nothing wider.

- **Local programs and accounts.** Loopback is not per-user: any process on
  the machine, under any account, can call the API. It can start downloads,
  read the job list (URLs and titles), fetch and delete kept files, and
  inspect links with cookies it supplies. A Docker port published beyond
  `127.0.0.1` extends this to the network: the Host check stops browsers
  there, not other clients, which can send any Host header.
- **Browser pages.** A web page can send requests to loopback, but the Host
  check refuses DNS-rebound names, foreign-Origin POSTs are refused, and
  CORS is never granted to web origins, so a page can neither change state
  nor read responses. GET handlers have no side effects.
- **Extensions.** CORS is granted to every `chrome-extension://` origin, not
  only offtube's (an unpacked extension's ID depends on where it is loaded
  from). Any installed extension can use the API, as can any extension that
  has host permissions for loopback, whatever this server answers.
- **Outbound fetches.** A link must have a dedicated extractor, and literal
  IPs and local-looking names are refused, but this is a check on the text
  of the URL. It does not resolve DNS or follow redirects: a public name
  that resolves to a private address, or a site that redirects to one, can
  still make an engine fetch from the local network. Extractor matching is
  not a network sandbox.
- **Credentials.** A cookies paste is trimmed to the link's site before it
  touches disk, and per-request copies are owner-only and removed when the
  request or job ends, at shutdown and at startup. A pasted export also
  stays in its job's in-memory payload so Retry can reuse it, until the job
  record is evicted or cleared or the server stops.

## Validation

Install `requirements.txt` and `requirements-dev.txt` into a Python 3.10+
environment. With Node.js, ffmpeg and ffprobe on PATH, run:

```sh
bash tools/check.sh
```

The gate compiles Python, runs pytest (including real generated-media
encoding, the Docker COPY-layout regression, and `tests/test_shutdown.py`,
which runs `main()` in a subprocess and sends it real SIGINT/SIGTERM), checks
shipped JavaScript syntax, runs the web/extension Node tests and the
Chromium suites in `tests/browser/` for the web desk and the unpacked
extension (skipped without Playwright), and checks
patch whitespace. It uses `.venv/bin/python` by default; set `PYTHON` to
select another interpreter.

GitHub CI runs the gate on Python 3.10 and 3.12, pytest on Windows (process
trees stopped with `taskkill`, the exclusive port bind, Windows file and
encoding behavior) and macOS, the launchers from a fresh checkout
(`tools/launcher_smoke.sh` for `run.sh`; `run.bat` on Windows), the README's
launchd agent under `launchctl` (it must find ffmpeg and a JS runtime),
`systemd-analyze verify` on the systemd unit, the browser suite with pinned
Playwright and axe-core (required there, never skipped), and, on amd64 and
arm64, a Docker build, packaged-worker check, HTTP startup smoke test, and a
check that the container reaps an orphaned child and stops on SIGTERM with
exit status 0. Actions are commit-pinned and the workflow has read-only
repository permissions. A COPY-layout test is not a substitute for the actual
container build.

## Next priorities

These are source-inspection findings, not evidence of a complete security,
performance or browser-accessibility audit:

1. **Decide on authentication for shared machines.** A per-install token
   issued by the server and entered once in the extension would close the
   local-account and other-extension paths above. This changes setup for
   every user, so it is a product decision.
2. **Constrain outbound destinations.** Refusing private and link-local
   addresses after DNS resolution, including on redirects, needs hooks into
   both engines' HTTP layers.
3. **Expand release verification.** The web desk, popup and side panel are
   exercised in Chromium with axe-core audits. The extension runs there as
   pages in tabs, so the toolbar popup's own window, opening the side panel,
   the context menu and the background worker are not; nor are real-site
   failures, the web and extension tests on Windows and macOS, and a
   running systemd service (its unit is only verified).
   All three surfaces are checked for keyboard-only use, and the desk for
   reflow at 320 CSS px; text spacing and screen readers themselves are
   not. An automated audit is a regression check, not accessibility
   certification.

Keep fixes incremental. Do not split the backend into a framework, introduce
a datastore or redesign the UI merely to reduce file length. Extract modules
when a tested responsibility boundary makes an actual change safer.
