#!/usr/bin/env python3
"""
offtube — paste-a-link YouTube keeper (local web UI + yt-dlp engine).

Features:
  - Paste any YouTube URL (single video, playlist, members-only/private with cookies)
  - Fetch metadata + thumbnails + available qualities before downloading
  - Quality picker (best / 2160p / 1440p / 1080p / 720p / 480p / 360p / audio-only)
  - Clip trim (from-to, uses yt-dlp --download-sections, needs ffmpeg)
  - Playlist controls (single video vs playlist, start/end/items)
  - Cookies: none / upload cookies.txt (Netscape) / paste text / from browser
  - Optional subtitles, live progress, file browser

Only stdlib + yt-dlp. No Flask/FastAPI needed.

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
import http.server
import json
import mimetypes
import os
import re
import shutil
import socketserver
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
    from yt_dlp.utils import DownloadError
except ImportError:  # pragma: no cover
    yt_dlp = None
    DownloadError = Exception

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
# targets to YouTube, and never serve files outside their directories.

MAX_JSON_BODY = 256 * 1024  # cookies pastes are KBs; bigger bodies are abuse/bug
MAX_URL_LEN = 2000
MAX_COOKIES_TEXT = 200 * 1024
MAX_PLAYLIST_ITEMS_LEN = 200
MAX_SUB_LANG_LEN = 100

YOUTUBE_HOSTS = frozenset({
    "youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
})

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


def validate_url(url: str | None) -> str:
    """Allowlist check: only http(s) YouTube URLs (incl. subdomains)."""
    u = (url or "").strip()
    if not u:
        raise ValueError("Paste a YouTube URL first.")
    if len(u) > MAX_URL_LEN:
        raise ValueError("That URL is too long to be a YouTube link.")
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    try:
        parts = urllib.parse.urlsplit(u)
        host = (parts.hostname or "").lower().rstrip(".")
    except Exception:
        raise ValueError("That doesn't look like a valid URL.")
    if parts.scheme not in ("http", "https") or not host:
        raise ValueError("That doesn't look like a valid URL.")
    if not any(host == h or host.endswith("." + h) for h in YOUTUBE_HOSTS):
        raise ValueError("Only YouTube links are supported (youtube.com / youtu.be).")
    return u


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
            raise ValueError("Cookies text is too large (max ~200 KB).")
        if text:
            target = cookie_file_for(job_id)
            if persist:
                target.write_text(text, encoding="utf-8")
            opts["cookiefile"] = str(target)
            return opts
        # file saved earlier via /api/cookies/upload (read-only shared use is safe)
        if SESSION_COOKIE_FILE.exists() and SESSION_COOKIE_FILE.stat().st_size > 0:
            opts["cookiefile"] = str(SESSION_COOKIE_FILE)
            return opts
        raise ValueError("No cookies provided. Upload a cookies.txt file or paste its contents.")
    return opts


def base_ydl_opts(payload: dict | None = None, job_id: str | None = None,
                 persist: bool = True) -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False,
        "socket_timeout": 30,
        "retries": 3,
    }
    # Help with YouTube bot-guard challenges when node/deno is present
    # (mirrors gist advice: --remote-components ejs:npm). Harmless if unavailable.
    if shutil.which("node") or shutil.which("deno"):
        try:
            opts["remote_components"] = ["ejs:npm"]
        except Exception:
            pass
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


def fetch_info(url: str, payload: dict) -> dict:
    if yt_dlp is None:
        raise RuntimeError("yt-dlp is not installed. Run: pip install -r requirements.txt")
    url = validate_url(url)
    # Inspect must be side-effect-free: pasted cookies go to an ephemeral
    # per-request file, never to the shared session file or a predictable
    # "validate" path. Cleaned up even if extract_info raises.
    info_cookie_id = f"info-{uuid.uuid4().hex[:12]}"
    opts = base_ydl_opts(payload, info_cookie_id)
    opts.update({"skip_download": True})
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    finally:
        cleanup_job_cookies(info_cookie_id)
    if info is None:
        raise RuntimeError("Could not extract info for that URL.")
    if info.get("_type") == "playlist":
        entries = []
        for e in (info.get("entries") or [])[:50]:
            if not e:
                continue
            entries.append(
                {
                    "id": e.get("id"),
                    "title": e.get("title"),
                    "url": e.get("url") or e.get("webpage_url"),
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
    return {
        "type": "video",
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "duration_str": format_seconds(info.get("duration")),
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url") or url,
        "is_live": bool(info.get("is_live")),
        "qualities": summarize_formats(info),
        "subtitles": sorted(list((info.get("subtitles") or {}).keys()))[:12],
        "automatic_captions": sorted(list((info.get("automatic_captions") or {}).keys()))[:12],
    }


def build_download_opts(job: dict, payload: dict, persist: bool = True) -> dict:
    quality = payload.get("quality") or "best"
    fmt = quality_to_format(quality)
    outtmpl = str(DOWNLOAD_DIR / "%(title)s [%(id)s].%(ext)s")

    opts = base_ydl_opts(payload, job.get("id"), persist=persist)
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
        if msg.startswith("[debug]"):
            return
        self._append(msg)

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
            fname = (d.get("filename", "") or "").split("/")[-1]
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
        assert yt_dlp is not None, "yt-dlp is not installed. Run: pip install -r requirements.txt"
        job = jobs.get(job_id)
        if job is None:
            return
        opts = build_download_opts(job, payload)
        url = validate_url(payload.get("url"))
        before = set(DOWNLOAD_DIR.iterdir())
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
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
            job["files"] = attribute_new_files(job, before)
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
        hint = ""
        if "Join this channel" in msg or "members-only" in msg.lower():
            hint = (" This looks like a members-only/private video. Make sure your cookies "
                    "come from an account that can already watch it, re-export them "
                    "(they expire), and check the membership tier.")
        elif "cookies" in msg.lower():
            hint = " Your cookies may be expired — re-export cookies.txt and try again."
        elif "ffmpeg" in msg.lower():
            hint = " ffmpeg is required for merging/trimming — install it and restart."
        with jobs_lock:
            job = jobs.get(job_id)
            if job is None:
                return
            job["status"] = "error"
            job["detail"] = clean_text(f"Download failed: {msg}{hint}", 1000)
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
            futures.pop(job_id, None)
            evict_jobs_locked()


# ------------------------------- HTTP layer -------------------------------

_health_cache: dict = {"at": 0.0, "checks": {}}


def environment_checks() -> dict:
    """yt-dlp/ffmpeg/node probes, cached 60s (ffmpeg -version spawn is slow)."""
    now = time.monotonic()
    if now - _health_cache["at"] < 60 and _health_cache["checks"]:
        return _health_cache["checks"]
    checks = {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "node": bool(shutil.which("node")),
        "deno": bool(shutil.which("deno")),
    }
    try:
        import subprocess
        out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        checks["ffmpeg_version"] = (out.stdout.splitlines() or [""])[0][:80] if out.returncode == 0 else "not found"
    except Exception:
        checks["ffmpeg_version"] = "not found"
        checks["ffmpeg"] = False
    if yt_dlp is not None:
        try:
            checks["yt_dlp"] = yt_dlp.version.__version__
        except Exception:
            checks["yt_dlp"] = "installed"
    else:
        checks["yt_dlp"] = "missing"
    _health_cache.update(at=now, checks=checks)
    return checks

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "offtube/1.0"

    def log_message(self, fmt, *args):  # quieter logs
        pass

    # -- helpers ---------------------------------------------------------
    def send_json(self, obj: dict, status: int = 200):
        body = json.dumps(obj, ensure_ascii=True, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

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
            raise HttpError(413, "Request body too large.")
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
        pass; a page on evil.com posting here is rejected."""
        for hdr in ("Origin", "Referer"):
            val = self.headers.get(hdr)
            if not val:
                continue
            try:
                host = (urllib.parse.urlsplit(val).hostname or "").lower().rstrip(".")
            except Exception:
                return False
            if host in ("127.0.0.1", "localhost", "::1"):
                continue
            return False
        return True

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
        with open(path, "rb") as fh:
            shutil.copyfileobj(fh, self.wfile)

    # -- routes ------------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if route in ("/", "/index.html"):
            return self.serve_static("index.html")
        if route in ("/app.js", "/styles.css"):
            return self.serve_static(route.lstrip("/"))
        if route == "/api/health":
            return self.send_json({"ok": True, "checks": environment_checks()})
        if route == "/api/jobs":
            try:
                limit = int((qs.get("limit") or ["50"])[0])
            except (TypeError, ValueError):
                limit = 50
            limit = max(1, min(50, limit))
            with jobs_lock:
                items = list(jobs.values())[-limit:]
                # Trim logs in list view; full log via /api/job.
                trimmed = [
                    {**j, "log": (j.get("log") or [])[-20:]}
                    for j in items
                ]
                return self.send_json({"jobs": trimmed})
        if route == "/api/job":
            jid = (qs.get("id") or [""])[0]
            with jobs_lock:
                job = jobs.get(jid)
            if not job:
                return self.send_json({"error": "Unknown job id."}, 404)
            return self.send_json(job)
        if route == "/api/files":
            files = []
            for p in sorted(DOWNLOAD_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if p.is_file() and not is_temp_file(p.name):
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
                if "Join this channel" in msg:
                    msg += (" — this video needs membership access. Add cookies from an "
                            "account that can watch it, then retry.")
                return self.send_json({"ok": False, "error": msg}, 400)

        if route == "/api/download":
            try:
                url = validate_url(body.get("url"))
            except ValueError as exc:
                return self.send_json({"ok": False, "error": str(exc)}, 400)
            if parse_time_to_seconds(body.get("clip_from")) is None and (body.get("clip_from") or "").strip():
                return self.send_json({"ok": False, "error": "Invalid 'from' time. Use MM:SS or HH:MM:SS."}, 400)
            if parse_time_to_seconds(body.get("clip_to")) is None and (body.get("clip_to") or "").strip():
                return self.send_json({"ok": False, "error": "Invalid 'to' time. Use MM:SS or HH:MM:SS."}, 400)
            try:
                resolve_cookies(body, persist=False)  # validate early for a clear error
                # Validation must not write cookie files (no "validate" orphan).
                build_download_opts({"id": "validate"}, body, persist=False)  # validate playlist/quality
            except ValueError as exc:
                return self.send_json({"ok": False, "error": str(exc)}, 400)
            jid = uuid.uuid4().hex[:12]
            job = {
                "id": jid,
                "url": url,
                "quality": body.get("quality") or "best",
                "status": "queued",
                "progress": 0,
                "detail": "Queued…",
                "log": [],
                "files": [],
                "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
                "payload": {k: v for k, v in body.items() if k != "cookies_text"},
            }
            with jobs_lock:
                if active_job_count_locked() >= MAX_ACTIVE_JOBS:
                    return self.send_json(
                        {"ok": False, "error": "Too many active downloads. Wait for one to finish."}, 429
                    )
                jobs[jid] = job
                evict_jobs_locked()
                futures[jid] = executor.submit(run_download, jid, body)
            return self.send_json({"ok": True, "job_id": jid})

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
                # notices on its next progress/postprocessor callback.
                job["cancel_requested"] = True
                job["detail"] = "Cancelling…"
                return self.send_json({"ok": True, "cancelled": True, "stopping": True})

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

        if route == "/api/cookies/upload":
            # JSON: {"cookies_text": "..."} — stores for this session
            text = body.get("cookies_text") or ""
            if len(text) > MAX_COOKIES_TEXT:
                return self.send_json({"ok": False, "error": "Cookies text is too large (max ~200 KB)."}, 400)
            if not text.strip():
                return self.send_json({"ok": False, "error": "Empty cookies."}, 400)
            SESSION_COOKIE_FILE.write_text(text, encoding="utf-8")
            lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
            return self.send_json({"ok": True, "cookies": len(lines)})

        if route.startswith("/api/"):
            return self.send_json({"ok": False, "error": "Not found."}, 404)
        return self.send_error(404)


def main():
    port = int(os.environ.get("PORT", "8000"))
    with socketserver.ThreadingTCPServer(("127.0.0.1", port), Handler) as httpd:
        httpd.allow_reuse_address = True
        httpd.daemon_threads = True
        print(f"\nofftube running → http://127.0.0.1:{port}")
        print(f"Downloads folder   → {DOWNLOAD_DIR}")
        print("Paste a YouTube link in the page, pick quality / from-to, hit Download.\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nbye!")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    main()
