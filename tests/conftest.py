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


def processes_started_by(pid: int) -> list[tuple[int, str]]:
    """(pid, name) of the running processes that `pid` started: the rest of
    its process group on POSIX (offtube's children lead their own), every
    descendant on Windows (where, say, a Chocolatey ffmpeg.exe is a shim
    that starts the real one)."""
    if os.name == "nt":
        listed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-CimInstance Win32_Process"
             " | ForEach-Object { '{0} {1} {2}' -f $_.ProcessId, $_.ParentProcessId, $_.Name }"],
            capture_output=True, text=True)
        rows = [line.split(maxsplit=2) for line in listed.stdout.splitlines()]
        rows = [(int(row[0]), int(row[1]), row[2]) for row in rows
                if len(row) == 3 and row[0].isdigit() and row[1].isdigit()]
        found, parents = [], {pid}
        while True:
            children = [(child, name) for child, parent, name in rows
                        if parent in parents and child not in parents and (child, name) not in found]
            if not children:
                return found
            found += children
            parents |= {child for child, _ in children}
    listed = subprocess.run(["ps", "-axo", "pid=,pgid=,comm="], capture_output=True, text=True)
    rows = [line.split(maxsplit=2) for line in listed.stdout.splitlines()]
    return [(int(row[0]), row[2]) for row in rows
            if len(row) == 3 and int(row[1]) == pid and int(row[0]) != pid]


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
