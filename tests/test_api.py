"""API integration tests against a live Handler on an ephemeral port.

yt-dlp is stubbed (no network): the fake YoutubeDL answers extract_info and
simulates a download by firing progress hooks and writing one output file.
"""

import json
import http.client
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from contextlib import closing
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
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
    httpd = app.LocalServer(("127.0.0.1", 0), app.Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()
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
    req = urllib.request.Request(server + "/", method="HEAD")
    with urllib.request.urlopen(req, timeout=10) as res:
        assert res.status == 200
        assert "Content-Security-Policy" in res.headers
        assert res.read() == b""
    # raw traversal request (urllib normalizes "..", so hit the handler directly)
    req = urllib.request.Request(server + "/files/../app.py", method="GET")
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("traversal should 404")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404


def test_info_rejects_unsupported_site_without_network(server):
    status, body, _ = api(server, "POST", "/api/info", {"url": "https://example.com/page"})
    assert status == 400
    assert "doesn't support" in body["error"]
    status, body, _ = api(server, "POST", "/api/info", {"url": "http://192.168.1.5/a.mp4"})
    assert status == 400


def test_source_endpoint_names_the_site(server):
    q = urllib.parse.quote("https://vimeo.com/76979871", safe="")
    status, body, _ = api(server, "GET", f"/api/source?url={q}")
    assert status == 200 and body["supported"] is True
    assert body["source"] == {"id": "vimeo", "label": "Vimeo"}
    q = urllib.parse.quote("https://example.com/page", safe="")
    status, body, _ = api(server, "GET", f"/api/source?url={q}")
    assert status == 200 and body["supported"] is False and body["error"]


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
                           {"url": "https://www.youtube.com/watch?v=abc123abc12"})
    assert status == 200
    info = body["info"]
    assert info["type"] == "video" and info["title"] == "Test Video"
    assert any(q["id"] == "720" for q in info["qualities"])


def test_download_end_to_end_and_files_and_delete(server):
    status, body, _ = api(server, "POST", "/api/download",
                           {"url": "https://youtu.be/abc123abc12", "quality": "720"})
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
                           {"url": "https://youtu.be/abc123abc12",
                            "clip_from": "02:00", "clip_to": "01:00"})
    assert status == 400
    assert "after" in body["error"]
    status, body, _ = api(server, "POST", "/api/download",
                           {"url": "https://example.com/page"})
    assert status == 400


def test_body_too_large_rejected(server):
    status, body, _ = api(server, "POST", "/api/info",
                           raw_body=b'{"url":"x", "pad":"' + b"y" * 2_600_000 + b'"}')
    assert status == 413
    assert "just this site" in body.get("error", "")


def test_invalid_json_rejected(server):
    status, body, _ = api(server, "POST", "/api/info", raw_body=b"not json{{{")
    assert status == 400
    assert "Invalid JSON" in body.get("error", "")


@pytest.mark.parametrize("route", ["/api/info", "/api/download"])
def test_media_payload_types_rejected_before_work(server, monkeypatch, route):
    def unexpected_inspection(*args, **kwargs):
        pytest.fail("Invalid input reached an engine")

    monkeypatch.setattr(app, "inspect_ytdlp", unexpected_inspection)
    monkeypatch.setattr(app, "inspect_gallery", unexpected_inspection)
    invalid_fields = {
        "url": {"value": "https://youtu.be/abc123abc12"},
        "quality": ["720"],
        "cookies_mode": True,
        "cookies_browser": ["chrome"],
        "cookies_browser_profile": {},
        "cookies_text": ["SECRET"],
        "playlist_mode": 1,
        "playlist_items": [1, 2],
        "playlist_start": True,
        "playlist_end": 2.5,
        "clip_from": [],
        "clip_to": 30,
        "subtitles": "false",
        "auto_subs": 1,
        "sub_lang": ["en"],
        "title": {"value": "Title"},
        "thumbnail": ["https://example.com/image.jpg"],
    }
    for field, value in invalid_fields.items():
        status, body, _ = api(server, "POST", route,
                              {"url": "https://youtu.be/abc123abc12", field: value})
        assert status == 400, (field, status, body)
        assert body["ok"] is False and body["error"], (field, body)
        assert "SECRET" not in body["error"]
    assert not app.jobs


@pytest.mark.parametrize("route", ["/api/info", "/api/download"])
def test_unknown_media_modes_rejected(server, route):
    for field in ("cookies_mode", "playlist_mode"):
        status, body, _ = api(server, "POST", route,
                              {"url": "https://youtu.be/abc123abc12", field: "unknown"})
        assert status == 400 and field in body["error"]
    assert not app.jobs


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
                           {"url": "https://youtu.be/abc123abc12", "quality": "720",
                            "cookies_mode": "none"})
    assert status == 200, body
    job = wait_for(server, body["job_id"])
    assert "payload" not in job
    status, data, _ = api(server, "GET", "/api/jobs?limit=5")
    assert status == 200
    assert all("payload" not in j for j in data["jobs"])


def test_cross_site_post_blocked(server):
    status, body, _ = api(server, "POST", "/api/download",
                           {"url": "https://youtu.be/abc123abc12"},
                           headers={"Origin": "https://evil.com"})
    assert status == 403
    status, body, _ = api(server, "POST", "/api/download",
                           {"url": "https://youtu.be/abc123abc12"},
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
    (_app.DOWNLOAD_DIR / ".DS_Store").write_bytes(b"finder")
    try:
        status, listing, _ = api(server, "GET", "/api/files")
        assert status == 200
        names = [f["name"] for f in listing["files"]]
        assert "real [abc123].mp4" in names
        assert "half.mp4.part" not in names
        assert ".DS_Store" not in names  # unservable, so unlisted
    finally:
        for n in ("half.mp4.part", "real [abc123].mp4", ".DS_Store"):
            try:
                (_app.DOWNLOAD_DIR / n).unlink()
            except OSError:
                pass


def test_download_disposition_uses_rfc5987(server):
    status, body, _ = api(server, "POST", "/api/download",
                           {"url": "https://youtu.be/abc123abc12", "quality": "720"})
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
                           {"url": "https://youtu.be/abc123abc12"})
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
                           {"url": "https://youtu.be/abc123abc12"})
    assert status == 507
    assert "Disk" in body.get("error", "")


def test_cancel_queued_job(server):
    BLOCK.clear()
    try:
        # Occupy both executor workers with blocking downloads.
        first, second = [], []
        for i in range(2):
            s, b, _ = api(server, "POST", "/api/download",
                           {"url": f"https://youtu.be/blockblock{i}"})
            assert s == 200
            first.append(b["job_id"]) if i == 0 else second.append(b["job_id"])
        time.sleep(0.5)  # let workers pick up the blocking jobs
        s, b, _ = api(server, "POST", "/api/download",
                       {"url": "https://youtu.be/queuedqueue"})
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


# --- public job field whitelist -------------------------------------------------
def test_public_job_omits_internal_paths(server):
    status, body, _ = api(server, "POST", "/api/download",
                           {"url": "https://youtu.be/abc123abc12", "quality": "720"})
    assert status == 200, body
    job = wait_for(server, body["job_id"])
    assert job["status"] == "done"
    for leak in ("filename", "seen_files", "seen_ids", "cancel_requested", "payload"):
        assert leak not in job
    # the file is still attributed and listed
    assert job["files"] == ["Test Video [abc123].mp4"]


# --- Host header guard (DNS rebinding) ------------------------------------------
def test_non_loopback_host_rejected(server):
    status, body, _ = api(server, "GET", "/api/health",
                           headers={"Host": "evil.example.com"})
    assert status == 403
    status, body, _ = api(server, "POST", "/api/download",
                           {"url": "https://youtu.be/abc123abc12"},
                           headers={"Host": "offtube.example.com:8000"})
    assert status == 403
    # loopback Host still works
    status, body, _ = api(server, "GET", "/api/health",
                           headers={"Host": "127.0.0.1:8000"})
    assert status == 200


def test_loopback_host_authorities(server):
    for host in ("localhost", "LOCALHOST:8000", "127.0.0.1", "127.0.0.1:8000",
                 "[::1]", "[::1]:8000"):
        status, _, _ = api(server, "GET", "/api/health", headers={"Host": host})
        assert status == 200, host


@pytest.mark.parametrize("method", ["GET", "POST", "OPTIONS"])
def test_invalid_host_authorities_rejected(server, method):
    for host in ("localhost.evil.test", "localhost@evil.test", "localhost/path",
                 "localhost:", "localhost:abc", "localhost:0", "localhost:65536",
                 "::1", "[::1]:abc", "[::1]:8000:9000", "127.0.0.1:8000/path"):
        status, _, headers = api(server, method, "/api/health", headers={"Host": host})
        assert status == 403, (method, host, status)
        assert _acao(headers) is None


def test_missing_and_duplicate_host_headers(server):
    host, port = urllib.parse.urlsplit(server).hostname, urllib.parse.urlsplit(server).port
    for values in ([], ["localhost", "evil.test"], ["localhost", "localhost"]):
        with closing(http.client.HTTPConnection(host, port, timeout=5)) as connection:
            connection.putrequest("GET", "/api/health", skip_host=True)
            for value in values:
                connection.putheader("Host", value)
            connection.endheaders()
            response = connection.getresponse()
            assert response.status == 403, values
            response.read()
    with socket.create_connection((host, port), timeout=5) as connection:
        connection.sendall(b"GET /api/health HTTP/1.0\r\n\r\n")
        assert b" 200 " in connection.recv(4096).split(b"\r\n", 1)[0]


def ipv6_loopback_available() -> bool:
    """socket.has_ipv6 only says Python was built with IPv6; containers and
    some CI kernels still refuse AF_INET6 sockets or have no ::1."""
    if not socket.has_ipv6:
        return False
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
    except OSError:
        return False
    return True


def test_server_binds_ipv6_loopback():
    if not ipv6_loopback_available():
        pytest.skip("IPv6 loopback is unavailable")
    with app.LocalServer(("::1", 0), app.Handler) as httpd:
        assert httpd.address_family == socket.AF_INET6
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            with closing(http.client.HTTPConnection("::1", httpd.server_address[1], timeout=5)) as connection:
                connection.request("GET", "/api/health")
                response = connection.getresponse()
                assert response.status == 200
                assert json.loads(response.read())["ok"] is True
        finally:
            httpd.shutdown()
            thread.join(timeout=5)


# --- /api/jobs/clear -------------------------------------------------------------
def test_jobs_clear_evicts_only_terminal(server):
    ids = []
    for i in range(3):
        s, b, _ = api(server, "POST", "/api/download",
                       {"url": "https://youtu.be/abc123abc12", "quality": "720"})
        assert s == 200, b
        ids.append(b["job_id"])
    for jid in ids:
        wait_for(server, jid)
    s, body, _ = api(server, "POST", "/api/jobs/clear", {})
    assert s == 200 and body["cleared"] == 3
    s, data, _ = api(server, "GET", "/api/jobs")
    assert data["jobs"] == []


# --- /api/retry -------------------------------------------------------------------
def test_retry_unknown_job(server):
    s, body, _ = api(server, "POST", "/api/retry", {"job_id": "nope"})
    assert s == 404


def test_retry_running_job_refused(server):
    BLOCK.clear()
    try:
        s, b, _ = api(server, "POST", "/api/download", {"url": "https://youtu.be/blockblock0"})
        assert s == 200
        jid = b["job_id"]
        time.sleep(0.5)
        s, body, _ = api(server, "POST", "/api/retry", {"job_id": jid})
        assert s == 400 and "still running" in body["error"]
    finally:
        BLOCK.set()
        for j in list(app.jobs.values()):
            if j.get("status") not in ("done", "error", "cancelled"):
                wait_for(server, j["id"], timeout=15)


def test_retry_resubmits_stored_payload(server):
    # First run succeeds; retry must re-enqueue the same url/quality.
    s, b, _ = api(server, "POST", "/api/download",
                   {"url": "https://youtu.be/abc123abc12", "quality": "720"})
    assert s == 200, b
    first = wait_for(server, b["job_id"])
    assert first["status"] == "done"

    s, body, _ = api(server, "POST", "/api/retry", {"job_id": first["id"]})
    assert s == 200, body
    assert body["retried"] == first["id"]
    second = wait_for(server, body["job_id"])
    assert second["status"] == "done"
    assert second["url"] == first["url"]
    assert second["quality"] == first["quality"]


def test_retry_validates_stored_payload(server, tmp_path, monkeypatch):
    # A stored payload that no longer validates (bad clip range) is refused.
    monkeypatch.setattr(app, "DOWNLOAD_DIR", tmp_path / "dl")
    (tmp_path / "dl").mkdir()
    app.jobs.clear()
    jid = "badclip"
    app.jobs[jid] = {
        "id": jid, "url": "https://youtu.be/abc123abc12", "quality": "720",
        "status": "error", "progress": 0, "detail": "x", "log": [], "files": [],
        "payload": {"url": "https://youtu.be/abc123abc12", "clip_from": "02:00", "clip_to": "01:00"},
    }
    try:
        s, body, _ = api(server, "POST", "/api/retry", {"job_id": jid})
        assert s == 400 and "after" in body["error"]
    finally:
        app.jobs.clear()


# --- serve_download_file traversal -----------------------------------------------
def test_download_file_traversal_blocked(server):
    import urllib.error
    for name in ("../app.py", "..%2Fapp.py", "%2e%2e%2fapp.py", ".gitkeep", "sub/../../app.py"):
        try:
            urllib.request.urlopen(server + f"/files/{name}", timeout=10)
            raise AssertionError(f"{name} should 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404


# --- request timeout is configured --------------------------------------------------
def test_handler_has_timeout():
    assert app.Handler.timeout is not None and app.Handler.timeout > 0


# --- gallery-dl engine over HTTP ------------------------------------------------
@pytest.fixture()
def fake_gallery(monkeypatch):
    from test_helpers import FAKE_GALLERY
    monkeypatch.setattr(app, "gallery_cmd",
                        lambda payload, cookie_id: [sys.executable, "-c", FAKE_GALLERY])


def test_pinterest_inspect_and_download_via_gallery(server, fake_gallery):
    status, body, _ = api(server, "POST", "/api/info",
                           {"url": "https://www.pinterest.com/ann/moodboard/"})
    assert status == 200, body
    info = body["info"]
    assert info["engine"] == "gallery-dl" and info["type"] == "gallery"
    assert info["source"] == {"id": "pinterest", "label": "Pinterest"}

    status, body, _ = api(server, "POST", "/api/download",
                           {"url": "https://www.pinterest.com/pin/664281013778109217/"})
    assert status == 200, body
    job = wait_for(server, body["job_id"])
    assert job["status"] == "done", job
    assert job["source"] == "Pinterest"
    assert "pinterest_1.jpg" in job["files"]
    assert "proc" not in job


def test_cancel_running_gallery_job_kills_process(server, fake_gallery):
    status, body, _ = api(server, "POST", "/api/download",
                           {"url": "https://www.pinterest.com/pin/664281013778109217/?slow=1"})
    assert status == 200, body
    jid = body["job_id"]
    deadline = time.time() + 10
    while time.time() < deadline and app.jobs[jid].get("proc") is None:
        time.sleep(0.05)
    started = time.time()
    status, _, _ = api(server, "POST", "/api/cancel", {"job_id": jid})
    assert status == 200
    job = wait_for(server, jid, timeout=10)
    assert job["status"] == "cancelled"
    assert time.time() - started < 5  # terminated, not left to sleep 30s


def test_download_keeps_display_fields_and_files_report_kind(server):
    status, body, _ = api(server, "POST", "/api/download",
                           {"url": "https://youtu.be/abc123abc12", "title": "Shown title",
                            "thumbnail": "javascript:alert(1)"})
    assert status == 200, body
    job = wait_for(server, body["job_id"])
    assert job["title"] == "Shown title"
    assert job["thumbnail"] is None  # unsafe scheme dropped; stub has no thumbnail
    status, listing, _ = api(server, "GET", "/api/files")
    assert {f["name"]: f["kind"] for f in listing["files"]}["Test Video [abc123].mp4"] == "video"


def test_fonts_are_served_and_traversal_blocked(server):
    import urllib.error
    with urllib.request.urlopen(server + "/fonts/archivo.woff2", timeout=10) as res:
        assert res.status == 200 and res.headers["Content-Type"] == "font/woff2"
    try:
        urllib.request.urlopen(server + "/fonts/..%2F..%2Fapp.py", timeout=10)
        raise AssertionError("traversal should 404")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404


# --- library snapshots: what is listed is exactly what is served -----------------
def _http_status(base, path, method="GET"):
    import urllib.error
    req = urllib.request.Request(base + path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""


def test_listing_survives_files_vanishing_mid_scan(server, monkeypatch):
    (app.DOWNLOAD_DIR / "stays [a].mp4").write_bytes(b"a")
    (app.DOWNLOAD_DIR / "gone [b].mp4").write_bytes(b"b")
    real_scandir = os.scandir

    class Snapshot:
        """scandir that loses a file after listing it, as when a concurrent
        delete or an engine rename lands between the scan and the stat."""
        def __init__(self, path):
            with real_scandir(path) as entries:
                self.entries = list(entries)
            (app.DOWNLOAD_DIR / "gone [b].mp4").unlink()

        def __enter__(self):
            return iter(self.entries)

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(app.os, "scandir", Snapshot)
    status, listing, _ = api(server, "GET", "/api/files")
    assert status == 200
    assert [f["name"] for f in listing["files"]] == ["stays [a].mp4"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlinks")
def test_symlinks_are_never_listed_served_or_followed_on_delete(server, tmp_path):
    inside = app.DOWNLOAD_DIR / "inside [a].mp4"
    inside.write_bytes(b"kept")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"private")
    (app.DOWNLOAD_DIR / "alias [a].mp4").symlink_to(inside)
    (app.DOWNLOAD_DIR / "escape [b].mp4").symlink_to(outside)

    status, listing, _ = api(server, "GET", "/api/files")
    assert [f["name"] for f in listing["files"]] == ["inside [a].mp4"]
    for name in ("alias [a].mp4", "escape [b].mp4"):
        assert _http_status(server, "/files/" + urllib.parse.quote(name))[0] == 404
        status, _, _ = api(server, "POST", "/api/files/delete", {"name": name})
        assert status == 404
    # Deleting the alias must never delete the file it points at.
    assert inside.read_bytes() == b"kept" and outside.read_bytes() == b"private"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFOs")
def test_special_files_are_refused_without_blocking(server):
    os.mkfifo(app.DOWNLOAD_DIR / "pipe [x].mp4")
    status, listing, _ = api(server, "GET", "/api/files")
    assert status == 200 and listing["files"] == []
    started = time.monotonic()
    assert _http_status(server, "/files/" + urllib.parse.quote("pipe [x].mp4"))[0] == 404
    assert time.monotonic() - started < 3


def test_names_are_one_component_and_taken_verbatim(server):
    (app.DOWNLOAD_DIR / "x.mp4").write_bytes(b"x")
    # An encoded separator is not a way to reach a differently named file.
    assert _http_status(server, "/files/sub%2Fx.mp4")[0] == 404
    assert _http_status(server, "/files/x.mp4")[0] == 200
    # Delete bodies are JSON, not URLs: a literal %XX in a title is kept.
    literal = app.DOWNLOAD_DIR / "Sale 100%BE [s].mp4"
    literal.write_bytes(b"s")
    status, body, _ = api(server, "POST", "/api/files/delete", {"name": literal.name})
    assert status == 200 and body["deleted"] == literal.name
    assert not literal.exists()


def test_partial_downloads_are_neither_served_nor_deleted(server):
    part = app.DOWNLOAD_DIR / "Video [v].mp4.part"
    part.write_bytes(b"half")
    assert _http_status(server, "/files/" + urllib.parse.quote(part.name))[0] == 404
    status, _, _ = api(server, "POST", "/api/files/delete", {"name": part.name})
    assert status == 404 and part.exists()


def test_delete_failure_names_the_reason_not_the_path(server, monkeypatch):
    (app.DOWNLOAD_DIR / "locked [l].mp4").write_bytes(b"l")

    def refuse(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "unlink", refuse)
    status, body, _ = api(server, "POST", "/api/files/delete", {"name": "locked [l].mp4"})
    assert status == 500
    assert body["error"] == "Could not delete: Permission denied."
    status, body, _ = api(server, "POST", "/api/files/delete", {"name": ["locked [l].mp4"]})
    assert status == 400


# --- byte ranges: media elements read what they need and can stop cleanly --------
def _fetch(base, path, headers=None, method="GET"):
    host, port = urllib.parse.urlsplit(base).hostname, urllib.parse.urlsplit(base).port
    with closing(http.client.HTTPConnection(host, port, timeout=5)) as connection:
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()


@pytest.fixture()
def kept_bytes(server):
    data = bytes(range(256)) * 4  # 1024 distinguishable bytes
    (app.DOWNLOAD_DIR / "clip [r].mp4").write_bytes(data)
    return server, "/files/" + urllib.parse.quote("clip [r].mp4"), data


@pytest.mark.parametrize("spec, first, last", [
    ("bytes=2-5", 2, 5), ("bytes=1000-", 1000, 1023), ("bytes=-24", 1000, 1023),
    ("bytes=1020-99999", 1020, 1023), ("bytes=-5000", 0, 1023), ("BYTES = 7 - 7", 7, 7),
])
def test_single_byte_range_is_partial_content(kept_bytes, spec, first, last):
    base, path, data = kept_bytes
    status, headers, body = _fetch(base, path, {"Range": spec})
    assert status == 206
    assert headers["Content-Range"] == f"bytes {first}-{last}/{len(data)}"
    assert int(headers["Content-Length"]) == last - first + 1
    assert body == data[first:last + 1]
    assert headers["Accept-Ranges"] == "bytes"


def test_whole_file_advertises_ranges_and_validators(kept_bytes):
    base, path, data = kept_bytes
    status, headers, body = _fetch(base, path)
    assert status == 200 and body == data
    assert headers["Accept-Ranges"] == "bytes"
    assert headers["ETag"].startswith('"') and headers["Last-Modified"].endswith("GMT")


@pytest.mark.parametrize("spec", ["bytes=0-1,4-5", "bytes=5-2", "bytes=-", "items=0-1", "bytes=x-",
                                  "bytes=" + "9" * 30 + "-"])
def test_unsupported_or_malformed_ranges_get_the_whole_file(kept_bytes, spec):
    base, path, data = kept_bytes
    status, _, body = _fetch(base, path, {"Range": spec})
    assert status == 200 and body == data


@pytest.mark.parametrize("spec", ["bytes=1024-", "bytes=5000-6000", "bytes=-0"])
def test_unsatisfiable_range_is_416_with_the_length(kept_bytes, spec):
    base, path, data = kept_bytes
    status, headers, body = _fetch(base, path, {"Range": spec})
    assert status == 416
    assert headers["Content-Range"] == f"bytes */{len(data)}"
    assert body == b""


def test_if_range_only_resumes_the_same_file(kept_bytes):
    base, path, data = kept_bytes
    _, headers, _ = _fetch(base, path)
    for validator in (headers["ETag"], headers["Last-Modified"]):
        status, _, body = _fetch(base, path, {"Range": "bytes=10-19", "If-Range": validator})
        assert status == 206 and body == data[10:20]
    for stale in ('"0-0"', "W/" + headers["ETag"], "Thu, 01 Jan 1970 00:00:00 GMT"):
        status, _, body = _fetch(base, path, {"Range": "bytes=10-19", "If-Range": stale})
        assert status == 200 and body == data


def test_head_ignores_ranges(kept_bytes):
    base, path, data = kept_bytes
    status, headers, body = _fetch(base, path, {"Range": "bytes=0-9"}, method="HEAD")
    assert status == 200 and body == b""
    assert int(headers["Content-Length"]) == len(data)


def test_client_closing_mid_file_is_not_a_server_error(server, capfd):
    big = app.DOWNLOAD_DIR / "big [b].mp4"
    with big.open("wb") as fh:
        fh.truncate(64 * 1024 * 1024)  # sparse: cheap to create, too big to buffer
    host, port = urllib.parse.urlsplit(server).hostname, urllib.parse.urlsplit(server).port
    with socket.create_connection((host, port), timeout=5) as connection:
        connection.sendall(b"GET /files/big%20%5Bb%5D.mp4 HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        assert connection.recv(64).startswith(b"HTTP/1.0 200")
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
    # The handler finishes on its own; the abort must not print a traceback.
    status, _, _ = api(server, "GET", "/api/health")
    assert status == 200
    time.sleep(0.5)
    assert "Traceback" not in capfd.readouterr().err


# --- admission: bounded threads, bounded time to send a request -------------------
@pytest.fixture()
def small_server(monkeypatch):
    monkeypatch.setattr(app.LocalServer, "max_connections", 2)
    monkeypatch.setattr(app, "REQUEST_READ_DEADLINE", 0.6)
    httpd = app.LocalServer(("127.0.0.1", 0), app.Handler)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _health(port):
    with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=5)) as connection:
        connection.request("GET", "/api/health")
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()


def test_connections_past_the_cap_are_refused_at_once(small_server):
    port = small_server.server_address[1]
    idle = [socket.create_connection(("127.0.0.1", port)) for _ in range(2)]
    try:
        time.sleep(0.1)  # both handlers are now waiting for a request
        started = time.monotonic()
        status, headers, body = _health(port)
        assert status == 503 and time.monotonic() - started < 0.5
        assert headers["Retry-After"] == "1"
        assert json.loads(body)["ok"] is False
    finally:
        for connection in idle:
            connection.close()
    deadline = time.monotonic() + 3
    while _health(port)[0] != 200:  # closed clients free their slots
        assert time.monotonic() < deadline


def test_slow_requests_are_cut_off_at_the_deadline(small_server):
    port = small_server.server_address[1]
    for opening in (b"GET /api/health HTTP/1.1\r\n",
                    b"POST /api/info HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 50\r\n\r\n{"):
        with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
            connection.sendall(opening)
            started = time.monotonic()
            with pytest.raises(OSError):
                # A byte every 0.1 s resets any per-recv timeout; only a
                # deadline for the whole request ends it.
                while time.monotonic() - started < 4:
                    connection.sendall(b"x")
                    time.sleep(0.1)
            assert time.monotonic() - started < 2
    assert _health(port)[0] == 200  # and the slots came back


def test_oversized_body_drain_is_bounded(server, monkeypatch):
    monkeypatch.setattr(app, "MAX_JSON_BODY", 1024)
    host, port = urllib.parse.urlsplit(server).hostname, urllib.parse.urlsplit(server).port
    with socket.create_connection((host, port), timeout=5) as connection:
        connection.sendall(b"POST /api/info HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                           b"Content-Type: application/json\r\nContent-Length: 10000000000\r\n\r\n")
        sent, started = 0, time.monotonic()
        with pytest.raises(OSError):
            while time.monotonic() - started < 5:  # a fast client claiming 10 GB
                connection.sendall(b"x" * 65536)
                sent += 65536
    assert sent < 64 * 1024 * 1024


def _process_gone(pid):
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return not state.stdout.strip() or state.stdout.strip().startswith("Z")


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_cancelling_a_gallery_job_stops_what_gallery_dl_started(server, fake_gallery):
    status, body, _ = api(server, "POST", "/api/download",
                          {"url": "https://www.pinterest.com/pin/664281013778109217/?child"})
    assert status == 200, body
    pid_file = app.DOWNLOAD_DIR / "child.pid"
    deadline = time.time() + 10
    while not pid_file.exists() or not pid_file.read_text():
        assert time.time() < deadline
        time.sleep(0.05)
    child = int(pid_file.read_text())
    status, _, _ = api(server, "POST", "/api/cancel", {"job_id": body["job_id"]})
    assert status == 200
    assert wait_for(server, body["job_id"], timeout=10)["status"] == "cancelled"
    deadline = time.time() + 5
    while not _process_gone(child):
        assert time.time() < deadline, "gallery-dl's child outlived the cancelled job"
        time.sleep(0.05)


def test_unknown_quality_is_refused_before_queueing(server):
    status, body, _ = api(server, "POST", "/api/download",
                          {"url": "https://youtu.be/abc123abc12", "quality": "GIF"})
    assert status == 400 and "quality" in body["error"]
    assert not app.jobs
