#!/usr/bin/env python3
"""
offtube — paste-a-link media keeper (local web UI + yt-dlp/gallery-dl engines).

Features:
  - Paste a link from any site yt-dlp (video/audio) or gallery-dl (images,
    boards, galleries) has a dedicated extractor for: YouTube, TikTok,
    Instagram, Pinterest, X, Reddit, Vimeo, Twitch, SoundCloud, DeviantArt, …
  - Fetch metadata + thumbnails + available qualities before downloading
  - Quality picker (best / 2160p / 1440p / 1080p / 720p / 480p / 360p / audio-only)
  - Clip trim (from-to, uses yt-dlp --download-sections, needs ffmpeg)
  - Playlist controls (single video vs playlist, start/end/items)
  - Cookies: none / upload cookies.txt (Netscape) / paste text / from browser
  - Optional subtitles, live progress, file browser

Only stdlib + yt-dlp + gallery-dl. No Flask/FastAPI needed.

Usage:
    ./run.sh                     # venv + deps + serve
    PORT=8080 ./run.sh
    # manual: python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
    #         ./.venv/bin/python app.py

Notes on members-only / private videos (from the gist workflow):
  They ONLY work if YOUR account has access. Export cookies from a browser
  where you can already watch the video (Netscape format, e.g. via the
  "Get cookies.txt Locally" extension), then select the Cookies tab in the UI.
  If yt-dlp says "Join this channel to get access", your cookies are expired,
  from the wrong account, or your membership tier doesn't cover that video.
"""

from __future__ import annotations

import datetime as _dt
import concurrent.futures
import email.utils
import errno
import functools
import http.server
import io
import ipaddress
import json
import mimetypes
import math
import os
import re
import selectors
import shutil
import signal
import socket
import socketserver
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
DOWNLOAD_DIR = BASE_DIR / "downloads"
COOKIES_DIR = BASE_DIR / "cookies"
SESSION_COOKIE_FILE = COOKIES_DIR / "session_cookies.txt"

DOWNLOAD_DIR.mkdir(exist_ok=True)
COOKIES_DIR.mkdir(exist_ok=True)
(COOKIES_DIR / ".gitkeep").touch(exist_ok=True)
(DOWNLOAD_DIR / ".gitkeep").touch(exist_ok=True)

try:
    import yt_dlp
    from yt_dlp.extractor import gen_extractor_classes
    from yt_dlp.utils import DownloadError
except ImportError:  # pragma: no cover
    yt_dlp = None
    gen_extractor_classes = None
    DownloadError = Exception

try:
    from gallery_dl import extractor as gallery_extractors
    from gallery_dl.version import __version__ as GALLERY_DL_VERSION
except ImportError:  # pragma: no cover
    gallery_extractors = None
    GALLERY_DL_VERSION = None

jobs: dict[str, dict] = {}
futures: dict[str, concurrent.futures.Future] = {}
jobs_lock = threading.Lock()
shutting_down = False  # set once by shutdown_jobs(); no job starts after it
# Bounded concurrency: at most 2 simultaneous yt-dlp runs; the rest queue in
# the executor instead of spawning unbounded threads.
executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="dl")
MAX_JOBS = 50  # stored job records; oldest terminal jobs are evicted past this
MAX_ACTIVE_JOBS = 5  # queued + running; beyond this /api/download returns 429

TERMINAL_STATUSES = frozenset({"done", "error", "cancelled"})


def is_terminal(job: dict) -> bool:
    return job.get("status") in TERMINAL_STATUSES


def disk_low_locked(job: dict) -> bool:
    """Throttled free-space probe for a running job. A whole profile or
    board can outgrow the disk long after the enqueue-time check passed.
    Call with jobs_lock held."""
    now = time.monotonic()
    if now - job.get("disk_checked_at", 0.0) < DISK_CHECK_EVERY:
        return False
    job["disk_checked_at"] = now
    try:
        return shutil.disk_usage(DOWNLOAD_DIR).free < MIN_DISK_FREE
    except OSError:
        return False


def active_job_count_locked() -> int:
    return sum(1 for j in jobs.values() if not is_terminal(j))


def evict_jobs_locked() -> None:
    """Drop oldest terminal jobs while over MAX_JOBS. Call with jobs_lock held."""
    while len(jobs) > MAX_JOBS:
        oldest = min(
            (j for j in jobs.values() if is_terminal(j)),
            key=lambda j: j.get("finished_at") or j.get("created_at") or "",
            default=None,
        )
        if oldest is None:
            break
        futures.pop(oldest["id"], None)
        jobs.pop(oldest["id"], None)

QUALITY_FORMATS = {
    # bv skips a separate audio stream. Direct files often have no probed
    # codec, so the last b still accepts that single file.
    "gif": "bv/b[vcodec!=none]/b",
    "best": "bv*+ba/b",
    "2160": "bv*[height<=2160]+ba/b[height<=2160]/bv*+ba/b",
    "1440": "bv*[height<=1440]+ba/b[height<=1440]/bv*+ba/b",
    "1080": "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
    "720": "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b",
    "480": "bv*[height<=480]+ba/b[height<=480]/bv*+ba/b",
    "360": "bv*[height<=360]+ba/b[height<=360]/bv*+ba/b",
    "audio_mp3": "ba/b",
    "audio_m4a": "ba/b",
}

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r")


# ------------------------------- security limits -------------------------------
# Local-only server, but still: bound request sizes, restrict outbound fetch
# targets to sites with a dedicated extractor, and never serve files outside
# their directories.

MAX_JSON_BODY = 2 * 1024 * 1024  # full-browser cookie pastes can be MBs; trimmed after parse
REQUEST_READ_DEADLINE = 30.0  # seconds to receive a whole request, head and body
MAX_URL_LEN = 2000
MAX_COOKIES_TEXT = 1024 * 1024  # raw paste cap; one site's rows surviving the trim are KBs
MAX_PLAYLIST_ITEMS_LEN = 200
MAX_SUB_LANG_LEN = 100
MAX_PROFILE_LEN = 256  # a profile name, or the path to one
MIN_DISK_FREE = 200 * 1024 * 1024  # refuse new downloads below 200 MB free
DISK_CHECK_EVERY = 5.0  # seconds between free-space probes per running job
DISK_FULL_MSG = "Stopped: the disk is almost full (<200 MB free). Free space and retry."

# Origins that may read JSON responses (extension + the local web UI).
# Never reflect * — that lets any website fetch /api/jobs and /api/files
# while offtube is running on loopback.
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# In-progress / fragment files yt-dlp leaves behind mid-download. These must
# never appear in the library or in a job's claimed outputs.
TEMP_SUFFIXES = (".part", ".temp", ".tmp", ".ytdl", ".ydl", ".frag")
# HLS/DASH fragments are "<name>.part-Frag<n>" while a download runs.
FRAGMENT_RE = re.compile(r"\.part-frag\d+$")


def is_temp_file(name: str) -> bool:
    low = name.lower()
    return low == ".gitkeep" or any(low.endswith(s) for s in TEMP_SUFFIXES) or bool(FRAGMENT_RE.search(low))


class HttpError(Exception):
    """Raised for request errors that map directly to an HTTP status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# Hostnames that only ever mean this machine or the LAN.
LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home.arpa")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def validate_url(url: str | None) -> str:
    """Syntax + host sanity: http(s) on a public-looking hostname.
    Whether the site is supported is resolve_source's call."""
    if url is not None and not isinstance(url, str):
        raise ValueError("URL must be text.")
    u = (url or "").strip()
    if not u:
        raise ValueError("Paste a link first.")
    if len(u) > MAX_URL_LEN:
        raise ValueError("That URL is too long.")
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    try:
        parts = urllib.parse.urlsplit(u)
        host = (parts.hostname or "").lower().rstrip(".")
    except Exception:
        raise ValueError("That doesn't look like a valid URL.")
    if parts.scheme not in ("http", "https") or "." not in host:
        raise ValueError("That doesn't look like a valid URL.")
    # Media sites have names. Refusing IPs and LAN names keeps a pasted or
    # right-clicked link from pointing the engines at this machine's network.
    if _is_ip(host) or host.endswith(LOCAL_SUFFIXES):
        raise ValueError("That's a local or IP address, not a media site.")
    return u


def origin_is_trusted(url: str) -> bool:
    """True for the local web UI and unpacked/packed Chrome extension pages."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        scheme = (parts.scheme or "").lower()
    except Exception:
        return False
    if scheme == "chrome-extension":
        return bool(parts.netloc)
    return scheme in ("http", "https") and host in LOCAL_HOSTS


def cors_allow_origin(origin: str | None) -> str | None:
    """Reflect a trusted Origin, otherwise omit CORS (browser hides the body)."""
    if not origin:
        return None
    return origin if origin_is_trusted(origin) else None


# Fields safe to expose on the wire. Everything else in a job record is
# internal: `payload` (may hold pasted cookies), `filename`/`seen_files`
# (absolute server paths), `seen_ids`, `cancel_requested`.
PUBLIC_JOB_FIELDS = (
    "id", "url", "source", "title", "thumbnail", "quality", "status", "progress",
    "detail", "log", "files",
    "created_at", "finished_at", "downloaded_bytes", "total_bytes", "speed",
    "eta", "playlist_index", "playlist_count",
)


def display_thumbnail(value) -> str | None:
    """A thumbnail URL safe to hand back to the UI: http(s), sane length."""
    s = str(value or "").strip()
    return s if s.startswith(("https://", "http://")) and len(s) <= MAX_URL_LEN else None


def file_kind(name: str) -> str:
    """'video' | 'audio' | 'image' | 'other', for library thumbnails."""
    major = (mimetypes.guess_type(name)[0] or "").split("/")[0]
    return major if major in ("video", "audio", "image") else "other"


def public_job(job: dict, log_limit: int | None = None) -> dict:
    """API view of a job: a whitelist, so new internal fields never leak."""
    out = {k: job[k] for k in PUBLIC_JOB_FIELDS if k in job}
    if log_limit is not None:
        out["log"] = (out.get("log") or [])[-log_limit:]
    return out


# Path kinds we will send to yt-dlp. Channel/home/search URLs are rejected
# because they can expand to thousands of videos and fill the disk.
_CHANNEL_PATH = re.compile(r"^/(channel|c|user)/|^/@", re.I)
_PLAYLIST_PATH = re.compile(r"/playlist(?:/|$)", re.I)
_VIDEO_PATH = re.compile(
    r"^/(watch/?$|shorts/|embed/|live/|v/|clip/|e/|tv[#/]|attribution_link)",
    re.I,
)
_OTHER_PATH = re.compile(
    r"^/(results|feed|gaming|premium|account|upload|music|hashtag|@me)/?$",
    re.I,
)


def classify_youtube_url(url: str) -> str:
    """Return 'video' | 'playlist' | 'channel' | 'other' for a validated URL."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    path = parts.path or "/"
    if host == "youtu.be" or host.endswith(".youtu.be"):
        return "video" if path.strip("/") else "other"
    if _PLAYLIST_PATH.search(path):
        return "playlist"
    if _CHANNEL_PATH.search(path):
        return "channel"
    if _OTHER_PATH.match(path) or path in ("", "/"):
        return "other"
    if _VIDEO_PATH.match(path):
        return "video"
    # Unknown YouTube path: treat as a single video (noplaylist), not a dump.
    return "video"


# ------------------------------- sources -------------------------------
# A link is supported when yt-dlp (video/audio, ~1,800 sites) or gallery-dl
# (images/galleries, ~300 sites) has a *dedicated* extractor for it — the
# one the tool itself would pick. Their catch-alls (yt-dlp Generic,
# gallery-dl directlink/generic) are never used, so outbound fetches stay on
# sites those projects explicitly support.

# Short-link and alternate domains, folded into the site they belong to.
SITE_ALIASES = {
    "youtu.be": "youtube.com",
    "youtube-nocookie.com": "youtube.com",
    "pin.it": "pinterest.com",
    "twitter.com": "x.com",
    "redd.it": "reddit.com",
    "fb.watch": "facebook.com",
}

# Display names. Unlisted sites still work; they show their domain.
SOURCE_LABELS = {
    "youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram",
    "pinterest": "Pinterest", "x": "X", "reddit": "Reddit",
    "facebook": "Facebook", "bsky": "Bluesky",
    "tumblr": "Tumblr", "snapchat": "Snapchat", "linkedin": "LinkedIn",
    "vimeo": "Vimeo", "dailymotion": "Dailymotion", "twitch": "Twitch",
    "kick": "Kick", "rumble": "Rumble", "streamable": "Streamable",
    "loom": "Loom", "ted": "TED", "archive": "Internet Archive",
    "bilibili": "Bilibili", "nicovideo": "Niconico", "douyin": "Douyin",
    "weibo": "Weibo", "vk": "VK", "odysee": "Odysee", "9gag": "9GAG",
    "soundcloud": "SoundCloud", "bandcamp": "Bandcamp",
    "mixcloud": "Mixcloud", "audiomack": "Audiomack",
    "flickr": "Flickr", "imgur": "Imgur", "deviantart": "DeviantArt",
    "artstation": "ArtStation", "behance": "Behance", "pixiv": "pixiv",
    "patreon": "Patreon", "redgifs": "RedGIFs",
}

_SECOND_LEVEL = frozenset({"co", "com", "net", "org", "ac", "gov", "edu", "ne", "or"})


def site_domain(host: str) -> str:
    """Registrable domain with aliases folded: m.youtu.be → youtube.com.
    ponytail: two-letter-ccTLD heuristic, not the public suffix list; swap
    one in if a real site misparses."""
    labels = (host or "").lower().strip(".").split(".")
    n = 3 if len(labels) > 2 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL else 2
    base = ".".join(labels[-n:])
    return SITE_ALIASES.get(base, base)


def site_id(host: str) -> str:
    """'youtube', 'pinterest', 'x', … — the site's name without its TLD."""
    return site_domain(host).split(".")[0]


def url_site(url: str) -> str:
    return site_id(urllib.parse.urlsplit(url).hostname or "")


def site_label(url: str) -> str:
    """'YouTube', 'Pinterest', … or the bare domain for unlisted sites."""
    host = urllib.parse.urlsplit(url).hostname or ""
    return SOURCE_LABELS.get(site_id(host), site_domain(host))


# gallery-dl catch-alls: bare file URLs on any host, and any-page scraping.
GALLERY_SKIP = frozenset({"directlink", "generic"})
# yt-dlp only sees the video posts on these and fails (or lists a partial
# board) on images, so gallery-dl goes first.
GALLERY_FIRST = frozenset({"pinterest", "imgur"})


@functools.lru_cache(maxsize=1024)
def gallery_extractor(url: str) -> str | None:
    """gallery-dl category for `url` ('pinterest', 'deviantart', …), or None.
    Local regex match, no network."""
    if gallery_extractors is None:
        return None
    ex = gallery_extractors.find(url)
    return None if ex is None or ex.category in GALLERY_SKIP else ex.category


@functools.lru_cache(maxsize=1024)
def ytdlp_extractor(url: str) -> str | None:
    """Key of the extractor yt-dlp would pick for `url`, or None when only
    its Generic page scraper would try. Local regex match, no network."""
    if gen_extractor_classes is None:
        return None
    for ie in gen_extractor_classes():
        if ie.suitable(url):
            key = ie.ie_key()
            return None if key == "Generic" else key
    return None


def resolve_source(url: str) -> dict:
    """{id, label, engines} for a validated URL. Raises ValueError when no
    engine supports it. No network."""
    host = urllib.parse.urlsplit(url).hostname or ""
    sid = site_id(host)
    if sid == "youtube":
        kind = classify_youtube_url(url)
        if kind == "channel":
            raise ValueError(
                "Channel pages aren't supported — they can be thousands of videos. "
                "Paste a video or playlist link."
            )
        if kind == "other":
            raise ValueError(
                "That YouTube page isn't a video or playlist. "
                "Paste a watch, shorts, or playlist link."
            )
    yt = ytdlp_extractor(url)
    if yt == "KnownDRM":
        raise ValueError(
            "That service is DRM-protected (Spotify, Netflix, Disney+, …). "
            "offtube can't download from it."
        )
    engines = [name for name, hit in (("yt-dlp", yt), ("gallery-dl", gallery_extractor(url))) if hit]
    if not engines:
        raise ValueError(
            f"offtube doesn't support that {site_domain(host)} link. Paste a "
            "specific video, post, board, track or playlist — YouTube, TikTok, "
            "Instagram, Pinterest, X, Reddit, SoundCloud and 2,000+ more sites work."
        )
    if sid in GALLERY_FIRST:
        engines.reverse()
    return {"id": sid, "label": site_label(url), "engines": engines}


def check_url(url: str | None) -> tuple[str, dict]:
    """validate_url + resolve_source: (normalized url, source)."""
    u = validate_url(url)
    return u, resolve_source(u)


def safe_child(base: Path, name: str) -> Path | None:
    """Resolve `name` strictly under `base`. Returns None on traversal attempts."""
    candidate = (base / name).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError:
        return None
    if candidate.name.startswith("."):
        return None
    return candidate


# The library is downloads/'s regular files: never dotfiles (.gitkeep, the
# private .gif-* folders), in-progress temps, symlinks or special files.
# Listing, serving and deleting share this one definition, so a listed name
# is exactly what /files/ serves and nothing else is reachable.
LIBRARY_PAGE_MAX = 500
LIBRARY_KINDS = ("all", "video", "audio", "image", "gif")


def library_name(name) -> str | None:
    """`name` if it can name a kept file: one path component, taken verbatim."""
    if not isinstance(name, str) or not name or name.startswith(".") or "\x00" in name:
        return None
    if any(sep in name for sep in (os.sep, os.altsep) if sep) or is_temp_file(name):
        return None
    return name


def library_snapshot() -> list[tuple[str, os.stat_result]]:
    """Every kept file, newest first, from one pass with one lstat per entry.
    A file that vanishes mid-scan (deleted, or renamed by an engine) is skipped."""
    found: list[tuple[str, os.stat_result]] = []
    with os.scandir(DOWNLOAD_DIR) as entries:
        for entry in entries:
            if library_name(entry.name) is None:
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                found.append((entry.name, info))
    found.sort(key=lambda item: (-item[1].st_mtime, item[0]))
    return found


def library_page(query: str = "", kind: str = "all", offset: int = 0,
                 limit: int = LIBRARY_PAGE_MAX) -> dict:
    """One page of the library, filtered by a name fragment and a kind, with
    the library's total and how many files match. Searching here, not in the
    client, reaches every file however large the library grows."""
    snapshot = library_snapshot()
    needle = query.casefold()
    matched = [(name, info) for name, info in snapshot
               if needle in name.casefold()
               and (kind == "all" or (name.lower().endswith(".gif") if kind == "gif"
                                      else file_kind(name) == kind))]
    return {
        "files": [{
            "name": name,
            "size": info.st_size,
            "size_str": format_bytes(info.st_size) or "?",
            "modified": _dt.datetime.fromtimestamp(info.st_mtime).isoformat(timespec="seconds"),
            "url": f"/files/{urllib.parse.quote(name)}",
            "kind": file_kind(name),
        } for name, info in matched[offset:offset + limit]],
        "total": len(snapshot),
        "matched": len(matched),
    }


def open_library_file(name) -> tuple[io.BufferedReader, os.stat_result] | None:
    """A kept file opened for reading plus its fstat, or None. The descriptor
    is checked rather than the path: open refuses a symlink (O_NOFOLLOW) and
    never blocks on a FIFO (O_NONBLOCK), and fstat refuses anything but a
    regular file, so the bytes and length served belong to one file."""
    name = library_name(name)
    if name is None:
        return None
    path = DOWNLOAD_DIR / name
    if not hasattr(os, "O_NOFOLLOW") and path.is_symlink():
        return None
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_BINARY", 0))
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    handle = os.fdopen(fd, "rb")
    try:
        info = os.fstat(fd)
        if stat.S_ISREG(info.st_mode):
            return handle, info
    except OSError:
        pass
    handle.close()
    return None


def byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """The one byte range a Range header asks of a `size`-byte file, as an
    inclusive (first, last), or None to send it whole: no header, an empty
    file, several ranges or a malformed one (RFC 9110 lets a server ignore
    those). Raises HttpError(416) for a range that starts past the end."""
    match = re.fullmatch(r"\s*bytes\s*=\s*(\d{0,20})\s*-\s*(\d{0,20})\s*", header or "", re.IGNORECASE)
    if not match or size <= 0 or not (match[1] or match[2]):
        return None
    if not match[1]:  # a suffix: the final N bytes
        if int(match[2]) == 0:
            raise HttpError(416, "Range not satisfiable.")
        return max(0, size - int(match[2])), size - 1
    first = int(match[1])
    if match[2] and int(match[2]) < first:
        return None  # an invalid spec, not an unsatisfiable one
    if first >= size:
        raise HttpError(416, "Range not satisfiable.")
    return first, min(int(match[2]), size - 1) if match[2] else size - 1


def delete_library_file(name) -> str | None:
    """Unlink a kept file and return its name, or None if there's no such
    file. The entry itself is checked (lstat), so deleting a symlink can
    never resolve into deleting the file it points at."""
    name = library_name(name)
    if name is None:
        return None
    path = DOWNLOAD_DIR / name
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
    except FileNotFoundError:
        return None
    path.unlink()
    return name


def clean_text(value, limit: int = 2000) -> str:
    """Strip ANSI codes, carriage returns and C0 controls so text is JSON-safe
    and displays cleanly in the UI log."""
    s = "" if value is None else str(value)
    s = ANSI_RE.sub("", s)
    s = "".join(ch for ch in s if ch in ("\n", "\t") or ord(ch) >= 32)
    if len(s) > limit:
        s = s[-limit:]
    return s


def quality_to_format(quality: str | None) -> str:
    """Map UI quality id to a yt-dlp format string. Any numeric height works."""
    q = (quality or "best").strip().lower()
    if q in QUALITY_FORMATS:
        return QUALITY_FORMATS[q]
    if re.fullmatch(r"\d{3,4}", q or ""):
        h = int(q)
        return f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b"
    return QUALITY_FORMATS["best"]


def parse_time_to_seconds(value: str | None) -> float | None:
    """Accept '90', '1:30', '01:30', '1:02:03', '1:02:03.5'. Returns seconds or None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # plain number = seconds
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s) if math.isfinite(float(s)) else None
    if not re.fullmatch(r"\d+(?::\d+){1,2}(?:\.\d+)?", s):
        return None
    parts = s.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if len(nums) > 3 or any(n < 0 for n in nums):
        return None
    total = 0.0
    for n in nums:
        total = total * 60 + n
    return total if math.isfinite(total) else None


def format_seconds(sec: float | None) -> str:
    """'1:02:03' for a duration; '-' for anything that isn't a sane number
    (flat playlist entries skip yt-dlp's numeric checks)."""
    if isinstance(sec, bool) or not isinstance(sec, (int, float)) or not math.isfinite(sec) or sec < 0:
        return "-"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_bytes(n: float | None) -> str | None:
    if not n:
        return None
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return None  # unreachable


def cookie_file_for(job_id: str | None) -> Path:
    """Per-job cookie file (avoids concurrent jobs clobbering each other).

    Falls back to the shared session file when no job id is given (e.g. the
    pre-download validation pass, which never launches yt-dlp).
    """
    if job_id:
        safe = re.sub(r"[^A-Za-z0-9_-]", "", job_id)[:32] or "job"
        return COOKIES_DIR / f"job-{safe}.cookies.txt"
    return SESSION_COOKIE_FILE


def cleanup_job_cookies(job_id: str) -> None:
    try:
        cookie_file_for(job_id).unlink(missing_ok=True)
    except OSError:
        pass


def _job_claims_name(job: dict, name: str) -> bool:
    """True if this job's hook-observed names/ids match a temp `name`."""
    seen = set(job.get("seen_files") or [])
    if name in seen:
        return True
    stem = Path(name).stem
    if any(Path(n).stem == stem for n in seen if n):
        return True
    ids = job.get("seen_ids") or []
    return bool(ids) and any(f"[{vid}]" in name for vid in ids)


def cleanup_job_temps(job: dict | None) -> None:
    """Remove .part/.temp files this job was seen writing. Called on any exit.

    A temp file another live job is also writing is left alone: two jobs can
    legitimately claim the same stem/id (the same video queued twice, or a
    playlist containing an in-flight video), and unlinking it mid-write would
    corrupt that download.
    """
    if not job:
        return
    try:
        with jobs_lock:
            others = [j for j in jobs.values()
                      if j.get("id") != job.get("id") and not is_terminal(j)]
        for p in DOWNLOAD_DIR.iterdir():
            if not p.is_file() or not is_temp_file(p.name):
                continue
            if not _job_claims_name(job, p.name):
                continue
            if any(_job_claims_name(o, p.name) for o in others):
                continue
            p.unlink(missing_ok=True)
    except OSError:
        pass


# Full-browser exports carry MBs of unrelated sites (bank, mail, ...), so a
# paste is trimmed to the target link's site before it touches disk.
# YouTube also signs in on Google's domains.
COOKIE_SIBLINGS = {"youtube": ("google", "googlevideo")}

NETSCAPE_MAGIC = "# Netscape HTTP Cookie File"


# YouTube answers some sessions with "Sign in to confirm you're not a bot" /
# "The page needs to be reloaded" on one player-client family but accepts
# another. When extraction fails with one of these markers, offtube retries
# once with alternate cookie-capable clients (none of these are in the
# authed defaults web_embedded/tv_downgraded/web, so the retry is never a
# repeat of the same request).
SESSION_FAILURE_MARKERS = ("not a bot", "sign in to confirm", "needs to be reloaded")
FALLBACK_PLAYER_CLIENTS = ["web_creator", "tv", "mweb"]


def _is_session_failure(msg: str | None) -> bool:
    low = (msg or "").lower()
    return any(m in low for m in SESSION_FAILURE_MARKERS)


# Engine errors that mean "this needs a signed-in session". yt-dlp phrases
# it per site ("login required", "Use --cookies…", "private"), so match
# the shared vocabulary rather than each site's sentence.
LOGIN_MARKERS = ("--cookies", "login", "log in", "sign in", "private",
                 "authenticat", "age-restricted", "inappropriate for some users")


def plain_reason(msg: str) -> str:
    """The site's own reason, without the engine's framing:
    'ERROR: [youtube] jNQXAC9IVRw: Private video. Sign in if…' → 'Private video.'"""
    body, prev = (msg or "").strip(), None
    while body != prev:  # joined engine errors stack prefixes: "yt-dlp: ERROR: [x] id: …"
        prev = body
        body = re.sub(r"^(ERROR:\s*)?(\[[^\]]+\]\s*)?([\w-]+:\s+)?", "", body)
    return re.split(r"(?<=[.!?])\s", body, maxsplit=1)[0][:160]


def failure_message(msg: str, label: str, prefix: str = "", logged: bool = False) -> str:
    """What the UI shows for an engine error: the site's own reason plus a
    plain-language next step when there is one. Without a hint, the raw
    error is all we know — shown whole unless it's already in a job log
    (`logged`), where the reason alone is enough."""
    hint = failure_hint(msg, label)
    if hint:
        return clean_text(f"{plain_reason(msg)} {hint}", 1000)
    return clean_text(f"{prefix}{plain_reason(msg) if logged else msg}", 1000)


def failure_hint(msg: str, label: str) -> str:
    """One plain-language next step for an engine error, or ''."""
    low = (msg or "").lower()
    if "join this channel" in low or "members-only" in low:
        return ("This looks like a members-only video. Use cookies from an account "
                "that can already watch it, re-export them (they expire), and check "
                "the membership tier.")
    if "not a bot" in low or "sign in to confirm" in low:
        return ("YouTube asked for a bot-check sign-in (it does this for public "
                "videos too). Add cookies and retry: upload a cookies.txt export, "
                "or run natively with Sign-in → Browser (Docker can't read your "
                "host browser, so use the file there).")
    if "needs to be reloaded" in low:
        return ("YouTube rejected the session. Re-export fresh cookies while on "
                "youtube.com, logged into a single account, then retry. "
                "Full-browser exports go stale fastest.")
    if any(m in low for m in LOGIN_MARKERS):
        return (f"{label} only shows this to a signed-in account. Open Sign-in and "
                f"pick Browser (native server) or a cookies file exported while "
                f"signed in to {label}.")
    if any(m in low for m in ("your country", "your location", "geo restrict", "geo-restrict")):
        return f"{label} region-locks this; it isn't available where offtube is running."
    if "cookies" in low:
        return "Your cookies may be expired — re-export cookies.txt and try again."
    if "ffmpeg" in low:
        return "ffmpeg is required for merging/trimming — install it and restart."
    return ""


HTTPONLY_PREFIX = "#HttpOnly_"


def _cookie_domain_from_row(line: str) -> str | None:
    """Domain of a Netscape cookie row, or None if this isn't a cookie row.

    HttpOnly cookies are stored as '#HttpOnly_.example.com\\t...' — they look
    like comments but must be filtered by domain the same as normal rows.
    """
    stripped = line.strip()
    if not stripped:
        return None
    if stripped.startswith(HTTPONLY_PREFIX):
        fields = stripped[len(HTTPONLY_PREFIX):].split("\t")
    elif stripped.startswith("#"):
        return None
    else:
        fields = line.split("\t")
    if len(fields) < 6:
        return None
    return fields[0].strip().lower().lstrip(".")


def _cookie_domain_kept(domain: str, site: str) -> bool:
    if domain == "":
        return True
    sid = site_id(domain)
    return sid == site or sid in COOKIE_SIBLINGS.get(site, ())


def looks_like_cookie_export(text: str) -> bool:
    """True if the paste contains tab-separated cookie rows (incl. #HttpOnly_)."""
    try:
        return any(_cookie_domain_from_row(ln) is not None for ln in text.splitlines())
    except Exception:
        return False


def filter_cookies_text(text: str, site: str) -> str:
    """Trim a cookies.txt paste to one site's domains (site_id, e.g. 'x').

    Keeps real comment lines, blank lines, and that site's cookie rows
    (including #HttpOnly_ rows and COOKIE_SIBLINGS). Drops other sites'
    cookies — including their HttpOnly rows, which used to sneak through as
    comments. Prepends the Netscape magic header when rows exist but it's
    missing. Non-exports pass through byte-identical.
    """
    if not looks_like_cookie_export(text):
        return text
    kept: list[str] = []
    has_magic = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        domain = _cookie_domain_from_row(line)
        if domain is None:
            if "Netscape HTTP Cookie File" in stripped:
                has_magic = True
            kept.append(line)
            continue
        if _cookie_domain_kept(domain, site):
            kept.append(line)
    if not has_magic:
        kept.insert(0, NETSCAPE_MAGIC)
    return "\n".join(kept) + "\n"


def cleanup_stale_files() -> None:
    """Remove orphaned per-job cookies and partial downloads from kills."""
    try:
        for p in COOKIES_DIR.glob("job-*.cookies.txt"):
            try:
                p.unlink()
            except OSError:
                pass
    except OSError:
        pass
    try:
        for p in DOWNLOAD_DIR.iterdir():
            if p.name == ".gitkeep":
                continue
            if p.is_dir() and not p.is_symlink() and re.fullmatch(r"\.gif-[0-9a-f]{12}-.+", p.name):
                shutil.rmtree(p, ignore_errors=True)
            elif p.is_file() and is_temp_file(p.name):
                try:
                    p.unlink()
                except OSError:
                    pass
    except OSError:
        pass


def write_cookie_file(target: Path, text: str) -> None:
    """Publish a complete, owner-only cookie file without following target symlinks."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix="job-write-", suffix=".cookies.txt",
                                         delete=False) as cookie_file:
            temporary = Path(cookie_file.name)
            cookie_file.write(text)
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def resolve_cookies(payload: dict, job_id: str | None = None, persist: bool = True) -> dict:
    """Return yt-dlp cookie opts from UI payload. Raises ValueError on bad input.

    persist=False only validates (used before a job exists, so one-off pastes
    don't pollute the shared session cookie file).
    """
    mode = (payload.get("cookies_mode") or "none").lower()
    opts: dict = {}
    if mode == "browser":
        browser = (payload.get("cookies_browser") or "chrome").strip().lower()
        allowed = {"chrome", "chromium", "edge", "firefox", "safari", "opera", "brave", "vivaldi"}
        if browser not in allowed:
            raise ValueError(f"Unsupported browser '{browser}'. Use one of: {', '.join(sorted(allowed))}.")
        profile = (payload.get("cookies_browser_profile") or "").strip() or None
        if profile and (len(profile) > MAX_PROFILE_LEN or any(ord(ch) < 32 for ch in profile)):
            raise ValueError("That browser profile name doesn't look right.")
        # yt-dlp python API expects a tuple: (browser, profile, keyring, container)
        opts["cookiesfrombrowser"] = (browser, profile, None, None)
        return opts
    if mode in ("upload", "text", "file"):
        text = payload.get("cookies_text") or ""
        try:
            if not text and SESSION_COOKIE_FILE.is_file():
                if SESSION_COOKIE_FILE.stat().st_size > MAX_COOKIES_TEXT:
                    raise ValueError("Saved cookies file is too large (max ~1 MB). Export just this site's cookies.")
                text = SESSION_COOKIE_FILE.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError("Could not read saved cookies. Check file permissions and export a UTF-8 cookies.txt file.") from exc
        if len(text) > MAX_COOKIES_TEXT:
            raise ValueError("Cookies paste is too large (max ~1 MB). Export just this site's cookies instead of your whole browser.")
        try:
            site = url_site(validate_url(payload.get("url")))
        except ValueError:
            site = ""  # no link yet: nothing to trim against
        if text:
            if site and looks_like_cookie_export(text):
                text = filter_cookies_text(text, site)
                if not any(ln.strip() and not ln.strip().startswith("#")
                           for ln in text.splitlines()):
                    label = SOURCE_LABELS.get(site, site)
                    raise ValueError(f"No {label} cookies found in that file. Export while signed in to {label} (single account), then retry.")
            target = cookie_file_for(job_id)
            if persist:
                write_cookie_file(target, text)
            opts["cookiefile"] = str(target)
            return opts
        raise ValueError("No cookies provided. Upload a cookies.txt file or paste its contents.")
    return opts


# yt-dlp rejects Node below 23.5 for YouTube's JS challenges. Passing an
# unsupported node in js_runtimes just produces "unsupported runtime" noise.
MIN_NODE = (23, 5)
_js_runtime_cache: dict = {"at": 0.0, "runtimes": None, "node_version": None}


def node_meets_yt_dlp(version: str | None) -> bool:
    m = re.search(r"(\d+)\.(\d+)", version or "")
    if not m:
        return False
    return (int(m.group(1)), int(m.group(2))) >= MIN_NODE


def _cmd_version(argv: list[str]) -> str | None:
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    text = (out.stdout or out.stderr or "").strip()
    return text.splitlines()[0][:80] if text else None


def detect_js_runtimes() -> dict:
    """Installed JS runtimes yt-dlp can actually use. Cached 60s."""
    now = time.monotonic()
    if _js_runtime_cache["runtimes"] is not None and now - _js_runtime_cache["at"] < 60:
        return _js_runtime_cache["runtimes"]
    runtimes: dict = {}
    if shutil.which("deno"):
        runtimes["deno"] = {}
    if shutil.which("node"):
        ver = _cmd_version(["node", "-v"])
        _js_runtime_cache["node_version"] = ver
        if node_meets_yt_dlp(ver):
            runtimes["node"] = {}
    else:
        _js_runtime_cache["node_version"] = None
    _js_runtime_cache.update(at=now, runtimes=runtimes)
    return runtimes


def base_ydl_opts(payload: dict | None = None, job_id: str | None = None,
                 persist: bool = True, player_clients: list[str] | None = None) -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False,
        "socket_timeout": 30,
        "retries": 3,
    }
    # YouTube PO-token/JS challenges need a JS runtime AND the ejs remote
    # component. yt-dlp enables only deno by default, so a supported node
    # sits unused unless requested explicitly. Never pass Node < 23.5.
    js_runtimes = detect_js_runtimes()
    if js_runtimes:
        opts["js_runtimes"] = js_runtimes
        opts["remote_components"] = ["ejs:npm"]
    if player_clients:
        opts["extractor_args"] = {"youtube": {"player_client": list(player_clients)}}
    if payload:
        try:
            opts.update(resolve_cookies(payload, job_id, persist=persist))
        except ValueError:
            raise
        except Exception as exc:  # pragma: no cover
            raise ValueError(f"Cookie error: {exc}")
    return opts


def summarize_formats(info: dict) -> list[dict]:
    """Reduce yt-dlp formats list to a compact quality ladder for the UI."""
    heights: dict[int, dict] = {}
    has_audio = False
    for f in info.get("formats") or []:
        vcodec = f.get("vcodec") or ""
        acodec = f.get("acodec") or ""
        h = f.get("height")
        ext = f.get("ext") or ""
        if vcodec != "none" and h:
            cur = heights.get(int(h))
            filesize = f.get("filesize") or f.get("filesize_approx")
            if cur is None or (filesize and filesize > (cur.get("filesize") or 0)):
                heights[int(h)] = {
                    "height": int(h),
                    "ext": ext,
                    "fps": f.get("fps"),
                    "filesize": filesize,
                    "format_id": f.get("format_id"),
                }
        if acodec != "none":
            has_audio = True
    ladder = sorted(heights.values(), key=lambda x: -x["height"])
    out = [{"id": "best", "label": "Best available", "height": None, "filesize": None, "filesize_str": None}]
    for q in ladder:
        h = q["height"]
        size = q["filesize"]
        size_str = format_bytes(size)
        label = f"{h}p" + (f" ~{size_str}" if size_str else "")
        out.append({"id": str(h), "label": label, "height": h,
                    "filesize": size, "filesize_str": size_str})
    if has_audio:
        out.append({"id": "audio_mp3", "label": "Audio only (MP3)", "height": None})
        out.append({"id": "audio_m4a", "label": "Audio only (M4A)", "height": None})
    return out


info_sema = threading.Semaphore(2)


def _inspect_opts(payload: dict, cookie_id: str, url: str,
                  player_clients: list[str] | None = None) -> dict:
    opts = base_ydl_opts(payload, cookie_id, player_clients=player_clients)
    opts["skip_download"] = True
    # Collections inspect flat (first 50) so a 2,000-video profile answers
    # fast; a no-op for single videos.
    opts["extract_flat"] = "in_playlist"
    opts["playlistend"] = 50
    # watch?v=…&list=… is a video page that happens to mention a playlist.
    # Inspect it as a single video so we don't auto-queue 200 mix items.
    # Only /playlist?list= extracts as a playlist. Other sites' pure
    # collection URLs ignore noplaylist and still list.
    opts["noplaylist"] = not (url_site(url) == "youtube"
                              and classify_youtube_url(url) == "playlist")
    return opts


def entry_thumbnail(entry: dict) -> str | None:
    """A playlist entry's thumbnail. Flat listings (YouTube, TikTok) often
    carry only a `thumbnails` list, smallest first; take the largest."""
    if entry.get("thumbnail"):
        return display_thumbnail(entry["thumbnail"])
    thumbs = [t for t in entry.get("thumbnails") or [] if isinstance(t, dict) and t.get("url")]
    return display_thumbnail(thumbs[-1]["url"]) if thumbs else None


def _engine_error(exc: Exception) -> str:
    """An engine's error without yt-dlp's 'ERROR:' prefix and 'please report
    this issue' tail."""
    msg = clean_text(str(exc), 400).split("; please report this issue")[0]
    return msg.removeprefix("ERROR: ")


def validate_media_payload(payload: dict) -> None:
    """Check JSON field types before an engine or queue sees the request."""
    text_fields = (
        "url", "quality", "clip_from", "clip_to", "playlist_mode", "playlist_items",
        "cookies_mode", "cookies_browser", "cookies_browser_profile", "cookies_text",
        "sub_lang", "title", "thumbnail",
    )
    for field in text_fields:
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{field} must be text.")
    for field in ("subtitles", "auto_subs"):
        value = payload.get(field)
        if value is not None and type(value) is not bool:
            raise ValueError(f"{field} must be true or false.")
    for field in ("playlist_start", "playlist_end"):
        value = payload.get(field)
        if value is not None and type(value) not in (str, int):
            raise ValueError(f"{field} must be an integer or text.")
    for field, allowed, default in (
        ("cookies_mode", ("none", "browser", "upload", "text", "file"), "none"),
        ("playlist_mode", ("single", "playlist"), "single"),
    ):
        if (payload.get(field) or default).lower() not in allowed:
            raise ValueError(f"{field} must be one of: {', '.join(allowed)}.")
    # Exact ids: "GIF" would pick GIF's stream format but skip its encoding.
    quality = payload.get("quality")
    if quality and quality not in QUALITY_FORMATS and not re.fullmatch(r"[1-9]\d{2,3}", quality):
        raise ValueError("quality must be best, a height such as 720, audio_mp3, audio_m4a or gif.")


def fetch_info(url: str, payload: dict) -> dict:
    """Inspect `url` with each engine that supports it, in order, and return
    the first answer, tagged with its source and engine."""
    validate_media_payload(payload)
    url, src = check_url(url)
    if not info_sema.acquire(timeout=60):
        raise RuntimeError("Too many inspect requests. Wait a moment and retry.")
    # Inspect must be side-effect-free: pasted cookies go to an ephemeral
    # per-request file, never to the shared session file or a predictable
    # "validate" path. Cleaned up even if an engine raises.
    cookie_id = f"info-{uuid.uuid4().hex[:12]}"
    errors: list[tuple[str, Exception]] = []
    try:
        for engine in src["engines"]:
            inspect = inspect_gallery if engine == "gallery-dl" else inspect_ytdlp
            try:
                info = inspect(url, payload, src, cookie_id)
                break
            except ValueError:
                raise  # bad cookies/input: no other engine will do better
            except Exception as exc:
                errors.append((engine, exc))
        else:
            if len(errors) == 1:
                raise errors[0][1]
            raise RuntimeError(" · ".join(f"{e}: {_engine_error(x)}" for e, x in errors))
    finally:
        info_sema.release()
        cleanup_job_cookies(cookie_id)
    info["source"] = {"id": src["id"], "label": src["label"]}
    info["engine"] = engine
    return info


def inspect_ytdlp(url: str, payload: dict, src: dict, cookie_id: str) -> dict:
    if yt_dlp is None:
        raise RuntimeError("yt-dlp is not installed. Run: pip install -r requirements.txt")
    youtube = src["id"] == "youtube"
    try:
        with yt_dlp.YoutubeDL(_inspect_opts(payload, cookie_id, url)) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        if not youtube or not _is_session_failure(str(exc)):
            raise
        # Same session, different player clients — never a blind repeat.
        fb_opts = _inspect_opts(payload, cookie_id, url,
                                player_clients=FALLBACK_PLAYER_CLIENTS)
        with yt_dlp.YoutubeDL(fb_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    if info is None:
        raise RuntimeError("Could not extract info for that URL.")
    list_id = None
    if youtube:
        list_id = (urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("list") or [None])[0]
    if info.get("_type") == "playlist":
        entries = []
        for e in (info.get("entries") or [])[:50]:
            if not e:
                continue
            vid = e.get("id")
            entry_url = e.get("url") or e.get("webpage_url")
            if not entry_url and vid and youtube:
                entry_url = f"https://www.youtube.com/watch?v={vid}"
            entries.append(
                {
                    "id": vid,
                    "title": e.get("title"),
                    "url": entry_url,
                    "duration": e.get("duration"),
                    "duration_str": format_seconds(e.get("duration")),
                    "thumbnail": entry_thumbnail(e),
                }
            )
        # Not among yt-dlp's sanitized numeric fields: an extractor can put
        # anything here, and clients print it.
        count = info.get("playlist_count")
        return {
            "type": "playlist",
            "title": info.get("title"),
            "uploader": info.get("uploader"),
            "count": count if type(count) is int and count > 0 else len(entries),
            "entries": entries,
            "thumbnail": (entries[0].get("thumbnail") if entries else info.get("thumbnail")),
            "webpage_url": info.get("webpage_url") or url,
        }
    qualities = summarize_formats(info)
    audio_only = not any(q["height"] for q in qualities) and any(
        q["id"].startswith("audio") for q in qualities)
    return {
        "type": "video",
        "media": "audio" if audio_only else "video",
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "duration_str": format_seconds(info.get("duration")),
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url") or url,
        "is_live": bool(info.get("is_live")),
        "live_status": info.get("live_status") if isinstance(info.get("live_status"), str) else None,
        "qualities": qualities,
        "subtitles": sorted(list((info.get("subtitles") or {}).keys()))[:12],
        "automatic_captions": sorted(list((info.get("automatic_captions") or {}).keys()))[:12],
        "part_of_playlist": bool(list_id or info.get("playlist") or info.get("playlist_id")),
        "playlist_id": list_id or info.get("playlist_id"),
    }


# ------------------------------- child processes -------------------------------
# Every child offtube starts leads its own process group, so stopping a job
# stops everything under it: gallery-dl's ffmpeg for an HLS pin, the GIF
# source worker's ffmpeg for a range. A terminal's Ctrl+C therefore no
# longer reaches them by itself; shutdown stops each job's tree instead.

OWN_PROCESS_GROUP = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                     else {"start_new_session": True})


def stop_process_tree(proc: subprocess.Popen, wait: float | None = 5) -> None:
    """Kill a child started with OWN_PROCESS_GROUP and everything it spawned.
    With wait=None it only signals, so it is safe under jobs_lock; the
    thread that owns `proc` reaps it."""
    if proc.pid <= 0:
        return  # killpg(0) would mean our own group
    if os.name == "nt":
        kill = ["taskkill", "/PID", str(proc.pid), "/T", "/F"]
        quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if wait is None:
            subprocess.Popen(kill, **quiet)
        else:
            subprocess.run(kill, timeout=5, **quiet)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass  # the group is gone, or holds only zombies (macOS: EPERM)
    if wait is not None:
        proc.wait(timeout=wait)


def job_must_stop_locked(job: dict) -> bool:
    """True once a job is cancelled or the disk runs low (which also marks it
    disk_full). Call with jobs_lock held."""
    if job.get("cancel_requested"):
        return True
    if disk_low_locked(job):
        job["disk_full"] = True
        return True
    return False


def watch_job_process(job: dict, proc: subprocess.Popen, done: threading.Event) -> None:
    """Stop `proc`'s tree once its job must stop, checked on a timer: a child
    busy with one large file can be silent for minutes, so this can't wait
    for its next line of output."""
    while not done.wait(0.25):
        with jobs_lock:
            stop = job_must_stop_locked(job)
        if stop:
            stop_process_tree(proc, wait=None)
            return


# ------------------------------- gallery-dl engine -------------------------------
# Runs as a subprocess, not via the Python API: gallery-dl's config is
# process-global, so two jobs with different cookies would trample each
# other. A process is also the one thing that cancels instantly.

GALLERY_INSPECT_LIMIT = 50
VIDEO_EXTS = frozenset({"mp4", "webm", "mov", "m4v", "mkv"})


def gallery_cmd(payload: dict, cookie_id: str) -> list[str]:
    """gallery-dl argv prefix: ignores the user's own gallery-dl config,
    never prompts, skips text-only 'files' (story text, captions), and
    carries the request's cookies."""
    cmd = [sys.executable, "-m", "gallery_dl", "--config-ignore", "--no-input",
           "--no-colors", "--filter", "extension != 'txt'"]
    opts = resolve_cookies(payload, cookie_id)
    if "cookiefile" in opts:
        cmd += ["--cookies", opts["cookiefile"]]
    elif "cookiesfrombrowser" in opts:
        browser, profile = opts["cookiesfrombrowser"][:2]
        cmd += ["--cookies-from-browser", browser + (f":{profile}" if profile else "")]
    return cmd


def gallery_range_args(payload: dict) -> list[str]:
    """The playlist range controls, as gallery-dl --range (same syntax)."""
    if (payload.get("playlist_mode") or "single").lower() == "single":
        return []
    items = (payload.get("playlist_items") or "").strip().replace(";", ",")
    if items:
        return ["--range", items]
    start, end = payload.get("playlist_start"), payload.get("playlist_end")
    if start or end:
        return ["--range", f"{int(start or 1)}-{int(end) if end else ''}"]
    return []


def _gallery_text(meta: dict, keys: tuple[str, ...]) -> str | None:
    """First non-empty string among `keys`; dict values yield their name."""
    for k in keys:
        v = meta.get(k)
        if isinstance(v, dict):
            v = v.get("full_name") or v.get("name") or v.get("username") or v.get("title")
        if isinstance(v, str) and v.strip():
            return clean_text(v.strip(), 200)
    return None


def _gallery_thumb(file_url: str, meta: dict) -> str | None:
    if file_url.startswith(("http://", "https://")) and \
            str(meta.get("extension") or "").lower() not in VIDEO_EXTS:
        return file_url
    for k in ("thumbnail", "image_medium_url", "preview"):
        v = meta.get(k)
        if isinstance(v, str) and v.startswith(("http://", "https://")):
            return v
    return None


def inspect_gallery(url: str, payload: dict, src: dict, cookie_id: str) -> dict:
    """List the first files at `url` (gallery-dl -j) as a 'gallery' info."""
    cmd = gallery_cmd(payload, cookie_id) + [
        "-j", "--range", f"1-{GALLERY_INSPECT_LIMIT}", "--", url]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=90)
    except subprocess.TimeoutExpired:
        raise RuntimeError("gallery-dl took too long to list that link.")
    try:
        messages = json.loads(out.stdout or "[]")
    except ValueError:
        messages = []
    # -j emits [type, ...]: 3 = file [3, url, meta], -1 = error [-1, {message}].
    files = [(m[1], m[2]) for m in messages
             if m and m[0] == 3 and len(m) > 2 and isinstance(m[2], dict)
             and isinstance(m[1], str) and not m[1].startswith("text:")]
    if not files:
        errors = [m[1].get("message") for m in messages
                  if m and m[0] == -1 and isinstance(m[1], dict)]
        tail = (out.stderr or "").strip().splitlines()
        raise RuntimeError(next((e for e in errors if e), None)
                           or (tail[-1] if tail else "Nothing downloadable at that link."))
    entries = []
    for file_url, meta in files:
        ext = str(meta.get("extension") or "").lower()
        entries.append({
            "title": _gallery_text(meta, ("title", "grid_title", "seo_title", "description",
                                          "content", "caption", "alt_text")),
            "thumbnail": _gallery_thumb(file_url, meta),
            "ext": ext,
            "is_video": ext in VIDEO_EXTS,
        })
    videos = sum(e["is_video"] for e in entries)
    first = files[0][1]
    one_post = len({str(m.get("id")) for _, m in files}) == 1
    title = (entries[0]["title"] if one_post else
             _gallery_text(first, ("board", "album", "gallery", "collection"))
             or f"{src['label']} {first.get('subcategory') or 'collection'}")
    return {
        "type": "gallery",
        "media": "video" if videos == len(entries) else ("image" if not videos else "mixed"),
        "title": title or f"{src['label']} post",
        "uploader": _gallery_text(first, ("author", "user", "owner", "pinner", "uploader", "artist", "username")),
        "count": len(entries),
        "more": len(entries) >= GALLERY_INSPECT_LIMIT,
        "thumbnail": next((e["thumbnail"] for e in entries if e["thumbnail"]), None),
        "entries": entries,
        "webpage_url": url,
    }


def run_gallery(job: dict, payload: dict, url: str) -> list[str]:
    """Download with gallery-dl; returns the names it saved (or already had)."""
    destination = Path(job.get("gif_dir") or DOWNLOAD_DIR)
    video_filter = (["--filter", f"extension in {tuple(sorted(VIDEO_EXTS))!r}"]
                    if payload.get("quality") == "gif" else [])
    cmd = gallery_cmd(payload, job["id"]) + video_filter + [
        "-D", str(destination), *gallery_range_args(payload), "--", url]
    # Unbuffered: a piped child would otherwise hold its lines until exit.
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            env={**os.environ, "PYTHONUNBUFFERED": "1"}, **OWN_PROCESS_GROUP)
    done = threading.Event()
    watchdog = threading.Thread(target=watch_job_process, args=(job, proc, done),
                                name=f"watch-{job['id']}", daemon=True)
    with jobs_lock:
        job["proc"] = proc  # internal: /api/cancel and shutdown stop its tree
        job["status"] = "downloading"
        job["detail"] = "Fetching…"
    watchdog.start()
    root = destination.resolve()
    files: list[str] = []
    errors: list[str] = []
    try:
        # stdout: one absolute path per saved file, "# path" when it already
        # existed; everything else is gallery-dl's log ("[site][error] …").
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            path = Path(line[2:] if line.startswith("# ") else line)
            with jobs_lock:
                if path.is_absolute() and path.parent.resolve() == root:
                    if path.name not in files:
                        files.append(path.name)
                    job["detail"] = clean_text(f"Saved {len(files)} file{'s' if len(files) != 1 else ''} — {path.name}", 500)
                elif line.strip():
                    job["log"].append(clean_text(line))
                    job["log"] = job["log"][-200:]
                    if "][error]" in line:
                        errors.append(line.split("][error]", 1)[1].strip())
                # Lines answer as fast as gallery-dl talks; the watchdog
                # covers its silences.
                if job_must_stop_locked(job):
                    stop_process_tree(proc, wait=None)
        proc.wait()
    finally:
        done.set()
        watchdog.join()
        # Finished or stopped, nothing gallery-dl started may outlive the job.
        stop_process_tree(proc)
        proc.stdout.close()
        with jobs_lock:
            job.pop("proc", None)
    if job.get("cancel_requested"):
        raise DownloadError("Cancelled by user.")
    if job.get("disk_full"):
        raise DownloadError(DISK_FULL_MSG)
    if not files:
        raise DownloadError(errors[-1] if errors else
                            ("No video found to turn into a GIF. Choose a video or reel."
                             if payload.get("quality") == "gif" else
                             f"gallery-dl saved nothing (exit {proc.returncode})."))
    if errors:
        with jobs_lock:
            job["log"].append(f"WARNING: {len(errors)} item(s) failed; kept the rest.")
    return files


def build_download_opts(job: dict, payload: dict, persist: bool = True,
                        player_clients: list[str] | None = None) -> dict:
    quality = payload.get("quality") or "best"
    fmt = quality_to_format(quality)
    # Titles on TikTok/X/Instagram are whole captions; cap the bytes so the
    # name stays under the 255-byte filesystem limit and keeps its [id].
    outtmpl = str(DOWNLOAD_DIR / "%(title).150B [%(id)s].%(ext)s")

    opts = base_ydl_opts(payload, job.get("id"), persist=persist,
                         player_clients=player_clients)
    opts.update(
        {
            "format": fmt,
            "outtmpl": outtmpl,
            "merge_output_format": "mp4",
            "writethumbnail": False,
            "writeinfojson": False,
            "continuedl": True,
            "progress_hooks": [lambda d, jid=job["id"]: progress_hook(jid, d)],
            "postprocessor_hooks": [lambda d, jid=job["id"]: postprocessor_hook(jid, d)],
            "logger": JobLogger(job["id"]),
        }
    )
    if quality == "gif":
        gif_settings(payload)
        opts["match_filter"] = lambda info, *, incomplete=False: gif_source_filter(info, payload, incomplete)
        if job.get("gif_dir"):
            opts["outtmpl"] = str(Path(job["gif_dir"]) / "%(title).150B [%(id)s].%(ext)s")
            opts["post_hooks"] = [lambda filename: convert_gif(job, Path(filename), payload, trimmed=True)]
    if quality == "audio_mp3":
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ]
    elif quality == "audio_m4a":
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "m4a", "preferredquality": "192"}
        ]

    # Clip trim (from-to) — same as --download-sections "*from-to" (needs ffmpeg).
    # NOTE: the Python API needs a callback, not a plain list (see yt-dlp __init__).
    clip_from = parse_time_to_seconds(payload.get("clip_from")) if (payload.get("clip_from") or "").strip() else None
    clip_to = parse_time_to_seconds(payload.get("clip_to")) if (payload.get("clip_to") or "").strip() else None
    if clip_to is not None and clip_to <= (clip_from or 0):
        raise ValueError("'To' must be after 'From'.")
    if clip_from is not None or clip_to is not None:
        try:
            from yt_dlp.utils import download_range_func
        except ImportError:
            raise ValueError("Trimming needs yt-dlp installed: pip install -r requirements.txt")
        start = clip_from if clip_from is not None else 0
        end = clip_to if clip_to is not None else float("inf")
        opts["download_ranges"] = download_range_func([], [(start, end)])
        opts["force_keyframes_at_cuts"] = True

    # Playlist controls
    mode = (payload.get("playlist_mode") or "single").lower()
    if mode == "single":
        opts["noplaylist"] = True
    else:
        opts["noplaylist"] = False
        opts["yes_playlist"] = True
        try:
            if payload.get("playlist_start"):
                opts["playliststart"] = int(payload["playlist_start"])
            if payload.get("playlist_end"):
                opts["playlistend"] = int(payload["playlist_end"])
        except (TypeError, ValueError):
            raise ValueError("Playlist start/end must be numbers.")
        items = (payload.get("playlist_items") or "").strip()
        if items:
            if len(items) > MAX_PLAYLIST_ITEMS_LEN:
                raise ValueError("Playlist items is too long. Example: 1-5,7,10-12")
            if not re.fullmatch(r"[\d\s,\-:;]+", items):
                raise ValueError("Playlist items looks invalid. Example: 1-5,7,10-12")
            opts["playlistitems"] = items.replace(";", ",")

    # Subtitles (optional)
    if payload.get("subtitles") and quality != "gif":
        opts["writesubtitles"] = True
        opts["writeautomaticsub"] = bool(payload.get("auto_subs"))
        lang = (payload.get("sub_lang") or "en").strip() or "en"
        if len(lang) > MAX_SUB_LANG_LEN or not re.fullmatch(r"[A-Za-z\-, ]+", lang):
            raise ValueError("Subtitle language looks invalid. Example: en or en,de.")
        opts["subtitleslangs"] = [s.strip() for s in lang.split(",") if s.strip()]
        opts["subtitlesformat"] = "srt"
        opts["embedsubtitles"] = False

    return opts


# GIF sources and both encoding passes live in a private per-job directory.
# Two passes avoid buffering a whole video in a split/palette filter graph.
GIF_SIZES = (320, 480, 640, 960)
GIF_FPS = (10, 15, 20, 25, 30)


def gif_settings(payload: dict) -> tuple[int, int, bool]:
    size, fps, loop = payload.get("gif_size", 480), payload.get("gif_fps", 15), payload.get("gif_loop", True)
    if type(size) is not int or size not in GIF_SIZES:
        raise ValueError("GIF size must be 320, 480, 640 or 960 pixels.")
    if type(fps) is not int or fps not in GIF_FPS:
        raise ValueError("GIF frame rate must be 10, 15, 20, 25 or 30 fps.")
    if type(loop) is not bool:
        raise ValueError("GIF loop must be true or false.")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise ValueError("GIF creation needs ffmpeg and ffprobe. Install ffmpeg, then restart offtube.")
    return size, fps, loop


def gif_source_filter(info: dict, payload: dict, incomplete: bool = False) -> None:
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise DownloadError("GIFs need a finished video. Wait for the live stream to end.")
    if incomplete:
        return
    duration = info.get("duration")
    start = parse_time_to_seconds(payload.get("clip_from")) or 0
    if duration and start >= duration:
        raise DownloadError("The GIF start time is past the end of this video.")


def check_job(job: dict) -> None:
    """Raise if the job was cancelled or the disk ran low; for loops that
    watch a child process rather than being called back by yt-dlp."""
    with jobs_lock:
        if job.get("cancel_requested"):
            raise DownloadError("Cancelled by user.")
        if disk_low_locked(job):
            job["disk_full"] = True
            raise DownloadError(DISK_FULL_MSG)


def ytdlp_worker(request: dict) -> None:
    """The yt-dlp side of tools/ytdlp_worker.py: one download from a JSON
    request, reported as JSON events on stdout. A request with a "clip" is a
    GIF source and refuses live streams and a start past the end."""
    def emit(kind, data):
        print(json.dumps({"kind": kind, "data": data}), flush=True)

    class Logger:
        def debug(self, message):
            if not str(message).startswith("[debug]"):
                emit("log", str(message))

        info = debug

        def warning(self, message):
            emit("log", f"WARNING: {message}")

        def error(self, message):
            emit("log", f"ERROR: {message}")

    def progress(data):
        event = {key: data.get(key) for key in (
            "status", "filename", "downloaded_bytes", "total_bytes",
            "total_bytes_estimate", "speed", "eta")}
        info = data.get("info_dict") or {}
        event["info_dict"] = {key: info.get(key) for key in (
            "id", "title", "thumbnail", "playlist_index", "playlist_count")}
        emit("progress", event)

    if yt_dlp is None:
        emit("error", "yt-dlp is not installed. Run: pip install -r requirements.txt")
        raise SystemExit(1)
    opts = request["opts"]
    if "cookiesfrombrowser" in opts:
        opts["cookiesfrombrowser"] = tuple(opts["cookiesfrombrowser"])
    ranges = request.get("ranges")
    if ranges is not None:
        from yt_dlp.utils import download_range_func
        opts["download_ranges"] = download_range_func([], [
            (item["start_time"], item["end_time"] if item["end_time"] is not None else float("inf"))
            for item in ranges])
    opts.update(logger=Logger(), progress_hooks=[progress],
                postprocessor_hooks=[lambda data: emit("postprocessor", {
                    "status": data.get("status"), "postprocessor_name": data.get("postprocessor_name")})],
                post_hooks=[lambda filename: emit("file", filename)])
    if request.get("clip") is not None:
        opts["match_filter"] = lambda info, *, incomplete=False: gif_source_filter(info, request["clip"], incomplete)
    try:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([request["url"]])
        except DownloadError as exc:
            if request["source"] != "youtube" or not _is_session_failure(str(exc)):
                raise
            # Same session, different player clients, visible in the log so a
            # repeated failure clearly means the session itself is bad.
            emit("log", "Session rejected by default players — retrying with alternate player clients…")
            emit("detail", "Retrying with alternate players…")
            opts.setdefault("extractor_args", {}).setdefault("youtube", {})["player_client"] = FALLBACK_PLAYER_CLIENTS
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([request["url"]])
    except Exception as exc:
        emit("error", str(exc))
        raise SystemExit(1)


def ytdlp_worker_cmd() -> list[str]:
    return [sys.executable, "-u", str(BASE_DIR / "tools" / "ytdlp_worker.py")]


def run_ytdlp_worker(job: dict, payload: dict, url: str, src: dict, *,
                     gif_folder: Path | None = None) -> tuple[list[Path], int, list[str]]:
    """Run one yt-dlp download in the worker process and follow it: its
    events update the job, and cancel or low disk stop its whole tree within
    a fraction of a second, whatever yt-dlp is doing. Returns the files it
    reported, its exit status and its error messages. A GIF source goes to
    `gif_folder`, numbered so repeated playlist entries stay apart."""
    check_job(job)
    opts = build_download_opts(job, payload)
    ranges = opts.pop("download_ranges", None)
    ranges = list(ranges({}, None)) if ranges is not None else None
    if ranges is not None:
        ranges = [{**item, "end_time": item["end_time"] if math.isfinite(item["end_time"]) else None}
                  for item in ranges]
    for key in ("logger", "progress_hooks", "postprocessor_hooks", "post_hooks", "match_filter"):
        opts.pop(key, None)
    root = (gif_folder or DOWNLOAD_DIR).resolve()
    if gif_folder is not None:
        opts["outtmpl"] = str(root / "%(autonumber)06d" / Path(opts["outtmpl"]).name)
    request = {"opts": opts, "ranges": ranges, "url": url, "source": src["id"],
               "clip": {"clip_from": payload.get("clip_from")} if gif_folder is not None else None}
    files: list[Path] = []
    errors: list[str] = []
    # A file, not a pipe: a full pipe would stall the worker, and the loop
    # reads it on its own schedule while checking cancel and disk.
    descriptor, log_name = tempfile.mkstemp(prefix=f"offtube-{job['id']}-", suffix=".log")
    os.close(descriptor)
    try:
        with open(log_name, "w+b") as output, open(log_name, "rb") as reader:
            proc = subprocess.Popen(ytdlp_worker_cmd(), stdin=subprocess.PIPE, stdout=output,
                                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", **OWN_PROCESS_GROUP)
            with jobs_lock:
                job["proc"] = proc
                if gif_folder is not None:
                    job.update(status="downloading", detail="Fetching GIF source…")
            pending = b""
            try:
                proc.stdin.write(json.dumps(request))
                proc.stdin.close()
                while True:
                    check_job(job)
                    try:
                        proc.wait(timeout=0.2)
                    except subprocess.TimeoutExpired:
                        pass
                    pending += reader.read()
                    lines = pending.split(b"\n")
                    pending = lines.pop()
                    for raw in lines:
                        try:
                            event = json.loads(raw)
                        except ValueError:
                            JobLogger(job["id"]).info(raw.decode("utf-8", "replace"))
                            continue
                        kind, data = event.get("kind"), event.get("data")
                        if kind == "progress":
                            progress_hook(job["id"], data)
                        elif kind == "postprocessor":
                            postprocessor_hook(job["id"], data)
                        elif kind == "file":
                            path = Path(data).resolve()
                            if not path.is_relative_to(root) or not path.is_file():
                                raise DownloadError("The download was not saved where offtube expected it.")
                            if path not in files:
                                files.append(path)
                        elif kind == "error":
                            errors.append(str(data))
                        elif kind == "log":
                            JobLogger(job["id"]).info(data)
                        elif kind == "detail":
                            with jobs_lock:
                                job["detail"] = clean_text(data, 500)
                    if proc.returncode is not None:
                        break
                check_job(job)
            finally:
                if proc.poll() is None or proc.returncode:
                    stop_process_tree(proc)
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
                with jobs_lock:
                    job.pop("proc", None)
    finally:
        try:
            os.unlink(log_name)
        except OSError:
            pass
    return files, proc.returncode, errors


def run_gif_ytdlp(job: dict, payload: dict, url: str, src: dict) -> None:
    """Fetch GIF sources into the job's private folder, then encode them."""
    files, code, errors = run_ytdlp_worker(job, payload, url, src, gif_folder=Path(job["gif_dir"]))
    if not files:
        if code:
            raise DownloadError(errors[-1] if errors else "Couldn’t fetch this GIF source; details are in the log.")
        return
    convert_gif_batch(job, payload, files, trimmed=True)
    if code:
        note = errors[-1] if errors else "The GIF source ended early."
        JobLogger(job["id"]).warning(f"{note} Finished GIFs were kept.")


def run_gif_ffmpeg(job: dict, args: list[str], detail: str,
                   start_pct: float, end_pct: float, duration: float) -> None:
    check_job(job)
    with jobs_lock:
        job.update(status="processing", detail=detail, progress=start_pct, speed=None, eta=None)
    # A file avoids full pipes stalling cancellation while ffmpeg is silent.
    log_path = Path(job["gif_dir"]) / "ffmpeg.log"
    with log_path.open("w+b") as progress, log_path.open("rb") as reader:
        proc = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                                 "-nostats", "-progress", "pipe:1", "-y", *args],
                                stdout=progress, stderr=subprocess.STDOUT, **OWN_PROCESS_GROUP)
        with jobs_lock:
            job["proc"] = proc
        try:
            while True:
                check_job(job)
                try:
                    proc.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    pass
                # A separate handle leaves the child's write offset untouched.
                length = os.fstat(progress.fileno()).st_size
                reader.seek(max(0, length - 8192))
                tail = reader.read(8192).decode("utf-8", "replace")
                times = re.findall(r"(?m)^out_time_us=(\d+)$", tail)
                with jobs_lock:
                    if times and duration > 0:
                        fraction = min(1, int(times[-1]) / 1_000_000 / duration)
                        job["progress"] = round(start_pct + (end_pct - start_pct) * fraction, 1)
                if proc.returncode is not None:
                    break
            check_job(job)
            if proc.returncode:
                JobLogger(job["id"]).error(tail[-2000:])
                raise DownloadError("GIF conversion failed. Try a shorter range or smaller size; details are in the log.")
        finally:
            stop_process_tree(proc)
            with jobs_lock:
                job.pop("proc", None)


def convert_gif(job: dict, source: Path, payload: dict, *, trimmed: bool = False,
                part: tuple[int, int] = (0, 1)) -> None:
    size, fps, loop = gif_settings(payload)
    check_job(job)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=codec_type:format=duration", "-of", "json", str(source)],
                           capture_output=True, text=True, timeout=20)
    try:
        metadata = json.loads(probe.stdout)
        duration = float(metadata.get("format", {}).get("duration", 0))
    except (ValueError, TypeError):
        raise DownloadError("Couldn’t read this video for GIF creation.")
    if probe.returncode or not metadata.get("streams"):
        raise DownloadError("This file has no video to turn into a GIF. Choose a video or reel.")
    if not math.isfinite(duration) or duration <= 0:
        raise DownloadError("Couldn’t determine the video length. Choose a finished video.")
    start = 0 if trimmed else (parse_time_to_seconds(payload.get("clip_from")) or 0)
    end = None if trimmed else parse_time_to_seconds(payload.get("clip_to"))
    if start >= duration:
        raise DownloadError("The GIF start time is past the end of this video.")
    duration = min(duration, end) - start if end is not None else duration - start
    video_input = ["-ss", str(start), "-t", str(duration), "-i", str(source)]
    # GIF pixels are square; fit the display aspect ratio, including anamorphic sources.
    filters = (f"fps={fps},scale=w='max(1,round(min(iw*sar,min({size},{size}*dar))))':"
               f"h='max(1,round(min(ih,min({size},{size}/dar))))':flags=lanczos,setsar=1")
    folder = Path(job["gif_dir"])
    palette, output = folder / "palette.png", folder / "output.gif"
    # Download uses 0–45. The remaining 54 points are sliced across the batch
    # so a later file continues the meter instead of jumping back to 45.
    part_index, count = part
    span = 54 / count
    color_start = 45 + part_index * span
    color_end = color_start + span * (20 / 54)
    encode_end = color_start + span
    run_gif_ffmpeg(job, [*video_input, "-vf", filters + ",palettegen=stats_mode=diff",
                        "-frames:v", "1", "-update", "1", str(palette)],
                   "GIF · analyzing colors…", color_start, color_end, duration)
    run_gif_ffmpeg(job, [*video_input, "-i", str(palette), "-filter_complex",
                        f"[0:v:0]{filters}[v];[v][1:v]paletteuse=dither=sierra2_4a:diff_mode=rectangle[out]",
                        "-map", "[out]", "-an", "-loop", "0" if loop else "-1", str(output)],
                   "GIF · encoding frames…", color_end, encode_end, duration)
    check_job(job)
    if not output.is_file() or output.stat().st_size == 0:
        raise DownloadError("No GIF frames were created. Choose a longer time range.")
    stem = source.stem.lstrip(".").encode("utf-8")[:150].decode("utf-8", "ignore") or "Video"
    index = len(job["files"]) + 1
    name = f"{stem} [GIF {size}px {fps}fps {job['id']}-{index}].gif"
    with jobs_lock:
        if job.get("cancel_requested"):
            raise DownloadError("Cancelled by user.")
        output.replace(DOWNLOAD_DIR / name)
        job["files"].append(name)
        job["log"].append(f"Created GIF: {name}")
    source.unlink(missing_ok=True)


def convert_gif_batch(job: dict, payload: dict, sources: list[Path], *, trimmed: bool) -> None:
    """One bad item does not throw away GIFs that already succeeded."""
    failed = 0
    last = None
    count = len(sources)
    for index, source in enumerate(sources):
        check_job(job)
        try:
            convert_gif(job, source, payload, trimmed=trimmed, part=(index, count))
        except DownloadError as exc:
            if job.get("cancel_requested") or job.get("disk_full"):
                raise
            failed += 1
            last = exc
            JobLogger(job["id"]).warning(str(exc))
    if not job["files"]:
        raise last or DownloadError("No video found to turn into a GIF.")
    if failed:
        noun = "item" if failed == 1 else "items"
        JobLogger(job["id"]).warning(f"{failed} {noun} could not be made into a GIF. The rest were kept.")


def run_gif_download(job: dict, payload: dict, url: str, src: dict) -> list[str]:
    with tempfile.TemporaryDirectory(prefix=f".gif-{job['id']}-", dir=DOWNLOAD_DIR) as folder:
        job["gif_dir"] = folder
        try:
            for index, engine in enumerate(src["engines"]):
                try:
                    if engine == "gallery-dl":
                        names = run_gallery(job, payload, url)
                        convert_gif_batch(job, payload, [Path(folder) / name for name in names], trimmed=False)
                    else:
                        run_gif_ytdlp(job, payload, url, src)
                    if not job["files"]:
                        raise DownloadError("No video found to turn into a GIF.")
                    return list(job["files"])
                except DownloadError:
                    if job.get("cancel_requested") or job.get("disk_full") or job["files"] or index == len(src["engines"]) - 1:
                        raise
                    JobLogger(job["id"]).info(f"Trying {src['engines'][index + 1]} for GIF source…")
        finally:
            job.pop("gif_dir", None)


class JobLogger:
    def __init__(self, job_id: str):
        self.job_id = job_id

    def _append(self, line: str):
        with jobs_lock:
            job = jobs.get(self.job_id)
            if job is not None:
                job["log"].append(clean_text(line))
                job["log"] = job["log"][-200:]

    def debug(self, msg):
        text = "" if msg is None else str(msg)
        if text.startswith("[debug]"):
            return
        self._append(text)

    def info(self, msg):
        self._append(msg)

    def warning(self, msg):
        self._append(f"WARNING: {msg}")

    def error(self, msg):
        self._append(f"ERROR: {msg}")


def progress_hook(job_id: str, d: dict):
    status = d.get("status")
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return
        # Raising ends the worker's event loop, which then stops the worker.
        if job.get("cancel_requested"):
            raise DownloadError("Cancelled by user.")
        if status == "downloading" and disk_low_locked(job):
            job["disk_full"] = True
            raise DownloadError(DISK_FULL_MSG)
        fn = d.get("filename")
        info_dict = d.get("info_dict") or {}
        if fn:
            # Remember files this job actually touched so concurrent jobs can't
            # claim each other's outputs in the before/after diff. yt-dlp
            # reports temp names (e.g. "...mp4.part"); store full names,
            # .part-stripped names, AND stems so a postprocessor rename
            # (...mp4 -> ...mp3) still matches.
            base = Path(fn).name
            stripped = base
            while any(stripped.lower().endswith(s) for s in TEMP_SUFFIXES):
                for s in TEMP_SUFFIXES:
                    if stripped.lower().endswith(s):
                        stripped = stripped[: -len(s)]
                        break
            stem = Path(stripped).stem
            seen = job.setdefault("seen_files", [])
            for variant in {base, stripped, stem}:
                if variant and variant not in seen:
                    seen.append(variant)
        # Display fields for the session strip, when inspect didn't send them.
        if info_dict.get("title") and not job.get("title"):
            job["title"] = clean_text(info_dict["title"], 300)
        if not job.get("thumbnail"):
            job["thumbnail"] = display_thumbnail(info_dict.get("thumbnail"))
        vid = info_dict.get("id")
        if vid:
            seen_ids = job.setdefault("seen_ids", [])
            if vid not in seen_ids:
                seen_ids.append(vid)
        # Playlist-aware progress: yt-dlp fires per-file hooks; blend the
        # per-file fraction into an overall fraction when playlist info exists.
        pl_index = info_dict.get("playlist_index")
        pl_count = info_dict.get("playlist_count")
        if isinstance(pl_index, int) and isinstance(pl_count, int) and pl_count > 1:
            job["playlist_index"] = pl_index
            job["playlist_count"] = pl_count
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes") or 0
            job["status"] = "downloading"
            job["downloaded_bytes"] = downloaded
            job["total_bytes"] = total
            job["speed"] = d.get("speed")
            job["eta"] = d.get("eta")
            job["filename"] = clean_text(d.get("filename") or job.get("filename"), 500)
            # total_bytes_estimate can be low: an overshoot reads as 100%, not 140%.
            file_pct = round(min(1.0, downloaded / total) * 100, 1) if total else 0
            pl_count_v = job.get("playlist_count")
            pl_index_v = job.get("playlist_index")
            overall = None
            if isinstance(pl_count_v, int) and pl_count_v > 1 and isinstance(pl_index_v, int):
                overall = ((pl_index_v - 1) + file_pct / 100) / pl_count_v * 100
            elif total:
                overall = file_pct
            # Fetching the source is the first 45%; encoding fills the rest.
            if overall is not None and job.get("quality") == "gif":
                overall *= 0.45
            if overall is not None:
                job["progress"] = round(overall, 1)
            fname = Path(d.get("filename") or "").name
            prefix = ""
            if job.get("playlist_count") and job.get("playlist_index"):
                prefix = f"[{job['playlist_index']}/{job['playlist_count']}] "
            job["detail"] = clean_text(
                f"Downloading {prefix}{fname} — "
                f"{job['progress'] if total else '?'}%",
                500,
            )
        elif status == "finished":
            job["status"] = "processing"
            job["detail"] = "Download finished, merging/converting with ffmpeg…"
            job["filename"] = clean_text(d.get("filename") or job.get("filename"), 500)


def postprocessor_hook(job_id: str, d: dict):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return
        if job.get("cancel_requested"):
            raise DownloadError("Cancelled by user.")
        if d.get("status") == "started":
            job["status"] = "processing"
            job["detail"] = f"Post-processing ({d.get('postprocessor_name', 'ffmpeg')})…"
        elif d.get("status") == "finished":
            job["detail"] = "Post-processing done."


def attribute_new_files(job: dict, before: set[Path]) -> list[str]:
    """Files created by this job. Filters the dir diff through hook-observed
    names (full, stripped, stem) and video ids so concurrent jobs don't claim
    each other's outputs; falls back to the raw diff when the hooks saw
    nothing (e.g. hook-less postprocessors). Temp files are never claimed."""
    after = set(DOWNLOAD_DIR.iterdir())
    claimed = {name for other in jobs.values() if other.get("id") != job.get("id")
               for name in other.get("files", [])}
    diff = sorted(
        p.name for p in (after - before)
        if p.is_file() and not is_temp_file(p.name) and p.name not in claimed
    )
    seen = set(job.get("seen_files") or [])
    seen_ids = set(job.get("seen_ids") or [])
    if seen or seen_ids:
        matched = []
        for f in diff:
            stem = Path(f).stem
            if f in seen or stem in seen:
                matched.append(f)
                continue
            if seen_ids and any(f"[{vid}]" in f for vid in seen_ids):
                matched.append(f)
        if matched:
            return matched
    return diff


def run_download(job_id: str, payload: dict):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None or job.get("status") == "cancelled":
            return
        job["status"] = "starting"
        job["detail"] = "Resolving formats…"
    try:
        job = jobs.get(job_id)
        if job is None:
            return
        url, src = check_url(payload.get("url"))
        before = set(DOWNLOAD_DIR.iterdir())
        files = None
        if payload.get("quality") == "gif":
            files = run_gif_download(job, payload, url, src)
        engines = [] if files is not None else src["engines"]
        for i, engine in enumerate(engines):
            try:
                if engine == "gallery-dl":
                    files = run_gallery(job, payload, url)
                else:
                    run_ytdlp(job, payload, url, src)
                break
            except DownloadError as exc:
                if job.get("cancel_requested") or job.get("disk_full") or i == len(engines) - 1:
                    raise
                # e.g. an image-only tweet: yt-dlp finds no video, gallery-dl
                # takes the photos. Logged so a double failure reads clearly.
                with jobs_lock:
                    job["log"].append(clean_text(
                        f"{engine}: {_engine_error(exc)} — trying {engines[i + 1]}…"))
                    job["detail"] = f"Trying {engines[i + 1]}…"
                    job["progress"] = 0
        with jobs_lock:
            job = jobs.get(job_id)
            if job is None:
                return
            if job.get("status") == "cancelled" or job.get("cancel_requested"):
                job["status"] = "cancelled"
                job["detail"] = "Cancelled."
                job["finished_at"] = job.get("finished_at") or _dt.datetime.now().isoformat(timespec="seconds")
                return
            job["status"] = "done"
            job["progress"] = 100
            job["detail"] = "Done."
            job["files"] = files if files is not None else attribute_new_files(job, before)
            job["finished_at"] = _dt.datetime.now().isoformat(timespec="seconds")
    except DownloadError as exc:
        msg = str(exc)
        with jobs_lock:
            job = jobs.get(job_id)
            if job is None:
                return
            # A cancel lands here: check_job and the hooks raise DownloadError.
            if "Cancelled by user" in msg or (job and job.get("cancel_requested")):
                job["status"] = "cancelled"
                job["detail"] = "Cancelled."
                job["finished_at"] = _dt.datetime.now().isoformat(timespec="seconds")
                return
        with jobs_lock:
            job = jobs.get(job_id)
            if job is None:
                return
            job["status"] = "error"
            job["detail"] = failure_message(msg, job.get("source") or "the site",
                                            "Couldn’t keep this: ", logged=True)
            job["log"].append(clean_text(f"ERROR: {msg}"))
            job["finished_at"] = _dt.datetime.now().isoformat(timespec="seconds")
    except Exception as exc:  # pragma: no cover - surfaced to UI
        with jobs_lock:
            job = jobs.get(job_id)
            if job is None:
                return
            if job.get("cancel_requested"):
                job["status"] = "cancelled"
                job["detail"] = "Cancelled."
                job["finished_at"] = _dt.datetime.now().isoformat(timespec="seconds")
                return
            job["status"] = "error"
            job["detail"] = clean_text(f"Error: {exc}", 1000)
            # No traceback: paths + internals would leak to any localhost client.
            job["log"].append(clean_text(f"ERROR: {exc}"))
            job["finished_at"] = _dt.datetime.now().isoformat(timespec="seconds")
    finally:
        cleanup_job_cookies(job_id)
        with jobs_lock:
            leftover = jobs.get(job_id)
        cleanup_job_temps(leftover)
        with jobs_lock:
            futures.pop(job_id, None)
            evict_jobs_locked()


def run_ytdlp(job: dict, payload: dict, url: str, src: dict) -> None:
    """A normal download, in the worker process: a cancel or shutdown stops
    it at once even mid-extraction or mid-merge. The worker also retries a
    rejected YouTube session with alternate player clients."""
    if yt_dlp is None:
        raise DownloadError("yt-dlp is not installed. Run: pip install -r requirements.txt")
    _, code, errors = run_ytdlp_worker(job, payload, url, src)
    if code:
        raise DownloadError(errors[-1] if errors else "yt-dlp stopped unexpectedly; details are in the log.")


def enqueue_download(body: dict) -> tuple[str | None, tuple[str, int] | None]:
    """Validate a download request and submit it. Returns (job_id, None) on
    success or (None, (message, status)) on refusal. Shared by /api/download
    and /api/retry so a retry is byte-for-byte the same code path."""
    try:
        validate_media_payload(body)
        url, src = check_url(body.get("url"))
    except ValueError as exc:
        return None, (str(exc), 400)
    for key in ("clip_from", "clip_to"):
        if body.get(key) is not None and not isinstance(body[key], str):
            return None, ("Clip times must be text: seconds, MM:SS or HH:MM:SS.", 400)
    if parse_time_to_seconds(body.get("clip_from")) is None and (body.get("clip_from") or "").strip():
        return None, ("Invalid 'from' time. Use MM:SS or HH:MM:SS.", 400)
    if parse_time_to_seconds(body.get("clip_to")) is None and (body.get("clip_to") or "").strip():
        return None, ("Invalid 'to' time. Use MM:SS or HH:MM:SS.", 400)
    try:
        resolve_cookies(body, persist=False)  # validate early for a clear error
        # Validation must not write cookie files (no "validate" orphan).
        build_download_opts({"id": "validate"}, body, persist=False)  # validate playlist/quality
    except ValueError as exc:
        return None, (str(exc), 400)
    try:
        if shutil.disk_usage(DOWNLOAD_DIR).free < MIN_DISK_FREE:
            return None, ("Disk is almost full (<200 MB free). Free space and retry.", 507)
    except OSError:
        pass
    jid = uuid.uuid4().hex[:12]
    job = {
        "id": jid,
        "url": url,
        "source": src["label"],
        "quality": body.get("quality") or "best",
        # Shown on the session strip; the UI sends what inspect found.
        "title": clean_text(body.get("title"), 300) or None,
        "thumbnail": display_thumbnail(body.get("thumbnail")),
        "status": "queued",
        "progress": 0,
        "detail": "Queued…",
        "log": [],
        "files": [],
        "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
        # The full request params, kept in memory only (public_job whitelists
        # what it exposes) so /api/retry can re-run a failed job faithfully.
        "payload": dict(body),
    }
    with jobs_lock:
        if shutting_down:
            return None, ("offtube is shutting down.", 503)
        if active_job_count_locked() >= MAX_ACTIVE_JOBS:
            return None, ("Too many active downloads. Wait for one to finish.", 429)
        jobs[jid] = job
        evict_jobs_locked()
        futures[jid] = executor.submit(run_download, jid, body)
    return jid, None


SHUTDOWN_GRACE = 5.0  # seconds running jobs get to stop before the process exits anyway


def shutdown_jobs(grace: float | None = None) -> bool:
    """Stop all work for a server shutdown: refuse new jobs, cancel queued
    ones, ask running ones to stop and kill their process trees, then give
    them `grace` seconds (SHUTDOWN_GRACE). Every job's cookie copy is
    removed either way. True when every job thread finished in time. Child
    processes stop at once; a thread can still be held up by something that
    is not a child process, such as a stalled network filesystem."""
    global shutting_down
    grace = SHUTDOWN_GRACE if grace is None else grace
    now = _dt.datetime.now().isoformat(timespec="seconds")
    running: list[concurrent.futures.Future] = []
    with jobs_lock:
        shutting_down = True
        for jid, job in jobs.items():
            if is_terminal(job):
                continue
            future = futures.get(jid)
            if future is not None and future.cancel():
                job.update(status="cancelled", detail="Cancelled: offtube stopped.", finished_at=now)
                futures.pop(jid, None)
                continue
            job["cancel_requested"] = True
            job["detail"] = "Stopping: offtube is shutting down…"
            if job.get("proc") is not None:
                stop_process_tree(job["proc"], wait=None)
            if future is not None:
                running.append(future)
    executor.shutdown(wait=False, cancel_futures=True)
    _, late = concurrent.futures.wait(running, timeout=grace)
    if late:
        # A late job may have started a child after the first pass.
        with jobs_lock:
            for job in jobs.values():
                if job.get("proc") is not None:
                    stop_process_tree(job["proc"], wait=None)
    # Credentials never outlive the server, even a job that is still busy.
    for cookie in list(COOKIES_DIR.glob("job-*.cookies.txt")):
        try:
            cookie.unlink(missing_ok=True)
        except OSError:
            pass
    return not late


# ------------------------------- HTTP layer -------------------------------

_health_cache: dict = {"at": 0.0, "checks": {}}


def environment_checks() -> dict:
    """yt-dlp/ffmpeg/JS-runtime probes, cached 60s (version spawns are slow)."""
    now = time.monotonic()
    if now - _health_cache["at"] < 60 and _health_cache["checks"]:
        return _health_cache["checks"]
    js = detect_js_runtimes()
    node_ver = _js_runtime_cache.get("node_version")
    checks = {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "node": bool(shutil.which("node")),
        "node_version": node_ver,
        "node_supported": node_meets_yt_dlp(node_ver) if node_ver else False,
        "deno": bool(shutil.which("deno")),
        "js_runtime": "deno" if "deno" in js else ("node" if "node" in js else "none"),
        "js_runtime_ok": bool(js),
    }
    ff = _cmd_version(["ffmpeg", "-version"])
    if ff:
        checks["ffmpeg_version"] = ff
        checks["ffmpeg"] = True
    else:
        checks["ffmpeg_version"] = "not found"
        checks["ffmpeg"] = False
    if yt_dlp is not None:
        try:
            checks["yt_dlp"] = yt_dlp.version.__version__
        except Exception:
            checks["yt_dlp"] = "installed"
    else:
        checks["yt_dlp"] = "missing"
    checks["gallery_dl"] = GALLERY_DL_VERSION or "missing"
    _health_cache.update(at=now, checks=checks)
    return checks

class DeadlineReader(io.RawIOBase):
    """Reads a request off a socket against one deadline for the whole of it.
    A per-recv timeout restarts with every byte, so a client trickling bytes
    could hold its worker forever; past the deadline a read raises
    TimeoutError, which BaseHTTPRequestHandler answers by closing."""

    def __init__(self, sock: socket.socket, deadline: float):
        super().__init__()
        self.sock, self.deadline = sock, deadline

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        remaining = self.deadline - time.monotonic()
        if remaining > 0:
            with selectors.DefaultSelector() as waiting:
                waiting.register(self.sock, selectors.EVENT_READ)
                if waiting.select(remaining):
                    return self.sock.recv_into(buffer)
        raise TimeoutError("the request took too long to arrive")


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "offtube/1.0"
    # A stalled client must not pin a worker thread forever: reading the
    # request has one overall deadline (DeadlineReader); writes, such as a
    # long file the client pauses, get this much time per operation.
    timeout = 30

    def setup(self):
        super().setup()
        self.rfile.close()
        self.rfile = io.BufferedReader(
            DeadlineReader(self.connection, time.monotonic() + REQUEST_READ_DEADLINE))

    def log_message(self, fmt, *args):  # stderr access log for API only
        try:
            requestline = str(args[0]) if args else ""
            code = str(args[1]) if len(args) > 1 else ""
            if "/api/" in requestline or code.startswith("4") or code.startswith("5"):
                sys.stderr.write(
                    f"{_dt.datetime.now().isoformat(timespec='seconds')} {self.address_string()} {fmt % args}\n"
                )
        except Exception:
            pass

    # -- helpers ---------------------------------------------------------
    def send_json(self, obj: dict, status: int = 200):
        body = json.dumps(obj, ensure_ascii=True, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self._apply_cors()
        self.end_headers()
        if not getattr(self, "_omit_body", False):
            self.wfile.write(body)

    def _apply_cors(self) -> None:
        """Allow the local UI and chrome-extension:// clients; never *."""
        allowed = cors_allow_origin(self.headers.get("Origin"))
        self.send_header("Vary", "Origin")
        if allowed:
            self.send_header("Access-Control-Allow-Origin", allowed)

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            raise HttpError(400, "Invalid Content-Length.")
        if length < 0:
            raise HttpError(400, "Invalid Content-Length.")
        if not length:
            return {}
        if length > MAX_JSON_BODY:
            # Drain so the client doesn't get RST mid-send (flaky 413s), but
            # only so far: a client can declare any length.
            try:
                remaining = min(length, 4 * MAX_JSON_BODY)
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            except Exception:
                pass
            raise HttpError(413, "Request body too large (max ~2 MB). If pasting cookies, export just this site's cookies instead of your whole browser.")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            raise HttpError(400, "Invalid JSON.")
        if not isinstance(data, dict):
            raise HttpError(400, "Invalid JSON.")
        return data

    def check_origin(self) -> bool:
        """Localhost CSRF/DNS-rebinding guard. Same-origin + curl (no header)
        pass; the unpacked extension (chrome-extension://) passes; a page on
        evil.com posting here is rejected."""
        for hdr in ("Origin", "Referer"):
            val = self.headers.get(hdr)
            if not val:
                continue
            if not origin_is_trusted(val):
                return False
        return True

    def check_host(self) -> bool:
        """Reject requests whose Host header isn't this machine's loopback.

        Without this, a site the user can reach could point a domain at
        127.0.0.1 (DNS rebinding) and talk to offtube directly. Origin/Referer
        checks already stop the browser path; this closes non-browser clients.
        """
        values = self.headers.get_all("Host", [])
        if not values:
            return self.request_version == "HTTP/1.0"
        if len(values) != 1:
            return False
        match = re.fullmatch(r"(?:127\.0\.0\.1|localhost|\[::1\])(?::([0-9]{1,5}))?",
                             values[0].strip(), re.IGNORECASE)
        return bool(match and (match[1] is None or 1 <= int(match[1]) <= 65535))

    def serve_static(self, rel: str):
        path = safe_child(WEB_DIR, rel)
        if path is None or not path.is_file():
            self.send_error(404)
            return
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-cache")
        if path.suffix == ".html":
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' https: data:; "
                "style-src 'self'; script-src 'self'; connect-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'",
            )
        self.end_headers()
        if not getattr(self, "_omit_body", False):
            self.wfile.write(data)

    def serve_download_file(self, raw_name: str):
        name = urllib.parse.unquote(raw_name)
        opened = open_library_file(name)
        if opened is None:
            self.send_error(404)
            return
        handle, info = opened
        with handle:
            size = info.st_size
            etag = f'"{size:x}-{info.st_mtime_ns:x}"'
            modified = email.utils.formatdate(info.st_mtime, usegmt=True)
            # Media elements read the index and the frames they need as
            # ranges. Ranges are GET-only, and If-Range resumes only the
            # file it names: a replaced file is sent whole.
            ranged = (self.command == "GET"
                      and self.headers.get("If-Range", etag).strip() in (etag, modified))
            try:
                span = byte_range(self.headers.get("Range"), size) if ranged else None
            except HttpError:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            first, last = span or (0, size - 1)
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            # RFC 5987: ascii fallback + UTF-8 encoded value; strip header breakers.
            fallback = re.sub(r'["\r\n]', "_", name)
            fallback = fallback.encode("ascii", "replace").decode("ascii")[:120] or "download"
            encoded = urllib.parse.quote(name, safe="")
            self.send_response(206 if span else 200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(last - first + 1))
            if span:
                self.send_header("Content-Range", f"bytes {first}-{last}/{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("ETag", etag)
            self.send_header("Last-Modified", modified)
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{encoded}',
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not getattr(self, "_omit_body", False):
                self.send_file_body(handle, first, last - first + 1)

    def send_file_body(self, handle: io.BufferedReader, offset: int, count: int) -> None:
        """Send `count` bytes of `handle` from `offset` (zero-copy where the
        OS allows). A client that stops reading - a media element that has
        what it needs, a cancelled Save - ends the response quietly."""
        if count <= 0:
            return
        try:
            self.connection.sendfile(handle, offset, count)
        except (ConnectionError, TimeoutError):
            self.close_connection = True

    # -- routes ------------------------------------------------------------
    def do_HEAD(self):
        self._omit_body = True
        try:
            self.do_GET()
        finally:
            self._omit_body = False

    def do_OPTIONS(self):
        # CORS preflight for the local extension client (fetch with
        # Content-Type: application/json triggers one). Untrusted origins
        # get 204 without ACAO so the browser hides the response.
        if not self.check_host():
            return self.send_error(403)
        self.send_response(204)
        self._apply_cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self.check_host():
            return self.send_error(403)
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if route in ("/", "/index.html"):
            return self.serve_static("index.html")
        if route in ("/app.js", "/styles.css", "/favicon.svg") or route.startswith("/fonts/"):
            return self.serve_static(route.lstrip("/"))
        if route == "/api/health":
            return self.send_json({"ok": True, "checks": environment_checks()})
        if route == "/api/source":
            # Is this link supported, and by which site? Local match, no
            # network — cheap enough for the extension to ask on every tab.
            try:
                _, src = check_url((qs.get("url") or [""])[0])
            except ValueError as exc:
                return self.send_json({"ok": True, "supported": False, "error": str(exc)})
            return self.send_json({"ok": True, "supported": True,
                                   "source": {"id": src["id"], "label": src["label"]},
                                   "engines": src["engines"]})
        if route == "/api/jobs":
            try:
                limit = int((qs.get("limit") or ["50"])[0])
            except (TypeError, ValueError):
                limit = 50
            limit = max(1, min(50, limit))
            with jobs_lock:
                items = list(jobs.values())[-limit:]
                trimmed = [public_job(j, log_limit=20) for j in items]
                return self.send_json({"jobs": trimmed})
        if route == "/api/job":
            jid = (qs.get("id") or [""])[0]
            with jobs_lock:
                job = jobs.get(jid)
                snapshot = public_job(job) if job else None
            if not snapshot:
                return self.send_json({"error": "Unknown job id."}, 404)
            return self.send_json(snapshot)
        if route == "/api/files":
            kind = (qs.get("kind") or ["all"])[0]
            if kind not in LIBRARY_KINDS:
                return self.send_json({"ok": False, "error": f"kind must be one of: {', '.join(LIBRARY_KINDS)}."}, 400)
            try:
                offset = max(0, int((qs.get("offset") or ["0"])[0]))
                limit = max(1, min(LIBRARY_PAGE_MAX, int((qs.get("limit") or [str(LIBRARY_PAGE_MAX)])[0])))
            except ValueError:
                return self.send_json({"ok": False, "error": "offset and limit must be whole numbers."}, 400)
            try:
                page = library_page((qs.get("q") or [""])[0][:200], kind, offset, limit)
            except OSError:
                return self.send_json({"ok": False, "error": "Can't read the downloads folder."}, 500)
            try:
                free = shutil.disk_usage(DOWNLOAD_DIR).free
            except OSError:
                free = None
            return self.send_json({**page, "disk_free": free, "disk_free_str": format_bytes(free)})
        if route.startswith("/files/"):
            return self.serve_download_file(route[len("/files/"):])
        if route.startswith("/api/"):
            return self.send_json({"ok": False, "error": "Not found."}, 404)
        return self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        if route.startswith("/api/") and not self.check_origin():
            return self.send_json({"ok": False, "error": "Cross-site requests blocked."}, 403)
        if not self.check_host():
            return self.send_error(403)
        try:
            body = self.read_json()
        except HttpError as exc:
            return self.send_json({"ok": False, "error": str(exc)}, exc.status)

        if route == "/api/info":
            try:
                info = fetch_info(body.get("url") or "", body)
                return self.send_json({"ok": True, "info": info})
            except ValueError as exc:
                return self.send_json({"ok": False, "error": str(exc)}, 400)
            except Exception as exc:
                msg = failure_message(clean_text(str(exc), 1000),
                                      site_label(validate_url(body.get("url"))))
                return self.send_json({"ok": False, "error": msg}, 400)

        if route == "/api/download":
            jid, err = enqueue_download(body)
            if err is not None:
                return self.send_json({"ok": False, "error": err[0]}, err[1])
            return self.send_json({"ok": True, "job_id": jid})

        if route == "/api/retry":
            # Re-run a finished job from its stored params. The payload is
            # internal-only; the client just names the job.
            jid = str(body.get("job_id") or "").strip()
            with jobs_lock:
                job = jobs.get(jid)
                if job is None:
                    return self.send_json({"ok": False, "error": "Unknown job id."}, 404)
                if not is_terminal(job):
                    return self.send_json({"ok": False, "error": "That job is still running."}, 400)
                payload = dict(job.get("payload") or {})
            new_id, err = enqueue_download(payload)
            if err is not None:
                return self.send_json({"ok": False, "error": err[0]}, err[1])
            return self.send_json({"ok": True, "job_id": new_id, "retried": jid})

        if route == "/api/cancel":
            jid = str(body.get("job_id") or "").strip()
            with jobs_lock:
                job = jobs.get(jid)
                if job is None:
                    return self.send_json({"ok": False, "error": "Unknown job id."}, 404)
                if is_terminal(job):
                    return self.send_json({"ok": False, "error": "That job already finished."}, 400)
                fut = futures.get(jid)
                if fut is not None and fut.cancel():
                    job["status"] = "cancelled"
                    job["detail"] = "Cancelled before it started."
                    job["finished_at"] = _dt.datetime.now().isoformat(timespec="seconds")
                    futures.pop(jid, None)
                    cleanup_job_cookies(jid)
                    return self.send_json({"ok": True, "cancelled": True})
                # Already running: its child process (the yt-dlp worker,
                # gallery-dl, ffmpeg) is stopped with everything it started,
                # and the job's thread sees the flag within a fraction of a
                # second.
                job["cancel_requested"] = True
                if job.get("proc") is not None:
                    stop_process_tree(job["proc"], wait=None)
                job["detail"] = "Cancelling…"
                return self.send_json({"ok": True, "cancelled": True, "stopping": True})

        if route == "/api/jobs/clear":
            with jobs_lock:
                terminal = [jid for jid, j in jobs.items() if is_terminal(j)]
                for jid in terminal:
                    futures.pop(jid, None)
                    jobs.pop(jid, None)
            return self.send_json({"ok": True, "cleared": len(terminal)})

        if route == "/api/files/delete":
            # A JSON body carries the name verbatim; it is not URL-decoded.
            name = body.get("name")
            if not isinstance(name, str) or not name:
                return self.send_json({"ok": False, "error": "Missing file name."}, 400)
            try:
                deleted = delete_library_file(name)
            except OSError as exc:
                # strerror only: the full message would carry the server path.
                reason = exc.strerror or "the system refused"
                return self.send_json({"ok": False, "error": f"Could not delete: {reason}."}, 500)
            if deleted is None:
                return self.send_json({"ok": False, "error": "File not found."}, 404)
            return self.send_json({"ok": True, "deleted": deleted})

        if route.startswith("/api/"):
            return self.send_json({"ok": False, "error": "Not found."}, 404)
        return self.send_error(404)


class LocalServer(socketserver.ThreadingTCPServer):
    # POSIX needs SO_REUSEADDR to rebind right after a restart (TIME_WAIT).
    # On Windows the same option makes the port shareable: any process, under
    # any account, could bind it too and receive offtube's requests, pasted
    # cookies included. There the port is claimed exclusively instead.
    exclusive_port = os.name == "nt"
    allow_reuse_address = not exclusive_port
    daemon_threads = True
    # One thread per connection, capped: past this many, a connection is
    # answered 503 at once rather than given a thread to wait in.
    max_connections = 64

    def __init__(self, server_address: tuple[str, int], handler_class: type[Handler]):
        self.address_family = socket.AF_INET6 if server_address[0] == "::1" else socket.AF_INET
        self.connection_slots = threading.BoundedSemaphore(self.max_connections)
        self.refused = 0
        self.refused_logged_at = -math.inf
        super().__init__(server_address, handler_class)

    def server_bind(self):
        if self.exclusive_port:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def process_request(self, request, client_address):
        if not self.connection_slots.acquire(blocking=False):
            self.refuse_busy(request)
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.connection_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connection_slots.release()

    def refuse_busy(self, request: socket.socket) -> None:
        """Say 503 without a thread or reading the request. What the client
        already sent is drained first, so the close is a FIN its HTTP stack
        reads the answer from, not a reset."""
        body = b'{"ok": false, "error": "offtube is busy. Retry in a moment."}'
        try:
            request.setblocking(False)
            try:
                while request.recv(65536):
                    pass
            except (BlockingIOError, InterruptedError):
                pass
            request.settimeout(1)
            request.sendall(b"HTTP/1.0 503 Service Unavailable\r\n"
                            b"Content-Type: application/json; charset=utf-8\r\n"
                            b"Retry-After: 1\r\nCache-Control: no-store\r\nConnection: close\r\n"
                            b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        except OSError:
            pass
        # Saturation is worth knowing about, but not once per connection.
        self.refused += 1
        now = time.monotonic()
        if now - self.refused_logged_at >= 10:
            sys.stderr.write(f"{_dt.datetime.now().isoformat(timespec='seconds')} busy: refused "
                             f"{self.refused} connection(s), all {self.max_connections} in use\n")
            self.refused, self.refused_logged_at = 0, now


def main():
    try:
        port = int(os.environ.get("PORT", "8000"))
    except ValueError:
        print("PORT must be a number (e.g. PORT=8080).", file=sys.stderr)
        raise SystemExit(2)
    if not 1 <= port <= 65535:
        print("PORT must be 1-65535.", file=sys.stderr)
        raise SystemExit(2)
    # HOST defaults to loopback. Docker sets HOST=0.0.0.0 so the mapped port
    # is reachable from the host (a container has its own loopback).
    host = os.environ.get("HOST", "127.0.0.1").strip() or "127.0.0.1"
    if host not in ("127.0.0.1", "localhost", "0.0.0.0", "::1"):
        print("HOST must be 127.0.0.1, localhost, ::1, or 0.0.0.0 (docker only).", file=sys.stderr)
        raise SystemExit(2)
    cleanup_stale_files()
    try:
        httpd = LocalServer((host, port), Handler)
    except OSError as exc:
        # Windows may report a port held exclusively as WSAEACCES.
        if exc.errno not in (errno.EADDRINUSE, getattr(errno, "WSAEACCES", errno.EADDRINUSE)):
            raise
        print(f"Port {port} is already in use — is offtube already running? "
              "Set PORT to use another one.", file=sys.stderr)
        raise SystemExit(1)
    # systemd, launchd and docker stop send SIGTERM: stop the same way as Ctrl+C.
    signal.signal(signal.SIGTERM, _interrupt)
    try:
        with httpd:
            shown_host = "127.0.0.1" if host == "0.0.0.0" else ("[::1]" if host == "::1" else host)
            print(f"\nofftube running → http://{shown_host}:{port}")
            if host == "0.0.0.0":
                print("WARNING: listening on all interfaces (docker mode). "
                      "Prefer 127.0.0.1:8000 port-mapping, not LAN exposure.")
            print(f"Downloads folder   → {DOWNLOAD_DIR}")
            print("Paste a link in the page, pick quality / from-to, hit Download.\n", flush=True)
            httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc()
        stop_work(exit_code=1)
    stop_work(exit_code=0)


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def _exit_now(signum, frame):
    os.write(2, b"Stopping now, without waiting for jobs.\n")
    os._exit(1)


def stop_work(exit_code: int) -> None:
    """Runs once the listener is closed. A second Ctrl+C or SIGTERM exits at
    once; otherwise jobs get SHUTDOWN_GRACE seconds to stop."""
    signal.signal(signal.SIGINT, _exit_now)
    signal.signal(signal.SIGTERM, _exit_now)
    print("\nStopping…", flush=True)
    if not shutdown_jobs():
        # Its thread would hold the interpreter open at exit; everything
        # else (process trees, cookie copies) is already cleaned up.
        print("A job was still busy; exiting without it.", flush=True)
        sys.stderr.flush()
        os._exit(exit_code)
    if exit_code:
        raise SystemExit(exit_code)
    print("bye!")


if __name__ == "__main__":
    main()
