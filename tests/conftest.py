"""Downloads run in a worker process that imports the real yt-dlp, which
would reach the network. Every test gets tests/fake_ytdlp_worker.py instead;
a test that drives the real worker against local fixtures says so with
@pytest.mark.real_worker."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app  # noqa: E402

FAKE_WORKER = [sys.executable, str(Path(__file__).resolve().parent / "fake_ytdlp_worker.py")]


def process_gone(pid: int) -> bool:
    """True once `pid` no longer runs. A zombie counts as gone: only its
    parent, not the test, can reap it."""
    if os.name == "nt":
        listed = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                capture_output=True, text=True)
        return f'"{pid}"' not in listed.stdout
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return not state.stdout.strip() or state.stdout.strip().startswith("Z")


def pytest_configure(config):
    config.addinivalue_line("markers", "real_worker: run tools/ytdlp_worker.py itself (local fixtures only)")


@pytest.fixture(autouse=True)
def offline_worker(request, monkeypatch):
    if request.node.get_closest_marker("real_worker") is None:
        monkeypatch.setattr(app, "ytdlp_worker_cmd", lambda: FAKE_WORKER)


@pytest.fixture(autouse=True)
def fresh_health_checks():
    # /api/health caches its probes for 60 s; a test's stubs must not leak
    # into the next test's answer.
    app._health_cache.update(at=0.0, checks={})
