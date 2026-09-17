"""API integration tests against a live Handler on an ephemeral port.

yt-dlp is stubbed (no network): the fake YoutubeDL answers extract_info and
simulates a download by firing progress hooks and writing one output file.
"""

import json
import socketserver
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app  # noqa: E402

BLOCK = threading.Event()


class FakeYoutubeDL:
    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        return {
            "id": "abc123", "title": "Test Video", "uploader": "Tester",
            "duration": 90, "thumbnail": "https://i.ytimg.com/vi/abc123/hq.jpg",
            "webpage_url": url, "is_live": False,
            "formats": [
                {"vcodec": "avc1", "acodec": "none", "height": 720,
                 "ext": "mp4", "fps": 30, "filesize": 10_000_000,
                 "format_id": "22"},
                {"vcodec": "none", "acodec": "opus", "height": None,
                 "ext": "webm", "format_id": "251"},
            ],
            "subtitles": {"en": []}, "automatic_captions": {},
        }

    def download(self, urls):
        if "block" in urls[0]:
            BLOCK.wait(timeout=15)
        for hook in self.opts.get("progress_hooks", []):
            hook({"status": "downloading", "downloaded_bytes": 5,
                  "total_bytes": 10, "speed": 1000, "eta": 1,
                  "filename": str(app.DOWNLOAD_DIR / "Test Video [abc123].mp4.part")})
            hook({"status": "finished",
                  "filename": str(app.DOWNLOAD_DIR / "Test Video [abc123].mp4")})
        (app.DOWNLOAD_DIR / "Test Video [abc123].mp4").write_bytes(b"fake-video")


class FakeYtDlp:
    YoutubeDL = FakeYoutubeDL

    class version:
        __version__ = "stub"


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DOWNLOAD_DIR", tmp_path / "downloads")
    monkeypatch.setattr(app, "COOKIES_DIR", tmp_path / "cookies")
    monkeypatch.setattr(app, "SESSION_COOKIE_FILE", tmp_path / "cookies" / "session.txt")
    monkeypatch.setattr(app, "yt_dlp", FakeYtDlp())
    monkeypatch.setattr(app, "shutil", app.shutil)  # no-op, clarity
    app.DOWNLOAD_DIR.mkdir(exist_ok=True)
    app.COOKIES_DIR.mkdir(exist_ok=True)
    app.jobs.clear()
    app.futures.clear()
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), app.Handler)
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    thread.join(timeout=5)
    app.jobs.clear()
    app.futures.clear()
    BLOCK.set()


def api(base, method, path, body=None, raw_body=None):
    data = raw_body if raw_body is not None else (
        json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, json.loads(res.read().decode() or "{}"), dict(res.headers)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode() or "{}")
        except Exception:
            payload = {}
        return exc.code, payload, dict(exc.headers)


def wait_for(base, job_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, job, _ = api(base, "GET", f"/api/job?id={job_id}")
        assert status == 200
        if job["status"] in ("done", "error", "cancelled"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not finish in time")


def test_health_and_security_headers(server):
    status, body, headers = api(server, "GET", "/api/health")
    assert status == 200 and body["ok"] is True
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert body["checks"]["yt_dlp"] == "stub"


def test_static_dotfile_and_traversal_blocked(server):
    import urllib.error
    with urllib.request.urlopen(server + "/app.js", timeout=10) as res:
        assert res.status == 200
        assert res.headers.get("X-Content-Type-Options") == "nosniff"
    with urllib.request.urlopen(server + "/", timeout=10) as res:
        assert "Content-Security-Policy" in res.headers
    # raw traversal request (urllib normalizes "..", so hit the handler directly)
    req = urllib.request.Request(server + "/files/../app.py", method="GET")
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("traversal should 404")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404


def test_info_rejects_non_youtube_without_network(server):
    status, body, _ = api(server, "POST", "/api/info", {"url": "https://vimeo.com/123"})
    assert status == 400
    assert "YouTube" in body["error"]


def test_info_video_with_stub(server):
    status, body, _ = api(server, "POST", "/api/info",
                           {"url": "https://www.youtube.com/watch?v=abc123"})
    assert status == 200
    info = body["info"]
    assert info["type"] == "video" and info["title"] == "Test Video"
    assert any(q["id"] == "720" for q in info["qualities"])


def test_download_end_to_end_and_files_and_delete(server):
    status, body, _ = api(server, "POST", "/api/download",
                           {"url": "https://youtu.be/abc123", "quality": "720"})
    assert status == 200, body
    job = wait_for(server, body["job_id"])
    assert job["status"] == "done", job
    assert job["files"] == ["Test Video [abc123].mp4"]
    assert job["progress"] == 100

    status, listing, _ = api(server, "GET", "/api/files")
    assert status == 200
    assert any(f["name"] == "Test Video [abc123].mp4" for f in listing["files"])
    assert listing["disk_free"] and listing["disk_free_str"]

    status, gone, _ = api(server, "POST", "/api/files/delete",
                           {"name": "Test Video [abc123].mp4"})
    assert status == 200 and gone["deleted"] == "Test Video [abc123].mp4"
    status, _, _ = api(server, "POST", "/api/files/delete",
                        {"name": "Test Video [abc123].mp4"})
    assert status == 404
    status, _, _ = api(server, "POST", "/api/files/delete", {"name": "../app.py"})
    assert status == 404


def test_download_rejects_bad_input(server):
    status, body, _ = api(server, "POST", "/api/download", {"url": ""})
    assert status == 400
    status, body, _ = api(server, "POST", "/api/download",
                           {"url": "https://youtu.be/abc123",
                            "clip_from": "02:00", "clip_to": "01:00"})
    assert status == 400
    assert "after" in body["error"]
    status, body, _ = api(server, "POST", "/api/download",
                           {"url": "https://vimeo.com/123"})
    assert status == 400


def test_body_too_large_rejected(server):
    status, body, _ = api(server, "POST", "/api/info",
                           raw_body=b'{"url":"x", "pad":"' + b"y" * 300_000 + b'"}')
    assert status == 413


def test_cancel_queued_job(server):
    BLOCK.clear()
    try:
        # Occupy both executor workers with blocking downloads.
        first, second = [], []
        for i in range(2):
            s, b, _ = api(server, "POST", "/api/download",
                           {"url": f"https://youtu.be/block{i}"})
            assert s == 200
            first.append(b["job_id"]) if i == 0 else second.append(b["job_id"])
        time.sleep(0.5)  # let workers pick up the blocking jobs
        s, b, _ = api(server, "POST", "/api/download",
                       {"url": "https://youtu.be/queued"})
        assert s == 200
        queued = b["job_id"]
        s, b, _ = api(server, "POST", "/api/cancel", {"job_id": queued})
        assert s == 200 and b["cancelled"] is True
        s, job, _ = api(server, "GET", f"/api/job?id={queued}")
        assert job["status"] == "cancelled"
        # Cancelling a running job is refused honestly.
        s, b, _ = api(server, "POST", "/api/cancel", {"job_id": first[0]})
        assert s == 409
    finally:
        BLOCK.set()
        for jid in list(app.jobs):
            job = app.jobs.get(jid)
            if job and job.get("status") not in ("done", "error", "cancelled"):
                wait_for(server, jid, timeout=15)
