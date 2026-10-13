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
import functools
import http.server
import ipaddress
import json
import mimetypes
import os
import re
import shutil
import socketserver
import subprocess
import sys
import threading
import time
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
# Bounded concurrency: at most 2 simultaneous yt-dlp runs; the rest queue in
# the executor instead of spawning unbounded threads.
executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="dl")
MAX_JOBS = 50  # stored job records; oldest terminal jobs are evicted past this
MAX_ACTIVE_JOBS = 5  # queued + running; beyond this /api/download returns 429

TERMINAL_STATUSES = frozenset({"done", "error", "cancelled"})


def is_terminal(job: dict) -> bool:
    return job.get("status") in TERMINAL_STATUSES


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
MAX_URL_LEN = 2000
MAX_COOKIES_TEXT = 1024 * 1024  # raw paste cap; one site's rows surviving the trim are KBs
MAX_PLAYLIST_ITEMS_LEN = 200
MAX_SUB_LANG_LEN = 100
MIN_DISK_FREE = 200 * 1024 * 1024  # refuse new downloads below 200 MB free

# Origins that may read JSON responses (extension + the local web UI).
# Never reflect * — that lets any website fetch /api/jobs and /api/files
# while offtube is running on loopback.
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# In-progress / fragment files yt-dlp leaves behind mid-download. These must
# never appear in the library or in a job's claimed outputs.
TEMP_SUFFIXES = (".part", ".temp", ".tmp", ".ytdl", ".ydl", ".frag")


def is_temp_file(name: str) -> bool:
    low = name.lower()
    return low == ".gitkeep" or any(low.endswith(s) for s in TEMP_SUFFIXES)


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
    "id", "url", "source", "quality", "status", "progress", "detail", "log", "files",
    "created_at", "finished_at", "downloaded_bytes", "total_bytes", "speed",
    "eta", "playlist_index", "playlist_count",
)


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
    "facebook": "Facebook", "threads": "Threads", "bsky": "Bluesky",
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
        return float(s)
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
    return total


def format_seconds(sec: float | None) -> str:
    if sec is None:
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
# paste is trimmed to the target link's site before it touches disk. Some
# sites sign in on a sibling domain too: YouTube rides Google's session,
# Threads rides Instagram's.
COOKIE_SIBLINGS = {"youtube": ("google", "googlevideo"), "threads": ("instagram",)}

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
                "or run natively with Access → Browser (Docker can't read your "
                "host browser, so use the file there).")
    if "needs to be reloaded" in low:
        return ("YouTube rejected the session. Re-export fresh cookies while on "
                "youtube.com, logged into a single account, then retry. "
                "Full-browser exports go stale fastest.")
    if any(m in low for m in LOGIN_MARKERS):
        return (f"{label} only shows this to a signed-in account. Options → Access: "
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
            if p.is_file() and is_temp_file(p.name):
                try:
                    p.unlink()
                except OSError:
                    pass
    except OSError:
        pass


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
        # yt-dlp python API expects a tuple: (browser, profile, keyring, container)
        opts["cookiesfrombrowser"] = (browser, profile, None, None)
        return opts
    if mode in ("upload", "text", "file"):
        text = payload.get("cookies_text") or ""
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
                target.write_text(text, encoding="utf-8")
            opts["cookiefile"] = str(target)
            return opts
        # Manually placed session file (read-only shared use is safe).
        # Copy your cookies.txt to cookies/session_cookies.txt to reuse it.
        if SESSION_COOKIE_FILE.exists() and SESSION_COOKIE_FILE.stat().st_size > 0:
            opts["cookiefile"] = str(SESSION_COOKIE_FILE)
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


def _engine_error(exc: Exception) -> str:
    """An engine's error without yt-dlp's 'ERROR:' prefix and 'please report
    this issue' tail."""
    msg = clean_text(str(exc), 400).split("; please report this issue")[0]
    return msg.removeprefix("ERROR: ")


def fetch_info(url: str, payload: dict) -> dict:
    """Inspect `url` with each engine that supports it, in order, and return
    the first answer, tagged with its source and engine."""
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
                    "thumbnail": e.get("thumbnail"),
                }
            )
        return {
            "type": "playlist",
            "title": info.get("title"),
            "uploader": info.get("uploader"),
            "count": info.get("playlist_count") or len(entries),
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
        "qualities": qualities,
        "subtitles": sorted(list((info.get("subtitles") or {}).keys()))[:12],
        "automatic_captions": sorted(list((info.get("automatic_captions") or {}).keys()))[:12],
        "part_of_playlist": bool(list_id or info.get("playlist") or info.get("playlist_id")),
        "playlist_id": list_id or info.get("playlist_id"),
    }


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
            "title": _gallery_text(meta, ("title", "grid_title", "description", "content", "caption")),
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
    cmd = gallery_cmd(payload, job["id"]) + [
        "-D", str(DOWNLOAD_DIR), *gallery_range_args(payload), "--", url]
    # Unbuffered: a piped child would otherwise hold its lines until exit.
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            env={**os.environ, "PYTHONUNBUFFERED": "1"})
    with jobs_lock:
        job["proc"] = proc  # internal: /api/cancel terminates it
        job["status"] = "downloading"
        job["detail"] = "Fetching…"
    root = DOWNLOAD_DIR.resolve()
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
                if job.get("cancel_requested"):
                    proc.terminate()
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
        with jobs_lock:
            job.pop("proc", None)
    if job.get("cancel_requested"):
        raise DownloadError("Cancelled by user.")
    if not files:
        raise DownloadError(errors[-1] if errors else
                            f"gallery-dl saved nothing (exit {proc.returncode}).")
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
    if clip_from is not None and clip_to is not None and clip_to <= clip_from:
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
    if payload.get("subtitles"):
        opts["writesubtitles"] = True
        opts["writeautomaticsub"] = bool(payload.get("auto_subs"))
        lang = (payload.get("sub_lang") or "en").strip() or "en"
        if len(lang) > MAX_SUB_LANG_LEN or not re.fullmatch(r"[A-Za-z\-, ]+", lang):
            raise ValueError("Subtitle language looks invalid. Example: en or en,de.")
        opts["subtitleslangs"] = [s.strip() for s in lang.split(",") if s.strip()]
        opts["subtitlesformat"] = "srt"
        opts["embedsubtitles"] = False

    return opts


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
        # Cooperative cancel: run_download's hooks raise to abort yt-dlp.
        if job.get("cancel_requested"):
            raise DownloadError("Cancelled by user.")
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
            file_pct = round(downloaded / total * 100, 1) if total else 0
            pl_count_v = job.get("playlist_count")
            pl_index_v = job.get("playlist_index")
            if isinstance(pl_count_v, int) and pl_count_v > 1 and isinstance(pl_index_v, int):
                job["progress"] = round(((pl_index_v - 1) + file_pct / 100) / pl_count_v * 100, 1)
            elif total:
                job["progress"] = file_pct
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
    diff = sorted(
        p.name for p in (after - before)
        if p.is_file() and not is_temp_file(p.name)
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
        engines = src["engines"]
        for i, engine in enumerate(engines):
            try:
                if engine == "gallery-dl":
                    files = run_gallery(job, payload, url)
                else:
                    run_ytdlp(job, payload, url, src)
                break
            except DownloadError as exc:
                if job.get("cancel_requested") or i == len(engines) - 1:
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
            # Cooperative cancel lands here (hooks raise DownloadError).
            if "Cancelled by user" in msg or (job and job.get("cancel_requested")):
                job["status"] = "cancelled"
                job["detail"] = "Cancelled."
                job["finished_at"] = _dt.datetime.now().isoformat(timespec="seconds")
                return
        hint = failure_hint(msg, job.get("source") or "the site")
        with jobs_lock:
            job = jobs.get(job_id)
            if job is None:
                return
            job["status"] = "error"
            job["detail"] = clean_text(f"Download failed: {msg}" + (f" — {hint}" if hint else ""), 1000)
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
    assert yt_dlp is not None, "yt-dlp is not installed. Run: pip install -r requirements.txt"
    opts = build_download_opts(job, payload)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except DownloadError as exc:
        if (src["id"] != "youtube" or not _is_session_failure(str(exc))
                or job.get("cancel_requested")):
            raise
        # Same session, different player clients — visible in the UI log
        # so a repeated failure clearly means the session itself is bad.
        with jobs_lock:
            job["log"].append("Session rejected by default players — retrying with alternate player clients…")
            job["detail"] = "Retrying with alternate players…"
        opts = build_download_opts(job, payload, player_clients=FALLBACK_PLAYER_CLIENTS)
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])


def enqueue_download(body: dict) -> tuple[str | None, tuple[str, int] | None]:
    """Validate a download request and submit it. Returns (job_id, None) on
    success or (None, (message, status)) on refusal. Shared by /api/download
    and /api/retry so a retry is byte-for-byte the same code path."""
    try:
        url, src = check_url(body.get("url"))
    except ValueError as exc:
        return None, (str(exc), 400)
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
        if active_job_count_locked() >= MAX_ACTIVE_JOBS:
            return None, ("Too many active downloads. Wait for one to finish.", 429)
        jobs[jid] = job
        evict_jobs_locked()
        futures[jid] = executor.submit(run_download, jid, body)
    return jid, None


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

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "offtube/1.0"
    # A stalled client must not pin a worker thread forever. BaseHTTPRequestHandler
    # checks this between requests and on socket reads.
    timeout = 30

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
            # Drain so the client doesn't get RST mid-send (flaky 413s).
            try:
                remaining = length
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
        host = (self.headers.get("Host") or "").split(":", 1)[0].strip().lower()
        if not host:
            return True  # HTTP/1.0-style probes
        host = host.replace("[", "").replace("]", "")
        return host in ("127.0.0.1", "localhost", "::1")

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

    def serve_download_file(self, name: str):
        safe = Path(urllib.parse.unquote(name)).name
        if not safe or safe == ".gitkeep":
            self.send_error(404)
            return
        path = safe_child(DOWNLOAD_DIR, safe)
        if path is None or not path.is_file():
            self.send_error(404)
            return
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        # RFC 5987: ascii fallback + UTF-8 encoded value; strip header breakers.
        fallback = re.sub(r'["\r\n]', "_", safe)
        fallback = fallback.encode("ascii", "replace").decode("ascii")[:120] or "download"
        encoded = urllib.parse.quote(safe, safe="")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{encoded}',
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if getattr(self, "_omit_body", False):
            return
        with open(path, "rb") as fh:
            shutil.copyfileobj(fh, self.wfile)

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
        if route in ("/app.js", "/styles.css", "/favicon.svg"):
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
            files = []
            listing = sorted(
                (p for p in DOWNLOAD_DIR.iterdir() if p.is_file() and not is_temp_file(p.name)),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for p in listing[:500]:
                st = p.stat()
                files.append(
                    {
                        "name": p.name,
                        "size": st.st_size,
                        "size_str": format_bytes(st.st_size) or "?",
                        "modified": _dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                        "url": f"/files/{urllib.parse.quote(p.name)}",
                    }
                )
            try:
                free = shutil.disk_usage(DOWNLOAD_DIR).free
            except OSError:
                free = None
            return self.send_json({
                "files": files,
                "disk_free": free,
                "disk_free_str": format_bytes(free),
            })
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
                msg = clean_text(str(exc), 1000)
                hint = failure_hint(msg, site_label(validate_url(body.get("url"))))
                if hint:
                    msg += f" — {hint}"
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
                # Already running: cooperative cancel via hooks. The worker
                # notices on its next progress/postprocessor callback; a
                # gallery-dl process is simply terminated.
                job["cancel_requested"] = True
                if job.get("proc") is not None:
                    job["proc"].terminate()
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
            name = str(body.get("name") or "")
            safe = Path(urllib.parse.unquote(name)).name
            if not safe:
                return self.send_json({"ok": False, "error": "Missing file name."}, 400)
            path = safe_child(DOWNLOAD_DIR, safe)
            if path is None or not path.is_file():
                return self.send_json({"ok": False, "error": "File not found."}, 404)
            try:
                path.unlink()
            except OSError as exc:
                return self.send_json({"ok": False, "error": f"Could not delete: {exc}"}, 500)
            return self.send_json({"ok": True, "deleted": safe})

        if route.startswith("/api/"):
            return self.send_json({"ok": False, "error": "Not found."}, 404)
        return self.send_error(404)


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
    # Must be set before bind; assigning on the instance afterwards is a no-op.
    socketserver.ThreadingTCPServer.allow_reuse_address = True

    class ReuseServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with ReuseServer((host, port), Handler) as httpd:
        shown_host = "127.0.0.1" if host in ("0.0.0.0", "::1") else host
        print(f"\nofftube running → http://{shown_host}:{port}")
        if host == "0.0.0.0":
            print("WARNING: listening on all interfaces (docker mode). "
                  "Prefer 127.0.0.1:8000 port-mapping, not LAN exposure.")
        print(f"Downloads folder   → {DOWNLOAD_DIR}")
        print("Paste a link in the page, pick quality / from-to, hit Download.\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nbye!")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            with jobs_lock:
                for job in jobs.values():
                    if job.get("proc") is not None:
                        job["proc"].terminate()


if __name__ == "__main__":
    main()
