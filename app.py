#!/usr/bin/env python3
"""
yt-downloader — paste-a-link YouTube downloader (local web UI + yt-dlp engine).

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
    pip install -r requirements.txt
    python app.py            # opens http://127.0.0.1:8000
    PORT=8080 python app.py

Notes on members-only / private videos (from the gist workflow):
  They ONLY work if YOUR account has access. Export cookies from a browser
  where you can already watch the video (Netscape format, e.g. via the
  "Get cookies.txt Locally" extension), then select the Cookies tab in the UI.
  If yt-dlp says "Join this channel to get access", your cookies are expired,
  from the wrong account, or your membership tier doesn't cover that video.
"""

from __future__ import annotations

import datetime as _dt
import http.server
import json
import mimetypes
import os
import re
import shutil
import socketserver
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
    from yt_dlp.utils import DownloadError
except ImportError:  # pragma: no cover
    yt_dlp = None
    DownloadError = Exception

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()

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

TIME_RE = re.compile(r"^\s*(\d+:)?(\d{1,3}:)?(\d{1,3})(\.\d+)?\s*$")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r")


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


def section_range_str(from_val: str | None, to_val: str | None) -> str | None:
    """Build yt-dlp --download-sections '*from-to' value."""
    f = parse_time_to_seconds(from_val) if from_val else None
    t = parse_time_to_seconds(to_val) if to_val else None
    if f is None and t is None:
        return None

    def fmt(sec: float | None, default: str) -> str:
        if sec is None:
            return default
        sec_i = int(sec)
        h, rem = divmod(sec_i, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    return f"*{fmt(f, '00:00')}-{fmt(t, 'inf')}"


def resolve_cookies(payload: dict) -> dict:
    """Return yt-dlp cookie opts from UI payload. Raises ValueError on bad input."""
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
        path_hint = (payload.get("cookies_file") or "").strip()
        if text:
            SESSION_COOKIE_FILE.write_text(text, encoding="utf-8")
            opts["cookiefile"] = str(SESSION_COOKIE_FILE)
            return opts
        # file saved earlier via /api/cookies/upload
        if SESSION_COOKIE_FILE.exists() and SESSION_COOKIE_FILE.stat().st_size > 0:
            opts["cookiefile"] = str(SESSION_COOKIE_FILE)
            return opts
        if path_hint:
            p = Path(path_hint).expanduser()
            if not p.exists():
                raise ValueError(f"Cookies file not found: {path_hint}")
            opts["cookiefile"] = str(p)
            return opts
        raise ValueError("No cookies provided. Upload a cookies.txt file or paste its contents.")
    return opts


def base_ydl_opts(payload: dict | None = None) -> dict:
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
            opts.update(resolve_cookies(payload))
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
    out = [{"id": "best", "label": "Best available", "height": None}]
    for q in ladder:
        h = q["height"]
        size = q["filesize"]
        size_str = f" ~{size / 1e6:.0f} MB" if size else ""
        out.append({"id": str(h), "label": f"{h}p{size_str}", "height": h})
    if has_audio:
        out.append({"id": "audio_mp3", "label": "Audio only (MP3)", "height": None})
        out.append({"id": "audio_m4a", "label": "Audio only (M4A)", "height": None})
    return out


def fetch_info(url: str, payload: dict) -> dict:
    if yt_dlp is None:
        raise RuntimeError("yt-dlp is not installed. Run: pip install -r requirements.txt")
    url = url.strip()
    if not url:
        raise ValueError("Paste a YouTube URL first.")
    opts = base_ydl_opts(payload)
    opts.update({"extract_flat": False, "skip_download": True})
    flat = payload.get("flat_playlist")
    if flat:
        opts["extract_flat"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
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


def build_download_opts(job: dict, payload: dict) -> dict:
    quality = payload.get("quality") or "best"
    fmt = quality_to_format(quality)
    outtmpl = str(DOWNLOAD_DIR / "%(title)s [%(id)s].%(ext)s")

    opts = base_ydl_opts(payload)
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
            if not re.fullmatch(r"[\d\s,\-:;]+", items):
                raise ValueError("Playlist items looks invalid. Example: 1-5,7,10-12")
            opts["playlistitems"] = items.replace(";", ",")

    # Subtitles (optional)
    if payload.get("subtitles"):
        opts["writesubtitles"] = True
        opts["writeautomaticsub"] = bool(payload.get("auto_subs"))
        lang = (payload.get("sub_lang") or "en").strip() or "en"
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
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes") or 0
            job["status"] = "downloading"
            job["downloaded_bytes"] = downloaded
            job["total_bytes"] = total
            job["speed"] = d.get("speed")
            job["eta"] = d.get("eta")
            job["filename"] = clean_text(d.get("filename") or job.get("filename"), 500)
            if total:
                job["progress"] = round(downloaded / total * 100, 1)
            job["detail"] = clean_text(
                f"Downloading {(d.get('filename', '') or '').split('/')[-1]} — "
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
        if d.get("status") == "started":
            job["status"] = "processing"
            job["detail"] = f"Post-processing ({d.get('postprocessor_name', 'ffmpeg')})…"
        elif d.get("status") == "finished":
            job["detail"] = "Post-processing done."


def run_download(job_id: str, payload: dict):
    with jobs_lock:
        job = jobs[job_id]
        job["status"] = "starting"
        job["detail"] = "Resolving formats…"
    try:
        assert yt_dlp is not None, "yt-dlp is not installed. Run: pip install -r requirements.txt"
        opts = build_download_opts(jobs[job_id], payload)
        url = payload.get("url", "").strip()
        if not url:
            raise ValueError("Missing URL.")
        before = set(DOWNLOAD_DIR.iterdir())
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        after = set(DOWNLOAD_DIR.iterdir())
        new_files = sorted(
            [p.name for p in (after - before) if p.is_file() and p.name != ".gitkeep"],
        )
        with jobs_lock:
            job = jobs[job_id]
            job["status"] = "done"
            job["progress"] = 100
            job["detail"] = "Done."
            job["files"] = new_files
            job["finished_at"] = _dt.datetime.now().isoformat(timespec="seconds")
    except DownloadError as exc:
        msg = str(exc)
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
            job = jobs[job_id]
            job["status"] = "error"
            job["detail"] = clean_text(f"Download failed: {msg}{hint}", 1000)
            job["log"].append(clean_text(f"ERROR: {msg}"))
    except Exception as exc:  # pragma: no cover - surfaced to UI
        with jobs_lock:
            job = jobs[job_id]
            job["status"] = "error"
            job["detail"] = clean_text(f"Error: {exc}", 1000)
            job["log"].append(clean_text(f"ERROR: {exc}\n{traceback.format_exc()[-2000:]}"))


# ------------------------------- HTTP layer -------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "yt-downloader/1.0"

    def log_message(self, fmt, *args):  # quieter logs
        pass

    # -- helpers ---------------------------------------------------------
    def send_json(self, obj: dict, status: int = 200):
        body = json.dumps(obj, ensure_ascii=True, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def serve_static(self, rel: str):
        path = (WEB_DIR / rel).resolve()
        if not str(path).startswith(str(WEB_DIR.resolve())) or not path.exists():
            self.send_error(404)
            return
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def serve_download_file(self, name: str):
        safe = Path(urllib.parse.unquote(name)).name
        path = (DOWNLOAD_DIR / safe).resolve()
        if not str(path).startswith(str(DOWNLOAD_DIR.resolve())) or not path.exists():
            self.send_error(404)
            return
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Content-Disposition", f'attachment; filename="{safe}"')
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
            checks = {
                "yt_dlp": getattr(yt_dlp, "version", None) and getattr(yt_dlp, "__version__", "installed") or ("missing" if yt_dlp is None else "installed"),
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
            if yt_dlp is not None:
                try:
                    checks["yt_dlp"] = __import__("yt_dlp").version.__version__
                except Exception:
                    checks["yt_dlp"] = "installed"
            return self.send_json({"ok": True, "checks": checks})
        if route == "/api/jobs":
            with jobs_lock:
                return self.send_json({"jobs": list(jobs.values())})
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
                if p.is_file() and p.name != ".gitkeep":
                    st = p.stat()
                    files.append(
                        {
                            "name": p.name,
                            "size": st.st_size,
                            "size_str": f"{st.st_size / 1e6:.1f} MB",
                            "modified": _dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                            "url": f"/files/{urllib.parse.quote(p.name)}",
                        }
                    )
            return self.send_json({"files": files})
        if route.startswith("/files/"):
            return self.serve_download_file(route[len("/files/"):])
        return self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        body = self.read_json()

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
            url = (body.get("url") or "").strip()
            if not url:
                return self.send_json({"ok": False, "error": "Paste a URL first."}, 400)
            if parse_time_to_seconds(body.get("clip_from")) is None and (body.get("clip_from") or "").strip():
                return self.send_json({"ok": False, "error": "Invalid 'from' time. Use MM:SS or HH:MM:SS."}, 400)
            if parse_time_to_seconds(body.get("clip_to")) is None and (body.get("clip_to") or "").strip():
                return self.send_json({"ok": False, "error": "Invalid 'to' time. Use MM:SS or HH:MM:SS."}, 400)
            try:
                resolve_cookies(body)  # validate early for a clear error
                build_download_opts({"id": "validate"}, body)  # validate playlist/quality
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
                jobs[jid] = job
            t = threading.Thread(target=run_download, args=(jid, body), daemon=True)
            t.start()
            return self.send_json({"ok": True, "job_id": jid})

        if route == "/api/cookies/upload":
            # JSON: {"cookies_text": "..."} — stores for this session
            text = body.get("cookies_text") or ""
            if not text.strip():
                return self.send_json({"ok": False, "error": "Empty cookies."}, 400)
            SESSION_COOKIE_FILE.write_text(text, encoding="utf-8")
            lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
            return self.send_json({"ok": True, "cookies": len(lines)})

        return self.send_error(404)


def main():
    port = int(os.environ.get("PORT", "8000"))
    with socketserver.ThreadingTCPServer(("127.0.0.1", port), Handler) as httpd:
        httpd.allow_reuse_address = True
        print(f"\nyt-downloader running → http://127.0.0.1:{port}")
        print(f"Downloads folder   → {DOWNLOAD_DIR}")
        print("Paste a YouTube link in the page, pick quality / from-to, hit Download.\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nbye!")


if __name__ == "__main__":
    main()
