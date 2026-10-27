"""GIF source isolation, real yt-dlp trimming and cancellation of its FFmpeg child."""
import http.server
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app


requires_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                    reason="ffmpeg and ffprobe needed")


@pytest.fixture
def source_server(tmp_path):
    source = tmp_path / "video.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=160x90:rate=20:duration=2", "-c:v", "mpeg4",
                    "-movflags", "+faststart", "-y", str(source)], check=True, capture_output=True)
    data = source.read_bytes()
    requested, release = threading.Event(), threading.Event()
    cookies = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_HEAD(self):
            self.send_video(head=True)

        def do_GET(self):
            self.send_video()

        def send_video(self, head=False):
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
            start = int(match[1]) if match else 0
            end = min(len(data) - 1, int(match[2])) if match and match[2] else len(data) - 1
            self.send_response(206 if match else 200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if match:
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
            self.end_headers()
            if not head:
                cookies.append(self.headers.get("Cookie", ""))
                requested.set()
                if self.path.startswith("/slow"):
                    release.wait(10)
                try:
                    self.wfile.write(data[start:end + 1])
                except (BrokenPipeError, ConnectionResetError):
                    pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", requested, cookies
    release.set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.fixture
def source_job(tmp_path, monkeypatch):
    downloads, private, cookies = tmp_path / "downloads", tmp_path / "private", tmp_path / "cookies"
    downloads.mkdir()
    private.mkdir()
    cookies.mkdir()
    monkeypatch.setattr(app, "DOWNLOAD_DIR", downloads)
    monkeypatch.setattr(app, "COOKIES_DIR", cookies)
    job = {"id": "source-test", "quality": "gif", "files": [], "log": [], "gif_dir": str(private)}
    app.jobs[job["id"]] = job
    yield job
    app.jobs.pop(job["id"], None)


@requires_ffmpeg
@pytest.mark.parametrize("layout", ["checkout", "container"])
def test_real_source_range_and_cookies(source_job, source_server, tmp_path, monkeypatch, layout):
    if layout == "container":
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        for line in (app.BASE_DIR / "Dockerfile").read_text().splitlines():
            if not line.startswith("COPY "):
                continue
            source_name, destination_name = shlex.split(line)[1:]
            source = app.BASE_DIR / source_name
            destination = runtime / destination_name
            if source.is_dir():
                shutil.copytree(source, destination, dirs_exist_ok=True)
            else:
                if destination_name.endswith("/"):
                    destination = destination / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        monkeypatch.setattr(app, "BASE_DIR", runtime)
    url, _, cookies = source_server
    payload = {"url": url + "/video.mp4", "quality": "gif", "gif_size": 320, "gif_fps": 10,
               "clip_from": "0.5", "clip_to": "1.5", "cookies_mode": "text",
               "cookies_text": "# Netscape HTTP Cookie File\n127.0.0.1\tFALSE\t/\tFALSE\t2147483647\tsession\tfixture\n"}
    app.run_gif_ytdlp(source_job, payload, payload["url"], {"id": "generic"})
    assert len(source_job["files"]) == 1
    assert any("session=fixture" in value for value in cookies)
    output = app.DOWNLOAD_DIR / source_job["files"][0]
    probe = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-of", "json", str(output)],
                           check=True, capture_output=True, text=True)
    stream = json.loads(probe.stdout)["streams"][0]
    assert int(stream["nb_read_frames"]) == 10
    assert float(stream["duration"]) == pytest.approx(1, abs=0.02)
    assert "proc" not in source_job


@requires_ffmpeg
@pytest.mark.parametrize("reason", ["cancel", "disk"])
def test_silent_source_stops_process_tree(source_job, source_server, monkeypatch, reason):
    url, requested, _ = source_server
    payload = {"quality": "gif", "clip_from": "0.5", "clip_to": "1.5"}
    errors = []
    if reason == "disk":
        monkeypatch.setattr(app, "disk_low_locked", lambda _: requested.is_set())

    def download():
        try:
            app.run_gif_ytdlp(source_job, payload, url + "/slow.mp4", {"id": "generic"})
        except app.DownloadError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=download)
    thread.start()
    assert requested.wait(8), source_job["log"]
    proc = source_job["proc"]
    descendants = []
    if os.name != "nt":
        # A direct file is fetched by yt-dlp itself. ffmpeg is in the same
        # process group only when the protocol needs it, and then it must die too.
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            result = subprocess.run(["ps", "-axo", "pid=,pgid=,comm="], capture_output=True, text=True, check=True)
            descendants = [int(parts[0]) for line in result.stdout.splitlines()
                           if len(parts := line.split(maxsplit=2)) == 3 and int(parts[1]) == proc.pid and "ffmpeg" in parts[2]]
            if descendants:
                break
            time.sleep(0.05)
    start = time.monotonic()
    if reason == "cancel":
        source_job["cancel_requested"] = True
    thread.join(timeout=3)
    assert not thread.is_alive() and time.monotonic() - start < 3
    assert proc.poll() is not None
    assert errors and ("Cancelled" if reason == "cancel" else "disk is almost full") in errors[0]
    assert "proc" not in source_job
    for pid in descendants:
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
        assert not state.stdout.strip() or state.stdout.strip().startswith("Z")


def test_worker_restores_options_and_retries_session(monkeypatch, capsys):
    seen = []

    class YoutubeDL:
        def __init__(self, opts):
            seen.append(dict(opts))

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def download(self, urls):
            if len(seen) == 1:
                raise app.DownloadError("Sign in to confirm you’re not a bot")
            assert urls == ["https://youtu.be/fixture"]

    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", YoutubeDL)
    app.ytdlp_worker({"opts": {"cookiesfrombrowser": ["chrome", "Profile 2", None, None],
                                   "noplaylist": False, "playlistitems": "2-4",
                                   "js_runtimes": {"node": {}}, "remote_components": ["ejs:npm"]},
                           "ranges": [{"start_time": 3.25, "end_time": None}],
                           "clip": {"clip_from": "3.25"}, "source": "youtube", "url": "https://youtu.be/fixture"})
    assert len(seen) == 2
    assert seen[-1]["cookiesfrombrowser"] == ("chrome", "Profile 2", None, None)
    assert seen[-1]["noplaylist"] is False and seen[-1]["playlistitems"] == "2-4"
    assert seen[-1]["js_runtimes"] == {"node": {}} and seen[-1]["remote_components"] == ["ejs:npm"]
    assert seen[-1]["extractor_args"]["youtube"]["player_client"] == app.FALLBACK_PLAYER_CLIENTS
    assert list(seen[-1]["download_ranges"]({}, None)) == [{"start_time": 3.25, "end_time": float("inf")}]
    with pytest.raises(app.DownloadError, match="finished video"):
        seen[-1]["match_filter"]({"is_live": True}, incomplete=True)
    assert "retrying with alternate" in capsys.readouterr().out
