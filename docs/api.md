# Local API

[Home](../README.md) · [User guide](guide.md) ·
[Architecture](../ARCHITECTURE.md) · [Contributing](../CONTRIBUTING.md)

The web desk and Chrome extension use this same HTTP API. The default base
URL is `http://127.0.0.1:8000`. Requests and responses are JSON except for
static assets and `/files/` downloads. There is no API version prefix or
authentication token.

Shell examples use POSIX syntax. On Windows, adapt quoting and line
continuations for PowerShell and invoke `curl.exe` rather than its alias.

**Keep it on loopback.** Any local program or installed extension can use
this API. Do not publish it as a hosted service. See the
[trust boundary](../ARCHITECTURE.md#trust-boundary).

## Check and inspect a link

### `GET /api/source?url=…`

Matches a URL against the installed engines' dedicated extractors without
contacting the source site. A match is not proof that the media exists or
that your account can access it.

```sh
curl --get 'http://127.0.0.1:8000/api/source' \
  --data-urlencode 'url=https://www.youtube.com/watch?v=BaW_jenozKc'
```

```json
{
  "ok": true,
  "supported": true,
  "source": {"id": "youtube", "label": "YouTube"},
  "engines": ["yt-dlp"]
}
```

Unsupported or invalid links return **HTTP 200** with `supported: false`
and an `error` message, not a 400. Media-operation endpoints reject those
same links with 400.

### `POST /api/info`

Inspects metadata, contacting the source site. Accepts `url` and the
cookie fields listed below. It does not start a download job or persist a
new session-cookie export; temporary cookie copies are cleaned afterward.

```sh
curl --fail-with-body 'http://127.0.0.1:8000/api/info' \
  -H 'Content-Type: application/json' \
  --data '{"url":"https://www.youtube.com/watch?v=BaW_jenozKc"}'
```

Success is `{ "ok": true, "info": { … } }`. Every `info` variant has:

| Field | Meaning |
| --- | --- |
| `source` | `{id, label}` for the source site. |
| `engine` | `yt-dlp` or `gallery-dl`, selected by the server. |
| `type` | `video`, `playlist`, or `gallery`. |
| `title`, `uploader`, `thumbnail` | Display metadata; values can be null. |
| `webpage_url` | The engine's page URL or the submitted URL. |

Branch on **`type` and `engine` first**, then on `media` where present.
The inspection result has three distinct shapes, not one object with
every possible key set to null:

| Variant | Additional fields |
| --- | --- |
| yt-dlp, `type: "video"` | `media` (`video` or `audio`), `id`, `duration`, `duration_str`, `is_live`, `live_status`, `qualities`, `subtitles`, `automatic_captions`, `part_of_playlist`, `playlist_id`. |
| yt-dlp, `type: "playlist"` | `count`, `entries`. **No `media`, `more`, or `qualities` field.** |
| gallery-dl, `type: "gallery"` | `media` (`video`, `image`, or `mixed`), `count`, `more`, `entries`. No `qualities`, subtitle, or duration fields. |

For a single video/audio item, `id`, `duration`, `live_status`, and
`playlist_id` may be null. `duration_str` is a formatted string or `"-"`
when the duration is unknown. The subtitle fields are arrays of language
codes. `qualities` is an array with `id`, `label`, and `height`; `height`
can be null. Best/height rows also include nullable `filesize` and
`filesize_str`, while audio rows **omit** those size fields.

Playlist `entries` have `id`, `title`, `url`, `duration`, `duration_str`,
and `thumbnail`; values other than the formatted duration can be null.
They do not have `ext` or `is_video`. At most 50 entries are previewed.
`count` uses a positive integer total reported by the extractor when
available, otherwise the number of previews; it is not always a full total.

Gallery `entries` instead have `title`, `thumbnail`, `ext`, and `is_video`.
Title/thumbnail can be null, `ext` can be empty, and `is_video` is a boolean;
these entries have no `id`, `url`, or duration fields. `count` is the
preview count, capped at 50. `more: true` means the preview reached that
cap, not proof of a known remaining item count.

Collection inspection is bounded, not a full listing. A YouTube watch URL
mentioning a playlist inspects as one video; use the playlist's own URL to
inspect the collection.

## Start a job

### `POST /api/download`

The URL is required; all other fields can be omitted for a default keep.
The examples use yt-dlp's test-video URL. Replace it with media you own or
are authorized to save.

```sh
curl --fail-with-body 'http://127.0.0.1:8000/api/download' \
  -H 'Content-Type: application/json' \
  --data '{"url":"https://www.youtube.com/watch?v=BaW_jenozKc","quality":"1080"}'
```

Returns `{ "ok": true, "job_id": "<id>" }`. This acknowledges queueing,
not successful completion. Poll the job until it reaches a terminal state.

| Field | Type / default | Purpose |
| --- | --- | --- |
| `url` | string, required | Supported media or collection URL, at most 2,000 characters. |
| `quality` | string, `best` | Output choice; see below. |
| `clip_from`, `clip_to` | strings, empty | Seconds, `MM:SS`, or `HH:MM:SS`; fractional seconds accepted. To must follow From. |
| `playlist_mode` | string, `single` | `single` or `playlist`. Set `playlist` explicitly for collection downloads. With gallery-dl, `single` keeps the first file at the link. |
| `playlist_start`, `playlist_end` | integer or numeric string | One-based collection bounds; both positive, with Start not after End. |
| `playlist_items` | string, empty | Item selection, such as `1-5,7,10-12`; one-based, ascending ranges, at most 200 characters. |
| `subtitles` | boolean, `false` | Request subtitle sidecars, not embedded subtitles. |
| `auto_subs` | boolean, `false` | Also request automatic captions when subtitles are enabled. |
| `sub_lang` | string, `en` | Comma-separated language codes, such as `en,de`; at most 100 characters. |
| `title`, `thumbnail` | strings, optional | Preview metadata for the session strip; not an alternative media source. |
| `cookies_*` | see below | Access through your own sign-in session. |

**Quality IDs:** `best`, `2160`, `1440`, `1080`, `720`, `480`, `360`,
`audio_mp3`, `audio_m4a`, `gif`. Other positive three- or four-digit height
strings are accepted by the backend. These select streams, not an exact
output size. gallery-dl keeps originals regardless of a normal video/audio
quality choice; `gif` instead selects video entries for conversion.

### GIF fields

Set `quality` to `gif`, with optional clip times and these typed fields:

| Field | Accepted JSON values | Default |
| --- | --- | --- |
| `gif_size` | integer: `320`, `480`, `640`, `960` | `480` |
| `gif_fps` | integer: `10`, `15`, `20`, `25`, `30` | `15` |
| `gif_loop` | boolean | `true` |

The size is the longest edge; display aspect ratio is preserved. GIFs need
FFmpeg and ffprobe, ignore subtitles, and cannot use audio-only links or
live/upcoming streams. A mixed collection skips still images. Temporary
sources and encoder scratch files stay in a private job directory and are
removed after completion, failure, or cancellation.

### Cookie fields

| Field | Accepted values |
| --- | --- |
| `cookies_mode` | `none` (default), `browser`, `upload`, `text`, `file` |
| `cookies_browser` | `chrome` (default), `chromium`, `edge`, `firefox`, `safari`, `opera`, `brave`, `vivaldi` |
| `cookies_browser_profile` | Optional browser profile name/path, at most 256 characters. |
| `cookies_text` | Netscape cookies.txt contents, up to approximately 1 MiB of text before site filtering. |

`upload`, `text`, and `file` are aliases for cookie **text**, not arbitrary
server filesystem paths. With one of those modes and no text, the server
uses `cookies/session_cookies.txt` if it already exists; otherwise the
request fails. The API does not create that saved export from a paste.
Browser mode reads the server user's local browser, so it does not work
with a host browser from Docker.

Do not log cookie payloads. Per-request cookie copies are cleaned, but a
job's private original payload remains in memory for Retry until cleared,
evicted, or shutdown. [Cookie lifecycle →](guide.md#what-is-retained)

## Follow and manage jobs

| Endpoint | Request | Success response |
| --- | --- | --- |
| `GET /api/job?id=<id>` | Job ID in query | One public job record. |
| `GET /api/jobs?limit=50` | Limit clamped to 1–50 | `{ "jobs": [ … ] }`; each log trimmed to its last 20 lines. |
| `POST /api/cancel` | `{ "job_id": "<id>" }` | `{ "ok": true, "cancelled": true }`; a running job also returns `stopping: true`. |
| `POST /api/retry` | `{ "job_id": "<id>" }` | `{ "ok": true, "job_id": "<new-id>", "retried": "<old-id>" }`. |
| `POST /api/jobs/clear` | `{}` | `{ "ok": true, "cleared": <count> }`. |

Poll `/api/job` with the returned ID, for example:

```sh
curl 'http://127.0.0.1:8000/api/job?id=YOUR_JOB_ID'
```

Public records contain `id`, `url`, a site-label **string** in `source`,
`title`, `thumbnail`, `quality`, `status`, `progress`, `detail`, `log`,
`files`, and timestamps. Byte counts, speed, ETA, and playlist counters are
included when available. `files` is an array of exact **filenames**, not
file objects or URLs. Internal subprocesses and the stored request payload
are not exposed.

```text
queued → starting → downloading / processing → done
                                           ↘ error / cancelled
```

These are status names, not a guarantee of visiting every intermediate
state. The terminal states are `done`, `error`, and `cancelled`. A cancel
acknowledgment may precede the terminal state; keep polling. Cancellation
and shutdown stop the job's child-process tree, including encoders.

Retry accepts any terminal job, revalidates its stored parameters, and
creates a new record. Unknown IDs return 404; cancelling or retrying an
ineligible job returns 400. Keep polling through a temporary connection
failure, but **stop on 404**: a server restart clears job history. Clear
finished removes records, not library files. Terminal records are evicted
when stored history exceeds 50.

## Browse and retrieve files

### `GET /api/files?q=&kind=&offset=&limit=`

| Parameter | Behavior |
| --- | --- |
| `q` | Case-insensitive filename substring, first 200 characters. |
| `kind` | `all` (default), `video`, `audio`, `image`, `gif`. |
| `offset` | Nonnegative integer; default 0. |
| `limit` | Clamped to 1–500; default 500. |

Returns `{files, total, matched, disk_free, disk_free_str}`, newest first.
`total` counts the full library; `matched` counts all files matching the
search before pagination. A file has `name`, `size` (bytes), `size_str`,
`modified`, `url`, and `kind`. A GIF's file `kind` is `image`; the `gif`
query filter selects `.gif` files specifically. Other files, such as
subtitle sidecars, can have kind `other` and appear with `kind=all`.

Follow the returned relative `url` against your server base URL to retrieve
a file. `GET /files/<encoded-name>` supports a single byte range with 206
or 416 responses, and supplies `ETag` / `Last-Modified` for `If-Range`.
Use the provided URL rather than interpolating an unescaped filename.

`POST /api/files/delete` accepts `{ "name": "<exact filename>" }` and
returns `{ "ok": true, "deleted": "<name>" }`, or 404. This deletes the
actual saved file. Send the name verbatim in JSON, **not URL-encoded**.

The library consists of regular files directly in `downloads/`. Dotfiles,
temporary/fragment files, symlinks, directories, and special files are
neither listed, served, nor deleted through the API.

## Health

`GET /api/health` returns `{ "ok": true, "checks": { … } }`, cached for
60 seconds. Checks report `yt_dlp` and `gallery_dl` versions (or `missing`),
FFmpeg availability/version, Node availability/version/support, Deno
availability, `js_runtime`, and `js_runtime_ok`.

This endpoint is a reachability and environment probe, not a real-site
download test. `ok: true` does not mean all dependencies are installed.
It does not probe ffprobe separately.

## Validation, errors, and limits

Send an object with `Content-Type: application/json` for POSTs. URL,
quality, clip, language, and cookie fields must be strings; use JSON
booleans for subtitle and GIF-loop flags, and JSON integers for GIF size
and frame rate. Prefer omitting optional fields to sending `null`; GIF
settings in particular must have the declared types when supplied.

Most JSON operation failures return `{ "ok": false, "error": "…" }`.
Some routes, including an unknown job, return only `{ "error": "…" }`;
check the HTTP status, not just `ok`. Low-level HTTP/Host refusals need not
be JSON.

| Status | Meaning |
| --- | --- |
| 400 | Invalid fields, unsupported media URL, inspection failure, or an ineligible job operation. |
| 403 | Foreign-origin API write or invalid Host header. |
| 404 | Unknown API route, job, or kept file. |
| 413 | JSON body exceeds 2 MiB. |
| 429 | Five jobs are already active. |
| 503 | Connection capacity reached, with `Retry-After: 1`; or shutdown refusing new work. |
| 507 | Less than 200 MiB free on the downloads filesystem. |

The server has 64 connection slots and a 30-second whole-request read
deadline. Two workers execute downloads, and inspection concurrency is
bounded separately. Request payloads are validated before queue mutation.
Literal IP/LAN media targets, generic extractors, known DRM services, and
YouTube channel/home/search pages are refused.

Host validation, foreign-Origin/Referer write rejection, and CORS are
separate checks. Allowed response origins are loopback HTTP origins and
`chrome-extension://` origins, never `*`. These are browser protections,
not client authentication or an outbound network sandbox.
