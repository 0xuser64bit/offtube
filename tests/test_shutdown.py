"""Stopping the server: queued jobs cancel, running ones stop with their
process trees, credentials are removed, and the process actually exits.

Downloads run in tests/fake_ytdlp_worker.py (see conftest.py). The
subprocess tests run the real main() and send real signals, because a hang
at interpreter exit is invisible to in-process tests.
"""
import concurrent.futures
import json
import os
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app  # noqa: E402
from conftest import FAKE_WORKER, process_gone  # noqa: E402

COOKIES = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tFALSE\t9999999999\tSID\tsecret\n"
CHATTY = "https://youtu.be/chattychat1"  # reports progress until stopped
SILENT = "https://youtu.be/blockblock1"  # silent, as during extraction or a merge


@pytest.fixture()
def fresh_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DOWNLOAD_DIR", tmp_path / "downloads")
    monkeypatch.setattr(app, "COOKIES_DIR", tmp_path / "cookies")
    monkeypatch.setattr(app, "executor", concurrent.futures.ThreadPoolExecutor(max_workers=2))
    monkeypatch.setattr(app, "shutting_down", False)
    app.DOWNLOAD_DIR.mkdir()
    app.COOKIES_DIR.mkdir()
    app.jobs.clear()
    app.futures.clear()
    yield
    app.shutdown_jobs(grace=5)
    app.executor.shutdown(wait=True, cancel_futures=True)
    app.jobs.clear()
    app.futures.clear()


def _enqueue(url=CHATTY):
    jid, error = app.enqueue_download({"url": url, "cookies_mode": "text", "cookies_text": COOKIES})
    assert error is None, error
    return jid


def _inside_engine(jid, timeout=10):
    """Wait until the job's thread has written its cookie copy, the last step
    before it starts the worker. Its status leaves "queued" earlier than
    that, before URL checks and runtime probes that can take a while on CI."""
    deadline = time.monotonic() + timeout
    while not app.cookie_file_for(jid).exists():
        assert time.monotonic() < deadline, app.jobs[jid]
        time.sleep(0.02)


def _running_worker(jid, timeout=10):
    deadline = time.monotonic() + timeout
    while app.jobs[jid].get("proc") is None:
        assert time.monotonic() < deadline, app.jobs[jid]
        time.sleep(0.02)
    return app.jobs[jid]["proc"]


def test_shutdown_cancels_queued_and_stops_running_jobs(fresh_queue):
    running = [_enqueue(), _enqueue()]
    queued = _enqueue()
    for jid in running:
        _inside_engine(jid)

    started = time.monotonic()
    assert app.shutdown_jobs(grace=5) is True
    assert time.monotonic() - started < 3
    assert app.jobs[queued]["status"] == "cancelled"
    assert all(app.jobs[jid]["status"] == "cancelled" for jid in running)
    assert not list(app.COOKIES_DIR.glob("job-*.cookies.txt"))
    # Nothing new is accepted once stopping has begun.
    jid, error = app.enqueue_download({"url": CHATTY})
    assert jid is None and error[1] == 503


def test_shutdown_stops_silent_downloads_too(fresh_queue):
    # yt-dlp reports nothing while it extracts or merges; stopping its
    # process is what ends that, not a progress callback.
    jid = _enqueue(SILENT)
    worker = _running_worker(jid)
    started = time.monotonic()
    assert app.shutdown_jobs(grace=5) is True
    assert time.monotonic() - started < 3
    assert app.jobs[jid]["status"] == "cancelled"
    assert worker.poll() is not None


def test_shutdown_reports_work_that_does_not_stop_in_time(fresh_queue, monkeypatch):
    # A job thread busy in something that is not a child process, here a
    # call that blocks before the worker starts, can't be stopped.
    entered, release = threading.Event(), threading.Event()

    def held():
        entered.set()
        release.wait(30)
        return FAKE_WORKER

    monkeypatch.setattr(app, "ytdlp_worker_cmd", held)
    jid = _enqueue()
    try:
        assert entered.wait(10)
        started = time.monotonic()
        assert app.shutdown_jobs(grace=0.5) is False
        assert 0.5 <= time.monotonic() - started < 2
        # Its credentials are removed even though its thread is still busy.
        assert not app.cookie_file_for(jid).exists()
    finally:
        release.set()


def test_shutdown_stops_child_process_trees(fresh_queue, monkeypatch):
    from test_helpers import FAKE_GALLERY
    monkeypatch.setattr(app, "gallery_cmd", lambda payload, cookie_id: [sys.executable, "-c", FAKE_GALLERY])
    jid, error = app.enqueue_download({"url": "https://www.pinterest.com/pin/664281013778109217/?child"})
    assert error is None
    pid_file = app.DOWNLOAD_DIR / "child.pid"
    deadline = time.monotonic() + 10
    while not (pid_file.exists() and pid_file.read_text()):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    child = int(pid_file.read_text())
    assert app.shutdown_jobs(grace=5) is True
    assert app.jobs[jid]["status"] == "cancelled"
    deadline = time.monotonic() + 5
    while not process_gone(child):
        assert time.monotonic() < deadline, "gallery-dl's child survived shutdown"
        time.sleep(0.05)


# --- the real process ------------------------------------------------------------
# argv: repo root, data dir, kind of work, then the fake worker's command.
# "held" keeps the job thread busy before it starts the worker, so shutdown
# has to give up on it after the grace period.
BOOT = textwrap.dedent("""
    import sys, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    import app

    def held():
        time.sleep(120)
        return sys.argv[4:]

    app.ytdlp_worker_cmd = held if sys.argv[3] == "held" else (lambda: sys.argv[4:])
    app.SHUTDOWN_GRACE = 1.0
    app.DOWNLOAD_DIR = Path(sys.argv[2]) / "downloads"
    app.COOKIES_DIR = Path(sys.argv[2]) / "cookies"
    app.DOWNLOAD_DIR.mkdir(exist_ok=True)
    app.COOKIES_DIR.mkdir(exist_ok=True)
    app.main()
""")


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _post(port, path, body):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "SIGINT"])
@pytest.mark.parametrize("work, url, within", [
    ("chatty", CHATTY, 2),
    ("silent", SILENT, 2),
    ("held", CHATTY, 1.0 + 2),  # the boot script's grace, then exit anyway
])
def test_server_process_exits_promptly_and_cleans_up(tmp_path, sig, work, url, within):
    boot = tmp_path / "boot.py"
    boot.write_text(BOOT)
    port = _free_port()
    server = subprocess.Popen([sys.executable, str(boot), str(app.BASE_DIR), str(tmp_path), work, *FAKE_WORKER],
                              env={**os.environ, "PORT": str(port)},
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
                break
            except OSError:
                assert server.poll() is None, server.stdout.read()
                assert time.monotonic() < deadline
                time.sleep(0.1)
        jid = _post(port, "/api/download", {"url": url, "cookies_mode": "text", "cookies_text": COOKIES})["job_id"]
        cookie = tmp_path / "cookies" / f"job-{jid}.cookies.txt"
        deadline = time.monotonic() + 5
        while not cookie.exists():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        time.sleep(0.3)  # the job is in its worker, or held before it
        started = time.monotonic()
        server.send_signal(sig)
        output, _ = server.communicate(timeout=10)
        elapsed = time.monotonic() - started
    finally:
        if server.poll() is None:
            server.kill()
            server.wait()
    assert server.returncode == 0, output
    assert elapsed < within, output
    assert not cookie.exists(), "a job's cookie copy outlived the server"
    assert "Traceback" not in output, output
