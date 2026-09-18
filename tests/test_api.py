"""API integration tests against a live Handler on an ephemeral port.

yt-dlp is stubbed (no network): the fake YoutubeDL answers extract_info and
simulates a download by firing progress hooks and writing one output file.
"""

import json
import socketserver
import sys
import threading
import time
import urllib.parse
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


def api(base, method, path, body=None, raw_body=None, headers=None):
    data = raw_body if raw_body is not None else (
        json.dumps(body).encode() if body is not None else None)
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(base + path, data=data, method=method, headers=h)
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
    with urllib.request.urlopen(server + "/favicon.svg", timeout=10) as res:
        assert res.status == 200
        assert "svg" in (res.headers.get("Content-Type") or "")
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


def test_info_and_download_reject_channel_urls(server):
    status, body, _ = api(server, "POST", "/api/info",
                           {"url": "https://www.youtube.com/@someone/videos"})
    assert status == 400
    assert "Channel" in body["error"]
    status, body, _ = api(server, "POST", "/api/download",
                           {"url": "https://www.youtube.com/channel/UCxxxx"})
    assert status == 400
    assert "Channel" in body["error"]


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
                           raw_body=b'{"url":"x", "pad":"' + b"y" * 2_600_000 + b'"}')
    assert status == 413
    assert "YouTube-only" in body.get("error", "")


def test_invalid_json_rejected(server):
    status, body, _ = api(server, "POST", "/api/info", raw_body=b"not json{{{")
    assert status == 400
    assert "Invalid JSON" in body.get("error", "")


def _acao(headers):
    return headers.get("Access-Control-Allow-Origin") or headers.get("access-control-allow-origin")


def test_cors_reflects_localhost_and_extension_not_star(server):
    status, _, headers = api(server, "GET", "/api/health",
                             headers={"Origin": "https://evil.com"})
    assert status == 200
    acao = _acao(headers)
    assert acao not in ("*", "https://evil.com")

    status, _, headers = api(server, "GET", "/api/health",
                             headers={"Origin": "http://127.0.0.1:8000"})
    assert status == 200
    assert _acao(headers) == "http://127.0.0.1:8000"

    ext = "chrome-extension://abcdefghijklmnopqrstuvwxyz123456"
    status, _, headers = api(server, "GET", "/api/health", headers={"Origin": ext})
    assert status == 200
    assert _acao(headers) == ext


def test_cors_preflight_extension_allowed_evil_omitted(server):
    ext = "chrome-extension://abcdefghijklmnopqrstuvwxyz123456"
    status, _, headers = api(server, "OPTIONS", "/api/download",
                             headers={"Origin": ext,
                                      "Access-Control-Request-Method": "POST"})
    assert status == 204
    assert _acao(headers) == ext

    status, _, headers = api(server, "OPTIONS", "/api/download",
                             headers={"Origin": "https://evil.com",
                                      "Access-Control-Request-Method": "POST"})
    assert status in (200, 204)
    assert _acao(headers) not in ("*", "https://evil.com")


def test_job_listing_omits_payload(server):
    status, body, _ = api(server, "POST", "/api/download",
                           {"url": "https://youtu.be/abc123", "quality": "720",
                            "cookies_mode": "none"})
    assert status == 200, body
    job = wait_for(server, body["job_id"])
    assert "payload" not in job
    status, data, _ = api(server, "GET", "/api/jobs?limit=5")
    assert status == 200
    assert all("payload" not in j for j in data["jobs"])


def test_cross_site_post_blocked(server):
    status, body, _ = api(server, "POST", "/api/download",
                           {"url": "https://youtu.be/abc123"},
                           headers={"Origin": "https://evil.com"})
    assert status == 403
    status, body, _ = api(server, "POST", "/api/download",
                           {"url": "https://youtu.be/abc123"},
                           headers={"Origin": "http://127.0.0.1:8000"})
    assert status in (200, 429)
    if status == 200:
        wait_for(server, body["job_id"])


def test_unknown_api_returns_json_404(server):
    status, body, _ = api(server, "GET", "/api/nope")
    assert status == 404
    assert body.get("ok") is False
    status, body, _ = api(server, "POST", "/api/nope", {"x": 1})
    assert status == 404
    assert body.get("ok") is False


def test_temp_files_hidden_from_library(server):
    import app as _app
    (_app.DOWNLOAD_DIR / "half.mp4.part").write_bytes(b"partial")
    (_app.DOWNLOAD_DIR / "real [abc123].mp4").write_bytes(b"ok")
    try:
        status, listing, _ = api(server, "GET", "/api/files")
        assert status == 200
        names = [f["name"] for f in listing["files"]]
        assert "real [abc123].mp4" in names
        assert "half.mp4.part" not in names
    finally:
        for n in ("half.mp4.part", "real [abc123].mp4"):
            try:
                (_app.DOWNLOAD_DIR / n).unlink()
            except OSError:
                pass


def test_download_disposition_uses_rfc5987(server):
    status, body, _ = api(server, "POST", "/api/download",
                           {"url": "https://youtu.be/abc123", "quality": "720"})
    assert status == 200, body
    job = wait_for(server, body["job_id"])
    assert job["status"] == "done"
    fname = job["files"][0]
    with urllib.request.urlopen(
        server + f"/files/{urllib.parse.quote(fname)}", timeout=10
    ) as res:
        disp = res.headers.get("Content-Disposition", "")
        assert "filename*=" in disp


def test_jobs_limit_trims_logs(server):
    status, body, _ = api(server, "POST", "/api/download",
                           {"url": "https://youtu.be/abc123"})
    assert status == 200
    wait_for(server, body["job_id"])
    status, data, _ = api(server, "GET", "/api/jobs?limit=1")
    assert status == 200
    assert len(data["jobs"]) == 1
    assert len(data["jobs"][0].get("log", [])) <= 20


def test_disk_guard_refuses_when_full(server, monkeypatch):
    import collections
    fake = collections.namedtuple("usage", "total used free")(100, 99, 1)
    monkeypatch.setattr(app.shutil, "disk_usage", lambda *a, **k: fake)
    status, body, _ = api(server, "POST", "/api/download",
                           {"url": "https://youtu.be/abc123"})
    assert status == 507
    assert "Disk" in body.get("error", "")


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
        # Cancelling a running job requests cooperative stop (hooks abort).
        s, b, _ = api(server, "POST", "/api/cancel", {"job_id": first[0]})
        assert s == 200 and b["cancelled"] is True
    finally:
        BLOCK.set()
        for jid in list(app.jobs):
            job = app.jobs.get(jid)
            if job and job.get("status") not in ("done", "error", "cancelled"):
                wait_for(server, jid, timeout=15)
    # The running job we cancelled must end as cancelled, not done/error.
    s, job, _ = api(server, "GET", f"/api/job?id={first[0]}")
    assert job["status"] == "cancelled"
