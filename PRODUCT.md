# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

People who want to keep a copy of media they can already see online: a video, a reel, a pin, a board, a track, a photo post. The same product serves two kinds of user:

- **Everyday keepers**, not technical, who should never have to think about engines, ffmpeg, formats or logs. They are on a page, they want the thing on it, saved.
- **Power users**, who run the local server themselves (`./run.sh`, Docker, launchd/systemd), use cookies files for sign-in-only content, pick quality ceilings, trim clips, take ranges out of playlists and boards, and read the transfer log when something fails.

Design is **progressive**: consumer-simple by default, power details one deliberate step away, never removed.

## Product Purpose

offtube keeps media from 2,000+ sites on the user's own machine. The user points it at a link (or the tab they're on), sees exactly what it found (site, title, kind, size options, items in a collection), chooses what to keep, and gets the file in `downloads/`. Success: the user gets the file they meant to keep, first try, and understands immediately when and why something can't be kept.

## Positioning

- **Local-only.** A Python stdlib server on 127.0.0.1 does the work; nothing is uploaded, tracked or proxied through a third party. Files never leave the machine.
- **One tool for every site.** Two engines behind one paste box: yt-dlp (video/audio, ~1,800 sites) and gallery-dl (images, boards, galleries, ~300 sites), chained automatically. The user never chooses an engine.
- **Honest.** It refuses what it can't do up front (DRM services, unsupported pages) and explains sign-in failures in plain language instead of tool errors.

## Operating Context

- **Extension first.** Most use starts from the tab the user is on: the toolbar popup (quick keep), the side panel (stays open, follows tabs, full controls, queue, library) and right-click "Keep with offtube" on any link or page. The extension is a thin client; it cannot download by itself.
- **Web app** at `http://127.0.0.1:8000` is the full desk and the library: paste, inspect, options, queue, saved files.
- Both talk to the same local API (`/api/source`, `/api/info`, `/api/download`, `/api/job(s)`, `/api/files`, `/api/cancel`, `/api/retry`, `/api/health`).
- The server must be running; "server unreachable — start ./run.sh" is a real, recurring state.

## Capabilities and Constraints

- **Inspect** returns `source {id,label}`, `engine`, `type` (`video` | `playlist` | `gallery`), `media` (`video` | `audio` | `image` | `mixed`), title, uploader, duration, thumbnail, quality ladder with sizes, subtitles, live flag, collection count/entries.
- **Options:** quality ceiling (best, 2160…360, audio MP3/M4A); galleries keep original files (no quality choice); clip trim from–to (video only); collection scope (this item / whole collection / range `1-5,7`); subtitles with languages; access (public, browser cookies — native server only, cookies file — required in Docker).
- **Jobs:** queue of up to 5 active, live progress (playlist-aware), cancel, retry, clear finished, log, disk-full stop.
- **Library:** files in `downloads/`, save, delete, filter, disk free.
- **Technical constraints:** no build step; plain HTML/CSS/JS served by the stdlib server with CSP `default-src 'self'; img-src 'self' https: data:; style-src 'self'; script-src 'self'` — no inline scripts or styles, no external fonts or CDNs. Extension is MV3 with no inline scripts; popup is width-constrained (~370px); side panel is a narrow, tall, resizable column.
- **Terminology:** "keep" (not "download" as the brand verb, though Download remains a clear action label), "link", "collection" (playlists, boards, albums, profiles, multi-photo posts), "Access" (sign-in via cookies).

## Brand Commitments

- Name **offtube** stays (lowercase). Logo mark, colors, typography and layout are open to replacement.
- Voice: plain, direct, confident, a little dry; tells the truth about limits ("can't bypass paywalls or DRM").

## Evidence on Hand

- No testimonials, user counts, press or benchmarks exist. Do not invent any.
- Real supported-site list is enforced by a test (`tests/test_helpers.py::FEATURED`) against the web UI's "works with" strip.

## Product Principles

1. **The link is the interface.** Everything follows from what was pasted or what tab is open; controls appear only when the inspected media can use them.
2. **Simple by default, deep on demand.** Never hide a power feature behind a dead end; never show it before it's relevant.
3. **Say what happened.** Every state — inspecting, unsupported, sign-in needed, queued, saving, done, failed, server down — has a clear, human sentence and a next step.
4. **Local and honest.** Never imply cloud, accounts, or abilities the tool doesn't have.

## Accessibility & Inclusion

No product-specific standard established; meet WCAG 2.2 AA as the floor (keyboard operable, visible focus, contrast, reduced motion, screen-reader status announcements for progress and errors).
