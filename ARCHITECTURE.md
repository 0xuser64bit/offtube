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
| HTTP and configuration | `app.py`, `LocalServer`, `Handler`, `main` | `HOST` defaults to IPv4 loopback; `::1` selects IPv6; `PORT` defaults to 8000. Docker alone binds all interfaces internally. |
| Source resolution | `validate_url`, `resolve_source`, extractor tables | Dedicated yt-dlp/gallery-dl extractors only; no generic catch-all. YouTube channel/home/search pages are refused. |
| Inspection | `fetch_info`, `inspect_ytdlp`, `inspect_gallery` | Two concurrent inspections; request-local cookies; engine fallback; bounded gallery preview. |
| Queue | `jobs`, `futures`, `jobs_lock`, `enqueue_download` | Two executor workers, five active jobs, fifty stored records; retry revalidates the original private payload. |
| Download engines | `run_download`, `run_ytdlp`, `run_gallery` | yt-dlp runs in-process for normal downloads; gallery-dl runs as a child process. Engine order depends on the source. |
| GIF pipeline | `run_gif_ytdlp`, `tools/gif_source.py`, FFmpeg | Isolated source process; private job directories; two-pass palette encoding; cancellation/process-tree cleanup; both ffmpeg and ffprobe required. |
| Cookies | `resolve_cookies`, `write_cookie_file` | Site-filtered, atomically published per-request copies; owner-only permissions on POSIX; cleanup on completion and startup. Saved session exports remain separate from engine writeback. |
| Library | `downloads/`, `/api/files`, `/files/` | Filesystem is the durable library. Job history is intentionally in memory and clears on restart. No database migrations are needed. |
| Web client | `web/index.html`, `web/app.js`, `web/styles.css` | Plain JS state, polling, local preferences, self-hosted fonts, restrictive CSP, escaped generated markup. |
| Extension | `extension/shared.js`, popup, side panel, background worker | Chrome storage for preferences and pending selections; loopback server URLs only; background worker has no durable global state. |
| Deployment | launchers, Docker/Compose, `deploy/` | Python 3.10+; pinned engine versions; native browser cookies are unavailable in Docker. Docker must package the GIF worker as well as `app.py` and `web/`. |

## Invariants to preserve

- Validate media field types and access/collection modes before engine work or
  queue mutation. Invalid client input must produce JSON errors, not broken
  connections or unexpected broad collection downloads.
- Treat Host validation, foreign-Origin POST rejection and CORS as separate
  layers. Validate Host on preflight too. Support bracketed IPv6 authorities
  without accepting malformed, missing HTTP/1.1 or duplicate Host headers.
- Never expose stored request payloads or subprocess objects in public jobs.
  Cookies are credentials, not ordinary application data.
- Maintain per-job ownership of temporary files; cleanup must not delete a
  concurrent job's outputs. Never put source videos in the public GIF library.
- Preserve the web/extension feature contract together. The engine and media
  type, not the URL hostname alone, determine available controls.
- Verify the packaged layout, not just execution from a source checkout.

## Validation

Install `requirements.txt` and `requirements-dev.txt` into a Python 3.10+
environment. With Node.js, ffmpeg and ffprobe on PATH, run:

```sh
bash tools/check.sh
```

The gate compiles Python, runs pytest (including real generated-media
encoding and the Docker COPY-layout regression), checks shipped JavaScript
syntax, runs the web/extension Node tests, and checks patch whitespace.
It uses `.venv/bin/python` by default; set `PYTHON` to select another interpreter.

GitHub CI runs the gate on Python 3.10 and 3.12, plus a separate Docker build,
packaged-worker check and HTTP startup smoke test. Actions are commit-pinned
and the workflow has read-only repository permissions. A COPY-layout test is
not a substitute for the actual container build or multi-architecture testing.

## Next priorities

These are source-inspection findings, not evidence of a complete security,
performance or browser-accessibility audit:

1. **Bound request lifetime and admission.** HTTP workers are unbounded.
   Inspection limits active engine work but waiting callers still occupy
   threads. Socket timeouts and oversized-body draining are not total-request
   deadlines. Add bounded admission and slow-client regression tests.
2. **Unify cancellation and shutdown.** Normal yt-dlp cancellation relies on
   engine callbacks; gallery disk checks rely on output lines. Unlike the GIF
   subprocess path, silent work has no uniform deadline/watchdog. Shutdown
   should cancel queued jobs and stop complete process trees predictably.
3. **Harden filesystem snapshots.** Library listing stats files multiple times
   while workers and delete requests can mutate them. Handle disappearing
   files and exclude out-of-root symlinks consistently from listings and
   retrieval; test concurrent completion/deletion.
4. **Document the trust boundary accurately.** The API trusts local programs
   and Chrome extensions, with no authentication. URL checks reject literal
   IPs and local-looking names but do not enforce public DNS resolution or
   redirect destinations for every engine request. Do not expose this API to
   a LAN or describe extractor matching as a full network sandbox.
5. **Expand release verification.** Exercise Chrome popup/side-panel behavior,
   keyboard/focus, narrow layouts, real-site failures, native service shutdown
   and arm64/amd64 images. Existing Node DOM fixtures are useful regressions,
   not end-to-end browser or accessibility certification.

Keep fixes incremental. Do not split the backend into a framework, introduce
a datastore or redesign the UI merely to reduce file length. Extract modules
when a tested responsibility boundary makes an actual change safer.
