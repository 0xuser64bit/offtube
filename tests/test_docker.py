"""The container is unprivileged and can write the bind-mounted folders."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_image_runs_as_an_unprivileged_user():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    users = [line.split(None, 1)[1].strip() for line in dockerfile.splitlines()
             if line.upper().startswith("USER ")]
    assert users, "the image never drops root"
    assert users[-1] not in ("root", "0"), "the image still runs as root"
    assert "chown -R offtube:offtube /app/downloads /app/cookies" in dockerfile


def test_compose_builds_for_the_host_user():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'UID: "${OFFTUBE_UID:-1000}"' in compose
    assert 'GID: "${OFFTUBE_GID:-1000}"' in compose


def test_image_user_is_not_root_by_default():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG UID=1000" in dockerfile
    assert "ARG GID=1000" in dockerfile
    assert 'USER offtube' in dockerfile
    # The app needs a writable home for yt-dlp's cache, even without a login.
    assert "ENV HOME=/home/offtube" in dockerfile
